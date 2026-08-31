from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from stock_data.orchestration.workflow_control.contracts import (
    EventKind, EventSource, Priority, TaskState, WorkflowEvent,
)
from stock_data.orchestration.workflow_control.controller import WorkflowController
from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryProcessError,
    CodexBoundaryUncertainOperationError,
    CodexCliBoundary,
)
from stock_data.orchestration.workflow_control.runner import (
    ExecutionMetadata,
    InjectedDirectRunner,
    LocalFakeDirectBoundary,
    RunnerAction,
)
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity, RoleKind, StaleRoleGeneration,
)
from stock_data.orchestration.workflow_control.listener_gateway import (
    ListenerGateway,
    ListenerIntent,
    ListenerRoute,
    ListenerSinks,
    RouteKind,
)
from stock_data.orchestration.workflow_control.session_runner import (
    InjectedSessionRunner,
    LocalFakeSessionBoundary,
    SessionAction,
)
from stock_data.orchestration.workflow_control.service import (
    ControllerServiceError, OperationActivity, WorkflowControllerService, WriterLeaseConflict,
)
import stock_data.orchestration.workflow_control.service as service_module
import stock_data.orchestration.workflow_control.event_runner as event_runner_module
from stock_data.orchestration.workflow_control.state import WorkflowStateStore


T0 = datetime(2026, 8, 30, tzinfo=UTC)
TASK = "RQ-20260830T010101-AB12"


def event() -> WorkflowEvent:
    return WorkflowEvent(
        event_id="pm-service-active", occurred_at=T0, kind=EventKind.TASK_TRANSITION,
        source=EventSource.SYSTEM, task_id=TASK, from_state=TaskState.READY,
        to_state=TaskState.ACTIVE, priority=Priority.P1, domain="infra",
        reason_code="SERVICE_CANARY",
    )


def service(root: Path, owner: str, fake: LocalFakeDirectBoundary | None = None) -> tuple[WorkflowControllerService, LocalFakeDirectBoundary]:
    boundary = fake or LocalFakeDirectBoundary()
    controller = WorkflowController(
        WorkflowStateStore(root / "state.sqlite3", root / "events.jsonl"),
        InjectedDirectRunner(boundary), root / "controller.sqlite3",
    )
    return WorkflowControllerService(controller, root / "service", owner_id=owner), boundary


def test_writer_single_live_generation_rejects_concurrent_controller(tmp_path: Path) -> None:
    first, _ = service(tmp_path, "pm-a")
    second, _ = service(tmp_path, "pm-b")
    first.start()
    with pytest.raises(WriterLeaseConflict):
        second.start()
    status = WorkflowControllerService.inspect(tmp_path / "service")
    assert status.active and status.owner_id == "pm-a" and status.generation_sequence == 1
    first.close()
    assert second.start().sequence == 2


def test_pm_fenced_app_coordination_lead_replacement_preserves_queue_assignment(
    tmp_path: Path,
) -> None:
    instance, _ = service(tmp_path, "project_manager")
    instance.start()
    try:
        pm = instance.register_role_session(
            RoleIdentity(
                "project_manager", RoleKind.PROJECT_MANAGER, "pm-session",
                "transport-disabled", "stock-investment-rev1-main", None,
                "codex-cli-owned-v1",
            ), observed_at=T0, lease_until=T0 + timedelta(hours=1),
        )
        lead = instance.register_role_session(
            RoleIdentity(
                "lead_infra", RoleKind.DOMAIN_LEAD, "app-session-old",
                "transport-disabled", "stock-investment-rev1-main", None,
                "codex-app-local", active_task_id=TASK,
                active_dispatch_id="dispatch-a", parent_role_key="project_manager",
            ), observed_at=T0, lease_until=T0 + timedelta(hours=1),
        )
        replacement = instance.replace_app_coordination_lead_session(
            pm_role_key="project_manager", expected_pm_generation=pm.generation,
            role_key="lead_infra", expected_generation=lead.generation,
            expected_session_id="app-session-old", replacement_session_id="app-session-new",
            expected_task_id=TASK, expected_dispatch_id="dispatch-a",
            expected_runtime_id="codex-app-local",
            expected_worktree_id="stock-investment-rev1-main",
        )
        assert replacement.generation == lead.generation + 1
        assert replacement.identity.active_task_id == TASK
        assert replacement.identity.active_dispatch_id == "dispatch-a"
        assert replacement.retry_attempt == lead.retry_attempt
        for task_id, dispatch_id in (
            ("RQ-20260830T010101-ZZ99", "dispatch-a"),
            (TASK, "dispatch-other"),
        ):
            with pytest.raises(StaleRoleGeneration, match="replacement identity changed"):
                instance.replace_app_coordination_lead_session(
                    pm_role_key="project_manager", expected_pm_generation=pm.generation,
                    role_key="lead_infra", expected_generation=replacement.generation,
                    expected_session_id="app-session-new", replacement_session_id="app-session-next",
                    expected_task_id=task_id, expected_dispatch_id=dispatch_id,
                    expected_runtime_id="codex-app-local",
                    expected_worktree_id="stock-investment-rev1-main",
                )
            assert instance.controller.role_registry.get("lead_infra") == replacement
        rogue = instance.register_role_session(
            RoleIdentity(
                "lead_rogue", RoleKind.DOMAIN_LEAD, "app-session-rogue",
                "transport-disabled", "stock-investment-rev1-main", None,
                "codex-app-local", active_task_id=TASK,
                active_dispatch_id="dispatch-rogue", parent_role_key="project_manager",
            ), observed_at=T0, lease_until=T0 + timedelta(hours=1),
        )
        with sqlite3.connect(instance.controller.role_registry.path) as connection:
            connection.execute(
                "UPDATE role_registry SET parent_role_key = 'other_pm' WHERE role_key = 'lead_rogue'"
            )
        with pytest.raises(ControllerServiceError, match="fenced PM role"):
            instance.replace_app_coordination_lead_session(
                pm_role_key="project_manager", expected_pm_generation=pm.generation,
                role_key="lead_rogue", expected_generation=rogue.generation,
                expected_session_id="app-session-rogue", replacement_session_id="app-session-rogue-new",
                expected_task_id=TASK, expected_dispatch_id="dispatch-rogue",
                expected_runtime_id="codex-app-local",
                expected_worktree_id="stock-investment-rev1-main",
            )
        with pytest.raises(ControllerServiceError, match="PM role generation changed"):
            instance.replace_app_coordination_lead_session(
                pm_role_key="project_manager", expected_pm_generation=pm.generation + 1,
                role_key="lead_infra", expected_generation=replacement.generation,
                expected_session_id="app-session-new", replacement_session_id="app-session-next",
                expected_task_id=TASK, expected_dispatch_id="dispatch-a",
                expected_runtime_id="codex-app-local",
                expected_worktree_id="stock-investment-rev1-main",
            )
        with sqlite3.connect(instance.controller.role_registry.path) as connection:
            connection.execute(
                "UPDATE role_registry SET state = 'idle' WHERE role_key = 'project_manager'"
            )
        with pytest.raises(ControllerServiceError, match="PM role generation changed"):
            instance.replace_app_coordination_lead_session(
                pm_role_key="project_manager", expected_pm_generation=pm.generation,
                role_key="lead_infra", expected_generation=replacement.generation,
                expected_session_id="app-session-new", replacement_session_id="app-session-next",
                expected_task_id=TASK, expected_dispatch_id="dispatch-a",
                expected_runtime_id="codex-app-local",
                expected_worktree_id="stock-investment-rev1-main",
            )
    finally:
        instance.close()


