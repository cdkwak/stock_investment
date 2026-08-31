from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_data.orchestration.workflow_control.production import (
    build_production_service,
    canonical_control_root,
)
from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryTerminalOperation,
    CodexProcessEventPins,
)
from stock_data.orchestration.workflow_control.service import ServiceMode
from stock_data.orchestration.workflow_control import service as service_module
from stock_data.orchestration.workflow_control.registry import RoleState


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "maintenance"
    / "workflow_controller.py"
)


def _repository(root: Path) -> Path:
    (root / "src" / "stock_data").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# test repository\n", encoding="utf-8")
    return root


def _entrypoint_module():
    spec = importlib.util.spec_from_file_location("workflow_controller_entrypoint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_composition_has_one_repository_owned_root_and_no_fake_default(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repo")
    expected = repository.resolve() / "data" / "runtime" / "python_pm"

    assert canonical_control_root(repository) == expected
    assert "control_root" not in inspect.signature(build_production_service).parameters
    canary = build_production_service(
        repository, "pm-canary", ServiceMode.CANARY, command=("codex-stub",)
    )
    run = build_production_service(
        repository, "pm-run", ServiceMode.RUN, command=("codex-stub",)
    )
    assert canary.control_root == run.control_root == expected
    assert canary.execution_metadata.profile_name == "codex_read_only"
    assert canary.execution_metadata.workspace_write_enabled is False
    assert run.execution_metadata.profile_name == "codex_workspace_write"
    assert run.execution_metadata.workspace_write_enabled is True
    assert run.execution_metadata.mutation_observed is None
    source = inspect.getsource(
        __import__(
            "stock_data.orchestration.workflow_control.production",
            fromlist=["production"],
        )
    )
    assert "LocalFake" not in source
    assert "orca" not in source.casefold()


def test_cli_uses_supported_production_default_without_factory_or_control_root(
    tmp_path: Path,
) -> None:
    module = _entrypoint_module()
    parser = module._parser()
    repository = _repository(tmp_path / "repo")

    canary = parser.parse_args(
        [
            "--repository-root",
            str(repository),
            "canary",
            "--owner-id",
            "pm-canary",
            "--events",
            str(tmp_path / "events.json"),
        ]
    )
    assert not hasattr(canary, "factory")
    assert not hasattr(canary, "control_root")


def test_status_and_rollback_never_construct_an_execution_boundary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")

    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("status/rollback constructed production boundary")

    monkeypatch.setattr(module, "build_production_service", forbidden)
    assert module.main(["--repository-root", str(repository), "status"]) == 0
    assert '"writer_state": "idle"' in capsys.readouterr().out
    assert module.main(
        [
            "--repository-root",
            str(repository),
            "rollback",
            "--owner-id",
            "pm-stale",
            "--generation-sequence",
            "1",
            "--generation-digest",
            "a" * 64,
        ]
    ) == 0
    assert '"writer_state": "idle"' in capsys.readouterr().out


def test_stranded_recovery_preflight_reports_live_process_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    captured: list[dict[str, object]] = []

    class Blocked:
        ready = False

        def to_dict(self) -> dict[str, object]:
            return {
                "ready": False,
                "process_live": True,
                "reason": "writer_process_live",
                "preflight_digest": "f" * 64,
            }

    def preflight(_root: Path, **pins: object) -> Blocked:
        captured.append(pins)
        return Blocked()

    monkeypatch.setattr(
        module.WorkflowControllerService,
        "preflight_stranded_recovery",
        staticmethod(preflight),
    )
    monkeypatch.setattr(
        module.WorkflowControllerService,
        "recover_stranded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight invoked recovery")
        ),
    )
    assert module.main([
        "--repository-root", str(repository), "recover-stranded",
        "--owner-id", "windows-task-scheduler",
        "--generation-sequence", "54",
        "--generation-digest", "a" * 64,
        "--boundary-operation-id", "session-op-" + ("b" * 64),
        "--boundary-request-digest", "c" * 64,
        "--preflight-only",
    ]) == 2
    output = capsys.readouterr().out
    assert '"process_live": true' in output
    assert captured == [{
        "owner_id": "windows-task-scheduler",
        "generation_sequence": 54,
        "generation_digest": "a" * 64,
        "boundary_operation_id": "session-op-" + ("b" * 64),
        "boundary_request_digest": "c" * 64,
    }]


