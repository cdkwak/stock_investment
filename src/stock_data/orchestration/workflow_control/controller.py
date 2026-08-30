"""Durable idempotent event pump for Canonical Queue workflow operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Iterable, Mapping, TypeVar

from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    TaskState,
    WorkflowEvent,
    parse_utc,
    utc_text,
)
from stock_data.orchestration.workflow_control.events import canonical_event_json
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleKind,
    RoleRecord,
    RoleRegistry,
    RoleRegistryError,
    RoleState,
    StaleRoleGeneration,
)
from stock_data.orchestration.workflow_control.routing import (
    LeadPlan,
    QueueWorkItem,
    RoleAction,
    TaskContract,
    WorkflowRole,
    require_role_authority,
    require_unique_role_sessions,
    select_dependency_ready_leads,
)
from stock_data.orchestration.workflow_control.runner import (
    ExecutionMetadata,
    InjectedDirectRunner,
    RunnerAction,
)
from stock_data.orchestration.workflow_control.session_runner import (
    InjectedSessionRunner,
    SessionAction,
)
from stock_data.orchestration.workflow_control.state import WorkflowStateStore
from stock_data.orchestration.workflow_control.watchdog import RecoveryProposal


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_GuardedResult = TypeVar("_GuardedResult")


class WorkflowControllerError(RuntimeError):
    pass


class StaleControlGeneration(WorkflowControllerError):
    pass


class StaleQueueGeneration(WorkflowControllerError):
    """A role message targeted an obsolete Queue contract generation."""


class MailboxConflict(WorkflowControllerError):
    """A durable message id or acknowledgement was rebound."""


class ReviewLoopError(WorkflowControllerError):
    """The Worker/Reviewer protocol left its bounded state machine."""


class MailboxStatus(StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"


class MailboxMessageType(StrEnum):
    OPERATIONAL_PM = "operational_pm"
    DIRECT_MESSAGE = "direct_message"
    OPERATIONAL_WAKE = "operational_wake"
    USER_INTENT = "user_intent"
    TASK_CONTRACT = "task_contract"
    WORKER_ASSIGNMENT = "worker_assignment"
    CANDIDATE = "candidate"
    REVIEW_VISIBILITY = "review_visibility"
    FIX = "fix"
    PASS = "pass"
    REPLAN_REQUIRED = "replan_required"


class ReviewDecision(StrEnum):
    FIX = "FIX"
    PASS = "PASS"


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ControlGeneration:
    sequence: int
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise WorkflowControllerError("generation sequence must be positive")
        if _DIGEST.fullmatch(self.digest) is None:
            raise WorkflowControllerError("generation digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class PumpReceipt:
    generation_sequence: int
    generation_digest: str
    input_digest: str
    accepted_event_ids: tuple[str, ...]
    duplicate_event_ids: tuple[str, ...]
    stale_event_ids: tuple[str, ...]
    runner_receipt_digests: tuple[str, ...]
    production_mutated: bool = False
    execution_profile: str = ""
    execution_profile_digest: str = ""
    workspace_write_enabled: bool = False
    mutation_observed: bool | None = False
    orca_used: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.execution_profile_digest:
            metadata = ExecutionMetadata(
                self.execution_profile,
                self.workspace_write_enabled,
                self.mutation_observed,
                self.orca_used,
                self.execution_profile_digest,
            )
            if self.production_mutated is not (metadata.mutation_observed is True):
                raise WorkflowControllerError(
                    "pump mutation claim does not match execution metadata"
                )
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise WorkflowControllerError("pump receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "generation_sequence": self.generation_sequence,
            "generation_digest": self.generation_digest,
            "input_digest": self.input_digest,
            "accepted_event_ids": list(self.accepted_event_ids),
            "duplicate_event_ids": list(self.duplicate_event_ids),
            "stale_event_ids": list(self.stale_event_ids),
            "runner_receipt_digests": list(self.runner_receipt_digests),
            "production_mutated": self.production_mutated,
        }
        if self.execution_profile_digest:
            value.update(
                {
                    "execution_profile": self.execution_profile,
                    "execution_profile_digest": self.execution_profile_digest,
                    "workspace_write_enabled": self.workspace_write_enabled,
                    "mutation_observed": self.mutation_observed,
                    "orca_used": self.orca_used,
                }
            )
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PumpReceipt":
        return cls(
            generation_sequence=int(value["generation_sequence"]),
            generation_digest=str(value["generation_digest"]),
            input_digest=str(value["input_digest"]),
            accepted_event_ids=tuple(str(item) for item in value["accepted_event_ids"]),  # type: ignore[union-attr]
            duplicate_event_ids=tuple(str(item) for item in value["duplicate_event_ids"]),  # type: ignore[union-attr]
            stale_event_ids=tuple(str(item) for item in value["stale_event_ids"]),  # type: ignore[union-attr]
            runner_receipt_digests=tuple(str(item) for item in value["runner_receipt_digests"]),  # type: ignore[union-attr]
            production_mutated=bool(value["production_mutated"]),
            execution_profile=str(value.get("execution_profile", "")),
            execution_profile_digest=str(value.get("execution_profile_digest", "")),
            workspace_write_enabled=bool(value.get("workspace_write_enabled", False)),
            mutation_observed=value.get("mutation_observed", False),  # type: ignore[arg-type]
            orca_used=bool(value.get("orca_used", False)),
            receipt_digest=str(value["receipt_digest"]),
        )


@dataclass(frozen=True, slots=True)
class RecoveryReceipt:
    action: str
    task_id: str | None
    retry_attempt: int | None
    retry_provenance: str | None
    connected_terminal: bool
    agent_process_live: bool
    runner_receipt_digests: tuple[str, ...]
    production_mutated: bool = False
    execution_profile: str = ""
    execution_profile_digest: str = ""
    workspace_write_enabled: bool = False
    mutation_observed: bool | None = False
    orca_used: bool = False


@dataclass(frozen=True, slots=True)
class MailboxEnvelope:
    message_id: str
    parent_message_id: str | None
    sender_role_key: str
    recipient_role_key: str
    message_type: MailboxMessageType
    task_id: str | None
    queue_generation: str | None
    recipient_generation: int
    recipient_session_id: str
    body_digest: str
    body: Mapping[str, object]
    created_at: datetime
    delivery_status: MailboxStatus

    def __post_init__(self) -> None:
        for value, label in (
            (self.message_id, "message id"),
            (self.sender_role_key, "sender role key"),
            (self.recipient_role_key, "recipient role key"),
        ):
            if _IDENTIFIER.fullmatch(value) is None:
                raise WorkflowControllerError(f"{label} is invalid")
        if self.parent_message_id is not None and _IDENTIFIER.fullmatch(self.parent_message_id) is None:
            raise WorkflowControllerError("parent message id is invalid")
        if self.task_id is not None and _TASK_ID.fullmatch(self.task_id) is None:
            raise WorkflowControllerError("mailbox task id is invalid")
        if self.queue_generation is not None and _IDENTIFIER.fullmatch(self.queue_generation) is None:
            raise WorkflowControllerError("mailbox Queue generation is invalid")
        if not isinstance(self.recipient_generation, int) or self.recipient_generation < 1:
            raise WorkflowControllerError("recipient generation must be positive")
        if _IDENTIFIER.fullmatch(self.recipient_session_id) is None:
            raise WorkflowControllerError("recipient session id is invalid")
        if _DIGEST.fullmatch(self.body_digest) is None:
            raise WorkflowControllerError("mailbox body digest must be SHA-256")
        if not isinstance(self.message_type, MailboxMessageType):
            raise WorkflowControllerError("mailbox message type is invalid")
        if not isinstance(self.delivery_status, MailboxStatus):
            raise WorkflowControllerError("mailbox status is invalid")
        if self.body_digest != hashlib.sha256(_canonical(self.body).encode("utf-8")).hexdigest():
            raise WorkflowControllerError("mailbox body digest does not match")
        utc_text(self.created_at)


@dataclass(frozen=True, slots=True)
class MailboxAcknowledgement:
    message_id: str
    recipient_role_key: str
    recipient_generation_before: int
    recipient_generation_after: int
    acknowledgement_ref: str
    acknowledged_at: datetime


@dataclass(frozen=True, slots=True)
class HierarchyResumeReceipt:
    root_role_key: str
    role_keys: tuple[str, ...]
    session_ids: tuple[str, ...]
    runner_receipt_digests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewLoopReceipt:
    task_id: str
    queue_generation: str
    worker_role_key: str
    reviewer_role_key: str
    decision: ReviewDecision
    fix_count: int
    state: str
    message_ids: tuple[str, ...]
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        expected = _digest(
            {
                "task_id": self.task_id,
                "queue_generation": self.queue_generation,
                "worker_role_key": self.worker_role_key,
                "reviewer_role_key": self.reviewer_role_key,
                "decision": self.decision.value,
                "fix_count": self.fix_count,
                "state": self.state,
                "message_ids": list(self.message_ids),
            }
        )
        if self.receipt_digest and self.receipt_digest != expected:
            raise ReviewLoopError("review receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)


class WorkflowController:
    """Consume ordered workflow facts and drive only an injected direct runner."""

    def __init__(
        self,
        state_store: WorkflowStateStore,
        runner: InjectedDirectRunner,
        receipt_path: Path,
        *,
        max_recovery_attempts: int = 3,
        session_runner: InjectedSessionRunner | None = None,
        role_registry: RoleRegistry | None = None,
    ) -> None:
        if not 1 <= max_recovery_attempts <= 10:
            raise WorkflowControllerError("recovery bound must be between one and ten")
        self.state_store = state_store
        self.runner = runner
        self.receipt_path = Path(receipt_path)
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_recovery_attempts = max_recovery_attempts
        self.session_runner = session_runner
        self.role_registry = role_registry or RoleRegistry(
            self.receipt_path.with_name("role_registry.sqlite3")
        )
        self.execution_metadata = runner.execution_metadata
        if (
            session_runner is not None
            and session_runner.execution_metadata.profile_digest
            != self.execution_metadata.profile_digest
        ):
            raise WorkflowControllerError(
                "direct and session runners must share one execution profile"
            )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.receipt_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS control_generation(sequence INTEGER PRIMARY KEY, digest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS event_disposition("
                "event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, disposition TEXT NOT NULL, "
                "generation_sequence INTEGER NOT NULL, generation_digest TEXT NOT NULL, "
                "runner_receipt_digest TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS pump_receipt(sequence INTEGER NOT NULL, generation_digest TEXT NOT NULL, input_digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(sequence, input_digest))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS hierarchy_task("
                "task_id TEXT PRIMARY KEY, queue_generation TEXT NOT NULL, "
                "pm_role_key TEXT NOT NULL, lead_role_key TEXT NOT NULL, reviewer_role_key TEXT NOT NULL, "
                "contract_digest TEXT NOT NULL, contract_json TEXT NOT NULL, state TEXT NOT NULL, "
                "fix_count INTEGER NOT NULL DEFAULT 0 CHECK(fix_count BETWEEN 0 AND 3))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS task_contract_history("
                "task_id TEXT NOT NULL, queue_generation TEXT NOT NULL, contract_digest TEXT NOT NULL, "
                "contract_json TEXT NOT NULL, PRIMARY KEY(task_id, queue_generation))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS worker_assignment("
                "task_id TEXT NOT NULL, queue_generation TEXT NOT NULL, worker_role_key TEXT NOT NULL, "
                "write_scope_json TEXT NOT NULL, candidate_digest TEXT, candidate_state TEXT, "
                "PRIMARY KEY(task_id, queue_generation, worker_role_key))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lead_checkpoint("
                "checkpoint_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, queue_generation TEXT NOT NULL, "
                "lead_role_key TEXT NOT NULL, checkpoint_digest TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS role_mailbox("
                "message_id TEXT PRIMARY KEY, parent_message_id TEXT, sender_role_key TEXT NOT NULL, "
                "recipient_role_key TEXT NOT NULL, message_type TEXT NOT NULL, task_id TEXT, "
                "queue_generation TEXT, recipient_generation INTEGER NOT NULL, body_digest TEXT NOT NULL, "
                "body_json TEXT NOT NULL, created_at TEXT NOT NULL, delivery_status TEXT NOT NULL, "
                "acknowledgement_ref TEXT, acknowledged_at TEXT, ack_recipient_generation INTEGER, "
                "recipient_session_id TEXT, envelope_digest TEXT)"
            )
            mailbox_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(role_mailbox)")
            }
            if "ack_recipient_generation" not in mailbox_columns:
                connection.execute(
                    "ALTER TABLE role_mailbox ADD COLUMN ack_recipient_generation INTEGER"
                )
            if "recipient_session_id" not in mailbox_columns:
                connection.execute(
                    "ALTER TABLE role_mailbox ADD COLUMN recipient_session_id TEXT"
                )
            if "envelope_digest" not in mailbox_columns:
                connection.execute(
                    "ALTER TABLE role_mailbox ADD COLUMN envelope_digest TEXT"
                )
            for row in connection.execute(
                "SELECT * FROM role_mailbox WHERE recipient_session_id IS NULL "
                "OR envelope_digest IS NULL"
            ).fetchall():
                session_id = row["recipient_session_id"]
                if session_id is None:
                    session_id = self.role_registry.get(
                        str(row["recipient_role_key"])
                    ).identity.codex_session_id
                    connection.execute(
                        "UPDATE role_mailbox SET recipient_session_id = ? WHERE message_id = ?",
                        (session_id, row["message_id"]),
                    )
                material = self._envelope_material(row, str(session_id))
                connection.execute(
                    "UPDATE role_mailbox SET envelope_digest = ? WHERE message_id = ?",
                    (_digest(material), row["message_id"]),
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS review_receipt("
                "operation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, queue_generation TEXT NOT NULL, "
                "worker_role_key TEXT NOT NULL, reviewer_role_key TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def pump(
        self,
        generation: ControlGeneration,
        events: Iterable[WorkflowEvent],
    ) -> PumpReceipt:
        ordered = tuple(sorted(events, key=lambda item: item.sort_key))
        input_material = {
            "events": [json.loads(canonical_event_json(item)) for item in ordered],
            "execution_profile_digest": self.execution_metadata.profile_digest,
        }
        input_digest = _digest(input_material)
        accepted: list[str] = []
        duplicates: list[str] = []
        stale: list[str] = []
        owned_events: list[WorkflowEvent] = []
        runner_digests: dict[str, str] = {}
        snapshots = {item.task_id: item for item in self.state_store.task_snapshots()}
        projected_keys = {
            task_id: (utc_text(item.updated_at), item.last_event_id)
            for task_id, item in snapshots.items()
        }
        state_events = {
            item.event_id: canonical_event_json(item) for item in self.state_store.events()
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cached = connection.execute(
                "SELECT payload FROM pump_receipt WHERE sequence = ? AND input_digest = ?",
                (generation.sequence, input_digest),
            ).fetchone()
            if cached is not None:
                receipt = PumpReceipt.from_dict(json.loads(cached["payload"]))
                if receipt.generation_digest != generation.digest:
                    connection.rollback()
                    raise StaleControlGeneration("generation sequence was rebound")
                connection.commit()
                return receipt
            latest = connection.execute(
                "SELECT sequence, digest FROM control_generation ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if latest is not None:
                if generation.sequence < latest["sequence"]:
                    connection.rollback()
                    raise StaleControlGeneration("controller generation is stale")
                if generation.sequence == latest["sequence"] and generation.digest != latest["digest"]:
                    connection.rollback()
                    raise StaleControlGeneration("generation sequence was rebound")
            connection.execute(
                "INSERT OR IGNORE INTO control_generation(sequence, digest) VALUES (?, ?)",
                (generation.sequence, generation.digest),
            )
            generation_has_receipt = connection.execute(
                "SELECT 1 FROM pump_receipt WHERE sequence = ? LIMIT 1",
                (generation.sequence,),
            ).fetchone() is not None
            for event in ordered:
                payload = canonical_event_json(event)
                previous = connection.execute(
                    "SELECT payload, disposition, generation_sequence, generation_digest, "
                    "runner_receipt_digest FROM event_disposition WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                if previous is not None:
                    if previous["payload"] != payload:
                        connection.rollback()
                        raise WorkflowControllerError(
                            "event_id conflicts with prior lifecycle input"
                        )
                    if (
                        not generation_has_receipt
                        and previous["generation_sequence"] == generation.sequence
                        and previous["generation_digest"] == generation.digest
                    ):
                        if previous["disposition"] == "accepted":
                            accepted.append(event.event_id)
                            if previous["runner_receipt_digest"] is None:
                                owned_events.append(event)
                            else:
                                runner_digests[event.event_id] = previous[
                                    "runner_receipt_digest"
                                ]
                        else:
                            stale.append(event.event_id)
                    else:
                        duplicates.append(event.event_id)
                    continue
                state_payload = state_events.get(event.event_id)
                if state_payload is not None and state_payload != payload:
                    connection.rollback()
                    raise WorkflowControllerError(
                        "event_id conflicts with workflow machine truth"
                    )
                task_key = projected_keys.get(event.task_id or "")
                if (
                    state_payload is None
                    and event.kind is EventKind.TASK_TRANSITION
                    and task_key is not None
                    and event.sort_key <= task_key
                ):
                    disposition = "stale"
                    stale.append(event.event_id)
                else:
                    disposition = "accepted"
                    accepted.append(event.event_id)
                    owned_events.append(event)
                    if event.kind is EventKind.TASK_TRANSITION and event.task_id is not None:
                        projected_keys[event.task_id] = event.sort_key
                connection.execute(
                    "INSERT INTO event_disposition("
                    "event_id, payload, disposition, generation_sequence, "
                    "generation_digest, runner_receipt_digest) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.event_id, payload, disposition, generation.sequence,
                        generation.digest, None,
                    ),
                )
            # Keep the durable controller write lock until every accepted
            # direct action has settled. A newer generation may wait, but it
            # cannot reserve or execute a later lifecycle action out of order.
            for event in owned_events:
                self.state_store.record(event)
                runner_digest = self._run_event(event, generation)
                if runner_digest is not None:
                    runner_digests[event.event_id] = runner_digest
                    connection.execute(
                        "UPDATE event_disposition SET runner_receipt_digest = ? "
                        "WHERE event_id = ? AND generation_sequence = ? "
                        "AND generation_digest = ? AND runner_receipt_digest IS NULL",
                        (
                            runner_digest, event.event_id, generation.sequence,
                            generation.digest,
                        ),
                    )

            receipt = PumpReceipt(
                generation_sequence=generation.sequence,
                generation_digest=generation.digest,
                input_digest=input_digest,
                accepted_event_ids=tuple(accepted),
                duplicate_event_ids=tuple(duplicates),
                stale_event_ids=tuple(stale),
                runner_receipt_digests=tuple(
                    runner_digests[event_id]
                    for event_id in accepted
                    if event_id in runner_digests
                ),
                production_mutated=self.execution_metadata.mutation_observed is True,
                execution_profile=self.execution_metadata.profile_name,
                execution_profile_digest=self.execution_metadata.profile_digest,
                workspace_write_enabled=self.execution_metadata.workspace_write_enabled,
                mutation_observed=self.execution_metadata.mutation_observed,
                orca_used=self.execution_metadata.orca_used,
            )
            receipt_payload = _canonical(receipt.to_dict())
            connection.execute(
                "INSERT OR IGNORE INTO pump_receipt("
                "sequence, generation_digest, input_digest, payload) VALUES (?, ?, ?, ?)",
                (
                    generation.sequence, generation.digest, input_digest,
                    receipt_payload,
                ),
            )
            persisted = connection.execute(
                "SELECT payload FROM pump_receipt WHERE sequence = ? AND input_digest = ?",
                (generation.sequence, input_digest),
            ).fetchone()
            if persisted is None or persisted["payload"] != receipt_payload:
                connection.rollback()
                raise WorkflowControllerError("concurrent pump receipt differs")
            connection.commit()
        return receipt

    def pump_queue_snapshot(
        self,
        generation: ControlGeneration,
        queue_snapshot: QueueSnapshot,
        workflow_events: Iterable[WorkflowEvent] = (),
    ) -> PumpReceipt:
        """Consume the canonical Queue snapshot beside sanitized workflow facts."""

        return self.pump(generation, (queue_snapshot.to_event(), *workflow_events))

    @staticmethod
    def plan_routes(
        items: Iterable[QueueWorkItem],
        *,
        writer_limit: int = 3,
        completed_task_ids: Iterable[str] = (),
    ) -> LeadPlan:
        """Select routes through the existing deterministic Queue policy."""

        return select_dependency_ready_leads(
            items,
            writer_limit=writer_limit,
            completed_task_ids=completed_task_ids,
        )

    def register_role_session(
        self,
        identity: RoleIdentity,
        *,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        """Register one durable session route; the Codex id remains SQLite-only."""

        return self.role_registry.claim(
            identity, observed_at=observed_at, lease_until=lease_until
        )

    def resume_session_hierarchy(
        self, root_role_key: str = "project_manager"
    ) -> HierarchyResumeReceipt:
        """Resume PM, then each Lead and its durable Workers/Reviewer in order."""

        if self.session_runner is None:
            raise WorkflowControllerError("hierarchy resume requires a direct session runner")
        records = tuple(
            item
            for item in self.role_registry.hierarchy(root_role_key)
            if item.state is not RoleState.STOPPED
        )
        receipt_digests: list[str] = []
        for record in records:
            material = {
                "parent_role_key": record.identity.parent_role_key,
                "role_generation": record.generation,
                "role_key": record.identity.role_key,
                "session_id": record.identity.codex_session_id,
            }
            provenance = _digest(material)
            receipt_digests.append(
                self.session_runner.run(
                    SessionAction.RESUME,
                    role_key=record.identity.role_key,
                    role_generation=record.generation,
                    session_id=record.identity.codex_session_id,
                    provenance=provenance,
                ).receipt_digest
            )
        return HierarchyResumeReceipt(
            root_role_key=root_role_key,
            role_keys=tuple(item.identity.role_key for item in records),
            session_ids=tuple(item.identity.codex_session_id for item in records),
            runner_receipt_digests=tuple(receipt_digests),
        )

    @staticmethod
    def _workflow_role(kind: RoleKind) -> WorkflowRole:
        return {
            RoleKind.PROJECT_MANAGER: WorkflowRole.PROJECT_MANAGER,
            RoleKind.DOMAIN_LEAD: WorkflowRole.LEAD,
            RoleKind.WORKER: WorkflowRole.WORKER,
            RoleKind.REVIEWER: WorkflowRole.REVIEWER,
        }[kind]

    def _require_role(
        self,
        role_key: str,
        expected_generation: int,
        *,
        action: RoleAction | None = None,
        expected_kind: RoleKind | None = None,
    ) -> RoleRecord:
        record = self.role_registry.get(role_key)
        if record.generation != expected_generation:
            raise StaleRoleGeneration("role generation changed")
        if record.state not in {RoleState.ACTIVE, RoleState.IDLE}:
            raise RoleRegistryError("role is not available for mailbox work")
        if expected_kind is not None and record.identity.role_kind is not expected_kind:
            raise RoleRegistryError("role kind does not match the requested operation")
        if action is not None:
            require_role_authority(self._workflow_role(record.identity.role_kind), action)
        return record

    def _run_generation_bound(
        self,
        preflight: RoleRecord,
        *,
        action: RoleAction,
        expected_kind: RoleKind,
        operation: Callable[[], _GuardedResult],
    ) -> _GuardedResult:
        """Run one controller mutation while lifecycle CAS is serialized."""

        with self.role_registry.generation_guard(
            preflight.identity.role_key,
            expected_generation=preflight.generation,
            expected_session_id=preflight.identity.codex_session_id,
        ) as guarded:
            if guarded.state not in {RoleState.ACTIVE, RoleState.IDLE}:
                raise RoleRegistryError("role is not available for mailbox work")
            if guarded.identity.role_kind is not expected_kind:
                raise RoleRegistryError("role kind does not match the requested operation")
            require_role_authority(self._workflow_role(guarded.identity.role_kind), action)
            return operation()

    @staticmethod
    def _envelope_material(
        row: sqlite3.Row | Mapping[str, object],
        recipient_session_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "body_digest": row["body_digest"],
            "body_json": row["body_json"],
            "created_at": row["created_at"],
            "message_id": row["message_id"],
            "message_type": row["message_type"],
            "parent_message_id": row["parent_message_id"],
            "queue_generation": row["queue_generation"],
            "recipient_generation": row["recipient_generation"],
            "recipient_role_key": row["recipient_role_key"],
            "recipient_session_id": (
                recipient_session_id
                if recipient_session_id is not None
                else row["recipient_session_id"]
            ),
            "sender_role_key": row["sender_role_key"],
            "task_id": row["task_id"],
        }

    @staticmethod
    def _internal_message_id(row: sqlite3.Row | Mapping[str, object]) -> str:
        return "msg-" + _digest(
            {
                "body_digest": row["body_digest"],
                "message_type": row["message_type"],
                "parent_message_id": row["parent_message_id"],
                "queue_generation": row["queue_generation"],
                "recipient_role_key": row["recipient_role_key"],
                "sender_role_key": row["sender_role_key"],
                "task_id": row["task_id"],
            }
        )

    @staticmethod
    def _listener_message_ids(
        *,
        parent_id: str,
        body: str,
        recipient: str,
        session_id: str,
        generation: int,
        message_type: str,
        queue_id: str | None,
    ) -> frozenset[str]:
        def canonical_json(value: Mapping[str, object]) -> str:
            return json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )

        base: dict[str, object] = {
            "generation": generation,
            "message": body,
            "recipient": recipient,
            "session_id": session_id,
        }
        message_type_options = (False, True) if message_type == "direct_message" else (True,)
        queue_options = (False, True) if queue_id is None else (True,)
        ids: set[str] = set()
        for include_message_type in message_type_options:
            for include_queue in queue_options:
                payload = dict(base)
                if include_message_type:
                    payload["message_type"] = message_type
                if include_queue:
                    payload["queue_id"] = queue_id
                action = canonical_json(
                    {
                        "intent_key": parent_id,
                        "payload": payload,
                        "route_kind": "direct_pm",
                    }
                )
                action_id = hashlib.sha256(
                    f"listener-action/v1\n{action}".encode("utf-8")
                ).hexdigest()
                identity = canonical_json(
                    {"action_key": action_id, "sink": "pm_mailbox"}
                )
                ids.add(
                    hashlib.sha256(
                        f"listener-delivery/v1\n{identity}".encode("utf-8")
                    ).hexdigest()
                )
        return frozenset(ids)

    def _insert_mailbox_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        sender_role_key: str,
        recipient_role_key: str,
        recipient_generation: int,
        message_type: MailboxMessageType,
        body: Mapping[str, object],
        task_id: str | None = None,
        queue_generation: str | None = None,
        parent_message_id: str | None = None,
        message_id_override: str | None = None,
        created_at_override: datetime | None = None,
    ) -> MailboxEnvelope:
        recipient = self._require_role(recipient_role_key, recipient_generation)
        if task_id is not None and _TASK_ID.fullmatch(task_id) is None:
            raise WorkflowControllerError("mailbox task id is invalid")
        if queue_generation is not None and _IDENTIFIER.fullmatch(queue_generation) is None:
            raise WorkflowControllerError("mailbox Queue generation is invalid")
        if _IDENTIFIER.fullmatch(sender_role_key) is None:
            raise WorkflowControllerError("mailbox sender role is invalid")
        if parent_message_id is not None and _IDENTIFIER.fullmatch(parent_message_id) is None:
            raise WorkflowControllerError("mailbox parent message is invalid")
        try:
            body_json = _canonical(body)
        except (TypeError, ValueError) as error:
            raise WorkflowControllerError("mailbox body must be canonical JSON") from error
        if len(body_json.encode("utf-8")) > 65_536:
            raise WorkflowControllerError("mailbox body exceeds the bounded limit")
        body_digest = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
        identity: dict[str, object] = {
            "body_digest": body_digest,
            "message_type": message_type.value,
            "parent_message_id": parent_message_id,
            "queue_generation": queue_generation,
            "recipient_role_key": recipient_role_key,
            "sender_role_key": sender_role_key,
            "task_id": task_id,
        }
        message_id = message_id_override or ("msg-" + _digest(identity))
        if _IDENTIFIER.fullmatch(message_id) is None:
            raise WorkflowControllerError("mailbox message id is invalid")
        row = connection.execute(
            "SELECT * FROM role_mailbox WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is not None:
            envelope = self._mailbox_from_row(row)
            if (
                envelope.body_digest != body_digest
                or envelope.recipient_role_key != recipient_role_key
                or envelope.recipient_generation != recipient.generation
                or envelope.recipient_session_id != recipient.identity.codex_session_id
                or envelope.sender_role_key != sender_role_key
                or envelope.message_type is not message_type
                or envelope.task_id != task_id
                or envelope.queue_generation != queue_generation
                or envelope.parent_message_id != parent_message_id
            ):
                raise MailboxConflict("mailbox message id was rebound")
            return envelope
        created_at = created_at_override or datetime.now(UTC)
        values: dict[str, object] = {
            **identity,
            "body_json": body_json,
            "created_at": utc_text(created_at),
            "message_id": message_id,
            "recipient_generation": recipient.generation,
            "recipient_session_id": recipient.identity.codex_session_id,
        }
        envelope_digest = _digest(self._envelope_material(values))
        connection.execute(
            "INSERT INTO role_mailbox(message_id, parent_message_id, sender_role_key, "
            "recipient_role_key, message_type, task_id, queue_generation, recipient_generation, "
            "recipient_session_id, body_digest, body_json, created_at, delivery_status, "
            "envelope_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id, parent_message_id, sender_role_key, recipient_role_key,
                message_type.value, task_id, queue_generation, recipient.generation,
                recipient.identity.codex_session_id, body_digest, body_json,
                utc_text(created_at), MailboxStatus.PENDING.value, envelope_digest,
            ),
        )
        row = connection.execute(
            "SELECT * FROM role_mailbox WHERE message_id = ?", (message_id,)
        ).fetchone()
        assert row is not None
        return self._mailbox_from_row(row)

    def _enqueue_mailbox(
        self,
        *,
        sender_role_key: str,
        recipient_role_key: str,
        recipient_generation: int,
        message_type: MailboxMessageType,
        body: Mapping[str, object],
        task_id: str | None = None,
        queue_generation: str | None = None,
        parent_message_id: str | None = None,
        message_id_override: str | None = None,
        created_at_override: datetime | None = None,
    ) -> MailboxEnvelope:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            envelope = self._insert_mailbox_in_transaction(
                connection,
                sender_role_key=sender_role_key,
                recipient_role_key=recipient_role_key,
                recipient_generation=recipient_generation,
                message_type=message_type,
                body=body,
                task_id=task_id,
                queue_generation=queue_generation,
                parent_message_id=parent_message_id,
                message_id_override=message_id_override,
                created_at_override=created_at_override,
            )
            connection.commit()
        return envelope

    @staticmethod
    def _mailbox_from_row(row: sqlite3.Row) -> MailboxEnvelope:
        body_json = str(row["body_json"])
        body_digest = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
        if body_digest != row["body_digest"]:
            raise MailboxConflict("mailbox body integrity check failed")
        expected_envelope = _digest(WorkflowController._envelope_material(row))
        if row["envelope_digest"] != expected_envelope:
            raise MailboxConflict("mailbox envelope integrity check failed")
        message_id = str(row["message_id"])
        body = json.loads(body_json)
        if message_id.startswith("msg-"):
            if message_id != WorkflowController._internal_message_id(row):
                raise MailboxConflict("mailbox message id was rebound")
        elif str(row["message_type"]) in {
            MailboxMessageType.DIRECT_MESSAGE.value,
            MailboxMessageType.OPERATIONAL_WAKE.value,
            MailboxMessageType.USER_INTENT.value,
        }:
            raw_body = body.get("message") if isinstance(body, dict) else None
            listener_digest = body.get("listener_body_digest") if isinstance(body, dict) else None
            if (
                not isinstance(raw_body, str)
                or hashlib.sha256(raw_body.encode("utf-8")).hexdigest() != listener_digest
                or message_id not in WorkflowController._listener_message_ids(
                    parent_id=str(row["parent_message_id"]),
                    body=raw_body,
                    recipient=str(row["recipient_role_key"]),
                    session_id=str(row["recipient_session_id"]),
                    generation=int(row["recipient_generation"]),
                    message_type=str(row["message_type"]),
                    queue_id=row["task_id"],
                )
            ):
                raise MailboxConflict("Listener mailbox message id was rebound")
        return MailboxEnvelope(
            message_id=message_id,
            parent_message_id=row["parent_message_id"],
            sender_role_key=str(row["sender_role_key"]),
            recipient_role_key=str(row["recipient_role_key"]),
            message_type=MailboxMessageType(str(row["message_type"])),
            task_id=row["task_id"],
            queue_generation=row["queue_generation"],
            recipient_generation=int(row["recipient_generation"]),
            recipient_session_id=str(row["recipient_session_id"]),
            body_digest=str(row["body_digest"]),
            body=body,
            created_at=parse_utc(str(row["created_at"])),
            delivery_status=MailboxStatus(str(row["delivery_status"])),
        )

    def mailbox(
        self,
        recipient_role_key: str,
        *,
        pending_only: bool = False,
    ) -> tuple[MailboxEnvelope, ...]:
        if _ROLE_KEY.fullmatch(recipient_role_key) is None:
            raise WorkflowControllerError("mailbox recipient role is invalid")
        query = "SELECT * FROM role_mailbox WHERE recipient_role_key = ?"
        params: list[object] = [recipient_role_key]
        if pending_only:
            query += " AND delivery_status = ?"
            params.append(MailboxStatus.PENDING.value)
        query += " ORDER BY created_at, message_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(self._mailbox_from_row(row) for row in rows)

    def acknowledge_mailbox(
        self,
        message_id: str,
        *,
        recipient_role_key: str,
        expected_generation: int,
        acknowledgement_ref: str,
        observed_at: datetime | None = None,
        lease_for: timedelta = timedelta(minutes=10),
    ) -> MailboxAcknowledgement:
        if _IDENTIFIER.fullmatch(message_id) is None or _IDENTIFIER.fullmatch(acknowledgement_ref) is None:
            raise WorkflowControllerError("mailbox acknowledgement identifiers are invalid")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM role_mailbox WHERE message_id = ?", (message_id,)
            ).fetchone()
        if row is None:
            raise WorkflowControllerError("mailbox message does not exist")
        envelope = self._mailbox_from_row(row)
        if envelope.recipient_role_key != recipient_role_key:
            raise WorkflowControllerError("only the exact recipient may acknowledge a message")
        if envelope.delivery_status is MailboxStatus.ACKNOWLEDGED:
            if row["acknowledgement_ref"] != acknowledgement_ref:
                raise MailboxConflict("mailbox acknowledgement was rebound")
            current = self.role_registry.get(recipient_role_key)
            return MailboxAcknowledgement(
                message_id, recipient_role_key, envelope.recipient_generation,
                int(row["ack_recipient_generation"] or current.generation), acknowledgement_ref,
                parse_utc(str(row["acknowledged_at"])),
            )
        if envelope.recipient_generation != expected_generation:
            raise StaleRoleGeneration("mailbox recipient generation changed")
        now = observed_at or datetime.now(UTC)
        updated_role = self.role_registry.acknowledge_message(
            recipient_role_key,
            expected_generation=expected_generation,
            message_id=message_id,
            observed_at=now,
            lease_until=now + lease_for,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE role_mailbox SET delivery_status = ?, acknowledgement_ref = ?, "
                "acknowledged_at = ?, ack_recipient_generation = ? "
                "WHERE message_id = ? AND delivery_status = ?",
                (
                    MailboxStatus.ACKNOWLEDGED.value, acknowledgement_ref,
                    utc_text(now), updated_role.generation,
                    message_id, MailboxStatus.PENDING.value,
                ),
            ).rowcount
            if changed != 1:
                settled = connection.execute(
                    "SELECT delivery_status, acknowledgement_ref, acknowledged_at, "
                    "ack_recipient_generation FROM role_mailbox WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if (
                    settled is None
                    or settled["delivery_status"] != MailboxStatus.ACKNOWLEDGED.value
                    or settled["acknowledgement_ref"] != acknowledgement_ref
                ):
                    connection.rollback()
                    raise MailboxConflict("mailbox status changed during acknowledgement")
                acknowledged_at = parse_utc(str(settled["acknowledged_at"]))
                acknowledged_generation = int(settled["ack_recipient_generation"])
            else:
                acknowledged_at = now
                acknowledged_generation = updated_role.generation
            connection.commit()
        return MailboxAcknowledgement(
            message_id, recipient_role_key, expected_generation,
            acknowledged_generation, acknowledgement_ref, acknowledged_at,
        )

    def deliver_pm_message(
        self, *, receipt_key: str, intent_key: str, message: str
    ) -> str:
        """Implement ListenerGateway's idempotent PM mailbox sink protocol."""

        if _DIGEST.fullmatch(receipt_key) is None or _DIGEST.fullmatch(intent_key) is None:
            raise WorkflowControllerError("Listener mailbox keys must be SHA-256 digests")
        if not isinstance(message, str) or not message.strip():
            raise WorkflowControllerError("Listener PM message cannot be empty")
        pm = self.role_registry.get("project_manager")
        envelope = self._enqueue_mailbox(
            sender_role_key="listener",
            recipient_role_key=pm.identity.role_key,
            recipient_generation=pm.generation,
            message_type=MailboxMessageType.OPERATIONAL_PM,
            body={"intent_key": intent_key, "message": message, "receipt_key": receipt_key},
        )
        return envelope.message_id

    def deliver_listener_mailbox_envelope(self, envelope: object) -> str:
        """Persist ListenerGateway's typed, generation-bound PM envelope."""

        required = (
            "message_id", "parent_id", "sender", "recipient", "session_id",
            "message_type", "queue_id", "generation", "body_digest", "body",
            "creation_time", "delivery_status",
        )
        if any(not hasattr(envelope, field) for field in required):
            raise WorkflowControllerError("Listener mailbox envelope is not typed")
        message_id = str(getattr(envelope, "message_id"))
        parent_id = str(getattr(envelope, "parent_id"))
        sender = str(getattr(envelope, "sender"))
        recipient_key = str(getattr(envelope, "recipient"))
        session_id = str(getattr(envelope, "session_id"))
        generation = getattr(envelope, "generation")
        raw_body = getattr(envelope, "body")
        body_digest = str(getattr(envelope, "body_digest"))
        if (
            _DIGEST.fullmatch(message_id) is None
            or _DIGEST.fullmatch(parent_id) is None
            or recipient_key != "project_manager"
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
            or not isinstance(raw_body, str)
            or not raw_body.strip()
            or hashlib.sha256(raw_body.encode("utf-8")).hexdigest() != body_digest
            or getattr(envelope, "delivery_status") != "pending"
        ):
            raise WorkflowControllerError("Listener mailbox envelope is invalid")
        try:
            message_type = MailboxMessageType(str(getattr(envelope, "message_type")))
        except ValueError as error:
            raise WorkflowControllerError("Listener mailbox type is unsupported") from error
        if message_type not in {
            MailboxMessageType.DIRECT_MESSAGE,
            MailboxMessageType.OPERATIONAL_WAKE,
            MailboxMessageType.USER_INTENT,
        }:
            raise WorkflowControllerError("Listener mailbox type is unsupported")
        queue_id = getattr(envelope, "queue_id")
        if message_id not in self._listener_message_ids(
            parent_id=parent_id,
            body=raw_body,
            recipient=recipient_key,
            session_id=session_id,
            generation=generation,
            message_type=message_type.value,
            queue_id=queue_id,
        ):
            raise WorkflowControllerError("Listener mailbox message id is not canonical")
        created_at = parse_utc(str(getattr(envelope, "creation_time")))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM role_mailbox WHERE message_id = ?", (message_id,)
            ).fetchone()
        if existing is not None:
            persisted = self._mailbox_from_row(existing)
            if (
                persisted.parent_message_id != parent_id
                or persisted.sender_role_key != sender
                or persisted.recipient_role_key != recipient_key
                or persisted.recipient_session_id != session_id
                or persisted.recipient_generation != generation
                or persisted.message_type is not message_type
                or persisted.task_id != queue_id
                or persisted.body.get("message") != raw_body
                or persisted.created_at != created_at
            ):
                raise MailboxConflict("Listener mailbox message id was rebound")
            return persisted.message_id
        current = self._require_role(recipient_key, generation)
        if current.identity.codex_session_id != session_id:
            raise StaleRoleGeneration("Listener mailbox session changed")
        return self._enqueue_mailbox(
            sender_role_key=sender,
            recipient_role_key=recipient_key,
            recipient_generation=generation,
            message_type=message_type,
            body={"listener_body_digest": body_digest, "message": raw_body},
            task_id=queue_id,
            parent_message_id=parent_id,
            message_id_override=message_id,
            created_at_override=created_at,
        ).message_id

    @staticmethod
    def _contract_payload(contract: TaskContract) -> dict[str, object]:
        return {
            "task_id": contract.task_id,
            "queue_generation": contract.queue_generation,
            "pm_role_key": contract.pm_role_key,
            "lead_role_key": contract.lead_role_key,
            "reviewer_role_key": contract.reviewer_role_key,
            "write_scope": list(contract.write_scope),
            "worker_assignments": [
                {
                    "worker_role_key": item.worker_role_key,
                    "write_scope": list(item.write_scope),
                }
                for item in contract.worker_assignments
            ],
            "worker_profile": contract.worker_profile,
            "reviewer_profile": contract.reviewer_profile,
            "contract_digest": contract.contract_digest,
        }

    def dispatch_task_contract(
        self,
        contract: TaskContract,
        *,
        pm_generation: int,
    ) -> MailboxEnvelope:
        """Generation-bind the PM contract mutation through durable settlement."""

        pm = self._require_role(
            contract.pm_role_key,
            pm_generation,
            action=RoleAction.ASSIGN_LEAD,
            expected_kind=RoleKind.PROJECT_MANAGER,
        )
        return self._run_generation_bound(
            pm,
            action=RoleAction.ASSIGN_LEAD,
            expected_kind=RoleKind.PROJECT_MANAGER,
            operation=lambda: self._dispatch_task_contract_unlocked(
                contract, pm_generation=pm_generation
            ),
        )

    def _dispatch_task_contract_unlocked(
        self,
        contract: TaskContract,
        *,
        pm_generation: int,
    ) -> MailboxEnvelope:
        """Persist and deliver an immutable PM-owned task contract to one Lead."""

        pm = self._require_role(
            contract.pm_role_key,
            pm_generation,
            action=RoleAction.ASSIGN_LEAD,
            expected_kind=RoleKind.PROJECT_MANAGER,
        )
        lead = self.role_registry.get(contract.lead_role_key)
        reviewer = self.role_registry.get(contract.reviewer_role_key)
        if lead.identity.role_kind is not RoleKind.DOMAIN_LEAD:
            raise RoleRegistryError("task contract Lead identity is not a Lead")
        if reviewer.identity.role_kind is not RoleKind.REVIEWER:
            raise RoleRegistryError("task contract Reviewer identity is not a Reviewer")
        if lead.identity.parent_role_key != pm.identity.role_key:
            raise RoleRegistryError("task contract Lead is outside the PM hierarchy")
        if reviewer.identity.parent_role_key != lead.identity.role_key:
            raise RoleRegistryError("task contract Reviewer is outside the Lead hierarchy")
        session_ids = {
            lead.identity.role_key: lead.identity.codex_session_id,
            reviewer.identity.role_key: reviewer.identity.codex_session_id,
        }
        for assignment in contract.worker_assignments:
            worker = self.role_registry.get(assignment.worker_role_key)
            if worker.identity.role_kind is not RoleKind.WORKER:
                raise RoleRegistryError("task contract Worker identity is not a Worker")
            if worker.identity.parent_role_key != lead.identity.role_key:
                raise RoleRegistryError("task contract Worker is outside the Lead hierarchy")
            session_ids[worker.identity.role_key] = worker.identity.codex_session_id
        require_unique_role_sessions(
            lead_role_key=lead.identity.role_key,
            reviewer_role_key=reviewer.identity.role_key,
            worker_role_keys=tuple(
                assignment.worker_role_key for assignment in contract.worker_assignments
            ),
            session_ids=session_ids,
        )
        payload = self._contract_payload(contract)
        payload_json = _canonical(payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT queue_generation, contract_digest, state FROM hierarchy_task "
                "WHERE task_id = ?", (contract.task_id,)
            ).fetchone()
            if current is not None:
                if (
                    current["queue_generation"] == contract.queue_generation
                    and current["contract_digest"] == contract.contract_digest
                ):
                    pass
                elif current["state"] != "replan_required":
                    connection.rollback()
                    raise StaleQueueGeneration("active task contract generation cannot be rebound")
                else:
                    connection.execute(
                        "UPDATE hierarchy_task SET queue_generation = ?, pm_role_key = ?, "
                        "lead_role_key = ?, reviewer_role_key = ?, contract_digest = ?, "
                        "contract_json = ?, state = 'assigned', fix_count = 0 WHERE task_id = ?",
                        (
                            contract.queue_generation, contract.pm_role_key,
                            contract.lead_role_key, contract.reviewer_role_key,
                            contract.contract_digest, payload_json, contract.task_id,
                        ),
                    )
                    connection.execute(
                        "DELETE FROM worker_assignment WHERE task_id = ?", (contract.task_id,)
                    )
            else:
                connection.execute(
                    "INSERT INTO hierarchy_task(task_id, queue_generation, pm_role_key, "
                    "lead_role_key, reviewer_role_key, contract_digest, contract_json, state, fix_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'assigned', 0)",
                    (
                        contract.task_id, contract.queue_generation, contract.pm_role_key,
                        contract.lead_role_key, contract.reviewer_role_key,
                        contract.contract_digest, payload_json,
                    ),
                )
            history = connection.execute(
                "SELECT contract_digest, contract_json FROM task_contract_history "
                "WHERE task_id = ? AND queue_generation = ?",
                (contract.task_id, contract.queue_generation),
            ).fetchone()
            if history is not None and (
                history["contract_digest"] != contract.contract_digest
                or history["contract_json"] != payload_json
            ):
                connection.rollback()
                raise MailboxConflict("task contract generation was rebound")
            connection.execute(
                "INSERT OR IGNORE INTO task_contract_history(task_id, queue_generation, "
                "contract_digest, contract_json) VALUES (?, ?, ?, ?)",
                (
                    contract.task_id, contract.queue_generation,
                    contract.contract_digest, payload_json,
                ),
            )
            for assignment in contract.worker_assignments:
                connection.execute(
                    "INSERT OR IGNORE INTO worker_assignment(task_id, queue_generation, "
                    "worker_role_key, write_scope_json) VALUES (?, ?, ?, ?)",
                    (
                        contract.task_id, contract.queue_generation,
                        assignment.worker_role_key,
                        _canonical({"write_scope": list(assignment.write_scope)}),
                    ),
                )
            envelope = self._insert_mailbox_in_transaction(
                connection,
                sender_role_key=contract.pm_role_key,
                recipient_role_key=contract.lead_role_key,
                recipient_generation=lead.generation,
                message_type=MailboxMessageType.TASK_CONTRACT,
                body={"contract_digest": contract.contract_digest},
                task_id=contract.task_id,
                queue_generation=contract.queue_generation,
            )
            connection.commit()
        return envelope

    def _current_task(self, task_id: str, queue_generation: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hierarchy_task WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise ReviewLoopError("task contract is not registered")
        if row["queue_generation"] != queue_generation:
            raise StaleQueueGeneration("Queue task generation is stale")
        return row

    def dispatch_workers(
        self,
        *,
        task_id: str,
        queue_generation: str,
        lead_role_key: str,
        lead_generation: int,
    ) -> tuple[MailboxEnvelope, ...]:
        """Generation-bind Lead fan-out through every durable side effect."""

        lead = self._require_role(
            lead_role_key,
            lead_generation,
            action=RoleAction.DISPATCH_WORKER,
            expected_kind=RoleKind.DOMAIN_LEAD,
        )
        return self._run_generation_bound(
            lead,
            action=RoleAction.DISPATCH_WORKER,
            expected_kind=RoleKind.DOMAIN_LEAD,
            operation=lambda: self._dispatch_workers_unlocked(
                task_id=task_id,
                queue_generation=queue_generation,
                lead_role_key=lead_role_key,
                lead_generation=lead_generation,
            ),
        )

    def _dispatch_workers_unlocked(
        self,
        *,
        task_id: str,
        queue_generation: str,
        lead_role_key: str,
        lead_generation: int,
    ) -> tuple[MailboxEnvelope, ...]:
        """Lead fan-out is deterministic and inherits the contract's disjoint scopes."""

        lead = self._require_role(
            lead_role_key,
            lead_generation,
            action=RoleAction.DISPATCH_WORKER,
            expected_kind=RoleKind.DOMAIN_LEAD,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                task = connection.execute(
                    "SELECT * FROM hierarchy_task WHERE task_id = ?", (task_id,)
                ).fetchone()
                if task is None:
                    raise ReviewLoopError("task contract is not registered")
                if task["queue_generation"] != queue_generation:
                    raise StaleQueueGeneration("Queue task generation is stale")
                if task["lead_role_key"] != lead_role_key:
                    raise RoleRegistryError("Lead does not own this task contract")
                if task["state"] == "replan_required":
                    raise ReviewLoopError("task requires PM replan before Worker dispatch")
                assignments = connection.execute(
                    "SELECT worker_role_key, write_scope_json FROM worker_assignment "
                    "WHERE task_id = ? AND queue_generation = ? ORDER BY worker_role_key",
                    (task_id, queue_generation),
                ).fetchall()
                recipients = tuple(
                    (
                        assignment,
                        self.role_registry.get(str(assignment["worker_role_key"])),
                    )
                    for assignment in assignments
                )
                for _assignment, worker in recipients:
                    self._require_role(worker.identity.role_key, worker.generation)
                messages = [
                    self._insert_mailbox_in_transaction(
                        connection,
                        sender_role_key=lead.identity.role_key,
                        recipient_role_key=worker.identity.role_key,
                        recipient_generation=worker.generation,
                        message_type=MailboxMessageType.WORKER_ASSIGNMENT,
                        body=json.loads(str(assignment["write_scope_json"])),
                        task_id=task_id,
                        queue_generation=queue_generation,
                    )
                    for assignment, worker in recipients
                ]
                connection.execute(
                    "UPDATE hierarchy_task SET state = 'working' WHERE task_id = ? "
                    "AND queue_generation = ? AND state = 'assigned'",
                    (task_id, queue_generation),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return tuple(messages)

    def record_lead_checkpoint(
        self,
        *,
        task_id: str,
        queue_generation: str,
        lead_role_key: str,
        lead_generation: int,
        checkpoint_digest: str,
    ) -> str:
        """Generation-bind a Lead checkpoint through its durable insert."""

        lead = self._require_role(
            lead_role_key,
            lead_generation,
            action=RoleAction.PROGRESS_CHECKPOINT,
            expected_kind=RoleKind.DOMAIN_LEAD,
        )
        return self._run_generation_bound(
            lead,
            action=RoleAction.PROGRESS_CHECKPOINT,
            expected_kind=RoleKind.DOMAIN_LEAD,
            operation=lambda: self._record_lead_checkpoint_unlocked(
                task_id=task_id,
                queue_generation=queue_generation,
                lead_role_key=lead_role_key,
                lead_generation=lead_generation,
                checkpoint_digest=checkpoint_digest,
            ),
        )

    def _record_lead_checkpoint_unlocked(
        self,
        *,
        task_id: str,
        queue_generation: str,
        lead_role_key: str,
        lead_generation: int,
        checkpoint_digest: str,
    ) -> str:
        """Record sanitized Lead-owned progress without mutating Queue structure."""

        self._require_role(
            lead_role_key,
            lead_generation,
            action=RoleAction.PROGRESS_CHECKPOINT,
            expected_kind=RoleKind.DOMAIN_LEAD,
        )
        if _DIGEST.fullmatch(checkpoint_digest) is None:
            raise WorkflowControllerError("checkpoint digest must be SHA-256")
        checkpoint_id = "checkpoint-" + _digest(
            {
                "checkpoint_digest": checkpoint_digest,
                "lead_role_key": lead_role_key,
                "queue_generation": queue_generation,
                "task_id": task_id,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                task = connection.execute(
                    "SELECT * FROM hierarchy_task WHERE task_id = ?", (task_id,)
                ).fetchone()
                if task is None:
                    raise ReviewLoopError("task contract is not registered")
                if task["queue_generation"] != queue_generation:
                    raise StaleQueueGeneration("Queue task generation is stale")
                if task["lead_role_key"] != lead_role_key:
                    raise RoleRegistryError("Lead does not own this task checkpoint")
                connection.execute(
                    "INSERT OR IGNORE INTO lead_checkpoint(checkpoint_id, task_id, "
                    "queue_generation, lead_role_key, checkpoint_digest, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        checkpoint_id, task_id, queue_generation, lead_role_key,
                        checkpoint_digest, utc_text(datetime.now(UTC)),
                    ),
                )
                row = connection.execute(
                    "SELECT checkpoint_digest FROM lead_checkpoint WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                ).fetchone()
                if row is None or row["checkpoint_digest"] != checkpoint_digest:
                    raise MailboxConflict("Lead checkpoint id was rebound")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return checkpoint_id

    def submit_worker_candidate(
        self,
        *,
        task_id: str,
        queue_generation: str,
        worker_role_key: str,
        worker_generation: int,
        candidate_digest: str,
    ) -> tuple[MailboxEnvelope, MailboxEnvelope]:
        """Generation-bind Worker submission through candidate delivery."""

        worker = self._require_role(
            worker_role_key,
            worker_generation,
            action=RoleAction.SUBMIT_CANDIDATE,
            expected_kind=RoleKind.WORKER,
        )
        return self._run_generation_bound(
            worker,
            action=RoleAction.SUBMIT_CANDIDATE,
            expected_kind=RoleKind.WORKER,
            operation=lambda: self._submit_worker_candidate_unlocked(
                task_id=task_id,
                queue_generation=queue_generation,
                worker_role_key=worker_role_key,
                worker_generation=worker_generation,
                candidate_digest=candidate_digest,
            ),
        )

    def _submit_worker_candidate_unlocked(
        self,
        *,
        task_id: str,
        queue_generation: str,
        worker_role_key: str,
        worker_generation: int,
        candidate_digest: str,
    ) -> tuple[MailboxEnvelope, MailboxEnvelope]:
        """Freeze one Worker candidate and send it to the preassigned Reviewer."""

        worker = self._require_role(
            worker_role_key,
            worker_generation,
            action=RoleAction.SUBMIT_CANDIDATE,
            expected_kind=RoleKind.WORKER,
        )
        if _DIGEST.fullmatch(candidate_digest) is None:
            raise ReviewLoopError("candidate digest must be SHA-256")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                task = connection.execute(
                    "SELECT * FROM hierarchy_task WHERE task_id = ?", (task_id,)
                ).fetchone()
                if task is None:
                    raise ReviewLoopError("task contract is not registered")
                if task["queue_generation"] != queue_generation:
                    raise StaleQueueGeneration("Queue task generation is stale")
                if task["state"] == "replan_required":
                    raise ReviewLoopError("third FIX requires Lead/PM replan")
                assignment = connection.execute(
                    "SELECT candidate_digest, candidate_state FROM worker_assignment "
                    "WHERE task_id = ? AND queue_generation = ? AND worker_role_key = ?",
                    (task_id, queue_generation, worker_role_key),
                ).fetchone()
                if assignment is None:
                    raise RoleRegistryError("Worker is outside the task fan-out")
                if assignment["candidate_state"] == "pending_review":
                    if assignment["candidate_digest"] != candidate_digest:
                        raise ReviewLoopError("a frozen candidate is already awaiting review")
                elif assignment["candidate_state"] == "passed":
                    raise ReviewLoopError("a passed candidate cannot be replaced")
                else:
                    connection.execute(
                        "UPDATE worker_assignment SET candidate_digest = ?, "
                        "candidate_state = 'pending_review' WHERE task_id = ? "
                        "AND queue_generation = ? AND worker_role_key = ?",
                        (candidate_digest, task_id, queue_generation, worker_role_key),
                    )
                reviewer = self.role_registry.get(str(task["reviewer_role_key"]))
                lead = self.role_registry.get(str(task["lead_role_key"]))
                self._require_role(reviewer.identity.role_key, reviewer.generation)
                self._require_role(lead.identity.role_key, lead.generation)
                body = {
                    "candidate_digest": candidate_digest,
                    "worker_role_key": worker.identity.role_key,
                }
                review = self._insert_mailbox_in_transaction(
                    connection,
                    sender_role_key=worker.identity.role_key,
                    recipient_role_key=reviewer.identity.role_key,
                    recipient_generation=reviewer.generation,
                    message_type=MailboxMessageType.CANDIDATE,
                    body=body,
                    task_id=task_id,
                    queue_generation=queue_generation,
                )
                visible = self._insert_mailbox_in_transaction(
                    connection,
                    sender_role_key=worker.identity.role_key,
                    recipient_role_key=lead.identity.role_key,
                    recipient_generation=lead.generation,
                    message_type=MailboxMessageType.REVIEW_VISIBILITY,
                    body=body,
                    task_id=task_id,
                    queue_generation=queue_generation,
                    parent_message_id=review.message_id,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return review, visible

    def review_worker_candidate(
        self,
        *,
        task_id: str,
        queue_generation: str,
        worker_role_key: str,
        reviewer_role_key: str,
        reviewer_generation: int,
        candidate_digest: str,
        decision: ReviewDecision,
        reason_code: str,
    ) -> ReviewLoopReceipt:
        """Generation-bind Reviewer settlement through its atomic controller CAS."""

        action = (
            RoleAction.REVIEW_PASS
            if decision is ReviewDecision.PASS
            else RoleAction.REVIEW_FIX
            if decision is ReviewDecision.FIX
            else None
        )
        if action is None:
            raise ReviewLoopError("review decision must use ReviewDecision")
        reviewer = self._require_role(
            reviewer_role_key,
            reviewer_generation,
            action=action,
            expected_kind=RoleKind.REVIEWER,
        )
        return self._run_generation_bound(
            reviewer,
            action=action,
            expected_kind=RoleKind.REVIEWER,
            operation=lambda: self._review_worker_candidate_unlocked(
                task_id=task_id,
                queue_generation=queue_generation,
                worker_role_key=worker_role_key,
                reviewer_role_key=reviewer_role_key,
                reviewer_generation=reviewer_generation,
                candidate_digest=candidate_digest,
                decision=decision,
                reason_code=reason_code,
            ),
        )

    def _review_worker_candidate_unlocked(
        self,
        *,
        task_id: str,
        queue_generation: str,
        worker_role_key: str,
        reviewer_role_key: str,
        reviewer_generation: int,
        candidate_digest: str,
        decision: ReviewDecision,
        reason_code: str,
    ) -> ReviewLoopReceipt:
        """Apply PASS or at most two ordinary FIX rounds; the third forces replan."""

        action = (
            RoleAction.REVIEW_PASS
            if decision is ReviewDecision.PASS
            else RoleAction.REVIEW_FIX
            if decision is ReviewDecision.FIX
            else None
        )
        if action is None:
            raise ReviewLoopError("review decision must use ReviewDecision")
        reviewer = self._require_role(
            reviewer_role_key,
            reviewer_generation,
            action=action,
            expected_kind=RoleKind.REVIEWER,
        )
        if _DIGEST.fullmatch(candidate_digest) is None:
            raise ReviewLoopError("candidate digest must be SHA-256")
        if _IDENTIFIER.fullmatch(reason_code) is None:
            raise ReviewLoopError("review reason code must be symbolic")
        operation_id = "review-" + _digest(
            {
                "candidate_digest": candidate_digest,
                "decision": decision.value,
                "queue_generation": queue_generation,
                "reason_code": reason_code,
                "reviewer_generation": reviewer_generation,
                "reviewer_role_key": reviewer_role_key,
                "task_id": task_id,
                "worker_role_key": worker_role_key,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cached = connection.execute(
                    "SELECT payload FROM review_receipt WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if cached is not None:
                    value = json.loads(str(cached["payload"]))
                    receipt = ReviewLoopReceipt(
                        task_id=value["task_id"],
                        queue_generation=value["queue_generation"],
                        worker_role_key=value["worker_role_key"],
                        reviewer_role_key=value["reviewer_role_key"],
                        decision=ReviewDecision(value["decision"]),
                        fix_count=int(value["fix_count"]),
                        state=value["state"],
                        message_ids=tuple(value["message_ids"]),
                        receipt_digest=value["receipt_digest"],
                    )
                    connection.commit()
                    return receipt
                task = connection.execute(
                    "SELECT * FROM hierarchy_task WHERE task_id = ?", (task_id,)
                ).fetchone()
                if task is None:
                    raise ReviewLoopError("task contract is not registered")
                if task["queue_generation"] != queue_generation:
                    raise StaleQueueGeneration("Queue task generation is stale")
                if task["reviewer_role_key"] != reviewer_role_key:
                    raise RoleRegistryError(
                        "review decision did not come from the preassigned Reviewer"
                    )
                if task["state"] == "replan_required":
                    raise ReviewLoopError("third FIX already requires Lead/PM replan")
                assignment = connection.execute(
                    "SELECT candidate_digest, candidate_state FROM worker_assignment "
                    "WHERE task_id = ? AND queue_generation = ? AND worker_role_key = ?",
                    (task_id, queue_generation, worker_role_key),
                ).fetchone()
                if assignment is None:
                    raise RoleRegistryError("reviewed Worker is outside the task fan-out")
                if (
                    assignment["candidate_state"] != "pending_review"
                    or assignment["candidate_digest"] != candidate_digest
                ):
                    raise ReviewLoopError("review decision targets a stale candidate")
                lead = self.role_registry.get(str(task["lead_role_key"]))
                pm = self.role_registry.get(str(task["pm_role_key"]))
                worker = self.role_registry.get(worker_role_key)
                current_fix_count = int(task["fix_count"])
                message_ids: list[str] = []
                body = {
                    "candidate_digest": candidate_digest,
                    "reason_code": reason_code,
                    "worker_role_key": worker_role_key,
                }

                def insert(recipient: RoleRecord, kind: MailboxMessageType, payload: Mapping[str, object], parent: str | None = None) -> MailboxEnvelope:
                    return self._insert_mailbox_in_transaction(
                        connection,
                        sender_role_key=reviewer.identity.role_key,
                        recipient_role_key=recipient.identity.role_key,
                        recipient_generation=recipient.generation,
                        message_type=kind,
                        body=payload,
                        task_id=task_id,
                        queue_generation=queue_generation,
                        parent_message_id=parent,
                    )

                if decision is ReviewDecision.PASS:
                    message_ids.append(insert(lead, MailboxMessageType.PASS, body).message_id)
                    state, next_candidate_state = "passed_to_lead", "passed"
                    fix_count = current_fix_count
                else:
                    fix_count = current_fix_count + 1
                    fix_body = body | {"fix_count": fix_count}
                    if fix_count >= 3:
                        state, next_candidate_state = "replan_required", "replan_required"
                        for recipient in (lead, pm):
                            message_ids.append(
                                insert(
                                    recipient,
                                    MailboxMessageType.REPLAN_REQUIRED,
                                    fix_body,
                                ).message_id
                            )
                    else:
                        state, next_candidate_state = "fix_returned", "fix_requested"
                        fix_message = insert(worker, MailboxMessageType.FIX, fix_body)
                        message_ids.append(fix_message.message_id)
                        message_ids.append(
                            insert(
                                lead,
                                MailboxMessageType.REVIEW_VISIBILITY,
                                fix_body,
                                fix_message.message_id,
                            ).message_id
                        )
                receipt = ReviewLoopReceipt(
                    task_id=task_id,
                    queue_generation=queue_generation,
                    worker_role_key=worker_role_key,
                    reviewer_role_key=reviewer_role_key,
                    decision=decision,
                    fix_count=fix_count,
                    state=state,
                    message_ids=tuple(message_ids),
                )
                receipt_payload = _canonical(
                    {
                        "task_id": receipt.task_id,
                        "queue_generation": receipt.queue_generation,
                        "worker_role_key": receipt.worker_role_key,
                        "reviewer_role_key": receipt.reviewer_role_key,
                        "decision": receipt.decision.value,
                        "fix_count": receipt.fix_count,
                        "state": receipt.state,
                        "message_ids": list(receipt.message_ids),
                        "receipt_digest": receipt.receipt_digest,
                    }
                )
                changed = connection.execute(
                    "UPDATE worker_assignment SET candidate_state = ? WHERE task_id = ? "
                    "AND queue_generation = ? AND worker_role_key = ? "
                    "AND candidate_digest = ? AND candidate_state = 'pending_review'",
                    (
                        next_candidate_state, task_id, queue_generation,
                        worker_role_key, candidate_digest,
                    ),
                ).rowcount
                if changed != 1:
                    raise ReviewLoopError("candidate changed before review settlement")
                connection.execute(
                    "UPDATE hierarchy_task SET fix_count = ?, state = ? WHERE task_id = ? "
                    "AND queue_generation = ?",
                    (fix_count, state, task_id, queue_generation),
                )
                connection.execute(
                    "INSERT INTO review_receipt(operation_id, task_id, queue_generation, "
                    "worker_role_key, reviewer_role_key, payload) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        operation_id, task_id, queue_generation, worker_role_key,
                        reviewer_role_key, receipt_payload,
                    ),
                )
                connection.commit()
                return receipt
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _runner_action(event: WorkflowEvent) -> RunnerAction | None:
        if event.kind is EventKind.TASK_TRANSITION:
            if event.to_state is TaskState.ACTIVE:
                return RunnerAction.LAUNCH
            if event.to_state in {TaskState.READY, TaskState.REVIEW, TaskState.DONE}:
                return RunnerAction.SETTLE
        if event.kind in {EventKind.REVIEW_RESULT, EventKind.REWORK_REQUESTED}:
            return RunnerAction.SETTLE
        return None

    def _run_event(
        self,
        event: WorkflowEvent,
        generation: ControlGeneration,
    ) -> str | None:
        action = self._runner_action(event)
        if action is None or event.task_id is None:
            return None
        return self.runner.run(
            action,
            task_id=event.task_id,
            role_key=f"lead_{event.domain or 'unrouted'}",
            generation=generation.digest,
            source_event_id=event.event_id,
        ).receipt_digest

    def recover(
        self,
        proposal: RecoveryProposal,
        *,
        generation: ControlGeneration,
        connected_terminal: bool,
        agent_process_live: bool,
    ) -> RecoveryReceipt:
        if proposal.reason.value == "TRANSPORT_UNAVAILABLE":
            return self._recovery_receipt(
                "WAIT_FOR_DIRECT_HEALTH_PROBE", proposal.task_id, proposal.retry_attempt,
                proposal.provenance, connected_terminal, agent_process_live, (),
            )
        if proposal.action == "WAIT_FOR_QUEUE_RECONCILIATION":
            return self._recovery_receipt(
                "WAIT_FOR_QUEUE_RECONCILIATION", proposal.task_id, None,
                proposal.provenance, connected_terminal, agent_process_live, (),
            )
        force_recovery = proposal.reason.value in {
            "INTERACTIVE_INPUT",
            "STALE_DISPATCH",
            "STALE_HEARTBEAT",
        }
        if connected_terminal and agent_process_live and not force_recovery:
            return self._recovery_receipt(
                "CONTINUE_CONNECTED_AGENT", proposal.task_id, proposal.retry_attempt,
                proposal.provenance, connected_terminal, agent_process_live, (),
            )
        if not connected_terminal and agent_process_live:
            return self._recovery_receipt(
                "WAIT_FOR_VERIFIED_TERMINAL", proposal.task_id, proposal.retry_attempt,
                proposal.provenance, connected_terminal, agent_process_live, (),
            )
        provenance_material = {
            "action": proposal.action,
            "reason": proposal.reason.value,
            "retry_attempt": proposal.retry_attempt,
            "retry_of_dispatch_id": proposal.retry_of_dispatch_id,
            "role_generation": proposal.role_generation,
            "role_key": proposal.role_key,
            "session_id": proposal.session_id,
            "state": RoleState.RECOVERY_REQUIRED.value,
            "task_id": proposal.task_id,
        }
        if proposal.provenance != _digest(provenance_material):
            raise WorkflowControllerError("recovery provenance does not match the exact attempt")
        if proposal.action in {
            "RESUME_ROLE_SESSION",
            "INTERRUPT_THEN_RESUME_ROLE_SESSION",
        }:
            if proposal.task_id is not None or proposal.retry_of_dispatch_id is not None:
                raise WorkflowControllerError("role-session recovery cannot own a Queue attempt")
            if self.session_runner is None or proposal.session_id is None:
                raise WorkflowControllerError("role-session recovery requires a direct session runner")
            receipts = []
            if proposal.action == "INTERRUPT_THEN_RESUME_ROLE_SESSION":
                receipts.append(
                    self.session_runner.run(
                        SessionAction.INTERRUPT,
                        role_key=proposal.role_key,
                        role_generation=proposal.role_generation,
                        session_id=proposal.session_id,
                        provenance=proposal.provenance,
                    ).receipt_digest
                )
            receipts.append(
                self.session_runner.run(
                    SessionAction.RESUME,
                    role_key=proposal.role_key,
                    role_generation=proposal.role_generation,
                    session_id=proposal.session_id,
                    provenance=proposal.provenance,
                ).receipt_digest
            )
            action = (
                "ROLE_SESSION_INTERRUPTED_AND_RESUMED"
                if proposal.action == "INTERRUPT_THEN_RESUME_ROLE_SESSION"
                else "ROLE_SESSION_RESUMED"
            )
            return self._recovery_receipt(
                action, None, None, proposal.provenance,
                connected_terminal, agent_process_live, tuple(receipts),
            )
        if proposal.action == "SETTLE_STALE_EXECUTION_RECEIPT":
            if proposal.task_id is None or proposal.retry_of_dispatch_id is None:
                raise WorkflowControllerError("stale settlement requires an exact Queue attempt")
            settle = self.runner.run(
                RunnerAction.SETTLE,
                task_id=proposal.task_id,
                role_key=proposal.role_key,
                generation=generation.digest,
                source_event_id=f"settle-{proposal.provenance[:24]}",
            )
            return self._recovery_receipt(
                "STALE_EXECUTION_RECEIPT_SETTLED", proposal.task_id, None,
                proposal.provenance, connected_terminal, agent_process_live,
                (settle.receipt_digest,),
            )
        if (
            proposal.action not in {"SETTLE_THEN_RETRY_SAME_TASK", "RETRY_REVIEW_ONLY"}
            or proposal.task_id is None
            or proposal.retry_of_dispatch_id is None
            or proposal.retry_attempt is None
        ):
            raise WorkflowControllerError("recovery is limited to an exact active Queue task")
        if proposal.retry_attempt > self.max_recovery_attempts:
            raise WorkflowControllerError("recovery attempt exceeds the bounded retry policy")
        settle = self.runner.run(
            RunnerAction.SETTLE,
            task_id=proposal.task_id,
            role_key=proposal.role_key,
            generation=generation.digest,
            source_event_id=f"settle-{proposal.provenance[:24]}",
        )
        resume = self.runner.run(
            RunnerAction.RESUME,
            task_id=proposal.task_id,
            role_key=proposal.role_key,
            generation=generation.digest,
            source_event_id=f"retry-{proposal.provenance[:24]}",
            attempt=proposal.retry_attempt,
            retry_of=proposal.retry_of_dispatch_id,
            retry_provenance=proposal.provenance,
        )
        action = (
            "REVIEW_RETRIED_WITHOUT_IMPLEMENTATION_RELAUNCH"
            if proposal.action == "RETRY_REVIEW_ONLY"
            else "SETTLED_AND_RETRIED_SAME_TASK"
        )
        return self._recovery_receipt(
            action, proposal.task_id, proposal.retry_attempt,
            proposal.provenance, connected_terminal, agent_process_live,
            (settle.receipt_digest, resume.receipt_digest),
        )

    def _recovery_receipt(
        self,
        action: str,
        task_id: str | None,
        retry_attempt: int | None,
        retry_provenance: str | None,
        connected_terminal: bool,
        agent_process_live: bool,
        runner_receipt_digests: tuple[str, ...],
    ) -> RecoveryReceipt:
        metadata = self.execution_metadata
        return RecoveryReceipt(
            action=action,
            task_id=task_id,
            retry_attempt=retry_attempt,
            retry_provenance=retry_provenance,
            connected_terminal=connected_terminal,
            agent_process_live=agent_process_live,
            runner_receipt_digests=runner_receipt_digests,
            production_mutated=metadata.mutation_observed is True,
            execution_profile=metadata.profile_name,
            execution_profile_digest=metadata.profile_digest,
            workspace_write_enabled=metadata.workspace_write_enabled,
            mutation_observed=metadata.mutation_observed,
            orca_used=metadata.orca_used,
        )

    def wake_role_session(
        self,
        *,
        role_key: str,
        role_generation: int,
        session_id: str,
        provenance: str,
    ) -> str:
        """Wake one exact reusable role for a sanitized material event."""

        if self.session_runner is None:
            raise WorkflowControllerError("role wakeup requires a direct session runner")
        return self.session_runner.run(
            SessionAction.RESUME,
            role_key=role_key,
            role_generation=role_generation,
            session_id=session_id,
            provenance=provenance,
        ).receipt_digest