def test_event_reconciliation_composition_uses_exact_material_binding_read_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    (repository / "src" / "stock_data").mkdir(parents=True)
    (repository / "AGENTS.md").write_text("test\n", encoding="utf-8")
    control_root = repository / "data" / "runtime" / "python_pm"
    boundary = LocalFakeDirectBoundary()
    controller = WorkflowController(
        WorkflowStateStore(
            control_root / "workflow_state.sqlite3",
            control_root / "workflow_events.jsonl",
        ),
        InjectedDirectRunner(boundary),
        control_root / "workflow_controller.sqlite3",
    )
    instance = WorkflowControllerService(
        controller, control_root, owner_id="event-owner"
    )
    generation = "a" * 64
    attempt = "b" * 64
    service_generation = "c" * 64
    request_digest = "d" * 64
    profile_digest = "e" * 64
    process_digest = "f" * 64
    preflight_digest = "1" * 64
    instance.start()
    instance.close()

    class FakeRunner:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def reconciliation_status(self, **pins: object) -> SimpleNamespace:
            assert pins == {
                "material_generation": generation,
                "expected_attempt_receipt_digest": attempt,
            }
            return SimpleNamespace(state="pending_failed")

    bindings: list[str] = []
    monkeypatch.setattr(event_runner_module, "WorkflowEventRunner", FakeRunner)
    monkeypatch.setattr(
        WorkflowControllerService, "inspect", classmethod(
            lambda _cls, _root: SimpleNamespace(
                active=False, writer_state="idle", pending_boundary_operations=0,
            )
        ),
    )
    monkeypatch.setattr(
        CodexCliBoundary, "lookup_terminal_operation_mapping", staticmethod(
            lambda _path, *, reconciliation_binding: (
                bindings.append(reconciliation_binding)
                or SimpleNamespace(
                    operation_id="session-op-" + ("2" * 64),
                    request_digest=request_digest, error_code="process_failed",
                    execution_profile_digest=profile_digest,
                    process_event_receipt_digest=process_digest,
                )
            )
        ),
    )
    monkeypatch.setattr(
        WorkflowControllerService, "preflight_terminal_reconciliation", staticmethod(
            lambda _root, **pins: SimpleNamespace(
                generation_sequence=pins["generation_sequence"],
                generation_digest=pins["generation_digest"],
                preflight_digest=preflight_digest,
            )
        ),
    )
    before = (control_root / "workflow_controller_service.sqlite3").read_bytes()
    receipt = WorkflowControllerService.event_reconciliation_status(
        repository, material_generation=generation, attempt_receipt_digest=attempt,
    )
    assert bindings == [generation]
    assert receipt.material_generation == generation
    assert receipt.attempt_receipt_digest == attempt
    assert (control_root / "workflow_controller_service.sqlite3").read_bytes() == before


