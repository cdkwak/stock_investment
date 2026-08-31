from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import io
import json
from pathlib import Path

import pytest

from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryProcessError,
    CodexCliBoundary,
)
from stock_data.orchestration.workflow_control.event_runner import WorkflowEventRunner
from stock_data.orchestration.workflow_control import (
    InjectedDirectRunner, InjectedSessionRunner, LocalFakeDirectBoundary,
    LocalFakeSessionBoundary, RoleIdentity, RoleRegistry, WorkflowController,
    WorkflowControllerService, WorkflowStateStore,
)
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot, QueueTaskOwnership
from stock_data.orchestration.workflow_control.registry import RoleKind, RoleState
from stock_data.orchestration.workflow_control.runner import RunnerAction
from stock_data.orchestration.workflow_control.session_runner import SessionAction


def _owned(_role_key: str, _session_id: str) -> str:
    return "f" * 64


class _Queue:
    def __init__(self, snapshot: QueueSnapshot) -> None:
        self.snapshot = snapshot

    def read_snapshot(self, *, observed_at: datetime) -> QueueSnapshot:
        return replace(self.snapshot, observed_at=observed_at)


class _Role:
    def __init__(self, key: str, kind: RoleKind) -> None:
        self.identity = type("Identity", (), {"role_key": key, "role_kind": kind, "codex_session_id": f"session-{key}"})()
        self.generation = 1
        self.state = RoleState.ACTIVE


class _Service:
    def __init__(self) -> None:
        records = (_Role("project_manager", RoleKind.PROJECT_MANAGER), _Role("lead_infra", RoleKind.DOMAIN_LEAD))
        self.controller = type("Controller", (), {"role_registry": type("Registry", (), {"records": lambda _self: records})()})()
        self.wakes: list[str] = []

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def wake_role_session(self, *, role_key: str, expected_generation: int, expected_session_id: str, source_event_id: str | None = None) -> str:
        del source_event_id
        self.wakes.append(role_key)
        return ("a" if role_key == "project_manager" else "b") * 64


def _repository(root: Path) -> Path:
    (root / "AGENTS.md").write_text("test", encoding="utf-8")
    (root / "src" / "stock_data").mkdir(parents=True)
    return root


def _snapshot() -> QueueSnapshot:
    observed_at = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)
    task = QueueTaskOwnership("RQ-20260831T020000-A101", "active", "lead_infra", "lead_infra", None, "infra", observed_at)
    return QueueSnapshot(observed_at, (("new", 0), ("waiting", 0), ("ready", 0), ("active", 1), ("review", 0), ("blocked", 0), ("done", 0)), (task.task_id,), 0, (task,))


