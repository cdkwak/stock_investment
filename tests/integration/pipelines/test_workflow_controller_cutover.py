from __future__ import annotations

from datetime import UTC, datetime, timedelta
import inspect
from pathlib import Path
import sys

import pytest

from stock_data.orchestration.workflow_control.contracts import (
    EventKind, EventSource, Priority, TaskState, WorkflowEvent,
)
from stock_data.orchestration.workflow_control.production import build_production_service
from stock_data.orchestration.workflow_control.monitoring import MonitoringSnapshotAdapter
from stock_data.orchestration.workflow_control.runner import RunnerAction
from stock_data.orchestration.workflow_control.service import (
    ServiceMode, WorkflowControllerService, WriterLeaseConflict,
)


T0 = datetime(2026, 8, 30, tzinfo=UTC)
TASK = "RQ-20260830T010101-AB12"
SECOND_TASK = "RQ-20260830T010102-CD34"


def _repository(root: Path) -> Path:
    (root / "src" / "stock_data").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# deterministic test repository\n", encoding="utf-8")
    return root


def _stub(root: Path) -> tuple[str, str]:
    script = root / "codex_jsonl_stub.py"
    script.write_text(
        """from __future__ import annotations
import json
from pathlib import Path
import sys
session = '019cafe0-1234-7000-8000-abcdef123456'
args = sys.argv[1:]
kind = 'resume' if len(args) >= 2 and args[:2] == ['exec', 'resume'] else 'launch'
with Path(__file__).with_suffix('.calls').open('a', encoding='utf-8') as stream:
    stream.write(kind + '\\n')
print(json.dumps({'type': 'thread.started', 'thread_id': session}))
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}}))
""",
        encoding="utf-8",
    )
    return sys.executable, str(script)


def _event(event_id: str, task_id: str, to_state: TaskState, *, sequence: int,
           from_state: TaskState) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id, occurred_at=T0 + timedelta(seconds=sequence),
        kind=EventKind.TASK_TRANSITION, source=EventSource.SYSTEM,
        task_id=task_id, from_state=from_state, to_state=to_state,
        priority=Priority.P1, domain="infra", reason_code="CUTOVER_CANARY",
    )


def test_production_composition_launch_replay_resume_settle_and_safe_rollback(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repo")
    command = _stub(tmp_path)
    active = _event("cutover-active", TASK, TaskState.ACTIVE,
                    sequence=1, from_state=TaskState.READY)
    review = _event("cutover-review", TASK, TaskState.REVIEW,
                    sequence=2, from_state=TaskState.ACTIVE)
    done = _event("cutover-done", TASK, TaskState.DONE,
                  sequence=3, from_state=TaskState.REVIEW)

    first = build_production_service(repository, "pm-one", ServiceMode.CANARY, command=command)
    generation_one = first.start()
    contender = build_production_service(
        repository, "pm-contender", ServiceMode.CANARY, command=command
    )
    with pytest.raises(WriterLeaseConflict):
        contender.start()
    receipt = first.canary((active,))
    assert receipt.execution_profile == "codex_read_only"
    assert receipt.workspace_write_enabled is False
    assert receipt.mutation_observed is False
    assert receipt.orca_used is False
    live_activity = {activity.role_kind: activity for activity in first.activities(active_only=True)}
    assert live_activity["project_manager"].state == "idle"
    assert live_activity["domain_lead"].task_id == TASK
    assert live_activity["domain_lead"].state == "working"
    first.close()

    restarted = build_production_service(
        repository, "pm-two", ServiceMode.CANARY, command=command
    )
    assert restarted.start().sequence == generation_one.sequence + 1
    assert restarted.canary((active,)) == receipt
    settled = restarted.canary((review,))
    resumed = restarted.controller.runner.run(
        RunnerAction.RESUME, task_id=TASK, role_key="lead_infra",
        generation=settled.generation_digest, source_event_id="cutover-retry",
        attempt=1, retry_of="dispatch-cutover", retry_provenance="b" * 64,
    )
    assert resumed.status == "resumed"
    resumed_activity = next(
        activity for activity in restarted.activities(active_only=True)
        if activity.role_kind == "domain_lead" and activity.task_id == TASK
    )
    assert resumed_activity.state == "working"
    restarted.canary((done,))
    settled_activity = next(
        activity for activity in restarted.activities()
        if activity.operation_id == resumed_activity.operation_id
    )
    assert settled_activity.state == "stopped" and settled_activity.active is False
    restarted.close()

    production = build_production_service(
        repository, "pm-production", ServiceMode.RUN, command=command
    )
    production_generation = production.start()
    run_receipt = production.run((
        _event("production-active", SECOND_TASK, TaskState.ACTIVE,
               sequence=4, from_state=TaskState.READY),
    ))
    assert run_receipt.execution_profile == "codex_workspace_write"
    assert run_receipt.workspace_write_enabled is True
    assert run_receipt.mutation_observed is None
    assert run_receipt.controller_receipt.production_mutated is False
    assert run_receipt.execution_profile_digest != receipt.execution_profile_digest

    assert production._mutex is not None
    production._mutex.release()  # Simulate a dead process with a durable stale lease.
    production._mutex = None
    calls_path = tmp_path / "codex_jsonl_stub.calls"
    before_rollback = tuple(calls_path.read_text(encoding="utf-8").splitlines())
    status = WorkflowControllerService.rollback_stale(
        production.control_root, owner_id="pm-production",
        generation_sequence=production_generation.sequence,
        generation_digest=production_generation.digest,
    )
    after_rollback = tuple(calls_path.read_text(encoding="utf-8").splitlines())
    assert status.writer_state == "idle"
    assert before_rollback == after_rollback
    assert before_rollback.count("launch") == 2
    assert before_rollback.count("resume") == 3
    rolled_back = MonitoringSnapshotAdapter(repository).snapshot(observed_at=T0 + timedelta(seconds=10))
    assert not [role for role in rolled_back.pm if role.active]
    assert not rolled_back.leads and not rolled_back.workers and not rolled_back.reviewers
    assert not any(warning.code == "DUPLICATE_PM_GENERATION" for warning in rolled_back.warnings)

    crashed = build_production_service(
        repository, "pm-crashed", ServiceMode.CANARY, command=command
    )
    crashed_generation = crashed.start()
    assert crashed._mutex is not None
    crashed._mutex.release()
    crashed._mutex = None
    replacement = build_production_service(
        repository, "pm-recovery", ServiceMode.CANARY, command=command
    )
    replacement_generation = replacement.start()
    recovered = MonitoringSnapshotAdapter(repository).snapshot(observed_at=T0 + timedelta(seconds=11))
    assert replacement_generation.sequence == crashed_generation.sequence + 1
    assert [(role.generation, role.active) for role in recovered.pm] == [(replacement_generation.sequence, True)]
    assert not recovered.leads and not recovered.workers and not recovered.reviewers
    assert not any(warning.code == "DUPLICATE_PM_GENERATION" for warning in recovered.warnings)
    replacement.close()

    source = inspect.getsource(__import__(
        "stock_data.orchestration.workflow_control.production", fromlist=["production"]
    ))
    assert "LocalFake" not in source
    assert "orca" not in source.casefold()