def test_writer_atomic_race_allows_one_generation(tmp_path: Path) -> None:
    left, _ = service(tmp_path, "pm-left")
    right, _ = service(tmp_path, "pm-right")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda item: _start(item), (left, right)))
    assert results.count("started") == 1
    assert results.count("conflict") == 1


def test_crash_stale_writer_row_is_recovered_under_os_mutex_with_new_generation(tmp_path: Path) -> None:
    crashed, _ = service(tmp_path, "pm-crashed")
    original = crashed.start()
    assert crashed._mutex is not None
    crashed._mutex.release()  # Simulate process exit: DB row remains, OS lock does not.
    crashed._mutex = None
    recovered, _ = service(tmp_path, "pm-restarted")
    generation = recovered.start()
    assert generation.sequence == original.sequence + 1
    assert WorkflowControllerService.inspect(tmp_path / "service").writer_state == "live"
    active = recovered.activities(active_only=True)
    assert [(item.role_kind, item.generation_sequence) for item in active] == [("project_manager", generation.sequence)]
    previous = [item for item in recovered.activities() if item.generation_sequence == original.sequence]
    assert previous and all(item.state == "stopped" and not item.active for item in previous)
    recovered.close()


def test_stale_rollback_refuses_live_writer_and_never_calls_a_boundary(tmp_path: Path) -> None:
    live, boundary = service(tmp_path, "pm-live")
    generation = live.start()
    with pytest.raises(WriterLeaseConflict, match="live"):
        WorkflowControllerService.rollback_stale(
            tmp_path / "service", owner_id="pm-live",
            generation_sequence=generation.sequence, generation_digest=generation.digest,
        )
    assert boundary.calls == 0
    live.close()


