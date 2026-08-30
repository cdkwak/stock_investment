"""Deterministic fake-boundary cycle exercise for offline regression tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Mapping

from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    EventSource,
    Priority,
    TaskState,
    WorkflowEvent,
    utc_text,
)
from stock_data.orchestration.workflow_control.controller import (
    ControlGeneration,
    StaleControlGeneration,
    WorkflowController,
)
from stock_data.orchestration.workflow_control.digest import render_state_projection
from stock_data.orchestration.workflow_control.registry import (
    RoleClaimConflict,
    RoleIdentity,
    RoleKind,
    RoleRecord,
    RoleRegistry,
    RoleState,
)
from stock_data.orchestration.workflow_control.runner import (
    InjectedDirectRunner,
    LocalFakeDirectBoundary,
    RunnerAction,
)
from stock_data.orchestration.workflow_control.session_runner import (
    InjectedSessionRunner,
    LocalFakeSessionBoundary,
)
from stock_data.orchestration.workflow_control.state import WorkflowStateStore
from stock_data.orchestration.workflow_control.supervisor import (
    WakeKind,
    WakeSignal,
    WorkflowSupervisor,
)
from stock_data.orchestration.workflow_control.watchdog import (
    DispatchObservation,
    OrcaObservation,
    RoleWatchdog,
    TerminalObservation,
)


_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class CycleCanaryError(RuntimeError):
    pass


class CycleScenario(StrEnum):
    HAPPY_PATH = "happy_path"
    DUPLICATE_CLAIM = "duplicate_claim"
    HEARTBEAT_RENEWAL = "heartbeat_renewal"
    LEASE_EXPIRY_RECOVERY = "lease_expiry_recovery"
    STALE_GENERATION = "stale_generation"
    WORKER_CRASH_RETRY = "worker_crash_retry"
    QUESTION_WAKEUP = "question_wakeup"
    REVIEW_SNAPSHOT_ISOLATION = "review_snapshot_isolation"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    ORCA_ABSENT = "orca_absent"


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    task_id: str
    implementation_identity: str
    entries: tuple[tuple[str, str], ...]
    snapshot_digest: str

    @classmethod
    def capture(
        cls,
        *,
        task_id: str,
        implementation_identity: str,
        files: Mapping[str, bytes],
    ) -> "ReviewSnapshot":
        if _TASK_ID.fullmatch(task_id) is None:
            raise CycleCanaryError("review snapshot task id is invalid")
        if _IDENTIFIER.fullmatch(implementation_identity) is None:
            raise CycleCanaryError("implementation identity is invalid")
        entries: list[tuple[str, str]] = []
        for path, body in sorted(files.items()):
            parsed = PurePosixPath(path)
            if (
                not path
                or parsed.is_absolute()
                or ".." in parsed.parts
                or parsed.as_posix() != path
                or not isinstance(body, bytes)
            ):
                raise CycleCanaryError("review snapshot contains a non-canonical file")
            entries.append((path, hashlib.sha256(body).hexdigest()))
        if not entries:
            raise CycleCanaryError("review snapshot cannot be empty")
        material = {
            "entries": entries,
            "implementation_identity": implementation_identity,
            "task_id": task_id,
        }
        digest = _digest(material)
        return cls(task_id, implementation_identity, tuple(entries), digest)

    def matches(self, files: Mapping[str, bytes]) -> bool:
        try:
            current = ReviewSnapshot.capture(
                task_id=self.task_id,
                implementation_identity=self.implementation_identity,
                files=files,
            )
        except CycleCanaryError:
            return False
        return current.snapshot_digest == self.snapshot_digest

    def accept(self, *, reviewer_identity: str, snapshot_digest: str) -> None:
        if _IDENTIFIER.fullmatch(reviewer_identity) is None:
            raise CycleCanaryError("reviewer identity is invalid")
        if reviewer_identity == self.implementation_identity:
            raise CycleCanaryError("reviewer must be independent")
        if snapshot_digest != self.snapshot_digest:
            raise CycleCanaryError("review decision targets a stale snapshot")


@dataclass(frozen=True, slots=True)
class CycleReceipt:
    cycle_index: int
    scenario: CycleScenario
    task_id: str
    final_state: TaskState
    event_count: int
    direct_boundary_calls: int
    review_snapshot_digest: str
    projection_digest: str
    role_session_reused: bool
    scenario_verified: bool
    orca_used: bool
    production_mutated: bool
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise CycleCanaryError("cycle receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "cycle_index": self.cycle_index,
            "scenario": self.scenario.value,
            "task_id": self.task_id,
            "final_state": self.final_state.value,
            "event_count": self.event_count,
            "direct_boundary_calls": self.direct_boundary_calls,
            "review_snapshot_digest": self.review_snapshot_digest,
            "projection_digest": self.projection_digest,
            "role_session_reused": self.role_session_reused,
            "scenario_verified": self.scenario_verified,
            "orca_used": self.orca_used,
            "production_mutated": self.production_mutated,
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value


def _digest(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class OperationalCycleCanary:
    """Exercise contracts with local fakes; never use as production cutover evidence."""

    def __init__(self, root: Path, *, started_at: datetime) -> None:
        utc_text(started_at)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.started_at = started_at
        self.state = WorkflowStateStore(
            self.root / "workflow.sqlite3",
            self.root / "events.jsonl",
        )
        self.registry = RoleRegistry(self.root / "roles.sqlite3")
        self.direct_boundary = LocalFakeDirectBoundary()
        self.session_boundary = LocalFakeSessionBoundary()
        self.runner = InjectedDirectRunner(self.direct_boundary)
        self.session_runner = InjectedSessionRunner(self.session_boundary)
        self.controller = WorkflowController(
            self.state,
            self.runner,
            self.root / "controller.sqlite3",
            session_runner=self.session_runner,
            role_registry=self.registry,
        )
        self.supervisor = WorkflowSupervisor(
            RoleWatchdog(heartbeat_timeout=timedelta(minutes=5)),
            self.controller,
        )
        self._generation_sequence = 0
        self._lead_session_id = "direct-lead-session"
        self._claim_pm()

    def _claim_pm(self) -> None:
        self.registry.claim(
            RoleIdentity(
                role_key="project_manager",
                role_kind=RoleKind.PROJECT_MANAGER,
                codex_session_id="direct-pm-session",
                orca_run_id="compat-run-none",
                worktree_id="repo::local-canary",
                terminal_handle="direct-pm-terminal",
                runtime_id="direct-runtime",
            ),
            observed_at=self.started_at,
            lease_until=self.started_at + timedelta(hours=1),
        )

    def _generation(self, label: str) -> ControlGeneration:
        self._generation_sequence += 1
        return ControlGeneration(
            self._generation_sequence,
            _digest({"label": label, "sequence": self._generation_sequence}),
        )

    def _transition(
        self,
        *,
        task_id: str,
        cycle_index: int,
        step: int,
        from_state: TaskState | None,
        to_state: TaskState,
    ) -> WorkflowEvent:
        return WorkflowEvent(
            event_id=f"cycle-{cycle_index:02d}-{to_state.value}",
            occurred_at=self.started_at + timedelta(minutes=cycle_index, seconds=step),
            kind=EventKind.TASK_TRANSITION,
            source=EventSource.QUEUE,
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            priority=Priority.P1,
            domain="infra",
        )

    def _activate_lead(self, task_id: str, cycle_index: int, now: datetime):
        dispatch_id = f"direct-dispatch-{cycle_index:02d}"
        records = {item.identity.role_key: item for item in self.registry.records()}
        current = records.get("lead_infra")
        if current is None:
            return self.registry.claim(
                RoleIdentity(
                    role_key="lead_infra",
                    role_kind=RoleKind.DOMAIN_LEAD,
                    codex_session_id=self._lead_session_id,
                    orca_run_id="compat-run-none",
                    worktree_id="repo::local-canary",
                    terminal_handle="direct-lead-terminal",
                    runtime_id="direct-runtime",
                    active_task_id=task_id,
                    active_dispatch_id=dispatch_id,
                ),
                observed_at=now,
                lease_until=now + timedelta(minutes=10),
            )
        return self.registry.assign(
            "lead_infra",
            expected_generation=current.generation,
            task_id=task_id,
            dispatch_id=dispatch_id,
            observed_at=now,
            lease_until=now + timedelta(minutes=10),
        )

    def _activate_worker(self, task_id: str, cycle_index: int, now: datetime):
        """Create one execution-scoped Worker fact; only PM/Lead sessions persist."""

        return RoleRecord(
            identity=RoleIdentity(
                role_key="worker_infra",
                role_kind=RoleKind.WORKER,
                codex_session_id="direct-worker-session",
                orca_run_id="compat-run-none",
                worktree_id="repo::local-canary",
                terminal_handle="direct-worker-terminal",
                runtime_id="direct-runtime",
                active_task_id=task_id,
                active_dispatch_id=f"worker-dispatch-{cycle_index:02d}",
            ),
            state=RoleState.ACTIVE,
            generation=1,
            heartbeat_at=now,
            lease_until=now + timedelta(minutes=10),
        )

    def run(self, cycle_index: int, scenario: CycleScenario) -> CycleReceipt:
        if not 1 <= cycle_index <= 99:
            raise CycleCanaryError("cycle index must be between one and 99")
        if not isinstance(scenario, CycleScenario):
            raise CycleCanaryError("scenario must use CycleScenario")
        task_id = f"RQ-20260829T22{cycle_index:04d}-C{cycle_index:03d}"
        now = self.started_at + timedelta(minutes=cycle_index)
        calls_before = self.direct_boundary.calls
        direct_receipts_before = len(self.runner.receipts)
        session_receipts_before = len(self.session_runner.receipts)
        prior_event_count = self.state.event_count()
        pm = self.registry.get("project_manager")
        pm = self.registry.heartbeat(
            "project_manager",
            expected_generation=pm.generation,
            observed_at=now,
            lease_until=now + timedelta(hours=1),
        )
        lead = self._activate_lead(task_id, cycle_index, now)
        reused = cycle_index > 1 and lead.identity.codex_session_id == self._lead_session_id
        verified = scenario is CycleScenario.HAPPY_PATH
        crash_worker = None

        events = (
            self._transition(
                task_id=task_id, cycle_index=cycle_index, step=0,
                from_state=None, to_state=TaskState.NEW,
            ),
            self._transition(
                task_id=task_id, cycle_index=cycle_index, step=1,
                from_state=TaskState.NEW, to_state=TaskState.READY,
            ),
            self._transition(
                task_id=task_id, cycle_index=cycle_index, step=2,
                from_state=TaskState.READY, to_state=TaskState.ACTIVE,
            ),
        )
        active_receipt = None
        for event in events:
            active_receipt = self.controller.pump(
                self._generation(event.event_id), (event,),
            )
        assert active_receipt is not None

        if scenario is CycleScenario.DUPLICATE_CLAIM:
            conflict = RoleIdentity(
                role_key="lead_infra",
                role_kind=RoleKind.DOMAIN_LEAD,
                codex_session_id="different-session",
                orca_run_id="compat-run-none",
                worktree_id="repo::local-canary",
                terminal_handle="different-terminal",
                runtime_id="direct-runtime",
                active_task_id=task_id,
                active_dispatch_id=f"different-{cycle_index:02d}",
            )
            try:
                self.registry.claim(
                    conflict, observed_at=now,
                    lease_until=now + timedelta(minutes=10),
                )
            except RoleClaimConflict:
                verified = True
        elif scenario is CycleScenario.HEARTBEAT_RENEWAL:
            lead = self.registry.heartbeat(
                "lead_infra",
                expected_generation=lead.generation,
                observed_at=now + timedelta(seconds=3),
                lease_until=now + timedelta(minutes=11),
            )
            verified = lead.heartbeat_at == now + timedelta(seconds=3)
        elif scenario is CycleScenario.STALE_GENERATION:
            stale_event = self._transition(
                task_id=task_id, cycle_index=cycle_index, step=3,
                from_state=TaskState.ACTIVE, to_state=TaskState.ACTIVE,
            )
            try:
                self.controller.pump(
                    ControlGeneration(1, "0" * 64), (stale_event,),
                )
            except StaleControlGeneration:
                verified = True
        elif scenario is CycleScenario.LEASE_EXPIRY_RECOVERY:
            observed_at = lead.lease_until + timedelta(seconds=1)
            observation = OrcaObservation(
                observed_at=observed_at,
                runtime_id="direct-runtime",
                terminal_handles=frozenset({"direct-lead-terminal", "direct-pm-terminal"}),
                dispatches=(
                    DispatchObservation(
                        task_id,
                        lead.identity.active_dispatch_id or "",
                        "active",
                        True,
                    ),
                ),
                terminals=(
                    TerminalObservation(
                        "direct-lead-terminal",
                        connected=True,
                        agent_process_live=True,
                        last_output_at=now,
                    ),
                    TerminalObservation(
                        "direct-pm-terminal",
                        connected=True,
                        agent_process_live=True,
                        last_output_at=observed_at,
                    ),
                ),
            )
            cycle = self.supervisor.run_cycle(
                (lead,),
                observation,
                queue_states={task_id: "active"},
                generation=self._generation(f"recovery-{cycle_index}"),
            )
            verified = cycle.recovery_actions == ("SETTLED_AND_RETRIED_SAME_TASK",)
        elif scenario is CycleScenario.WORKER_CRASH_RETRY:
            crash_worker = self._activate_worker(task_id, cycle_index, now)
            receipts_before_crash = len(self.runner.receipts)
            observation = OrcaObservation(
                observed_at=now + timedelta(seconds=4),
                runtime_id="direct-runtime",
                terminal_handles=frozenset({
                    "direct-worker-terminal", "direct-lead-terminal", "direct-pm-terminal",
                }),
                dispatches=(
                    DispatchObservation(
                        task_id,
                        crash_worker.identity.active_dispatch_id or "",
                        "failed",
                        False,
                    ),
                ),
                terminals=(
                    TerminalObservation(
                        "direct-worker-terminal",
                        connected=True,
                        agent_process_live=False,
                        last_output_at=now,
                    ),
                ),
            )
            cycle = self.supervisor.run_cycle(
                (crash_worker,),
                observation,
                queue_states={task_id: "active"},
                generation=self._generation(f"worker-crash-{cycle_index}"),
            )
            crash_receipts = self.runner.receipts[receipts_before_crash:]
            verified = (
                cycle.recovery_actions == ("SETTLED_AND_RETRIED_SAME_TASK",)
                and tuple(item.role_key for item in crash_receipts)
                == ("worker_infra", "worker_infra")
                and tuple(item.action for item in crash_receipts)
                == (RunnerAction.SETTLE, RunnerAction.RESUME)
            )
        elif scenario is CycleScenario.QUESTION_WAKEUP:
            observation = OrcaObservation(
                observed_at=now + timedelta(seconds=4),
                runtime_id="direct-runtime",
                terminal_handles=frozenset({"direct-lead-terminal", "direct-pm-terminal"}),
                dispatches=(
                    DispatchObservation(
                        task_id,
                        lead.identity.active_dispatch_id or "",
                        "active",
                        True,
                    ),
                ),
                terminals=(
                    TerminalObservation(
                        "direct-lead-terminal", True, True,
                        last_output_at=now + timedelta(seconds=3),
                    ),
                    TerminalObservation(
                        "direct-pm-terminal", True, True,
                        last_output_at=now + timedelta(seconds=3),
                    ),
                ),
            )
            cycle = self.supervisor.run_cycle(
                (pm,),
                observation,
                queue_states={task_id: "active"},
                generation=self._generation(f"question-{cycle_index}"),
                wake_signals=(
                    WakeSignal(
                        signal_id=f"question-{cycle_index:02d}",
                        role_key="project_manager",
                        kind=WakeKind.QUESTION,
                        occurred_at=now + timedelta(seconds=3),
                    ),
                ),
            )
            verified = len(cycle.wakeup_runner_receipts) == 1
        elif scenario is CycleScenario.IDEMPOTENT_REPLAY:
            before_replay = self.direct_boundary.calls
            replay = self.controller.pump(
                ControlGeneration(
                    active_receipt.generation_sequence,
                    active_receipt.generation_digest,
                ),
                (events[-1],),
            )
            verified = replay == active_receipt and self.direct_boundary.calls == before_replay
        elif scenario is CycleScenario.ORCA_ABSENT:
            cycle = self.supervisor.run_cycle(
                (lead,),
                OrcaObservation(
                    observed_at=now + timedelta(seconds=4),
                    runtime_id="absent-runtime",
                    terminal_handles=frozenset(),
                    dispatches=(),
                    runtime_reachable=False,
                ),
                queue_states={task_id: "active"},
                generation=self._generation(f"transport-absent-{cycle_index}"),
            )
            verified = (
                cycle.recovery_actions
                and set(cycle.recovery_actions) == {"WAIT_FOR_DIRECT_HEALTH_PROBE"}
                and not cycle.recovery_runner_receipts
            )

        candidate = {f"src/cycle_{cycle_index:02d}.py": f"cycle={cycle_index}\n".encode()}
        snapshot = ReviewSnapshot.capture(
            task_id=task_id,
            implementation_identity=f"worker-{cycle_index:02d}",
            files=candidate,
        )
        if scenario is CycleScenario.REVIEW_SNAPSHOT_ISOLATION:
            changed = {
                f"src/cycle_{cycle_index:02d}.py": f"cycle={cycle_index}-next\n".encode()
            }
            verified = snapshot.matches(candidate) and not snapshot.matches(changed)

        review_event = self._transition(
            task_id=task_id, cycle_index=cycle_index, step=4,
            from_state=TaskState.ACTIVE, to_state=TaskState.REVIEW,
        )
        self.controller.pump(self._generation(review_event.event_id), (review_event,))
        reviewer = f"reviewer-{cycle_index:02d}"
        review_launch = self.runner.run(
            RunnerAction.LAUNCH,
            task_id=task_id,
            role_key=reviewer,
            generation=snapshot.snapshot_digest,
            source_event_id=f"review-start-{cycle_index:02d}",
        )
        snapshot.accept(
            reviewer_identity=reviewer,
            snapshot_digest=snapshot.snapshot_digest,
        )
        self.runner.run(
            RunnerAction.SETTLE,
            task_id=task_id,
            role_key=reviewer,
            generation=snapshot.snapshot_digest,
            source_event_id=f"review-pass-{cycle_index:02d}",
        )
        if review_launch.orca_used or review_launch.production_mutated:
            raise CycleCanaryError("review boundary escaped the offline canary")

        done_event = self._transition(
            task_id=task_id, cycle_index=cycle_index, step=5,
            from_state=TaskState.REVIEW, to_state=TaskState.DONE,
        )
        self.controller.pump(self._generation(done_event.event_id), (done_event,))
        lead = self.registry.get("lead_infra")
        idle = self.registry.settle(
            "lead_infra",
            expected_generation=lead.generation,
            observed_at=now + timedelta(seconds=6),
            lease_until=now + timedelta(minutes=30),
        )
        if idle.state is not RoleState.IDLE:
            raise CycleCanaryError("lead session did not return to the reusable idle pool")
        snapshots = self.state.task_snapshots()
        current = next(item for item in snapshots if item.task_id == task_id)
        projection = render_state_projection(
            snapshots,
            as_of=now + timedelta(seconds=6),
        )
        projection_path = self.root / "WORKFLOW_STATE.md"
        temporary_path = projection_path.with_suffix(".md.tmp")
        temporary_path.write_text(projection, encoding="utf-8", newline="\n")
        os.replace(temporary_path, projection_path)
        boundary_receipts = (
            self.runner.receipts[direct_receipts_before:]
            + self.session_runner.receipts[session_receipts_before:]
        )
        receipt = CycleReceipt(
            cycle_index=cycle_index,
            scenario=scenario,
            task_id=task_id,
            final_state=current.state,
            event_count=self.state.event_count() - prior_event_count,
            direct_boundary_calls=self.direct_boundary.calls - calls_before,
            review_snapshot_digest=snapshot.snapshot_digest,
            projection_digest=hashlib.sha256(projection.encode("utf-8")).hexdigest(),
            role_session_reused=reused,
            scenario_verified=verified,
            orca_used=any(item.orca_used for item in boundary_receipts),
            production_mutated=any(item.production_mutated for item in boundary_receipts),
        )
        if (
            receipt.final_state is not TaskState.DONE
            or receipt.event_count != 5
            or not receipt.scenario_verified
            or receipt.orca_used
            or receipt.production_mutated
        ):
            raise CycleCanaryError(f"cycle {cycle_index} did not satisfy its contract")
        return receipt
