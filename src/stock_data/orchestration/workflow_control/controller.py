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
    LEAD_CHECKPOINT = "lead_checkpoint"
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


@dataclass(frozen=True, slots=True)
class PhaseBoundaryReceipt:
    """Sanitized PM-only bridge from an accepted phase to a fresh contract."""

    task_id: str
    queue_generation: str
    prior_queue_generation: str
    prior_contract_digest: str
    phase_a_candidate_digest: str
    phase_a_review_digest: str
    pm_role_key: str
    pm_generation: int
    prior_state: str
    next_state: str
    reason_code: str
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if (
            _TASK_ID.fullmatch(self.task_id) is None
            or any(
                _DIGEST.fullmatch(value) is None
                for value in (
                    self.queue_generation,
                    self.prior_queue_generation,
                    self.prior_contract_digest,
                    self.phase_a_candidate_digest,
                    self.phase_a_review_digest,
                )
            )
            or _ROLE_KEY.fullmatch(self.pm_role_key) is None
            or not isinstance(self.pm_generation, int)
            or isinstance(self.pm_generation, bool)
            or self.pm_generation < 1
            or self.prior_state != "assigned"
            or self.next_state != "replan_required"
            or self.reason_code != "phase_a_pass_requires_phase_b_contract"
        ):
            raise WorkflowControllerError("phase-boundary receipt is invalid")
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise WorkflowControllerError("phase-boundary receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "task_id": self.task_id,
            "queue_generation": self.queue_generation,
            "prior_queue_generation": self.prior_queue_generation,
            "prior_contract_digest": self.prior_contract_digest,
            "phase_a_candidate_digest": self.phase_a_candidate_digest,
            "phase_a_review_digest": self.phase_a_review_digest,
            "pm_role_key": self.pm_role_key,
            "pm_generation": self.pm_generation,
            "prior_state": self.prior_state,
            "next_state": self.next_state,
            "reason_code": self.reason_code,
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "PhaseBoundaryReceipt":
        expected = {
            "task_id", "queue_generation", "prior_queue_generation",
            "prior_contract_digest", "phase_a_candidate_digest",
            "phase_a_review_digest", "pm_role_key", "pm_generation",
            "prior_state", "next_state", "reason_code", "receipt_digest",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise WorkflowControllerError("phase-boundary receipt fields changed")
        return cls(**dict(value))  # type: ignore[arg-type]


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
                "reviewer_role_key TEXT, reviewer_generation INTEGER, reviewer_session_id TEXT, "
                "fix_count INTEGER NOT NULL DEFAULT 0, assignment_digest TEXT, "
                "PRIMARY KEY(task_id, queue_generation, worker_role_key))"
            )
            assignment_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(worker_assignment)")
            }
            for column, declaration in (
                ("reviewer_role_key", "TEXT"),
                ("reviewer_generation", "INTEGER"),
                ("reviewer_session_id", "TEXT"),
                ("fix_count", "INTEGER NOT NULL DEFAULT 0"),
                ("assignment_digest", "TEXT"),
            ):
                if column not in assignment_columns:
                    connection.execute(
                        f"ALTER TABLE worker_assignment ADD COLUMN {column} {declaration}"
                    )
            ambiguous_legacy = connection.execute(
                "SELECT task_id, queue_generation FROM worker_assignment "
                "GROUP BY task_id, queue_generation HAVING COUNT(*) > 1 AND "
                "SUM(CASE WHEN reviewer_role_key IS NULL OR reviewer_generation IS NULL "
                "OR reviewer_session_id IS NULL THEN 1 ELSE 0 END) > 0"
            ).fetchone()
            if ambiguous_legacy is not None:
                raise MailboxConflict(
                    "legacy multi-Worker contract has no unique durable Reviewer mapping"
                )
            for row in connection.execute(
                "SELECT * FROM worker_assignment WHERE assignment_digest IS NULL"
            ).fetchall():
                values = dict(row)
                task = connection.execute(
                    "SELECT reviewer_role_key, contract_json FROM hierarchy_task "
                    "WHERE task_id = ? AND queue_generation = ?",
                    (row["task_id"], row["queue_generation"]),
                ).fetchone()
                if task is None:
                    raise MailboxConflict("legacy Worker assignment task is missing")
                try:
                    contract_payload = json.loads(str(task["contract_json"]))
                    contract_assignments = tuple(contract_payload["worker_assignments"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    raise MailboxConflict(
                        "legacy Worker assignment contract is invalid"
                    ) from error
                matches = tuple(
                    item
                    for item in contract_assignments
                    if item.get("worker_role_key") == row["worker_role_key"]
                )
                if len(matches) != 1:
                    raise MailboxConflict("legacy Worker assignment is not canonical")
                contract_assignment = matches[0]
                expected_scope = _canonical(
                    {"write_scope": list(contract_assignment.get("write_scope", ())) }
                )
                expected_reviewer = contract_assignment.get(
                    "reviewer_role_key", task["reviewer_role_key"]
                )
                if (
                    row["write_scope_json"] != expected_scope
                    or not isinstance(expected_reviewer, str)
                    or _ROLE_KEY.fullmatch(expected_reviewer) is None
                    or (
                        row["reviewer_role_key"] is not None
                        and row["reviewer_role_key"] != expected_reviewer
                    )
                ):
                    raise MailboxConflict("legacy Worker assignment integrity check failed")
                if row["reviewer_role_key"] is None:
                    try:
                        legacy_reviewer = self.role_registry.get(expected_reviewer)
                    except RoleRegistryError as error:
                        raise MailboxConflict(
                            "legacy Worker Reviewer route is unavailable"
                        ) from error
                    if legacy_reviewer.identity.role_kind is not RoleKind.REVIEWER:
                        raise MailboxConflict("legacy Worker Reviewer route is invalid")
                    values.update(
                        {
                            "reviewer_role_key": legacy_reviewer.identity.role_key,
                            "reviewer_generation": legacy_reviewer.generation,
                            "reviewer_session_id": legacy_reviewer.identity.codex_session_id,
                        }
                    )
                else:
                    raise MailboxConflict(
                        "digestless non-legacy Worker assignment cannot be self-signed"
                    )
                connection.execute(
                    "UPDATE worker_assignment SET reviewer_role_key = ?, "
                    "reviewer_generation = ?, reviewer_session_id = ?, assignment_digest = ? "
                    "WHERE task_id = ? "
                    "AND queue_generation = ? AND worker_role_key = ?",
                    (
                        values["reviewer_role_key"],
                        values["reviewer_generation"],
                        values["reviewer_session_id"],
                        self._worker_assignment_digest(values),
                        row["task_id"],
                        row["queue_generation"],
                        row["worker_role_key"],
                    ),
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lead_checkpoint("
                "checkpoint_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, queue_generation TEXT NOT NULL, "
                "lead_role_key TEXT NOT NULL, checkpoint_digest TEXT NOT NULL, created_at TEXT NOT NULL, "
                "row_digest TEXT)"
            )
            checkpoint_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(lead_checkpoint)")
            }
            if "row_digest" not in checkpoint_columns:
                connection.execute("ALTER TABLE lead_checkpoint ADD COLUMN row_digest TEXT")
            unverifiable_checkpoint = connection.execute(
                "SELECT * FROM lead_checkpoint WHERE row_digest IS NULL"
            ).fetchone()
            if unverifiable_checkpoint is not None:
                raise MailboxConflict(
                    "unverifiable digestless checkpoint requires explicit recovery/replan"
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
            connection.execute(
                "CREATE TABLE IF NOT EXISTS phase_boundary_receipt("
                "task_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS role_wake_outbox("
                "wake_id TEXT PRIMARY KEY, role_key TEXT NOT NULL, role_generation INTEGER NOT NULL, "
                "session_id TEXT NOT NULL, message_id TEXT, provenance TEXT NOT NULL, "
                "status TEXT NOT NULL, runner_receipt_digest TEXT, outbox_digest TEXT)"
            )
            wake_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(role_wake_outbox)")
            }
            if "outbox_digest" not in wake_columns:
                connection.execute("ALTER TABLE role_wake_outbox ADD COLUMN outbox_digest TEXT")
            unverifiable_wake = connection.execute(
                "SELECT * FROM role_wake_outbox WHERE outbox_digest IS NULL"
            ).fetchone()
            if unverifiable_wake is not None:
                raise MailboxConflict(
                    "unverifiable digestless wake requires explicit recovery/replan"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lead_checkpoint_delivery("
                "checkpoint_id TEXT NOT NULL, recipient_role_key TEXT NOT NULL, "
                "recipient_generation INTEGER NOT NULL, recipient_session_id TEXT NOT NULL, "
                "message_id TEXT NOT NULL UNIQUE, delivery_status TEXT NOT NULL, "
                "acknowledgement_ref TEXT, acknowledged_at TEXT, ack_recipient_generation INTEGER, "
                "ledger_digest TEXT NOT NULL, PRIMARY KEY(checkpoint_id, recipient_role_key, "
                "recipient_generation, recipient_session_id))"
            )
            for mailbox_row in connection.execute(
                "SELECT * FROM role_mailbox WHERE message_type = ?",
                (MailboxMessageType.LEAD_CHECKPOINT.value,),
            ).fetchall():
                envelope = self._mailbox_from_row(mailbox_row)
                checkpoint_id = envelope.body.get("checkpoint_id")
                if not isinstance(checkpoint_id, str) or _IDENTIFIER.fullmatch(
                    checkpoint_id
                ) is None:
                    raise MailboxConflict("checkpoint delivery body is invalid")
                checkpoint_row = connection.execute(
                    "SELECT * FROM lead_checkpoint WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                ).fetchone()
                if (
                    checkpoint_row is None
                    or checkpoint_row["row_digest"]
                    != self._checkpoint_row_digest(checkpoint_row)
                    or envelope.task_id != checkpoint_row["task_id"]
                    or envelope.queue_generation != checkpoint_row["queue_generation"]
                    or envelope.sender_role_key != checkpoint_row["lead_role_key"]
                    or envelope.body.get("lead_role_key")
                    != checkpoint_row["lead_role_key"]
                    or envelope.body.get("checkpoint_digest")
                    != checkpoint_row["checkpoint_digest"]
                ):
                    raise MailboxConflict(
                        "checkpoint delivery legacy evidence is inconsistent"
                    )
                values = {
                    "checkpoint_id": checkpoint_id,
                    "recipient_role_key": envelope.recipient_role_key,
                    "recipient_generation": envelope.recipient_generation,
                    "recipient_session_id": envelope.recipient_session_id,
                    "message_id": envelope.message_id,
                    "delivery_status": envelope.delivery_status.value,
                    "acknowledgement_ref": mailbox_row["acknowledgement_ref"],
                    "acknowledged_at": mailbox_row["acknowledged_at"],
                    "ack_recipient_generation": mailbox_row["ack_recipient_generation"],
                }
                connection.execute(
                    "INSERT OR IGNORE INTO lead_checkpoint_delivery("
                    "checkpoint_id, recipient_role_key, recipient_generation, "
                    "recipient_session_id, message_id, delivery_status, acknowledgement_ref, "
                    "acknowledged_at, ack_recipient_generation, ledger_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*values.values(), self._checkpoint_delivery_digest(values)),
                )
            for delivery in connection.execute(
                "SELECT * FROM lead_checkpoint_delivery"
            ).fetchall():
                self._validate_checkpoint_delivery(delivery)
                mailbox_row = connection.execute(
                    "SELECT * FROM role_mailbox WHERE message_id = ?",
                    (delivery["message_id"],),
                ).fetchone()
                if mailbox_row is None:
                    raise MailboxConflict("checkpoint delivery mailbox is missing")
                envelope = self._mailbox_from_row(mailbox_row)
                if (
                    envelope.recipient_role_key != delivery["recipient_role_key"]
                    or envelope.recipient_generation
                    != int(delivery["recipient_generation"])
                    or envelope.recipient_session_id
                    != delivery["recipient_session_id"]
                    or envelope.body.get("checkpoint_id")
                    != delivery["checkpoint_id"]
                ):
                    raise MailboxConflict("checkpoint delivery ledger was rebound")

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
        additional_preflights: tuple[RoleRecord, ...] = (),
    ) -> _GuardedResult:
        """Run one controller mutation while lifecycle CAS is serialized."""

        preflights = (preflight, *additional_preflights)
        with self.role_registry.generations_guard(
            tuple(
                (
                    item.identity.role_key,
                    item.generation,
                    item.identity.codex_session_id,
                )
                for item in preflights
            )
        ) as guarded_records:
            guarded = guarded_records[0]
            if guarded.state not in {RoleState.ACTIVE, RoleState.IDLE}:
                raise RoleRegistryError("role is not available for mailbox work")
            if guarded.identity.role_kind is not expected_kind:
                raise RoleRegistryError("role kind does not match the requested operation")
            if any(
                item.state not in {RoleState.ACTIVE, RoleState.IDLE}
                for item in guarded_records[1:]
            ):
                raise RoleRegistryError("guarded recipient role is not available")
            require_role_authority(self._workflow_role(guarded.identity.role_kind), action)
            return operation()

    def _require_unique_live_project_manager(self) -> RoleRecord:
        project_managers = tuple(
            item for item in self.role_registry.records()
            if item.identity.role_kind is RoleKind.PROJECT_MANAGER
            and item.state in {RoleState.ACTIVE, RoleState.IDLE}
        )
        if len(project_managers) != 1:
            raise RoleRegistryError("checkpoint requires exactly one live project manager")
        pm = project_managers[0]
        return self._require_role(
            pm.identity.role_key,
            pm.generation,
            expected_kind=RoleKind.PROJECT_MANAGER,
        )

    @staticmethod
    def _checkpoint_material(row: sqlite3.Row | Mapping[str, object]) -> dict[str, object]:
        return {
            "checkpoint_id": row["checkpoint_id"],
            "task_id": row["task_id"],
            "queue_generation": row["queue_generation"],
            "lead_role_key": row["lead_role_key"],
            "checkpoint_digest": row["checkpoint_digest"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _worker_assignment_digest(
        row: sqlite3.Row | Mapping[str, object],
    ) -> str:
        return _digest(
            {
                "task_id": row["task_id"],
                "queue_generation": row["queue_generation"],
                "worker_role_key": row["worker_role_key"],
                "write_scope_json": row["write_scope_json"],
                "reviewer_role_key": row["reviewer_role_key"],
                "reviewer_generation": row["reviewer_generation"],
                "reviewer_session_id": row["reviewer_session_id"],
            }
        )

    @staticmethod
    def _validate_worker_assignment(row: sqlite3.Row) -> None:
        if row["assignment_digest"] != WorkflowController._worker_assignment_digest(row):
            raise MailboxConflict("Worker/Reviewer assignment integrity check failed")

    @staticmethod
    def _checkpoint_row_digest(row: sqlite3.Row | Mapping[str, object]) -> str:
        return _digest(WorkflowController._checkpoint_material(row))

    @staticmethod
    def _wake_outbox_material(
        row: sqlite3.Row | Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "wake_id": row["wake_id"],
            "role_key": row["role_key"],
            "role_generation": row["role_generation"],
            "session_id": row["session_id"],
            "message_id": row["message_id"],
            "provenance": row["provenance"],
            "status": row["status"],
            "runner_receipt_digest": row["runner_receipt_digest"],
        }

    @staticmethod
    def _wake_outbox_digest(row: sqlite3.Row | Mapping[str, object]) -> str:
        return _digest(WorkflowController._wake_outbox_material(row))

    @staticmethod
    def _checkpoint_delivery_material(
        row: sqlite3.Row | Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "checkpoint_id": row["checkpoint_id"],
            "recipient_role_key": row["recipient_role_key"],
            "recipient_generation": row["recipient_generation"],
            "recipient_session_id": row["recipient_session_id"],
            "message_id": row["message_id"],
            "delivery_status": row["delivery_status"],
            "acknowledgement_ref": row["acknowledgement_ref"],
            "acknowledged_at": row["acknowledged_at"],
            "ack_recipient_generation": row["ack_recipient_generation"],
        }

    @staticmethod
    def _checkpoint_delivery_digest(
        row: sqlite3.Row | Mapping[str, object],
    ) -> str:
        return _digest(WorkflowController._checkpoint_delivery_material(row))

    @staticmethod
    def _validate_checkpoint_delivery(row: sqlite3.Row) -> None:
        if row["ledger_digest"] != WorkflowController._checkpoint_delivery_digest(row):
            raise MailboxConflict("checkpoint delivery ledger integrity check failed")

    def _checkpoint_delivery_for_mailbox(
        self,
        connection: sqlite3.Connection,
        mailbox_row: sqlite3.Row,
        envelope: MailboxEnvelope,
    ) -> sqlite3.Row | None:
        if envelope.message_type is not MailboxMessageType.LEAD_CHECKPOINT:
            return None
        checkpoint_id = envelope.body.get("checkpoint_id")
        if not isinstance(checkpoint_id, str):
            raise MailboxConflict("checkpoint mailbox body is invalid")
        delivery = connection.execute(
            "SELECT * FROM lead_checkpoint_delivery WHERE message_id = ?",
            (envelope.message_id,),
        ).fetchone()
        if delivery is None:
            raise MailboxConflict("checkpoint delivery ledger is missing")
        self._validate_checkpoint_delivery(delivery)
        if (
            delivery["checkpoint_id"] != checkpoint_id
            or delivery["recipient_role_key"] != envelope.recipient_role_key
            or int(delivery["recipient_generation"]) != envelope.recipient_generation
            or delivery["recipient_session_id"] != envelope.recipient_session_id
            or delivery["delivery_status"] != envelope.delivery_status.value
            or delivery["acknowledgement_ref"] != mailbox_row["acknowledgement_ref"]
            or delivery["acknowledged_at"] != mailbox_row["acknowledged_at"]
            or delivery["ack_recipient_generation"]
            != mailbox_row["ack_recipient_generation"]
        ):
            raise MailboxConflict("checkpoint delivery ledger was rebound")
        return delivery

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
        material = {
            "body_digest": row["body_digest"],
            "message_type": row["message_type"],
            "parent_message_id": row["parent_message_id"],
            "queue_generation": row["queue_generation"],
            "recipient_role_key": row["recipient_role_key"],
            "sender_role_key": row["sender_role_key"],
            "task_id": row["task_id"],
        }
        if row["message_type"] == MailboxMessageType.LEAD_CHECKPOINT.value:
            material.update(
                {
                    "recipient_generation": row["recipient_generation"],
                    "recipient_session_id": row["recipient_session_id"],
                }
            )
        return "msg-" + _digest(material)

    @staticmethod
    def _legacy_internal_message_id(row: sqlite3.Row | Mapping[str, object]) -> str:
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
        if message_type is MailboxMessageType.LEAD_CHECKPOINT:
            identity.update(
                {
                    "recipient_generation": recipient.generation,
                    "recipient_session_id": recipient.identity.codex_session_id,
                }
            )
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
            valid_internal_ids = {WorkflowController._internal_message_id(row)}
            if str(row["message_type"]) == MailboxMessageType.LEAD_CHECKPOINT.value:
                valid_internal_ids.add(WorkflowController._legacy_internal_message_id(row))
            if message_id not in valid_internal_ids:
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
            with self._connect() as connection:
                self._checkpoint_delivery_for_mailbox(connection, row, envelope)
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
            if envelope.message_type is MailboxMessageType.LEAD_CHECKPOINT:
                pending_row = connection.execute(
                    "SELECT * FROM lead_checkpoint_delivery WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if pending_row is None:
                    connection.rollback()
                    raise MailboxConflict("checkpoint delivery ledger is missing")
                self._validate_checkpoint_delivery(pending_row)
                if pending_row["delivery_status"] not in {
                    MailboxStatus.PENDING.value,
                    MailboxStatus.ACKNOWLEDGED.value,
                }:
                    connection.rollback()
                    raise MailboxConflict("checkpoint delivery status is invalid")
                delivery_values = {
                    "checkpoint_id": pending_row["checkpoint_id"],
                    "recipient_role_key": pending_row["recipient_role_key"],
                    "recipient_generation": pending_row["recipient_generation"],
                    "recipient_session_id": pending_row["recipient_session_id"],
                    "message_id": pending_row["message_id"],
                    "delivery_status": MailboxStatus.ACKNOWLEDGED.value,
                    "acknowledgement_ref": acknowledgement_ref,
                    "acknowledged_at": utc_text(acknowledged_at),
                    "ack_recipient_generation": acknowledged_generation,
                }
                connection.execute(
                    "UPDATE lead_checkpoint_delivery SET delivery_status = ?, "
                    "acknowledgement_ref = ?, acknowledged_at = ?, "
                    "ack_recipient_generation = ?, ledger_digest = ? WHERE message_id = ?",
                    (
                        MailboxStatus.ACKNOWLEDGED.value,
                        acknowledgement_ref,
                        utc_text(acknowledged_at),
                        acknowledged_generation,
                        self._checkpoint_delivery_digest(delivery_values),
                        message_id,
                    ),
                )
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
                        **(
                            {"reviewer_role_key": item.reviewer_role_key}
                            if item.reviewer_role_key is not None
                            else {}
                        ),
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
        lead = self._require_role(
            contract.lead_role_key,
            self.role_registry.get(contract.lead_role_key).generation,
            expected_kind=RoleKind.DOMAIN_LEAD,
        )
        workers = tuple(
            self._require_role(
                assignment.worker_role_key,
                self.role_registry.get(assignment.worker_role_key).generation,
                expected_kind=RoleKind.WORKER,
            )
            for assignment in contract.worker_assignments
        )
        reviewers = tuple(
            self._require_role(
                assignment.reviewer_role_key or contract.reviewer_role_key,
                self.role_registry.get(
                    assignment.reviewer_role_key or contract.reviewer_role_key
                ).generation,
                expected_kind=RoleKind.REVIEWER,
            )
            for assignment in contract.worker_assignments
        )
        return self._run_generation_bound(
            pm,
            action=RoleAction.ASSIGN_LEAD,
            expected_kind=RoleKind.PROJECT_MANAGER,
            additional_preflights=(lead, *workers, *reviewers),
            operation=lambda: self._dispatch_task_contract_unlocked(
                contract, pm_generation=pm_generation
            ),
        )

    def mark_task_replan_ready(
        self,
        *,
        task_id: str,
        expected_queue_generation: str,
        expected_prior_contract_digest: str,
        expected_phase_a_candidate_digest: str,
        expected_phase_a_review_digest: str,
        expected_prior_state: str,
        reason_code: str,
        pm_role_key: str,
        pm_generation: int,
        ) -> PhaseBoundaryReceipt:
        """CAS one PM-approved accepted phase into the replan state.

        The caller must separately verify the Queue-held Phase-A evidence before
        invoking this controller method.  This durable receipt binds every
        verified digest so a later contract cannot silently substitute it.
        """

        if (
            _TASK_ID.fullmatch(task_id) is None
            or any(
                _DIGEST.fullmatch(value) is None
                for value in (
                    expected_queue_generation,
                    expected_prior_contract_digest,
                    expected_phase_a_candidate_digest,
                    expected_phase_a_review_digest,
                )
            )
            or expected_prior_state != "assigned"
            or reason_code != "phase_a_pass_requires_phase_b_contract"
        ):
            raise WorkflowControllerError("phase-boundary pins are invalid")
        pm = self._require_role(
            pm_role_key,
            pm_generation,
            action=RoleAction.REPLAN,
            expected_kind=RoleKind.PROJECT_MANAGER,
        )
        return self._run_generation_bound(
            pm,
            action=RoleAction.REPLAN,
            expected_kind=RoleKind.PROJECT_MANAGER,
            operation=lambda: self._mark_task_replan_ready_unlocked(
                task_id=task_id,
                expected_queue_generation=expected_queue_generation,
                expected_prior_contract_digest=expected_prior_contract_digest,
                expected_phase_a_candidate_digest=expected_phase_a_candidate_digest,
                expected_phase_a_review_digest=expected_phase_a_review_digest,
                expected_prior_state=expected_prior_state,
                reason_code=reason_code,
                pm_role_key=pm.identity.role_key,
                pm_generation=pm.generation,
            ),
        )

    def preflight_task_replan_ready(
        self,
        *,
        task_id: str,
        expected_queue_generation: str,
        expected_prior_contract_digest: str,
        expected_phase_a_candidate_digest: str,
        expected_phase_a_review_digest: str,
        expected_prior_state: str,
        reason_code: str,
        pm_role_key: str,
        pm_generation: int,
    ) -> PhaseBoundaryReceipt:
        """Read-only exact proof for one subsequent PM phase-boundary CAS."""

        if (
            _TASK_ID.fullmatch(task_id) is None
            or any(
                _DIGEST.fullmatch(value) is None
                for value in (
                    expected_queue_generation,
                    expected_prior_contract_digest,
                    expected_phase_a_candidate_digest,
                    expected_phase_a_review_digest,
                )
            )
            or expected_prior_state != "assigned"
            or reason_code != "phase_a_pass_requires_phase_b_contract"
        ):
            raise WorkflowControllerError("phase-boundary pins are invalid")
        pm = self._require_role(
            pm_role_key,
            pm_generation,
            action=RoleAction.REPLAN,
            expected_kind=RoleKind.PROJECT_MANAGER,
        )
        return self._run_generation_bound(
            pm,
            action=RoleAction.REPLAN,
            expected_kind=RoleKind.PROJECT_MANAGER,
            operation=lambda: self._preflight_task_replan_ready_unlocked(
                task_id=task_id,
                expected_queue_generation=expected_queue_generation,
                expected_prior_contract_digest=expected_prior_contract_digest,
                expected_phase_a_candidate_digest=expected_phase_a_candidate_digest,
                expected_phase_a_review_digest=expected_phase_a_review_digest,
                expected_prior_state=expected_prior_state,
                reason_code=reason_code,
                pm_role_key=pm.identity.role_key,
                pm_generation=pm.generation,
            ),
        )

    @staticmethod
    def preflight_task_replan_ready_at(
        receipt_path: Path,
        *,
        task_id: str,
        expected_queue_generation: str,
        expected_prior_contract_digest: str,
        expected_phase_a_candidate_digest: str,
        expected_phase_a_review_digest: str,
        expected_prior_state: str,
        reason_code: str,
        pm_role_key: str,
        pm_generation: int,
    ) -> PhaseBoundaryReceipt:
        """Read-only PM preflight without constructing a writable service."""

        if (
            _TASK_ID.fullmatch(task_id) is None
            or any(
                _DIGEST.fullmatch(value) is None
                for value in (
                    expected_queue_generation,
                    expected_prior_contract_digest,
                    expected_phase_a_candidate_digest,
                    expected_phase_a_review_digest,
                )
            )
            or expected_prior_state != "assigned"
            or reason_code != "phase_a_pass_requires_phase_b_contract"
            or not isinstance(pm_generation, int)
            or isinstance(pm_generation, bool)
            or pm_generation < 1
        ):
            raise WorkflowControllerError("phase-boundary pins are invalid")
        path = Path(receipt_path)
        registry_path = path.with_name("role_registry.sqlite3")
        if not path.is_file() or not registry_path.is_file():
            raise WorkflowControllerError("phase-boundary preflight is unavailable")
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                receipt_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'phase_boundary_receipt'"
                ).fetchone()
                existing = (
                    connection.execute(
                        "SELECT payload FROM phase_boundary_receipt WHERE task_id = ?",
                        (task_id,),
                    ).fetchone()
                    if receipt_table is not None
                    else None
                )
                task = connection.execute(
                    "SELECT queue_generation, contract_digest, state FROM hierarchy_task "
                    "WHERE task_id = ?", (task_id,)
                ).fetchone()
            with sqlite3.connect(registry_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                pm = connection.execute(
                    "SELECT role_kind, state, generation FROM role_registry WHERE role_key = ?",
                    (pm_role_key,),
                ).fetchone()
        except sqlite3.Error as error:
            raise WorkflowControllerError("phase-boundary preflight is unavailable") from error
        if pm is None:
            raise RoleRegistryError("role key is not registered")
        if pm["generation"] != pm_generation:
            raise StaleRoleGeneration("role generation changed")
        if pm["state"] not in {RoleState.ACTIVE.value, RoleState.IDLE.value}:
            raise RoleRegistryError("role is not available for mailbox work")
        if pm["role_kind"] != RoleKind.PROJECT_MANAGER.value:
            raise RoleRegistryError("role kind does not match the requested operation")
        if existing is not None:
            try:
                receipt = PhaseBoundaryReceipt.from_dict(json.loads(str(existing["payload"])))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise MailboxConflict("phase-boundary receipt integrity failed") from error
            expected = PhaseBoundaryReceipt(
                task_id=task_id,
                queue_generation=expected_queue_generation,
                prior_queue_generation=receipt.prior_queue_generation,
                prior_contract_digest=expected_prior_contract_digest,
                phase_a_candidate_digest=expected_phase_a_candidate_digest,
                phase_a_review_digest=expected_phase_a_review_digest,
                pm_role_key=pm_role_key,
                pm_generation=pm_generation,
                prior_state=expected_prior_state,
                next_state="replan_required",
                reason_code=reason_code,
            )
            if (
                receipt.to_dict() != expected.to_dict()
                or task is None
                or task["state"] != "replan_required"
            ):
                raise MailboxConflict("phase-boundary receipt rebound")
            return receipt
        if (
            task is None
            or task["contract_digest"] != expected_prior_contract_digest
            or task["state"] != expected_prior_state
            or _DIGEST.fullmatch(str(task["queue_generation"])) is None
        ):
            raise StaleQueueGeneration("phase-boundary task pins changed")
        return PhaseBoundaryReceipt(
            task_id=task_id,
            queue_generation=expected_queue_generation,
            prior_queue_generation=str(task["queue_generation"]),
            prior_contract_digest=expected_prior_contract_digest,
            phase_a_candidate_digest=expected_phase_a_candidate_digest,
            phase_a_review_digest=expected_phase_a_review_digest,
            pm_role_key=pm_role_key,
            pm_generation=pm_generation,
            prior_state=expected_prior_state,
            next_state="replan_required",
            reason_code=reason_code,
        )

    def _preflight_task_replan_ready_unlocked(
        self,
        *,
        task_id: str,
        expected_queue_generation: str,
        expected_prior_contract_digest: str,
        expected_phase_a_candidate_digest: str,
        expected_phase_a_review_digest: str,
        expected_prior_state: str,
        reason_code: str,
        pm_role_key: str,
        pm_generation: int,
    ) -> PhaseBoundaryReceipt:
        uri = self.receipt_path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                existing = connection.execute(
                    "SELECT payload FROM phase_boundary_receipt WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                task = connection.execute(
                    "SELECT queue_generation, contract_digest, state FROM hierarchy_task "
                    "WHERE task_id = ?", (task_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise WorkflowControllerError("phase-boundary preflight is unavailable") from error
        if existing is not None:
            try:
                receipt = PhaseBoundaryReceipt.from_dict(json.loads(str(existing["payload"])))
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise MailboxConflict("phase-boundary receipt integrity failed") from error
            expected = PhaseBoundaryReceipt(
                task_id=task_id,
                queue_generation=expected_queue_generation,
                prior_queue_generation=receipt.prior_queue_generation,
                prior_contract_digest=expected_prior_contract_digest,
                phase_a_candidate_digest=expected_phase_a_candidate_digest,
                phase_a_review_digest=expected_phase_a_review_digest,
                pm_role_key=pm_role_key,
                pm_generation=pm_generation,
                prior_state=expected_prior_state,
                next_state="replan_required",
                reason_code=reason_code,
            )
            if receipt != expected or task is None or task["state"] != "replan_required":
                raise MailboxConflict("phase-boundary receipt was rebound")
            return receipt
        if (
            task is None
            or task["contract_digest"] != expected_prior_contract_digest
            or task["state"] != expected_prior_state
            or _DIGEST.fullmatch(str(task["queue_generation"])) is None
        ):
            raise StaleQueueGeneration("phase-boundary task pins changed")
        return PhaseBoundaryReceipt(
            task_id=task_id,
            queue_generation=expected_queue_generation,
            prior_queue_generation=str(task["queue_generation"]),
            prior_contract_digest=expected_prior_contract_digest,
            phase_a_candidate_digest=expected_phase_a_candidate_digest,
            phase_a_review_digest=expected_phase_a_review_digest,
            pm_role_key=pm_role_key,
            pm_generation=pm_generation,
            prior_state=expected_prior_state,
            next_state="replan_required",
            reason_code=reason_code,
        )

    def _mark_task_replan_ready_unlocked(
        self,
        *,
        task_id: str,
        expected_queue_generation: str,
        expected_prior_contract_digest: str,
        expected_phase_a_candidate_digest: str,
        expected_phase_a_review_digest: str,
        expected_prior_state: str,
        reason_code: str,
        pm_role_key: str,
        pm_generation: int,
    ) -> PhaseBoundaryReceipt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT payload FROM phase_boundary_receipt WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if existing is not None:
                    try:
                        receipt = PhaseBoundaryReceipt.from_dict(
                            json.loads(str(existing["payload"]))
                        )
                    except (TypeError, ValueError, json.JSONDecodeError) as error:
                        raise MailboxConflict(
                            "phase-boundary receipt integrity failed"
                        ) from error
                    if receipt.to_dict() != PhaseBoundaryReceipt(
                        task_id=task_id,
                        queue_generation=expected_queue_generation,
                        prior_queue_generation=receipt.prior_queue_generation,
                        prior_contract_digest=expected_prior_contract_digest,
                        phase_a_candidate_digest=expected_phase_a_candidate_digest,
                        phase_a_review_digest=expected_phase_a_review_digest,
                        pm_role_key=pm_role_key,
                        pm_generation=pm_generation,
                        prior_state=expected_prior_state,
                        next_state="replan_required",
                        reason_code=reason_code,
                    ).to_dict():
                        raise MailboxConflict("phase-boundary receipt was rebound")
                    task = connection.execute(
                        "SELECT state FROM hierarchy_task WHERE task_id = ?", (task_id,)
                    ).fetchone()
                    if task is None or task["state"] != "replan_required":
                        raise MailboxConflict("phase-boundary state changed")
                    connection.commit()
                    return receipt

                task = connection.execute(
                    "SELECT queue_generation, contract_digest, state FROM hierarchy_task "
                    "WHERE task_id = ?", (task_id,)
                ).fetchone()
                if (
                    task is None
                    or task["contract_digest"] != expected_prior_contract_digest
                    or task["state"] != expected_prior_state
                    or _DIGEST.fullmatch(str(task["queue_generation"])) is None
                ):
                    raise StaleQueueGeneration("phase-boundary task pins changed")
                receipt = PhaseBoundaryReceipt(
                    task_id=task_id,
                    queue_generation=expected_queue_generation,
                    prior_queue_generation=str(task["queue_generation"]),
                    prior_contract_digest=expected_prior_contract_digest,
                    phase_a_candidate_digest=expected_phase_a_candidate_digest,
                    phase_a_review_digest=expected_phase_a_review_digest,
                    pm_role_key=pm_role_key,
                    pm_generation=pm_generation,
                    prior_state=expected_prior_state,
                    next_state="replan_required",
                    reason_code=reason_code,
                )
                changed = connection.execute(
                    "UPDATE hierarchy_task SET state = 'replan_required' "
                    "WHERE task_id = ? AND state = ? AND contract_digest = ?",
                    (task_id, expected_prior_state, expected_prior_contract_digest),
                ).rowcount
                if changed != 1:
                    raise StaleQueueGeneration("phase-boundary task changed before transition")
                connection.execute(
                    "INSERT INTO phase_boundary_receipt(task_id, payload) VALUES (?, ?)",
                    (
                        task_id,
                        _canonical(receipt.to_dict()),
                    ),
                )
                connection.commit()
                return receipt
            except BaseException:
                connection.rollback()
                raise

    def inspect_phase_boundary_receipt(
        self, *, task_id: str
    ) -> PhaseBoundaryReceipt:
        return self.inspect_phase_boundary_receipt_at(self.receipt_path, task_id=task_id)

    @staticmethod
    def inspect_phase_boundary_receipt_at(
        receipt_path: Path, *, task_id: str
    ) -> PhaseBoundaryReceipt:
        """Read a phase-boundary receipt through a SQLite read-only handle."""

        if _TASK_ID.fullmatch(task_id) is None:
            raise WorkflowControllerError("phase-boundary task id is invalid")
        path = Path(receipt_path)
        if not path.is_file():
            raise WorkflowControllerError("phase-boundary receipt is absent")
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT payload FROM phase_boundary_receipt WHERE task_id = ?", (task_id,)
                ).fetchone()
        except sqlite3.Error as error:
            raise WorkflowControllerError("phase-boundary receipt is unavailable") from error
        if row is None:
            raise WorkflowControllerError("phase-boundary receipt is absent")
        try:
            return PhaseBoundaryReceipt.from_dict(json.loads(str(row["payload"])))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise MailboxConflict("phase-boundary receipt integrity failed") from error

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
        if lead.identity.role_kind is not RoleKind.DOMAIN_LEAD:
            raise RoleRegistryError("task contract Lead identity is not a Lead")
        if lead.identity.parent_role_key != pm.identity.role_key:
            raise RoleRegistryError("task contract Lead is outside the PM hierarchy")
        session_ids = {
            lead.identity.role_key: lead.identity.codex_session_id,
        }
        reviewers_by_worker: dict[str, RoleRecord] = {}
        for assignment in contract.worker_assignments:
            worker = self.role_registry.get(assignment.worker_role_key)
            reviewer = self.role_registry.get(
                assignment.reviewer_role_key or contract.reviewer_role_key
            )
            if worker.identity.role_kind is not RoleKind.WORKER:
                raise RoleRegistryError("task contract Worker identity is not a Worker")
            if reviewer.identity.role_kind is not RoleKind.REVIEWER:
                raise RoleRegistryError("task contract Reviewer identity is not a Reviewer")
            if worker.identity.parent_role_key != lead.identity.role_key:
                raise RoleRegistryError("task contract Worker is outside the Lead hierarchy")
            if reviewer.identity.parent_role_key != lead.identity.role_key:
                raise RoleRegistryError("task contract Reviewer is outside the Lead hierarchy")
            session_ids[worker.identity.role_key] = worker.identity.codex_session_id
            session_ids[reviewer.identity.role_key] = reviewer.identity.codex_session_id
            reviewers_by_worker[assignment.worker_role_key] = reviewer
        require_unique_role_sessions(
            lead_role_key=lead.identity.role_key,
            reviewer_role_key=contract.reviewer_role_key,
            worker_role_keys=tuple(
                assignment.worker_role_key for assignment in contract.worker_assignments
            ),
            reviewer_role_keys=tuple(
                reviewers_by_worker[assignment.worker_role_key].identity.role_key
                for assignment in contract.worker_assignments
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
                reviewer = reviewers_by_worker[assignment.worker_role_key]
                write_scope_json = _canonical(
                    {"write_scope": list(assignment.write_scope)}
                )
                existing_assignment = connection.execute(
                    "SELECT * FROM worker_assignment WHERE task_id = ? "
                    "AND queue_generation = ? AND worker_role_key = ?",
                    (
                        contract.task_id,
                        contract.queue_generation,
                        assignment.worker_role_key,
                    ),
                ).fetchone()
                if existing_assignment is None:
                    assignment_values = {
                        "task_id": contract.task_id,
                        "queue_generation": contract.queue_generation,
                        "worker_role_key": assignment.worker_role_key,
                        "write_scope_json": write_scope_json,
                        "reviewer_role_key": reviewer.identity.role_key,
                        "reviewer_generation": reviewer.generation,
                        "reviewer_session_id": reviewer.identity.codex_session_id,
                    }
                    connection.execute(
                        "INSERT INTO worker_assignment(task_id, queue_generation, "
                        "worker_role_key, write_scope_json, reviewer_role_key, "
                        "reviewer_generation, reviewer_session_id, assignment_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            *assignment_values.values(),
                            self._worker_assignment_digest(assignment_values),
                        ),
                    )
                elif (
                    existing_assignment["write_scope_json"] != write_scope_json
                    or existing_assignment["reviewer_role_key"]
                    not in {None, reviewer.identity.role_key}
                    or existing_assignment["reviewer_generation"]
                    not in {None, reviewer.generation}
                    or existing_assignment["reviewer_session_id"]
                    not in {None, reviewer.identity.codex_session_id}
                ):
                    raise MailboxConflict("Worker/Reviewer assignment was rebound")
                elif existing_assignment["reviewer_role_key"] is None:
                    assignment_values = {
                        "task_id": contract.task_id,
                        "queue_generation": contract.queue_generation,
                        "worker_role_key": assignment.worker_role_key,
                        "write_scope_json": write_scope_json,
                        "reviewer_role_key": reviewer.identity.role_key,
                        "reviewer_generation": reviewer.generation,
                        "reviewer_session_id": reviewer.identity.codex_session_id,
                    }
                    connection.execute(
                        "UPDATE worker_assignment SET reviewer_role_key = ?, "
                        "reviewer_generation = ?, reviewer_session_id = ?, "
                        "assignment_digest = ? WHERE task_id = ? "
                        "AND queue_generation = ? AND worker_role_key = ?",
                        (
                            reviewer.identity.role_key,
                            reviewer.generation,
                            reviewer.identity.codex_session_id,
                            self._worker_assignment_digest(assignment_values),
                            contract.task_id,
                            contract.queue_generation,
                            assignment.worker_role_key,
                        ),
                    )
                else:
                    self._validate_worker_assignment(existing_assignment)
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
        task = self._current_task(task_id, queue_generation)
        if task["lead_role_key"] != lead_role_key:
            raise RoleRegistryError("Lead does not own this task contract")
        with self._connect() as connection:
            assignment_rows = tuple(
                connection.execute(
                    "SELECT * FROM worker_assignment WHERE task_id = ? "
                    "AND queue_generation = ? ORDER BY worker_role_key",
                    (task_id, queue_generation),
                ).fetchall()
            )
        for row in assignment_rows:
            self._validate_worker_assignment(row)
        workers = tuple(
            self._require_role(
                role_key,
                self.role_registry.get(role_key).generation,
                expected_kind=RoleKind.WORKER,
            )
            for role_key in (str(row["worker_role_key"]) for row in assignment_rows)
        )
        reviewers = tuple(
            self._require_role(
                str(row["reviewer_role_key"]),
                int(row["reviewer_generation"]),
                expected_kind=RoleKind.REVIEWER,
            )
            for row in assignment_rows
        )
        if any(
            reviewer.identity.codex_session_id != str(row["reviewer_session_id"])
            for reviewer, row in zip(reviewers, assignment_rows, strict=True)
        ):
            raise StaleRoleGeneration("preassigned Reviewer session changed")
        return self._run_generation_bound(
            lead,
            action=RoleAction.DISPATCH_WORKER,
            expected_kind=RoleKind.DOMAIN_LEAD,
            additional_preflights=(*workers, *reviewers),
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
                    "SELECT * FROM worker_assignment "
                    "WHERE task_id = ? AND queue_generation = ? ORDER BY worker_role_key",
                    (task_id, queue_generation),
                ).fetchall()
                for assignment in assignments:
                    self._validate_worker_assignment(assignment)
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
                        body=json.loads(str(assignment["write_scope_json"]))
                        | {
                            "reviewer_role_key": assignment["reviewer_role_key"],
                            "reviewer_generation": assignment["reviewer_generation"],
                            "reviewer_session_id": assignment["reviewer_session_id"],
                        },
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
        project_manager = self._require_unique_live_project_manager()
        return self._run_generation_bound(
            lead,
            action=RoleAction.PROGRESS_CHECKPOINT,
            expected_kind=RoleKind.DOMAIN_LEAD,
            additional_preflights=(project_manager,),
            operation=lambda: self._record_lead_checkpoint_unlocked(
                task_id=task_id,
                queue_generation=queue_generation,
                lead_role_key=lead_role_key,
                lead_generation=lead_generation,
                checkpoint_digest=checkpoint_digest,
                project_manager=project_manager,
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
        project_manager: RoleRecord,
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
                created_at = utc_text(datetime.now(UTC))
                checkpoint_values = {
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "queue_generation": queue_generation,
                    "lead_role_key": lead_role_key,
                    "checkpoint_digest": checkpoint_digest,
                    "created_at": created_at,
                }
                connection.execute(
                    "INSERT OR IGNORE INTO lead_checkpoint(checkpoint_id, task_id, "
                    "queue_generation, lead_role_key, checkpoint_digest, created_at, row_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*checkpoint_values.values(), self._checkpoint_row_digest(checkpoint_values)),
                )
                row = connection.execute(
                    "SELECT checkpoint_digest, checkpoint_id, task_id, queue_generation, "
                    "lead_role_key, created_at, row_digest FROM lead_checkpoint "
                    "WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                ).fetchone()
                if row is None:
                    raise MailboxConflict("Lead checkpoint id was rebound")
                try:
                    parse_utc(str(row["created_at"]))
                except (TypeError, ValueError) as error:
                    raise MailboxConflict("checkpoint created_at integrity failed") from error
                if (
                    row["checkpoint_id"] != checkpoint_id
                    or row["task_id"] != task_id
                    or row["queue_generation"] != queue_generation
                    or row["lead_role_key"] != lead_role_key
                    or row["checkpoint_digest"] != checkpoint_digest
                    or row["row_digest"] != self._checkpoint_row_digest(row)
                ):
                    raise MailboxConflict("checkpoint row integrity check failed")
                delivery = connection.execute(
                    "SELECT * FROM lead_checkpoint_delivery WHERE checkpoint_id = ? "
                    "AND recipient_role_key = ? AND recipient_generation = ? "
                    "AND recipient_session_id = ?",
                    (
                        checkpoint_id,
                        project_manager.identity.role_key,
                        project_manager.generation,
                        project_manager.identity.codex_session_id,
                    ),
                ).fetchone()
                if delivery is not None:
                    self._validate_checkpoint_delivery(delivery)
                    mailbox_row = connection.execute(
                        "SELECT * FROM role_mailbox WHERE message_id = ?",
                        (delivery["message_id"],),
                    ).fetchone()
                    if mailbox_row is None:
                        raise MailboxConflict("checkpoint delivery mailbox is missing")
                    envelope = self._mailbox_from_row(mailbox_row)
                    if (
                        envelope.recipient_role_key
                        != project_manager.identity.role_key
                        or envelope.recipient_generation != project_manager.generation
                        or envelope.recipient_session_id
                        != project_manager.identity.codex_session_id
                        or envelope.message_type is not MailboxMessageType.LEAD_CHECKPOINT
                        or envelope.body.get("checkpoint_id") != checkpoint_id
                        or envelope.delivery_status.value != delivery["delivery_status"]
                        or mailbox_row["acknowledgement_ref"]
                        != delivery["acknowledgement_ref"]
                        or mailbox_row["acknowledged_at"] != delivery["acknowledged_at"]
                        or mailbox_row["ack_recipient_generation"]
                        != delivery["ack_recipient_generation"]
                    ):
                        raise MailboxConflict("checkpoint delivery ledger was rebound")
                else:
                    envelope = self._insert_mailbox_in_transaction(
                        connection,
                        sender_role_key=lead_role_key,
                        recipient_role_key=project_manager.identity.role_key,
                        recipient_generation=project_manager.generation,
                        message_type=MailboxMessageType.LEAD_CHECKPOINT,
                        body={
                            "checkpoint_digest": checkpoint_digest,
                            "checkpoint_id": checkpoint_id,
                            "lead_role_key": lead_role_key,
                        },
                        task_id=task_id,
                        queue_generation=queue_generation,
                    )
                    delivery_values = {
                        "checkpoint_id": checkpoint_id,
                        "recipient_role_key": envelope.recipient_role_key,
                        "recipient_generation": envelope.recipient_generation,
                        "recipient_session_id": envelope.recipient_session_id,
                        "message_id": envelope.message_id,
                        "delivery_status": envelope.delivery_status.value,
                        "acknowledgement_ref": None,
                        "acknowledged_at": None,
                        "ack_recipient_generation": None,
                    }
                    connection.execute(
                        "INSERT INTO lead_checkpoint_delivery(checkpoint_id, "
                        "recipient_role_key, recipient_generation, recipient_session_id, "
                        "message_id, delivery_status, acknowledgement_ref, acknowledged_at, "
                        "ack_recipient_generation, ledger_digest) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            *delivery_values.values(),
                            self._checkpoint_delivery_digest(delivery_values),
                        ),
                    )
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
        task = self._current_task(task_id, queue_generation)
        with self._connect() as connection:
            assignment = connection.execute(
                "SELECT * FROM worker_assignment WHERE task_id = ? "
                "AND queue_generation = ? AND worker_role_key = ?",
                (task_id, queue_generation, worker_role_key),
            ).fetchone()
        if assignment is None:
            raise RoleRegistryError("Worker is outside the task fan-out")
        self._validate_worker_assignment(assignment)
        reviewer = self._require_role(
            str(assignment["reviewer_role_key"]),
            int(assignment["reviewer_generation"]),
            expected_kind=RoleKind.REVIEWER,
        )
        if reviewer.identity.codex_session_id != assignment["reviewer_session_id"]:
            raise StaleRoleGeneration("preassigned Reviewer session changed")
        lead = self._require_role(
            str(task["lead_role_key"]),
            self.role_registry.get(str(task["lead_role_key"])).generation,
            expected_kind=RoleKind.DOMAIN_LEAD,
        )
        return self._run_generation_bound(
            worker,
            action=RoleAction.SUBMIT_CANDIDATE,
            expected_kind=RoleKind.WORKER,
            additional_preflights=(reviewer, lead),
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
                    "SELECT * FROM worker_assignment "
                    "WHERE task_id = ? AND queue_generation = ? AND worker_role_key = ?",
                    (task_id, queue_generation, worker_role_key),
                ).fetchone()
                if assignment is None:
                    raise RoleRegistryError("Worker is outside the task fan-out")
                self._validate_worker_assignment(assignment)
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
                reviewer = self._require_role(
                    str(assignment["reviewer_role_key"]),
                    int(assignment["reviewer_generation"]),
                    expected_kind=RoleKind.REVIEWER,
                )
                if reviewer.identity.codex_session_id != assignment["reviewer_session_id"]:
                    raise StaleRoleGeneration("preassigned Reviewer session changed")
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
                if task["state"] == "replan_required":
                    raise ReviewLoopError("third FIX already requires Lead/PM replan")
                assignment = connection.execute(
                    "SELECT * FROM worker_assignment "
                    "WHERE task_id = ? AND queue_generation = ? AND worker_role_key = ?",
                    (task_id, queue_generation, worker_role_key),
                ).fetchone()
                if assignment is None:
                    raise RoleRegistryError("reviewed Worker is outside the task fan-out")
                self._validate_worker_assignment(assignment)
                if (
                    assignment["reviewer_role_key"] != reviewer_role_key
                    or int(assignment["reviewer_generation"]) != reviewer_generation
                    or assignment["reviewer_session_id"]
                    != reviewer.identity.codex_session_id
                ):
                    raise RoleRegistryError(
                        "review decision did not come from the preassigned Reviewer"
                    )
                if (
                    assignment["candidate_state"] != "pending_review"
                    or assignment["candidate_digest"] != candidate_digest
                ):
                    raise ReviewLoopError("review decision targets a stale candidate")
                lead = self.role_registry.get(str(task["lead_role_key"]))
                pm = self.role_registry.get(str(task["pm_role_key"]))
                worker = self.role_registry.get(worker_role_key)
                current_fix_count = int(assignment["fix_count"])
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
                    "UPDATE worker_assignment SET candidate_state = ?, fix_count = ? "
                    "WHERE task_id = ? "
                    "AND queue_generation = ? AND worker_role_key = ? "
                    "AND candidate_digest = ? AND candidate_state = 'pending_review'",
                    (
                        next_candidate_state, fix_count, task_id, queue_generation,
                        worker_role_key, candidate_digest,
                    ),
                ).rowcount
                if changed != 1:
                    raise ReviewLoopError("candidate changed before review settlement")
                connection.execute(
                    "UPDATE hierarchy_task SET fix_count = MAX(fix_count, ?), state = ? "
                    "WHERE task_id = ? "
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
        expected_generation: int,
        expected_session_id: str,
        message_id: str | None = None,
        source_event_id: str | None = None,
    ) -> str:
        """Resume one exact durable role through a retry-safe wake outbox.

        A candidate-bound wake additionally proves that the durable mailbox is
        still addressed to this exact Reviewer lifecycle.  The external Codex
        resume is keyed by a stable provenance digest, so a crash after the
        boundary call can safely retry the same operation.
        """

        if self.session_runner is None:
            raise WorkflowControllerError("role wakeup requires a direct session runner")
        preflight = self._require_role(role_key, expected_generation)
        if preflight.identity.codex_session_id != expected_session_id:
            raise StaleRoleGeneration("role generation or session changed")
        if message_id is not None and _IDENTIFIER.fullmatch(message_id) is None:
            raise WorkflowControllerError("wake message id is invalid")
        if source_event_id is not None and _DIGEST.fullmatch(source_event_id) is None:
            raise WorkflowControllerError("wake source event id is invalid")
        wake_id = "wake-" + _digest(
            {
                "message_id": message_id,
                "role_generation": expected_generation,
                "role_key": role_key,
                "session_id": expected_session_id,
                "source_event_id": source_event_id,
            }
        )
        provenance = _digest({"wake_id": wake_id})
        with self.role_registry.generation_guard(
            role_key,
            expected_generation=expected_generation,
            expected_session_id=expected_session_id,
        ) as guarded:
            if guarded.state not in {RoleState.ACTIVE, RoleState.IDLE}:
                raise RoleRegistryError("role is not available for wakeup")
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if message_id is not None:
                        message = connection.execute(
                            "SELECT * FROM role_mailbox WHERE message_id = ?",
                            (message_id,),
                        ).fetchone()
                        if message is None:
                            raise MailboxConflict("wake mailbox message does not exist")
                        envelope = self._mailbox_from_row(message)
                        if (
                            envelope.recipient_role_key != role_key
                            or envelope.recipient_generation != expected_generation
                            or envelope.recipient_session_id != expected_session_id
                        ):
                            raise MailboxConflict(
                                "wake mailbox is addressed to a different role lifecycle"
                            )
                        if (
                            envelope.message_type is MailboxMessageType.CANDIDATE
                            and guarded.identity.role_kind is not RoleKind.REVIEWER
                        ):
                            raise RoleRegistryError(
                                "candidate wake requires the assigned Reviewer role"
                            )
                        if envelope.message_type is MailboxMessageType.CANDIDATE:
                            worker_role_key = envelope.body.get("worker_role_key")
                            candidate_digest = envelope.body.get("candidate_digest")
                            if (
                                envelope.task_id is None
                                or envelope.queue_generation is None
                                or not isinstance(worker_role_key, str)
                                or _ROLE_KEY.fullmatch(worker_role_key) is None
                                or not isinstance(candidate_digest, str)
                                or _DIGEST.fullmatch(candidate_digest) is None
                                or envelope.sender_role_key != worker_role_key
                            ):
                                raise MailboxConflict(
                                    "candidate wake mailbox facts are invalid"
                                )
                            task = connection.execute(
                                "SELECT queue_generation FROM hierarchy_task "
                                "WHERE task_id = ?",
                                (envelope.task_id,),
                            ).fetchone()
                            assignment = connection.execute(
                                "SELECT * FROM worker_assignment WHERE task_id = ? "
                                "AND queue_generation = ? AND worker_role_key = ?",
                                (
                                    envelope.task_id,
                                    envelope.queue_generation,
                                    worker_role_key,
                                ),
                            ).fetchone()
                            if (
                                task is None
                                or task["queue_generation"] != envelope.queue_generation
                                or assignment is None
                            ):
                                raise MailboxConflict(
                                    "candidate wake task assignment is missing"
                                )
                            self._validate_worker_assignment(assignment)
                            if (
                                assignment["reviewer_role_key"] != role_key
                                or int(assignment["reviewer_generation"])
                                != expected_generation
                                or assignment["reviewer_session_id"]
                                != expected_session_id
                                or assignment["candidate_state"] != "pending_review"
                                or assignment["candidate_digest"] != candidate_digest
                            ):
                                raise MailboxConflict(
                                    "candidate wake does not match the frozen pending pair"
                                )
                    existing = connection.execute(
                        "SELECT * FROM role_wake_outbox WHERE wake_id = ?", (wake_id,)
                    ).fetchone()
                    if existing is None:
                        pending_values = {
                            "wake_id": wake_id,
                            "role_key": role_key,
                            "role_generation": expected_generation,
                            "session_id": expected_session_id,
                            "message_id": message_id,
                            "provenance": provenance,
                            "status": "pending",
                            "runner_receipt_digest": None,
                        }
                        connection.execute(
                            "INSERT INTO role_wake_outbox(wake_id, role_key, role_generation, "
                            "session_id, message_id, provenance, status, runner_receipt_digest, "
                            "outbox_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                *pending_values.values(),
                                self._wake_outbox_digest(pending_values),
                            ),
                        )
                    else:
                        if existing["outbox_digest"] != self._wake_outbox_digest(existing):
                            raise MailboxConflict("wake outbox integrity check failed")
                        if (
                            existing["role_key"] != role_key
                            or int(existing["role_generation"]) != expected_generation
                            or existing["session_id"] != expected_session_id
                            or existing["message_id"] != message_id
                            or existing["provenance"] != provenance
                        ):
                            raise MailboxConflict("wake id was rebound")
                        if existing["status"] == "completed":
                            receipt_digest = existing["runner_receipt_digest"]
                            if not isinstance(receipt_digest, str) or _DIGEST.fullmatch(
                                receipt_digest
                            ) is None:
                                raise MailboxConflict("completed wake receipt is invalid")
                            connection.commit()
                            return receipt_digest
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

            receipt = self.session_runner.run(
                SessionAction.RESUME,
                role_key=role_key,
                role_generation=expected_generation,
                session_id=expected_session_id,
                provenance=provenance,
                reconciliation_binding=source_event_id,
            )
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT * FROM role_wake_outbox WHERE wake_id = ?",
                        (wake_id,),
                    ).fetchone()
                    if row is None:
                        raise MailboxConflict("wake outbox row disappeared")
                    if row["outbox_digest"] != self._wake_outbox_digest(row):
                        raise MailboxConflict("wake outbox integrity check failed")
                    if row["status"] == "completed":
                        if row["runner_receipt_digest"] != receipt.receipt_digest:
                            raise MailboxConflict("wake receipt was rebound")
                    elif row["status"] == "pending":
                        completed_values = {
                            **self._wake_outbox_material(row),
                            "status": "completed",
                            "runner_receipt_digest": receipt.receipt_digest,
                        }
                        connection.execute(
                            "UPDATE role_wake_outbox SET status = 'completed', "
                            "runner_receipt_digest = ?, outbox_digest = ? "
                            "WHERE wake_id = ? AND status = 'pending'",
                            (
                                receipt.receipt_digest,
                                self._wake_outbox_digest(completed_values),
                                wake_id,
                            ),
                        )
                    else:
                        raise MailboxConflict("wake outbox status is invalid")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        return receipt.receipt_digest