def test_stale_rollback_clears_only_exact_observed_generation_without_new_work(tmp_path: Path) -> None:
    crashed, boundary = service(tmp_path, "pm-crashed")
    generation = crashed.start()
    crashed.canary((event(),))
    assert crashed._mutex is not None
    crashed._mutex.release()
    crashed._mutex = None
    status = WorkflowControllerService.rollback_stale(
        tmp_path / "service", owner_id="pm-crashed",
        generation_sequence=generation.sequence, generation_digest=generation.digest,
    )
    assert status.writer_state == "idle"
    assert boundary.calls == 1  # rollback itself never invokes the boundary
    assert not crashed.activities(active_only=True)
    assert all(item.state == "stopped" for item in crashed.activities())
    with sqlite3.connect(tmp_path / "service" / "workflow_controller_service.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 1
        assert connection.execute("SELECT release_reason FROM generation_history").fetchone()[0] == "rollback"
        assert connection.execute("SELECT COUNT(*) FROM operation_activity_receipt").fetchone()[0] >= 2


def test_exact_stranded_recovery_blocks_live_process_then_fences_uncertain_operation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    live, _ = service(tmp_path, "windows-task-scheduler")
    generation = live.start()

    class InterruptedFactory:
        def __call__(self, argv: list[str], **kwargs: object) -> object:
            del argv, kwargs
            raise KeyboardInterrupt("simulated host interruption")

    boundary = CodexCliBoundary(
        root / "codex_boundary.sqlite3",
        cwd=tmp_path,
        sandbox_mode="workspace-write",
        process_factory=InterruptedFactory(),  # type: ignore[arg-type]
    )
    runner = InjectedDirectRunner(boundary)
    with pytest.raises(KeyboardInterrupt):
        runner.run(
            RunnerAction.LAUNCH,
            task_id=TASK,
            role_key="lead_infra",
            generation="a" * 64,
            source_event_id="pending-boundary",
        )
    pending = CodexCliBoundary.inspect(root / "codex_boundary.sqlite3")
    assert len(pending.pending_operation_pins) == 1
    pin = pending.pending_operation_pins[0]
    exact = {
        "owner_id": "windows-task-scheduler",
        "generation_sequence": generation.sequence,
        "generation_digest": generation.digest,
        "boundary_operation_id": pin.operation_id,
        "boundary_request_digest": pin.request_digest,
    }

    blocked = WorkflowControllerService.preflight_stranded_recovery(root, **exact)
    assert blocked.process_live is True and blocked.ready is False
    with pytest.raises(ControllerServiceError, match="pin changed"):
        WorkflowControllerService.preflight_stranded_recovery(
            root,
            **(exact | {"boundary_request_digest": "d" * 64}),
        )
    with pytest.raises(WriterLeaseConflict, match="process is live"):
        WorkflowControllerService.recover_stranded(root, **exact)
    assert CodexCliBoundary.inspect(root / "codex_boundary.sqlite3").pending_operations == 1

    assert live._mutex is not None
    live._mutex.release()  # Simulate natural process exit; production recovery never does this.
    live._mutex = None
    ready = WorkflowControllerService.preflight_stranded_recovery(root, **exact)
    assert ready.ready is True and ready.process_live is False
    receipt = WorkflowControllerService.recover_stranded(root, **exact)
    assert WorkflowControllerService.recover_stranded(root, **exact) == receipt
    assert WorkflowControllerService.assert_stranded_recovery(
        root, recovery_proof=receipt.recovery_proof
    ) == receipt
    assert WorkflowControllerService.assert_event_recovery_proof(
        root, recovery_proof=receipt.recovery_proof
    ) == receipt
    status = WorkflowControllerService.inspect(root)
    assert status.writer_state == "idle"
    assert status.pending_boundary_operations == 0
    assert status.failed_boundary_operations == 1
    with pytest.raises(CodexBoundaryUncertainOperationError, match="fresh generation"):
        runner.run(
            RunnerAction.LAUNCH,
            task_id=TASK,
            role_key="lead_infra",
            generation="a" * 64,
            source_event_id="pending-boundary",
        )


def test_exact_terminal_reconciliation_preserves_natural_failure_and_is_idempotent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    live, _ = service(tmp_path, "windows-task-scheduler")
    generation = live.start()

    class Process:
        def __init__(self, stdout: bytes, returncode: int) -> None:
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(b"sensitive process detail")
            self.returncode = returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return self.returncode

        def poll(self) -> int:
            return self.returncode

        def kill(self) -> None:
            raise AssertionError("terminal process must not be killed")

    session_id = "cli-owned-session"
    successful_output = b"".join(
        json.dumps(item, separators=(",", ":")).encode("utf-8") + b"\n"
        for item in (
            {"type": "thread.started", "thread_id": session_id},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ready"}},
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
        )
    )
    processes = [Process(successful_output, 0), Process(b"", 7)]
    boundary = CodexCliBoundary(
        root / "codex_boundary.sqlite3",
        cwd=tmp_path,
        sandbox_mode="workspace-write",
        process_factory=lambda *_args, **_kwargs: processes.pop(0),  # type: ignore[arg-type]
    )
    InjectedDirectRunner(boundary).run(
        RunnerAction.LAUNCH,
        task_id=TASK,
        role_key="project_manager",
        generation="c" * 64,
        source_event_id="runtime_bootstrap_v1",
    )
    captured: dict[str, str] = {}

    class CaptureBoundary:
        execution_metadata = boundary.execution_metadata

        def execute(self, request: object) -> object:
            captured.update(request)  # type: ignore[arg-type]
            return boundary.execute(request)  # type: ignore[arg-type]

    with pytest.raises(CodexBoundaryProcessError):
        InjectedSessionRunner(CaptureBoundary()).run(  # type: ignore[arg-type]
            SessionAction.RESUME,
            role_key="project_manager",
            role_generation=2,
            session_id=session_id,
            provenance="e" * 64,
        )
    terminal = CodexCliBoundary.inspect_terminal_operation(
        root / "codex_boundary.sqlite3",
        operation_id=captured["operation_id"],
    )
    assert terminal.state == "failed"
    assert terminal.error_code == "process_failed"
    assert terminal.request_kind == "session"
    exact = {
        "owner_id": "windows-task-scheduler",
        "generation_sequence": generation.sequence,
        "generation_digest": generation.digest,
        "boundary_operation_id": terminal.operation_id,
        "boundary_request_digest": terminal.request_digest,
        "boundary_error_code": terminal.error_code,
        "release_reason": "stopped",
    }
    with pytest.raises(WriterLeaseConflict, match="process is live"):
        WorkflowControllerService.preflight_terminal_reconciliation(root, **exact)

    live.close()  # Natural one-shot release; recovery does not release or rewrite it.
    ready = WorkflowControllerService.preflight_terminal_reconciliation(root, **exact)
    assert ready.ready is True and ready.process_live is False
    assert ready.execution_profile_digest == boundary.execution_metadata.profile_digest

    for changed in (
        {"generation_digest": "a" * 64},
        {"boundary_request_digest": "b" * 64},
        {"boundary_error_code": "process_timeout"},
        {"release_reason": "rollback"},
    ):
        with pytest.raises(ControllerServiceError, match="pin changed"):
            WorkflowControllerService.preflight_terminal_reconciliation(
                root, **(exact | changed),
            )
    with sqlite3.connect(root / "workflow_controller_service.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name = 'terminal_reconciliation_receipt'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM terminal_reconciliation_receipt"
        ).fetchone()[0] == 0

    receipt = WorkflowControllerService.reconcile_terminal(root, **exact)
    assert WorkflowControllerService.reconcile_terminal(root, **exact) == receipt
    assert WorkflowControllerService.assert_terminal_reconciliation(
        root, reconciliation_proof=receipt.reconciliation_proof,
    ) == receipt
    assert WorkflowControllerService.assert_event_recovery_proof(
        root, recovery_proof=receipt.reconciliation_proof,
    ) == receipt
    with pytest.raises(ControllerServiceError, match="replay pins changed"):
        WorkflowControllerService.reconcile_terminal(
            root, **(exact | {"boundary_error_code": "process_timeout"}),
        )
    status = WorkflowControllerService.inspect(root)
    assert status.writer_state == "idle"
    assert status.pending_boundary_operations == 0
    assert status.failed_boundary_operations == 1


def _start(candidate: WorkflowControllerService) -> str:
    try:
        candidate.start()
        return "started"
    except WriterLeaseConflict:
        return "conflict"


def test_restart_recovery_preserves_receipt_and_never_relaunches(tmp_path: Path) -> None:
    first, boundary = service(tmp_path, "pm-a")
    first.start()
    original = first.canary((event(),))
    assert boundary.calls == 1
    first.close()

    restarted, _ = service(tmp_path, "pm-b", boundary)
    replacement = restarted.start()
    replay = restarted.canary((event(),))
    assert replay == original
    assert replay.controller_receipt.accepted_event_ids == ("pm-service-active",)
    assert boundary.calls == 1
    assert WorkflowControllerService.inspect(tmp_path / "service").completed_operations == 1
    pm = next(item for item in restarted.activities(active_only=True) if item.role_kind == "project_manager")
    assert pm.state == "idle" and pm.generation_sequence == replacement.sequence


def test_restart_recovers_a_durable_pending_operation_with_its_original_generation(tmp_path: Path) -> None:
    class FailingBoundary:
        execution_metadata = LocalFakeDirectBoundary().execution_metadata

        def execute(self, request: object) -> object:
            del request
            raise RuntimeError("simulated boundary interruption")

    interrupted, _ = service(tmp_path, "pm-a", FailingBoundary())
    interrupted.start()
    with pytest.raises(RuntimeError, match="interruption"):
        interrupted.canary((event(),))
    interrupted.close()
    recovered_boundary = LocalFakeDirectBoundary()
    recovered, _ = service(tmp_path, "pm-b", recovered_boundary)
    recovered.start()
    receipt = recovered.canary((event(),))
    assert receipt.generation_sequence == 1
    assert recovered_boundary.calls == 1


def test_execution_requires_writer_and_nonempty_durable_input(tmp_path: Path) -> None:
    instance, _ = service(tmp_path, "pm-a")
    with pytest.raises(ControllerServiceError, match="started"):
        instance.run((event(),))
    instance.start()
    with pytest.raises(ControllerServiceError, match="at least one"):
        instance.canary(())


def test_activity_projection_keeps_sanitized_identity_and_immutable_receipt(tmp_path: Path) -> None:
    instance, _ = service(tmp_path, "pm-a")
    generation = instance.start()
    session = hashlib.sha256(b"session-private").hexdigest()
    working = OperationActivity("op-service", "worker", session, TASK, "working", T0, True,
                                generation_sequence=generation.sequence, generation_digest=generation.digest)
    idle = OperationActivity("op-service", "worker", session, TASK, "idle", T0 + timedelta(minutes=1), False,
                             generation_sequence=generation.sequence, generation_digest=generation.digest)
    assert instance.report_activity(working) == working
    assert instance.report_activity(idle) == idle
    assert not any(item.role_kind == "worker" for item in instance.activities(active_only=True))
    assert next(item for item in instance.activities() if item.operation_id == "op-service") == idle
    with pytest.raises(ControllerServiceError, match="rebound"):
        instance.report_activity(OperationActivity(
            "op-service", "reviewer", session, TASK, "working", T0 + timedelta(minutes=2), True,
        ))


def test_service_lifecycle_automatically_projects_pm_and_exact_lead_task(tmp_path: Path) -> None:
    instance, _ = service(tmp_path, "pm-a")
    instance.start()
    started = {item.role_kind: item for item in instance.activities(active_only=True)}
    assert started["project_manager"].state == "working"
    assert started["project_manager"].task_id is None

    instance.canary((event(),))
    active = {item.role_kind: item for item in instance.activities(active_only=True)}
    assert active["project_manager"].state == "idle"
    assert active["domain_lead"].state == "working"
    assert active["domain_lead"].task_id == TASK

    review = WorkflowEvent(
        "pm-service-review", T0 + timedelta(seconds=1), EventKind.TASK_TRANSITION,
        EventSource.SYSTEM, task_id=TASK, from_state=TaskState.ACTIVE,
        to_state=TaskState.REVIEW, priority=Priority.P1, domain="infra",
        reason_code="SERVICE_SETTLE",
    )
    instance.canary((review,))
    all_activity = {item.role_kind: item for item in instance.activities()}
    assert all_activity["domain_lead"].state == "stopped"
    assert all_activity["domain_lead"].active is False

    instance.close()
    final = {item.role_kind: item for item in instance.activities()}
    assert final["project_manager"].state == "stopped"
    assert final["project_manager"].active is False
    with sqlite3.connect(tmp_path / "service" / "workflow_controller_service.sqlite3") as connection:
        persisted = "\n".join(str(row[0]) for row in connection.execute(
            "SELECT payload FROM operation_activity_receipt"
        ))
    assert "pm-service-active" not in persisted
    assert "session-private" not in persisted


def test_phase_boundary_queue_evidence_is_verified_in_the_public_service_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "RQ-20260831T080429-5516"
    queue_generation = "a" * 64
    candidate_digest = "b" * 64
    review_digest = "c" * 64
    handoff = (
        tmp_path / "artifacts" / "request_queue" / "active"
        / f"P1-{task_id}-phase-boundary" / "HANDOFF.md"
    )
    handoff.parent.mkdir(parents=True)
    handoff.write_text(
        "phase: phase_a_pass\n"
        f"summary: candidate {candidate_digest} reviewed PASS {review_digest}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        service_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                f"state=active task=P1-{task_id}-phase-boundary "
                f"generation={queue_generation} phase=phase_a_pass next=bounded\n"
            ),
        ),
    )
    service_module.verify_phase_a_queue_evidence(
        tmp_path,
        task_id=task_id,
        expected_queue_generation=queue_generation,
        expected_candidate_digest=candidate_digest,
        expected_review_digest=review_digest,
    )
    with pytest.raises(ControllerServiceError, match="Phase-A evidence"):
        service_module.verify_phase_a_queue_evidence(
            tmp_path,
            task_id=task_id,
            expected_queue_generation=queue_generation,
            expected_candidate_digest="d" * 64,
            expected_review_digest=review_digest,
        )
    handoff.write_text(
        "phase: phase_a_pass\n"
        f"summary: candidate {candidate_digest}0 reviewed PASS {review_digest}\n",
        encoding="utf-8",
    )
    with pytest.raises(ControllerServiceError, match="Phase-A evidence"):
        service_module.verify_phase_a_queue_evidence(
            tmp_path,
            task_id=task_id,
            expected_queue_generation=queue_generation,
            expected_candidate_digest=candidate_digest,
            expected_review_digest=review_digest,
        )
    handoff.write_text(
        "phase: phase_a_pass\n"
        f"summary: candidate {candidate_digest} reviewed PASS {review_digest}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        service_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                f"state=active task=P1-{task_id}-phase-boundary "
                f"generation={queue_generation}0 phase=phase_a_pass next=bounded\n"
            ),
        ),
    )
    with pytest.raises(ControllerServiceError, match="phase-boundary status"):
        service_module.verify_phase_a_queue_evidence(
            tmp_path,
            task_id=task_id,
            expected_queue_generation=queue_generation,
            expected_candidate_digest=candidate_digest,
            expected_review_digest=review_digest,
        )
    monkeypatch.setattr(
        service_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                f"state=active task=P1-{task_id}-phase-boundary-tampered "
                f"generation={queue_generation} phase=phase_a_pass next=bounded\n"
            ),
        ),
    )
    with pytest.raises(ControllerServiceError, match="phase-boundary status"):
        service_module.verify_phase_a_queue_evidence(
            tmp_path,
            task_id=task_id,
            expected_queue_generation=queue_generation,
            expected_candidate_digest=candidate_digest,
            expected_review_digest=review_digest,
        )


