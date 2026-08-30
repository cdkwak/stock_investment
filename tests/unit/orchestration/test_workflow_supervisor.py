from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

import pytest

from stock_data.orchestration.workflow_control.controller import (
    ControlGeneration,
    WorkflowController,
    WorkflowControllerError,
)
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleKind,
    RoleRecord,
    RoleState,
)
from stock_data.orchestration.workflow_control.runner import (
    InjectedDirectRunner,
    LocalFakeDirectBoundary,
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
    RecoveryReason,
    RoleWatchdog,
    TerminalCondition,
    TerminalObservation,
    classify_terminal_preview,
)


UTC = timezone.utc
T0 = datetime(2026, 8, 29, tzinfo=UTC)
GENERATION = ControlGeneration(1, "a" * 64)
TASK = "RQ-20260829T173816-BC24"


class RecordingDirectBoundary(LocalFakeDirectBoundary):
    def __init__(self) -> None:
        super().__init__()
        self.actions: list[str] = []

    def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
        self.actions.append(request["action"])
        return super().execute(request)


def role_record(
    *,
    role_key: str = "project_manager",
    role_kind: RoleKind = RoleKind.PROJECT_MANAGER,
    task_id: str | None = None,
    dispatch_id: str | None = None,
    heartbeat_at: datetime = T0,
    lease_until: datetime = T0 + timedelta(minutes=30),
) -> RoleRecord:
    return RoleRecord(
        identity=RoleIdentity(
            role_key=role_key,
            role_kind=role_kind,
            codex_session_id=f"session-{role_key}",
            orca_run_id="run-existing",
            worktree_id="repo::C:/workspace",
            terminal_handle=f"term-{role_key}",
            runtime_id="runtime-a",
            active_task_id=task_id,
            active_dispatch_id=dispatch_id,
        ),
        state=RoleState.ACTIVE,
        generation=4,
        heartbeat_at=heartbeat_at,
        lease_until=lease_until,
    )


def build_supervisor(
    tmp_path: Path,
) -> tuple[
    WorkflowSupervisor,
    RecordingDirectBoundary,
    LocalFakeSessionBoundary,
]:
    direct = RecordingDirectBoundary()
    sessions = LocalFakeSessionBoundary()
    controller = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(direct),
        tmp_path / "controller.sqlite3",
        session_runner=InjectedSessionRunner(sessions),
    )
    return (
        WorkflowSupervisor(
            RoleWatchdog(
                heartbeat_timeout=timedelta(minutes=5),
                prompt_timeout=timedelta(seconds=30),
            ),
            controller,
        ),
        direct,
        sessions,
    )


def test_join_path_parameter_prompt_is_reduced_to_sanitized_input_state() -> None:
    preview = (
        "cmdlet Join-Path at command pipeline position 1\n"
        "Supply values for the following parameters:\n"
        "ChildPath:"
    )

    assert classify_terminal_preview(preview) is TerminalCondition.INPUT_REQUIRED
    assert classify_terminal_preview("Working on focused tests") is TerminalCondition.UNKNOWN


def test_taskless_pm_prompt_stall_is_interrupted_and_resumed_idempotently(
    tmp_path: Path,
) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(heartbeat_at=T0 + timedelta(seconds=50))
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=1),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-project_manager"}),
        dispatches=(),
        terminals=(
            TerminalObservation(
                terminal_handle="term-project_manager",
                connected=True,
                agent_process_live=True,
                last_output_at=T0,
                condition=TerminalCondition.INPUT_REQUIRED,
                condition_since=T0,
            ),
        ),
    )

    first = supervisor.run_cycle(
        (record,), observation, queue_states={}, generation=GENERATION
    )
    replay = supervisor.run_cycle(
        (record,), observation, queue_states={}, generation=GENERATION
    )

    assert first == replay
    assert first.recovery_actions == ("ROLE_SESSION_INTERRUPTED_AND_RESUMED",)
    assert sessions.actions == ["interrupt", "resume"]
    assert sessions.calls == 2
    assert direct.calls == 0
    assert not first.production_mutated


def test_failed_review_dispatch_retries_review_without_implementation_launch(
    tmp_path: Path,
) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(
        role_key="data_lead",
        role_kind=RoleKind.DOMAIN_LEAD,
        task_id=TASK,
        dispatch_id="dispatch-review",
        heartbeat_at=T0 + timedelta(minutes=1),
    )
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=2),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-data_lead"}),
        dispatches=(
            DispatchObservation(TASK, "dispatch-review", "failed", True),
        ),
        terminals=(
            TerminalObservation(
                "term-data_lead",
                connected=True,
                agent_process_live=True,
                last_output_at=T0 + timedelta(minutes=2),
                condition=TerminalCondition.PROGRESS,
            ),
        ),
    )

    first = supervisor.run_cycle(
        (record,), observation, queue_states={TASK: "review"}, generation=GENERATION
    )
    replay = supervisor.run_cycle(
        (record,), observation, queue_states={TASK: "review"}, generation=GENERATION
    )

    assert first == replay
    assert first.recovery_actions == (
        "REVIEW_RETRIED_WITHOUT_IMPLEMENTATION_RELAUNCH",
    )
    assert "launch" not in direct.actions
    assert direct.calls == 2
    assert sessions.calls == 0