def test_terminal_reconciliation_cli_pins_natural_failure_and_preflight_is_read_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    captured: list[dict[str, object]] = []

    class Ready:
        def to_dict(self) -> dict[str, object]:
            return {
                "ready": True,
                "process_live": False,
                "boundary_error_code": "process_timeout",
                "preflight_digest": "f" * 64,
            }

    def preflight(_root: Path, **pins: object) -> Ready:
        captured.append(pins)
        return Ready()

    monkeypatch.setattr(
        module.WorkflowControllerService,
        "preflight_terminal_reconciliation",
        staticmethod(preflight),
    )
    monkeypatch.setattr(
        module.WorkflowControllerService,
        "reconcile_terminal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight invoked reconciliation")
        ),
    )
    assert module.main([
        "--repository-root", str(repository), "reconcile-terminal",
        "--owner-id", "windows-task-scheduler",
        "--generation-sequence", "54",
        "--generation-digest", "a" * 64,
        "--boundary-operation-id", "session-op-" + ("b" * 64),
        "--boundary-request-digest", "c" * 64,
        "--boundary-error-code", "process_timeout",
        "--release-reason", "stopped",
        "--preflight-only",
    ]) == 0
    assert '"process_live": false' in capsys.readouterr().out
    assert captured == [{
        "owner_id": "windows-task-scheduler",
        "generation_sequence": 54,
        "generation_digest": "a" * 64,
        "boundary_operation_id": "session-op-" + ("b" * 64),
        "boundary_request_digest": "c" * 64,
        "boundary_error_code": "process_timeout",
        "release_reason": "stopped",
    }]