def test_public_phase_boundary_operations_check_queue_evidence_before_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _ = service(tmp_path, "pm-a")
    observed: list[dict[str, object]] = []

    def reject_evidence(*_args: object, **kwargs: object) -> None:
        observed.append(dict(kwargs))
        raise ControllerServiceError("Queue evidence sentinel")

    monkeypatch.setattr(service_module, "verify_phase_a_queue_evidence", reject_evidence)
    with pytest.raises(ControllerServiceError, match="sentinel"):
        WorkflowControllerService.preflight_task_replan_ready_at(
            repository_root=tmp_path,
            receipt_path=tmp_path / "absent.sqlite3",
            task_id="RQ-20260831T080429-5516",
            expected_queue_generation="a" * 64,
            expected_prior_contract_digest="b" * 64,
            expected_phase_a_candidate_digest="c" * 64,
            expected_phase_a_review_digest="d" * 64,
            expected_prior_state="assigned",
            reason_code="phase_a_pass_requires_phase_b_contract",
            pm_role_key="project_manager",
            pm_generation=1,
        )
    with pytest.raises(ControllerServiceError, match="sentinel"):
        instance.mark_task_replan_ready(
            repository_root=tmp_path,
            task_id="RQ-20260831T080429-5516",
            expected_queue_generation="a" * 64,
            expected_prior_contract_digest="b" * 64,
            expected_phase_a_candidate_digest="c" * 64,
            expected_phase_a_review_digest="d" * 64,
            expected_prior_state="assigned",
            reason_code="phase_a_pass_requires_phase_b_contract",
            pm_role_key="project_manager",
            pm_generation=1,
        )
    expected = {
        "task_id": "RQ-20260831T080429-5516",
        "expected_queue_generation": "a" * 64,
        "expected_candidate_digest": "c" * 64,
        "expected_review_digest": "d" * 64,
    }
    assert observed == [expected, expected]


