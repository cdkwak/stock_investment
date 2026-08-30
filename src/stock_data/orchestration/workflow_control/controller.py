"""Durable idempotent event pump for Canonical Queue workflow operation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterable, Mapping

from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    TaskState,
    WorkflowEvent,
    utc_text,
)
from stock_data.orchestration.workflow_control.events import canonical_event_json
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot
from stock_data.orchestration.workflow_control.registry import RoleState
from stock_data.orchestration.workflow_control.routing import (
    LeadPlan,
    QueueWorkItem,
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


class WorkflowControllerError(RuntimeError):
    pass


class StaleControlGeneration(WorkflowControllerError):
    pass


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
    ) -> None:
        if not 1 <= max_recovery_attempts <= 10:
            raise WorkflowControllerError("recovery bound must be between one and ten")
        self.state_store = state_store
        self.runner = runner
        self.receipt_path = Path(receipt_path)
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_recovery_attempts = max_recovery_attempts
        self.session_runner = session_runner
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