def test_done_queue_state_settles_stale_execution_without_retry(
    tmp_path: Path,
) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(
        role_key="infra_lead",
        role_kind=RoleKind.DOMAIN_LEAD,
        task_id=TASK,
        dispatch_id="dispatch-stale",
    )
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=1),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-infra_lead"}),
        dispatches=(DispatchObservation(TASK, "dispatch-stale", "failed", False),),
    )

    receipt = supervisor.run_cycle(
        (record,), observation, queue_states={TASK: "done"}, generation=GENERATION
    )
    supervisor.run_cycle(
        (record,), observation, queue_states={TASK: "done"}, generation=GENERATION
    )

    assert receipt.recovery_actions == ("STALE_EXECUTION_RECEIPT_SETTLED",)
    assert direct.actions == ["settle", "settle"]
    assert direct.calls == 1
    assert sessions.calls == 0


def test_recent_terminal_output_prevents_false_stale_heartbeat_recovery() -> None:
    watchdog = RoleWatchdog(heartbeat_timeout=timedelta(minutes=5))
    record = role_record(
        role_key="data_lead",
        role_kind=RoleKind.DOMAIN_LEAD,
        task_id=TASK,
        dispatch_id="dispatch-active",
        heartbeat_at=T0,
    )
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=10),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-data_lead"}),
        dispatches=(DispatchObservation(TASK, "dispatch-active", "active", True),),
        terminals=(
            TerminalObservation(
                "term-data_lead",
                connected=True,
                agent_process_live=True,
                last_output_at=T0 + timedelta(minutes=9),
                condition=TerminalCondition.PROGRESS,
            ),
        ),
    )

    assert watchdog.inspect((record,), observation, queue_states={TASK: "active"}) == ()


def test_transport_outage_defers_recovery_instead_of_declaring_agents_dead(
    tmp_path: Path,
) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(
        role_key="data_lead",
        role_kind=RoleKind.DOMAIN_LEAD,
        task_id=TASK,
        dispatch_id="dispatch-active",
    )
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=10),
        runtime_id="runtime-a",
        terminal_handles=frozenset(),
        dispatches=(),
        runtime_reachable=False,
    )

    receipt = supervisor.run_cycle(
        (record,), observation, queue_states={TASK: "active"}, generation=GENERATION
    )

    assert receipt.recovery_actions == ("WAIT_FOR_DIRECT_HEALTH_PROBE",)
    assert direct.calls == sessions.calls == 0
    proposal = supervisor.watchdog.inspect(
        (record,), observation, queue_states={TASK: "active"}
    )[0]
    assert proposal.reason is RecoveryReason.TRANSPORT_UNAVAILABLE


def test_missing_queue_state_never_restarts_an_active_attempt(tmp_path: Path) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(
        role_key="data_lead",
        role_kind=RoleKind.DOMAIN_LEAD,
        task_id=TASK,
        dispatch_id="dispatch-unknown",
    )
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=1),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-data_lead"}),
        dispatches=(DispatchObservation(TASK, "dispatch-unknown", "failed", False),),
    )

    receipt = supervisor.run_cycle(
        (record,), observation, queue_states={}, generation=GENERATION
    )

    assert receipt.recovery_actions == ("WAIT_FOR_QUEUE_RECONCILIATION",)
    assert direct.calls == sessions.calls == 0


def test_session_recovery_provenance_binds_the_exact_session(tmp_path: Path) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(heartbeat_at=T0 + timedelta(seconds=50))
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=1),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-project_manager"}),
        dispatches=(),
        terminals=(
            TerminalObservation(
                "term-project_manager",
                connected=True,
                agent_process_live=True,
                condition=TerminalCondition.INPUT_REQUIRED,
                condition_since=T0,
            ),
        ),
    )
    proposal = supervisor.watchdog.inspect((record,), observation, queue_states={})[0]

    with pytest.raises(WorkflowControllerError, match="exact attempt"):
        supervisor.controller.recover(
            replace(proposal, session_id="session-other"),
            generation=GENERATION,
            connected_terminal=True,
            agent_process_live=True,
        )

    assert direct.calls == sessions.calls == 0


def test_material_question_wakes_exact_pm_once_without_orca_dependency(
    tmp_path: Path,
) -> None:
    supervisor, direct, sessions = build_supervisor(tmp_path)
    record = role_record(heartbeat_at=T0 + timedelta(seconds=50))
    durable = supervisor.controller.register_role_session(
        record.identity,
        observed_at=record.heartbeat_at,
        lease_until=record.lease_until,
    )
    record = replace(record, generation=durable.generation)
    observation = OrcaObservation(
        observed_at=T0 + timedelta(minutes=1),
        runtime_id="runtime-a",
        terminal_handles=frozenset({"term-project_manager"}),
        dispatches=(),
        terminals=(
            TerminalObservation(
                "term-project_manager",
                connected=True,
                agent_process_live=True,
                last_output_at=T0 + timedelta(seconds=55),
            ),
        ),
    )
    signal = WakeSignal(
        signal_id="question-001",
        role_key="project_manager",
        kind=WakeKind.QUESTION,
        occurred_at=T0 + timedelta(seconds=58),
    )

    first = supervisor.run_cycle(
        (record,), observation, queue_states={}, generation=GENERATION,
        wake_signals=(signal,),
    )
    replay = supervisor.run_cycle(
        (record,), observation, queue_states={}, generation=GENERATION,
        wake_signals=(signal,),
    )

    assert first.wake_signal_ids == ("question-001",)
    assert first.wakeup_runner_receipts == replay.wakeup_runner_receipts
    assert first.unhandled_wake_signal_ids == ()
    assert sessions.actions == ["resume"]
    assert sessions.calls == 1
    assert direct.calls == 0