def test_lead_activity_is_visible_while_the_boundary_is_running(tmp_path: Path) -> None:
    class InspectingBoundary(LocalFakeDirectBoundary):
        def __init__(self) -> None:
            super().__init__()
            self.observe = lambda: None

        def execute(self, request: object) -> object:
            self.observe()
            return super().execute(request)  # type: ignore[arg-type]

    boundary = InspectingBoundary()
    instance, _ = service(tmp_path, "pm-visible", boundary)
    instance.start()
    observed: list[tuple[str, str | None, str, bool]] = []
    boundary.observe = lambda: observed.extend(
        (item.role_kind, item.task_id, item.state, item.active)
        for item in instance.activities(active_only=True)
    )

    instance.canary((event(),))

    assert ("domain_lead", TASK, "working", True) in observed
    instance.close()


def test_service_exception_is_visible_as_stalled_then_finally_stopped(tmp_path: Path) -> None:
    class FailingBoundary:
        execution_metadata = LocalFakeDirectBoundary().execution_metadata

        def execute(self, request: object) -> object:
            del request
            raise RuntimeError("simulated boundary interruption")

    instance, _ = service(tmp_path, "pm-a", FailingBoundary())
    instance.start()
    with pytest.raises(RuntimeError, match="interruption"):
        instance.canary((event(),))
    pm = next(item for item in instance.activities(active_only=True) if item.role_kind == "project_manager")
    assert pm.state == "stalled" and pm.active is True
    instance.close()
    pm = next(item for item in instance.activities() if item.role_kind == "project_manager")
    assert pm.state == "stopped" and pm.active is False