def _listener_line() -> str:
    body = {"checkpoint_cursor": "turn-2", "conversation_id": "conversation", "event_type": "checkpoint", "intent_key": "a" * 64, "listener_id": "listener", "received_at": "2026-08-31T02:00:00Z", "version": 1}
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return json.dumps({"event_id": sha256(("listener-journal/v1\n" + encoded).encode("utf-8")).hexdigest(), **body}, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def test_later_durable_listener_event_advances_only_through_runner(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    service = _Service()
    runner = WorkflowEventRunner(root, owner_id="integration-runner", queue_reader=_Queue(_snapshot()), service_factory=lambda *_: service, session_ownership_verifier=_owned, now=lambda: datetime(2026, 8, 31, 2, 0, tzinfo=UTC))

    assert runner.run_once().outcome == "progressed"
    assert runner.run_once().outcome == "woken"
    assert len(service.wakes) == 2
    journal = root / "data" / "runtime" / "python_pm" / "listener_events.jsonl"
    journal.write_text(_listener_line(), encoding="utf-8")
    assert len(service.wakes) == 2
    assert runner.run_once().outcome == "progressed"
    assert runner.run_once().outcome == "woken"
    assert len(service.wakes) == 4


def test_public_controller_wake_outbox_is_not_recalled_for_completed_generation(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    control_root = root / "data" / "runtime" / "python_pm"
    direct, sessions = LocalFakeDirectBoundary(), LocalFakeSessionBoundary()

    def service(owner: str) -> WorkflowControllerService:
        state = WorkflowStateStore(control_root / "workflow_state.sqlite3", control_root / "workflow_events.jsonl")
        controller = WorkflowController(state, InjectedDirectRunner(direct), control_root / "hierarchy.sqlite3", session_runner=InjectedSessionRunner(sessions), role_registry=RoleRegistry(control_root / "role_registry.sqlite3"))
        return WorkflowControllerService(controller, control_root, owner_id=owner)

    setup = service("setup")
    setup.start()
    until = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
    for key, kind, parent in (("project_manager", RoleKind.PROJECT_MANAGER, None), ("lead_infra", RoleKind.DOMAIN_LEAD, "project_manager")):
        setup.register_role_session(RoleIdentity(key, kind, f"session-{key}", "legacy-denied", "test-root", None, "python-only", "RQ-20260831T020000-A101" if key != "project_manager" else None, "dispatch-lead" if key != "project_manager" else None, parent), observed_at=datetime(2026, 8, 31, 2, 0, tzinfo=UTC), lease_until=until)
    setup.close()
    runner = WorkflowEventRunner(root, owner_id="public-runner", queue_reader=_Queue(_snapshot()), service_factory=lambda _root, owner: service(owner), session_ownership_verifier=_owned, now=lambda: datetime(2026, 8, 31, 2, 0, tzinfo=UTC))

    assert runner.run_once().outcome == "progressed"
    assert runner.run_once().outcome == "woken"
    assert RoleRegistry(control_root / "role_registry.sqlite3").get(
        "project_manager"
    ).generation == 2
    assert RoleRegistry(control_root / "role_registry.sqlite3").get(
        "lead_infra"
    ).generation == 2
    calls = sessions.calls
    assert runner.run_once().outcome == "unchanged"
    assert sessions.calls == calls == 2


def test_public_terminal_reconciliation_rotates_failed_event_generation(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    control_root = root / "data" / "runtime" / "python_pm"
    direct = LocalFakeDirectBoundary()
    state = WorkflowStateStore(
        control_root / "workflow_state.sqlite3",
        control_root / "workflow_events.jsonl",
    )
    controller = WorkflowController(
        state,
        InjectedDirectRunner(direct),
        control_root / "hierarchy.sqlite3",
    )
    terminal_service = WorkflowControllerService(
        controller, control_root, owner_id="windows-task-scheduler",
    )
    generation = terminal_service.start()

    class Process:
        def __init__(self, stdout: bytes, returncode: int) -> None:
            self.stdout = io.BytesIO(stdout)
            self.stderr = io.BytesIO(b"not retained")
            self.returncode = returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return self.returncode

        def poll(self) -> int:
            return self.returncode

        def kill(self) -> None:
            raise AssertionError("terminal reconciliation must not kill a process")

    session_id = "cli-project-manager"
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
        control_root / "codex_boundary.sqlite3",
        cwd=root,
        sandbox_mode="workspace-write",
        process_factory=lambda *_args, **_kwargs: processes.pop(0),  # type: ignore[arg-type]
    )
    InjectedDirectRunner(boundary).run(
        RunnerAction.LAUNCH,
        task_id="RQ-20260831T020000-A101",
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
            role_generation=1,
            session_id=session_id,
            provenance="e" * 64,
        )
    terminal_service.close()
    terminal = CodexCliBoundary.inspect_terminal_operation(
        control_root / "codex_boundary.sqlite3",
        operation_id=captured["operation_id"],
    )

    class FailOnceService(_Service):
        fail = True

        def wake_role_session(self, **kwargs: object) -> str:
            if self.fail:
                self.fail = False
                raise ValueError("simulated failed stored wake")
            return super().wake_role_session(**kwargs)  # type: ignore[arg-type]

    wake_service = FailOnceService()
    runner = WorkflowEventRunner(
        root,
        owner_id="integration-terminal-recovery",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_: wake_service,
        session_ownership_verifier=_owned,
        now=lambda: datetime(2026, 8, 31, 2, 0, tzinfo=UTC),
    )
    failed = runner.run_once()
    assert failed.outcome == "failed"
    receipt = WorkflowControllerService.reconcile_terminal(
        control_root,
        owner_id="windows-task-scheduler",
        generation_sequence=generation.sequence,
        generation_digest=generation.digest,
        boundary_operation_id=terminal.operation_id,
        boundary_request_digest=terminal.request_digest,
        boundary_error_code=terminal.error_code,
        release_reason="stopped",
    )
    recovery = runner.recover_pending_generation(
        material_generation=failed.material_generation,
        expected_attempt_receipt_digest=failed.receipt_digest,
        recovery_proof=receipt.reconciliation_proof,
    )
    assert recovery.prior_generation == failed.material_generation
    assert runner.status().pending_generations == 0
    assert runner.run_once().outcome == "progressed"
    assert runner.run_once().outcome == "woken"
    assert runner.status().pending_generations == 0