def test_terminal_operation_status_is_public_read_only_and_rejects_malformed_id(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    operation_id = "session-op-" + ("b" * 64)
    observed: list[str] = []

    def inspect(_path: Path, *, operation_id: str) -> SimpleNamespace:
        observed.append(operation_id)
        if not operation_id.startswith("session-op-"):
            raise module.CodexBoundaryError("invalid operation id")
        return CodexBoundaryTerminalOperation(
            operation_id=operation_id,
            request_digest="c" * 64,
            request_kind="session",
            execution_profile_digest="d" * 64,
            state="failed",
            error_code="process_timeout",
        )

    monkeypatch.setattr(
        module.CodexCliBoundary,
        "inspect_terminal_operation",
        staticmethod(inspect),
    )
    assert module.main([
        "--repository-root", str(repository),
        "terminal-operation-status",
        "--boundary-operation-id", operation_id,
    ]) == 0
    assert '"error_code": "process_timeout"' in capsys.readouterr().out
    assert module.main([
        "--repository-root", str(repository),
        "terminal-operation-status",
        "--boundary-operation-id", "malformed",
    ]) == 2
    assert observed == [operation_id, "malformed"]


def test_event_reconciliation_status_uses_exact_pins_without_recovery(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    observed: list[dict[str, str]] = []

    @dataclass
    class Status:
        material_generation: str
        attempt_receipt_digest: str
        state: str
        recovery_proof: str | None
        controller_writer_state: str
        pending_boundary_operations: int
        ready: bool

    monkeypatch.setattr(
        module.WorkflowControllerService,
        "event_reconciliation_status",
        staticmethod(lambda _root, **pins: (
            observed.append(pins)
            or Status(
                pins["material_generation"], pins["attempt_receipt_digest"],
                "pending_failed", None, "idle", 0, True,
            )
        )),
    )
    generation = "a" * 64
    attempt = "b" * 64
    assert module.main([
        "--repository-root", str(repository), "event-reconciliation-status",
        "--material-generation", generation, "--attempt-receipt-digest", attempt,
    ]) == 0
    assert '"ready": true' in capsys.readouterr().out
    assert observed == [{
        "material_generation": generation,
        "attempt_receipt_digest": attempt,
    }]


def test_app_coordination_lead_replacement_entrypoint_hides_session_values(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    calls: list[dict[str, object]] = []
    identity = SimpleNamespace(
        active_dispatch_id="dispatch-a", active_task_id="RQ-20260829T003900-24A5",
        codex_session_id="replacement-session", parent_role_key="project_manager",
        role_key="lead_infra", runtime_id="codex-app-local",
    )

    class FakeService:
        def start(self) -> None:
            return None

        def close(self) -> None:
            return None

        def replace_app_coordination_lead_session(self, **kwargs: object):
            calls.append(dict(kwargs))
            return SimpleNamespace(identity=identity, generation=4, state=RoleState.ACTIVE)

    monkeypatch.setattr(module, "build_production_service", lambda *_args, **_kwargs: FakeService())
    assert module.main([
        "--repository-root", str(repository), "replace-app-coordination-lead",
        "--owner-id", "pm-owner", "--pm-role-key", "project_manager",
        "--expected-pm-generation", "2", "--role-key", "lead_infra",
        "--expected-generation", "3", "--expected-session-id", "old-session",
        "--replacement-session-id", "replacement-session",
        "--expected-task-id", "RQ-20260829T003900-24A5",
        "--expected-dispatch-id", "dispatch-a",
    ]) == 0
    output = capsys.readouterr().out
    assert "old-session" not in output and "replacement-session" not in output
    assert '"generation": 4' in output
    assert calls == [{
        "pm_role_key": "project_manager", "expected_pm_generation": 2,
        "role_key": "lead_infra", "expected_generation": 3,
        "expected_session_id": "old-session", "replacement_session_id": "replacement-session",
        "expected_task_id": "RQ-20260829T003900-24A5", "expected_dispatch_id": "dispatch-a",
        "expected_runtime_id": "codex-app-local",
        "expected_worktree_id": "stock-investment-rev1-main",
    }]


def test_process_event_status_replays_only_exact_sanitized_receipt_without_control_effects(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    operation_id = "session-op-" + ("b" * 64)
    observed: list[tuple[Path, CodexProcessEventPins, str | None]] = []

    class Receipt:
        def to_dict(self) -> dict[str, object]:
            return {
                "schema": "codex-process-event/v1",
                "classifier_version": 1,
                "operation_id": operation_id,
                "request_digest": "c" * 64,
                "generation_sequence": 7,
                "generation_digest": "d" * 64,
                "execution_profile_digest": "e" * 64,
                "reason": "model_capacity",
                "full_stream_byte_length": 123,
                "full_stream_sha256": "f" * 64,
                "parser_error": False,
                "truncated": False,
                "receipt_digest": "1" * 64,
            }

    def inspect(
        path: Path,
        *,
        pins: CodexProcessEventPins,
        expected_receipt_digest: str | None = None,
    ) -> Receipt:
        observed.append((path, pins, expected_receipt_digest))
        return Receipt()

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("read-only process-event status invoked a control path")

    monkeypatch.setattr(
        module.CodexCliBoundary,
        "inspect_process_event",
        staticmethod(inspect),
    )
    monkeypatch.setattr(module, "build_production_service", forbidden)
    monkeypatch.setattr(module.WorkflowControllerService, "recover_stranded", forbidden)
    monkeypatch.setattr(module.WorkflowControllerService, "reconcile_terminal", forbidden)
    assert module.main([
        "--repository-root", str(repository),
        "process-event-status",
        "--boundary-operation-id", operation_id,
        "--boundary-request-digest", "c" * 64,
        "--generation-sequence", "7",
        "--generation-digest", "d" * 64,
        "--execution-profile-digest", "e" * 64,
        "--expected-receipt-digest", "1" * 64,
    ]) == 0
    output = capsys.readouterr().out
    assert '"reason": "model_capacity"' in output
    assert set(observed[0][1].__dataclass_fields__) == {
        "operation_id",
        "request_digest",
        "generation_sequence",
        "generation_digest",
        "execution_profile_digest",
    }
    assert observed[0][2] == "1" * 64


def test_bootstrap_role_launches_cli_session_without_printing_raw_id(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    captured: list[object] = []

    class FakeService:
        def start(self) -> None:
            captured.append("started")

        def register_role_session(self, identity, **_kwargs):
            captured.append(identity)
            return SimpleNamespace(identity=identity, generation=1, state=RoleState.ACTIVE)

        def close(self) -> None:
            captured.append("closed")

    class FakeBoundary:
        def __init__(self, *_args, **_kwargs) -> None:
            captured.append("boundary")

        def execute(self, request) -> dict[str, str]:
            captured.append(dict(request))
            return {"status": "launched", "agent_id": session_id}

        def assert_cli_owned_session(self, **kwargs: str) -> str:
            captured.append(kwargs)
            return "b" * 64

    monkeypatch.setattr(module, "build_production_service", lambda *_args, **_kwargs: FakeService())
    monkeypatch.setattr(module, "CodexCliBoundary", FakeBoundary)
    session_id = "01a0560c-a739-7fe0-b7a1-c3a184e4b3f6"
    assert module.main([
        "--repository-root", str(repository),
        "bootstrap-role",
        "--owner-id", "role-bootstrap",
        "--role-key", "project_manager",
        "--role-kind", "project_manager",
        "--binding-task-id", "RQ-20260831T080429-5516",
    ]) == 0
    output = capsys.readouterr().out
    assert '"role_key": "project_manager"' in output
    assert '"ownership_proof": "' + ("b" * 64) + '"' in output
    assert '"transport": "direct"' in output
    assert session_id not in output
    assert captured[0] == "boundary"
    assert captured[1] == "started" and "closed" in captured


def test_bootstrap_role_requires_parent_and_task_dispatch_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    monkeypatch.setattr(
        module, "build_production_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid role packet reached production service")
        ),
    )

    assert module.main([
        "--repository-root", str(repository), "bootstrap-role",
        "--owner-id", "role-bootstrap", "--role-key", "lead_data",
        "--role-kind", "domain_lead",
        "--binding-task-id", "RQ-20260831T080429-5516",
    ]) == 2
    assert module.main([
        "--repository-root", str(repository), "bootstrap-role",
        "--owner-id", "role-bootstrap", "--role-key", "lead_data",
        "--role-kind", "domain_lead",
        "--binding-task-id", "RQ-20260831T080429-5516",
        "--parent-role-key", "project_manager",
        "--task-id", "RQ-20260831T080429-5516",
    ]) == 2


def test_bootstrap_role_rejects_out_of_range_attempt_before_boundary_or_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")

    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("invalid bootstrap attempt reached runtime construction")

    monkeypatch.setattr(module, "build_production_service", forbidden)
    monkeypatch.setattr(module, "CodexCliBoundary", forbidden)

    for attempt in ("0", "10"):
        assert module.main([
            "--repository-root", str(repository), "bootstrap-role",
            "--owner-id", "role-bootstrap", "--role-key", "project_manager",
            "--role-kind", "project_manager",
            "--binding-task-id", "RQ-20260831T080429-5516",
            "--bootstrap-attempt", attempt,
        ]) == 2


def test_refresh_role_rotates_only_the_exact_registered_generation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    captured: list[object] = []

    identity = SimpleNamespace(
        active_task_id="RQ-20260831T080429-5516",
        role_key="queue_orchestration_lead",
        role_kind=SimpleNamespace(value="domain_lead"),
    )

    class FakeRegistry:
        def heartbeat(self, role_key: str, **kwargs: object):
            captured.append((role_key, kwargs))
            return SimpleNamespace(identity=identity, generation=2, state=RoleState.ACTIVE)

    class FakeService:
        controller = SimpleNamespace(role_registry=FakeRegistry())

        def start(self) -> None:
            captured.append("started")

        def close(self) -> None:
            captured.append("closed")

    monkeypatch.setattr(
        module, "build_production_service", lambda *_args, **_kwargs: FakeService()
    )
    assert module.main([
        "--repository-root", str(repository), "refresh-role",
        "--owner-id", "role-refresh", "--role-key", "queue_orchestration_lead",
        "--expected-generation", "1",
    ]) == 0
    output = capsys.readouterr().out
    assert '"generation": 2' in output
    assert '"role_key": "queue_orchestration_lead"' in output
    assert captured[0] == "started" and captured[-1] == "closed"


def test_phase_boundary_cli_verifies_queue_evidence_before_public_pm_operation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")
    task_id = "RQ-20260831T080429-5516"
    queue_generation = "a" * 64
    candidate_digest = "b" * 64
    review_digest = "c" * 64
    handoff = (
        repository / "artifacts" / "request_queue" / "active"
        / f"P1-{task_id}-phase-boundary" / "HANDOFF.md"
    )
    handoff.parent.mkdir(parents=True)
    handoff.write_text(
        "phase: phase_a_pass\n"
        f"summary: candidate {candidate_digest} reviewed PASS {review_digest}\n",
        encoding="utf-8",
    )
    observed: list[tuple[str, dict[str, object]]] = []

    class Receipt:
        def to_dict(self) -> dict[str, object]:
            return {"receipt_digest": "d" * 64, "next_state": "replan_required"}

    class Service:
        def mark_task_replan_ready(self, **kwargs: object) -> Receipt:
            observed.append(("mark", kwargs))
            return Receipt()

        def inspect_phase_boundary_receipt(self, **_kwargs: object) -> Receipt:
            return Receipt()

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
    production_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        module.WorkflowControllerService,
        "preflight_task_replan_ready_at",
        staticmethod(lambda repository_root, receipt_path, **kwargs: (
            observed.append(("preflight", {"receipt_path": receipt_path, **kwargs}))
            or Receipt()
        )),
    )
    monkeypatch.setattr(
        module,
        "build_production_service",
        lambda *args, **_kwargs: (production_calls.append(args) or Service()),
    )
    arguments = [
        "--repository-root", str(repository), "preflight-task-replan-ready",
        "--task-id", task_id,
        "--expected-queue-generation", queue_generation,
        "--expected-prior-contract-digest", "e" * 64,
        "--expected-phase-a-candidate-digest", candidate_digest,
        "--expected-phase-a-review-digest", review_digest,
        "--expected-prior-state", "assigned",
        "--reason-code", "phase_a_pass_requires_phase_b_contract",
        "--pm-role-key", "project_manager", "--pm-generation", "7",
    ]
    assert module.main(arguments) == 0
    expected = {
        "task_id": task_id,
        "expected_queue_generation": queue_generation,
        "expected_prior_contract_digest": "e" * 64,
        "expected_phase_a_candidate_digest": candidate_digest,
        "expected_phase_a_review_digest": review_digest,
        "expected_prior_state": "assigned",
        "reason_code": "phase_a_pass_requires_phase_b_contract",
        "pm_role_key": "project_manager",
        "pm_generation": 7,
    }
    assert observed == [(
        "preflight",
        {"receipt_path": repository / "data" / "runtime" / "python_pm" / "workflow_controller.sqlite3", **expected},
    )]
    assert production_calls == []
    arguments[2] = "mark-task-replan-ready"
    assert module.main(arguments) == 0
    assert observed == [
        ("preflight", {"receipt_path": repository / "data" / "runtime" / "python_pm" / "workflow_controller.sqlite3", **expected}),
        ("mark", {"repository_root": repository, **expected}),
    ]
    assert production_calls == [(repository, "pm-phase-boundary", module.ServiceMode.RUN)]
    assert '"next_state": "replan_required"' in capsys.readouterr().out

    with pytest.raises(module.ControllerServiceError, match="Phase-A evidence"):
        module._verify_phase_a_queue_evidence(
            repository,
            task_id=task_id,
            expected_queue_generation=queue_generation,
            expected_candidate_digest="f" * 64,
            expected_review_digest=review_digest,
        )
    assert len(observed) == 2


def test_phase_boundary_status_is_read_only_and_does_not_construct_a_boundary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")

    class Receipt:
        def to_dict(self) -> dict[str, object]:
            return {"receipt_digest": "a" * 64, "next_state": "replan_required"}

    monkeypatch.setattr(
        module.WorkflowController,
        "inspect_phase_boundary_receipt_at",
        staticmethod(lambda *_args, **_kwargs: Receipt()),
    )
    monkeypatch.setattr(
        module,
        "build_production_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("phase-boundary status constructed a production boundary")
        ),
    )
    assert module.main([
        "--repository-root", str(repository), "phase-boundary-status",
        "--task-id", "RQ-20260831T080429-5516",
    ]) == 0
    assert '"next_state": "replan_required"' in capsys.readouterr().out