def test_activity_is_restart_visible_and_refuses_unhashed_session_identity(tmp_path: Path) -> None:
    first, _ = service(tmp_path, "pm-a")
    generation = first.start()
    session = hashlib.sha256(b"session-private").hexdigest()
    activity = OperationActivity("op-restart", "reviewer", session, TASK, "reviewing", T0, True,
                                 generation_sequence=generation.sequence, generation_digest=generation.digest)
    first.report_activity(activity)
    restarted, _ = service(tmp_path, "pm-b")
    assert activity in restarted.activities(active_only=True)
    with pytest.raises(ControllerServiceError, match="SHA-256"):
        OperationActivity("op-raw", "worker", "raw-session-id", TASK, "working", T0, True)
    with pytest.raises(ControllerServiceError, match="boolean"):
        OperationActivity("op-bool", "worker", session, TASK, "working", T0, 1)  # type: ignore[arg-type]
    with pytest.raises(ControllerServiceError, match="timezone-aware"):
        OperationActivity("op-naive", "worker", session, TASK, "working", datetime(2026, 8, 30), True)


def test_inspect_does_not_mutate_the_service_database(tmp_path: Path) -> None:
    instance, _ = service(tmp_path, "pm-a")
    instance.start()
    instance.close()
    database = tmp_path / "service" / "workflow_controller_service.sqlite3"
    before = database.read_bytes()
    assert WorkflowControllerService.inspect(tmp_path / "service").writer_state == "idle"
    assert database.read_bytes() == before


def test_service_receipts_are_bound_to_distinct_execution_profiles(
    tmp_path: Path,
) -> None:
    read_only, _ = service(tmp_path, "pm-read")
    read_only.start()
    canary = read_only.canary((event(),))
    read_only.close()

    workspace_boundary = LocalFakeDirectBoundary()
    workspace_boundary.execution_metadata = ExecutionMetadata(
        "codex_workspace_write", True, None
    )
    workspace, _ = service(tmp_path, "pm-write", workspace_boundary)
    workspace.start()
    run = workspace.run((event(),))

    assert canary.execution_profile_digest != run.execution_profile_digest
    assert canary.workspace_write_enabled is False
    assert canary.mutation_observed is False
    assert run.workspace_write_enabled is True
    assert run.mutation_observed is None
    assert run.controller_receipt.production_mutated is False
    assert run.orca_used is False
    workspace.close()


def test_stale_rollback_refuses_pending_boundary_work_and_status_is_uncertain(
    tmp_path: Path,
) -> None:
    root = tmp_path / "service"
    crashed, _ = service(tmp_path, "pm-crashed")
    generation = crashed.start()
    assert crashed._mutex is not None
    crashed._mutex.release()
    crashed._mutex = None

    class InterruptedFactory:
        def __call__(self, argv: list[str], **kwargs: object) -> object:
            del argv, kwargs
            raise KeyboardInterrupt("simulated host interruption")

    boundary = CodexCliBoundary(
        root / "codex_boundary.sqlite3",
        cwd=tmp_path,
        process_factory=InterruptedFactory(),  # type: ignore[arg-type]
    )
    with pytest.raises(KeyboardInterrupt):
        InjectedDirectRunner(boundary).run(
            RunnerAction.LAUNCH,
            task_id=TASK,
            role_key="lead_infra",
            generation="a" * 64,
            source_event_id="pending-boundary",
        )

    status = WorkflowControllerService.inspect(root)
    assert status.pending_boundary_operations == 1
    assert status.writer_state == "stale"
    with pytest.raises(ControllerServiceError, match="uncertain"):
        WorkflowControllerService.rollback_stale(
            root,
            owner_id="pm-crashed",
            generation_sequence=generation.sequence,
            generation_digest=generation.digest,
        )


def test_legacy_service_database_migrates_profile_columns(tmp_path: Path) -> None:
    root = tmp_path / "service"
    root.mkdir(parents=True)
    database = root / "workflow_controller_service.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE service_operation("
            "operation_id TEXT PRIMARY KEY, mode TEXT NOT NULL, input_digest TEXT NOT NULL, "
            "generation_sequence INTEGER NOT NULL, generation_digest TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE service_receipt("
            "operation_id TEXT PRIMARY KEY, mode TEXT NOT NULL, input_digest TEXT NOT NULL, "
            "payload TEXT NOT NULL, completed_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE operation_activity("
            "operation_id TEXT PRIMARY KEY, role_kind TEXT NOT NULL, session_fingerprint TEXT NOT NULL, "
            "task_id TEXT, state TEXT NOT NULL, heartbeat_at TEXT NOT NULL, active INTEGER NOT NULL, activity_digest TEXT NOT NULL)"
        )

    service(tmp_path, "pm-migration")
    with sqlite3.connect(database) as connection:
        operation_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(service_operation)")
        }
        receipt_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(service_receipt)")
        }
        activity_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(operation_activity)")
        }
    assert "execution_profile_digest" in operation_columns
    assert "execution_profile_digest" in receipt_columns
    assert {"generation_sequence", "generation_digest"} <= activity_columns


