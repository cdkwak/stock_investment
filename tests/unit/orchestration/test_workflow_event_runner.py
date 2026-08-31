from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_data.orchestration.workflow_control.event_runner import (
    EventRunnerError,
    WorkflowEventRunner,
)
from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryProcessError,
)
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot, QueueTaskOwnership
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleKind,
    RoleRecord,
    RoleState,
)
from stock_data.orchestration.workflow_control.service import WorkflowControllerService


T0 = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)


def _owned(_role_key: str, _session_id: str) -> str:
    return "f" * 64


def _listener_line() -> str:
    body = {"checkpoint_cursor": "turn-2", "conversation_id": "conversation", "event_type": "checkpoint", "intent_key": "a" * 64, "listener_id": "listener", "received_at": "2026-08-31T02:00:00Z", "version": 1}
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return json.dumps({"event_id": sha256(("listener-journal/v1\n" + encoded).encode("utf-8")).hexdigest(), **body}, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


class _Queue:
    def __init__(self, snapshot: QueueSnapshot) -> None:
        self.snapshot = snapshot

    def read_snapshot(self, *, observed_at: datetime) -> QueueSnapshot:
        return replace(self.snapshot, observed_at=observed_at)


class _Role:
    def __init__(self, key: str, kind: RoleKind, generation: int = 1, state: RoleState = RoleState.ACTIVE) -> None:
        self.identity = type("Identity", (), {"role_key": key, "role_kind": kind, "codex_session_id": f"session-{key}"})()
        self.generation = generation
        self.state = state


class _Service:
    def __init__(self, records: list[_Role]) -> None:
        self.controller = type("Controller", (), {"role_registry": type("Registry", (), {"records": lambda _self: tuple(records)})()})()
        self.wakes: list[tuple[str, int, str]] = []
        self.wake_sources: list[str | None] = []

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def wake_role_session(self, *, role_key: str, expected_generation: int, expected_session_id: str, source_event_id: str | None = None) -> str:
        self.wakes.append((role_key, expected_generation, expected_session_id))
        self.wake_sources.append(source_event_id)
        return ("a" if role_key == "project_manager" else "b") * 64


def _snapshot() -> QueueSnapshot:
    task = QueueTaskOwnership(
        "RQ-20260831T020000-A101", "active", "queue_orchestration_lead",
        "lead_infra", None, "infra", T0,
    )
    return QueueSnapshot(
        T0, (("new", 0), ("waiting", 0), ("ready", 0), ("active", 1), ("review", 0), ("blocked", 0), ("done", 0)),
        (task.task_id,), 0, (task,),
    )


def _repository(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("test", encoding="utf-8")
    (tmp_path / "src" / "stock_data").mkdir(parents=True)
    return tmp_path


def test_material_change_wakes_only_stored_pm_and_routed_lead_once(tmp_path: Path) -> None:
    service = _Service([_Role("project_manager", RoleKind.PROJECT_MANAGER), _Role("lead_infra", RoleKind.DOMAIN_LEAD)])
    factory_calls = 0

    def factory(*_args: object) -> _Service:
        nonlocal factory_calls
        factory_calls += 1
        return service

    runner = WorkflowEventRunner(_repository(tmp_path), owner_id="runner-one", queue_reader=_Queue(_snapshot()), service_factory=factory, session_ownership_verifier=_owned, now=lambda: T0)

    first = runner.run_once()
    second = runner.run_once()
    third = runner.run_once()

    assert first.outcome == "progressed"
    assert second.outcome == "woken"
    assert third.outcome == "unchanged"
    assert [item[0] for item in service.wakes] == ["project_manager", "lead_infra"]
    assert factory_calls == 2, "an unchanged generation must not acquire another PM writer"


def test_queue_heartbeat_timestamp_is_not_a_material_wake_event(tmp_path: Path) -> None:
    runner = WorkflowEventRunner(
        _repository(tmp_path),
        owner_id="runner-heartbeat",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_args: _Service([]),
        session_ownership_verifier=_owned,
        now=lambda: T0,
    )
    baseline = _snapshot()
    task = baseline.current_tasks[0]
    heartbeat_only = QueueSnapshot(
        baseline.observed_at,
        baseline.state_counts,
        baseline.active_task_ids,
        baseline.compacted_count,
        (QueueTaskOwnership(
            task.task_id, task.state, task.owner, task.lead_owner,
            task.reviewer, task.domain, T0.replace(hour=3), task.title,
        ),),
    )

    assert runner._queue_generation(heartbeat_only) == runner._queue_generation(baseline)


def test_stale_routed_lead_is_sanitized_noop(tmp_path: Path) -> None:
    service = _Service([_Role("project_manager", RoleKind.PROJECT_MANAGER)])
    runner = WorkflowEventRunner(_repository(tmp_path), owner_id="runner-two", queue_reader=_Queue(_snapshot()), service_factory=lambda *_: service, session_ownership_verifier=_owned, now=lambda: T0)

    receipt = runner.run_once()

    assert receipt.outcome == "stale_identity"
    assert service.wakes == []


def test_unknown_cli_session_ownership_refuses_before_any_wake(tmp_path: Path) -> None:
    service = _Service([
        _Role("project_manager", RoleKind.PROJECT_MANAGER),
        _Role("lead_infra", RoleKind.DOMAIN_LEAD),
    ])

    def unknown(_role_key: str, _session_id: str) -> str:
        raise ValueError("app-created coordination task has an active writer")

    runner = WorkflowEventRunner(
        _repository(tmp_path),
        owner_id="runner-unowned",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_: service,
        session_ownership_verifier=unknown,
        now=lambda: T0,
    )

    assert runner.run_once().outcome == "failed"
    assert service.wakes == []
    assert runner.status().pending_generations == 0


def test_wake_timeout_is_failed_pending_and_never_advances_the_next_role(
    tmp_path: Path,
) -> None:
    class TimedOutService(_Service):
        def wake_role_session(self, **_kwargs: object) -> str:
            raise CodexBoundaryProcessError("bounded process timeout")

    service = TimedOutService([
        _Role("project_manager", RoleKind.PROJECT_MANAGER),
        _Role("lead_infra", RoleKind.DOMAIN_LEAD),
    ])
    runner = WorkflowEventRunner(
        _repository(tmp_path),
        owner_id="runner-timeout",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_: service,
        session_ownership_verifier=_owned,
        now=lambda: T0,
    )

    receipt = runner.run_once()

    assert receipt.outcome == "failed"
    assert receipt.wake_receipt_digests == ()
    assert runner.status().pending_generations == 1


def test_listener_generation_change_reuses_durable_targets_without_duplicate_wake(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    service = _Service([_Role("project_manager", RoleKind.PROJECT_MANAGER), _Role("lead_infra", RoleKind.DOMAIN_LEAD)])
    runner = WorkflowEventRunner(root, owner_id="runner-three", queue_reader=_Queue(_snapshot()), service_factory=lambda *_: service, session_ownership_verifier=_owned, now=lambda: T0)

    assert runner.run_once().outcome == "progressed"
    assert runner.run_once().outcome == "woken"
    journal = root / "data" / "runtime" / "python_pm" / "listener_events.jsonl"
    journal.write_text(_listener_line(), encoding="utf-8")
    changed = runner.run_once()
    replay = runner.run_once()

    unchanged = runner.run_once()
    assert changed.outcome == "progressed"
    assert replay.outcome == "woken"
    assert unchanged.outcome == "unchanged"
    assert [item[0] for item in service.wakes] == ["project_manager", "lead_infra", "project_manager", "lead_infra"]
    assert len(set(service.wake_sources)) == 2


def test_status_is_read_only_before_the_first_run(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    runner = WorkflowEventRunner(root, owner_id="runner-status", queue_reader=_Queue(_snapshot()), service_factory=lambda *_: _Service([]), session_ownership_verifier=_owned, now=lambda: T0)

    assert runner.status().completed_generations == 0
    assert not (root / "data" / "runtime" / "python_pm").exists()


def test_partial_wake_retries_only_the_unsettled_target_and_stale_retry_wakes_none(tmp_path: Path) -> None:
    records = [_Role("project_manager", RoleKind.PROJECT_MANAGER), _Role("lead_infra", RoleKind.DOMAIN_LEAD)]

    class InterruptedService(_Service):
        def __init__(self) -> None:
            super().__init__(records)
            self.interrupt = True

        def wake_role_session(self, **kwargs: object) -> str:
            role_key = str(kwargs["role_key"])
            if role_key == "lead_infra" and self.interrupt:
                self.interrupt = False
                raise ValueError("simulated")
            return super().wake_role_session(**kwargs)  # type: ignore[arg-type]

    service = InterruptedService()
    runner = WorkflowEventRunner(_repository(tmp_path), owner_id="runner-partial", queue_reader=_Queue(_snapshot()), service_factory=lambda *_: service, session_ownership_verifier=_owned, now=lambda: T0)
    assert runner.run_once().outcome == "progressed"
    assert [item[0] for item in service.wakes] == ["project_manager"]
    assert runner.run_once().outcome == "failed"
    assert runner.run_once().outcome == "woken"
    assert [item[0] for item in service.wakes] == ["project_manager", "lead_infra"]

    journal = runner.listener_journal_path
    journal.write_text(_listener_line(), encoding="utf-8")
    service.interrupt = True
    assert runner.run_once().outcome == "progressed"
    assert runner.run_once().outcome == "failed"
    records[1].generation = 2
    assert runner.run_once().outcome == "stale_identity"
    assert [item[0] for item in service.wakes] == ["project_manager", "lead_infra", "project_manager"]


def test_superseded_material_waits_for_interrupted_generation_to_settle(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    records = [
        _Role("project_manager", RoleKind.PROJECT_MANAGER),
        _Role("lead_infra", RoleKind.DOMAIN_LEAD),
    ]

    class InterruptedService(_Service):
        def __init__(self) -> None:
            super().__init__(records)
            self.interrupt = True

        def wake_role_session(self, **kwargs: object) -> str:
            if kwargs["role_key"] == "lead_infra" and self.interrupt:
                self.interrupt = False
                raise ValueError("simulated interruption")
            return super().wake_role_session(**kwargs)  # type: ignore[arg-type]

    service = InterruptedService()
    runner = WorkflowEventRunner(
        root,
        owner_id="runner-superseded",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_: service,
        session_ownership_verifier=_owned,
        now=lambda: T0,
    )
    first = runner.run_once()
    assert first.outcome == "progressed"
    assert [item[0] for item in service.wakes] == ["project_manager"]

    runner.listener_journal_path.write_text(_listener_line(), encoding="utf-8")
    interrupted = runner.run_once()
    assert interrupted.outcome == "failed"
    assert interrupted.material_generation == first.material_generation
    drained = runner.run_once()
    assert drained.material_generation == first.material_generation
    assert drained.outcome == "woken"
    assert [item[0] for item in service.wakes] == ["project_manager", "lead_infra"]

    current_progress = runner.run_once()
    assert current_progress.outcome == "progressed"
    current = runner.run_once()
    assert current.material_generation != first.material_generation
    assert current.outcome == "woken"
    assert [item[0] for item in service.wakes] == [
        "project_manager", "lead_infra", "project_manager", "lead_infra",
    ]


def test_exact_cli_migration_rebinds_pending_target_without_settling_generation(
    tmp_path: Path,
) -> None:
    records = [
        _Role("project_manager", RoleKind.PROJECT_MANAGER),
        _Role("lead_infra", RoleKind.DOMAIN_LEAD),
    ]

    class InterruptedService(_Service):
        def wake_role_session(self, **_kwargs: object) -> str:
            raise ValueError("simulated coordination conflict")

    service = InterruptedService(records)
    runner = WorkflowEventRunner(
        _repository(tmp_path),
        owner_id="runner-migration",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_: service,
        session_ownership_verifier=_owned,
        now=lambda: T0,
    )
    assert runner.run_once().outcome == "failed"
    assert runner.status().pending_generations == 1

    cli = RoleRecord(
        RoleIdentity(
            "project_manager",
            RoleKind.PROJECT_MANAGER,
            "cli-project-manager",
            "transport-disabled",
            "stock-investment-rev1-main",
            None,
            "codex-cli-owned-v1",
        ),
        RoleState.ACTIVE,
        2,
        T0,
        T0,
    )
    migrated = runner.migrate_pending_role_identity(
        role_key="project_manager",
        expected_generation=1,
        expected_session_fingerprint=sha256(
            b"session-project_manager"
        ).hexdigest(),
        cli_record=cli,
    )
    assert migrated == 1
    assert runner.status().pending_generations == 1


def test_exact_recovery_preserves_failed_generation_and_rotates_fresh_material_epoch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records = [
        _Role("project_manager", RoleKind.PROJECT_MANAGER),
        _Role("lead_infra", RoleKind.DOMAIN_LEAD),
    ]

    class FailOnceService(_Service):
        fail = True

        def wake_role_session(self, **kwargs: object) -> str:
            if self.fail:
                self.fail = False
                raise ValueError("simulated uncertain wake")
            return super().wake_role_session(**kwargs)  # type: ignore[arg-type]

    service = FailOnceService(records)
    runner = WorkflowEventRunner(
        _repository(tmp_path),
        owner_id="runner-recovery",
        queue_reader=_Queue(_snapshot()),
        service_factory=lambda *_: service,
        session_ownership_verifier=_owned,
        now=lambda: T0,
    )
    failed = runner.run_once()
    assert failed.outcome == "failed"
    assert runner.status().pending_generations == 1

    recovery_proof = "e" * 64
    monkeypatch.setattr(
        WorkflowControllerService,
        "assert_event_recovery_proof",
        classmethod(lambda cls, *_args, **_kwargs: SimpleNamespace()),
    )
    monkeypatch.setattr(
        WorkflowControllerService,
        "inspect",
        staticmethod(lambda *_args, **_kwargs: SimpleNamespace(
            active=False,
            writer_state="idle",
            pending_boundary_operations=0,
        )),
    )
    recovery = runner.recover_pending_generation(
        material_generation=failed.material_generation,
        expected_attempt_receipt_digest=failed.receipt_digest,
        recovery_proof=recovery_proof,
    )
    assert runner.recover_pending_generation(
        material_generation=failed.material_generation,
        expected_attempt_receipt_digest=failed.receipt_digest,
        recovery_proof=recovery_proof,
    ) == recovery
    with pytest.raises(EventRunnerError, match="replay pins changed"):
        runner.recover_pending_generation(
            material_generation=failed.material_generation,
            expected_attempt_receipt_digest=failed.receipt_digest,
            recovery_proof="d" * 64,
        )
    assert recovery.prior_generation == failed.material_generation
    recovered_status = runner.status()
    assert recovered_status.pending_generations == 0
    assert recovered_status.recovered_generations == 1
    assert recovered_status.last_attempt is not None
    assert recovered_status.last_attempt.outcome == "recovered"

    progressed = runner.run_once()
    settled = runner.run_once()
    assert progressed.material_generation != failed.material_generation
    assert progressed.outcome == "progressed"
    assert settled.outcome == "woken"
    final = runner.status()
    assert final.pending_generations == 0
    assert final.completed_generations == 2


def test_exact_reconciliation_status_is_read_only_and_generation_pinned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records = [
        _Role("project_manager", RoleKind.PROJECT_MANAGER),
        _Role("lead_infra", RoleKind.DOMAIN_LEAD),
    ]

    class FailedService(_Service):
        def wake_role_session(self, **_kwargs: object) -> str:
            raise ValueError("simulated uncertain wake")

    runner = WorkflowEventRunner(
        _repository(tmp_path), owner_id="runner-status-pins",
        queue_reader=_Queue(_snapshot()), service_factory=lambda *_: FailedService(records),
        session_ownership_verifier=_owned, now=lambda: T0,
    )
    failed = runner.run_once()
    assert failed.outcome == "failed"
    # The PT1M scheduler can observe the same pending material generation
    # again.  Its deterministic failed receipt is preserved as another audit
    # row and must remain a valid exact reconciliation pin.
    assert runner.run_once().receipt_digest == failed.receipt_digest
    monkeypatch.setattr(
        WorkflowControllerService,
        "inspect",
        staticmethod(lambda *_args, **_kwargs: SimpleNamespace(
            active=False, writer_state="idle", pending_boundary_operations=0,
        )),
    )

    observed = runner.reconciliation_status(
        material_generation=failed.material_generation,
        expected_attempt_receipt_digest=failed.receipt_digest,
    )

    assert observed.state == "pending_failed"
    assert observed.ready is True
    assert runner.status().pending_generations == 1
    with pytest.raises(EventRunnerError, match="attempt.*(changed|absent|ambiguous)"):
        runner.reconciliation_status(
            material_generation=failed.material_generation,
            expected_attempt_receipt_digest="e" * 64,
        )

def test_malformed_listener_is_durable_sanitized_failure(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    service = _Service([_Role("project_manager", RoleKind.PROJECT_MANAGER), _Role("lead_infra", RoleKind.DOMAIN_LEAD)])
    runner = WorkflowEventRunner(root, owner_id="runner-corrupt", queue_reader=_Queue(_snapshot()), service_factory=lambda *_: service, session_ownership_verifier=_owned, now=lambda: T0)
    runner.listener_journal_path.parent.mkdir(parents=True)
    runner.listener_journal_path.write_text('{"event_id":"tampered"}\n', encoding="utf-8")

    assert runner.run_once().outcome == "failed"
    assert service.wakes == []
    status = runner.status()
    assert status.service_identity == "python_pm_event_runner"
    assert status.last_attempt is not None and status.last_attempt.outcome == "failed"