def test_service_restart_resumes_stored_pm_lead_worker_reviewer_sessions(
    tmp_path: Path,
) -> None:
    sessions = LocalFakeSessionBoundary()

    def build(owner: str) -> WorkflowControllerService:
        direct = LocalFakeDirectBoundary()
        controller = WorkflowController(
            WorkflowStateStore(tmp_path / "hierarchy-state.sqlite3", tmp_path / "hierarchy-events.jsonl"),
            InjectedDirectRunner(direct),
            tmp_path / "hierarchy-controller.sqlite3",
            session_runner=InjectedSessionRunner(sessions),
        )
        return WorkflowControllerService(
            controller, tmp_path / "hierarchy-service", owner_id=owner
        )

    first = build("pm-hierarchy-a")
    first.start()
    identities = (
        RoleIdentity(
            "project_manager", RoleKind.PROJECT_MANAGER, "session-pm",
            "python-control", "repo::C:/workspace", "term-pm", "runtime-a",
        ),
        RoleIdentity(
            "lead_data", RoleKind.DOMAIN_LEAD, "session-lead",
            "python-control", "repo::C:/workspace", "term-lead", "runtime-a",
            parent_role_key="project_manager",
        ),
        RoleIdentity(
            "worker_data", RoleKind.WORKER, "session-worker",
            "python-control", "repo::C:/workspace", "term-worker", "runtime-a",
            parent_role_key="lead_data",
        ),
        RoleIdentity(
            "reviewer_data", RoleKind.REVIEWER, "session-reviewer",
            "python-control", "repo::C:/workspace", "term-reviewer", "runtime-a",
            parent_role_key="lead_data",
        ),
    )
    for identity in identities:
        first.register_role_session(
            identity, observed_at=T0, lease_until=T0 + timedelta(hours=1)
        )
    original = first.resume_session_hierarchy()
    first.close()

    restarted = build("pm-hierarchy-b")
    restarted.start()
    replay = restarted.resume_session_hierarchy()

    assert replay.role_keys == original.role_keys
    assert replay.session_ids == original.session_ids
    assert sessions.calls == 4
    restarted.close()


def test_service_exposes_generation_bound_retry_safe_reviewer_wake(
    tmp_path: Path,
) -> None:
    sessions = LocalFakeSessionBoundary()
    controller = WorkflowController(
        WorkflowStateStore(tmp_path / "wake-state.sqlite3", tmp_path / "wake-events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "wake-controller.sqlite3",
        session_runner=InjectedSessionRunner(sessions),
    )
    instance = WorkflowControllerService(
        controller, tmp_path / "wake-service", owner_id="pm-wake"
    )
    instance.start()
    for identity in (
        RoleIdentity(
            "project_manager", RoleKind.PROJECT_MANAGER, "session-pm",
            "python-control", "repo::C:/workspace", "term-pm", "runtime-a",
        ),
        RoleIdentity(
            "lead_data", RoleKind.DOMAIN_LEAD, "session-lead",
            "python-control", "repo::C:/workspace", "term-lead", "runtime-a",
            parent_role_key="project_manager",
        ),
        RoleIdentity(
            "reviewer_data", RoleKind.REVIEWER, "session-reviewer",
            "python-control", "repo::C:/workspace", "term-reviewer", "runtime-a",
            parent_role_key="lead_data",
        ),
    ):
        instance.register_role_session(
            identity, observed_at=T0, lease_until=T0 + timedelta(hours=1)
        )
    reviewer = controller.role_registry.get("reviewer_data")
    receipt = instance.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    )
    assert instance.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    ) == receipt
    assert sessions.calls == 1
    instance.close()


def test_listener_generation_bound_envelope_delivers_to_service_exactly_once(
    tmp_path: Path,
) -> None:
    instance, _ = service(tmp_path, "pm-listener")
    instance.start()
    instance.register_role_session(
        RoleIdentity(
            "project_manager", RoleKind.PROJECT_MANAGER, "pm-session-7",
            "python-control", "repo::C:/workspace", "term-pm", "runtime-a",
        ),
        observed_at=T0,
        lease_until=T0 + timedelta(hours=1),
    )
    identity = instance.resolve_pm_mailbox_identity()
    intent = ListenerIntent(
        listener_id="root-listener",
        conversation_id="conversation-7",
        checkpoint_cursor="turn-1",
        user_text="PM에 전달",
        received_at="2026-08-31T00:00:00Z",
    )
    route = ListenerRoute(
        RouteKind.DIRECT_PM,
        {
            "message": "resume current work",
            "recipient": identity.recipient,
            "session_id": identity.session_id,
            "generation": identity.generation,
            "message_type": "operational_wake",
            "queue_id": None,
        },
    )
    with ListenerGateway(
        tmp_path / "listener.sqlite3",
        tmp_path / "listener.jsonl",
        sinks=ListenerSinks(pm_mailbox=instance),
        pm_identity_resolver=instance,
    ) as gateway:
        first = gateway.intake(intent, (route,))
        replay = gateway.intake(intent, (route,))

    assert replay == first
    pending = instance.mailbox("project_manager", pending_only=True)
    assert len(pending) == 1
    assert pending[0].message_id == first[0].deliveries[0].receipt_key
    assert pending[0].recipient_generation == identity.generation
    instance.close()
