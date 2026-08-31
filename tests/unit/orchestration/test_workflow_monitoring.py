from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
from pathlib import Path

from stock_data.orchestration.workflow_control.monitoring import MonitoringSnapshotAdapter
from stock_data.orchestration.workflow_control.registry import RoleIdentity, RoleKind, RoleRegistry
from stock_data.orchestration.workflow_control.state import WorkflowStateStore
from stock_data.orchestration.workflow_control.contracts import EventKind, EventSource, TaskState, WorkflowEvent
from stock_data.orchestration.workflow_control.queue_adapter import (
    QueueSnapshot,
    QueueTaskOwnership,
)
from stock_data.orchestration.workflow_control.service import OperationActivity


class FakeQueue:
    def __init__(self, now):
        self.now = now
    def read_snapshot(self, *, observed_at):
        return QueueSnapshot(observed_at, (("new", 0), ("waiting", 0), ("ready", 1), ("active", 1), ("review", 0), ("blocked", 0), ("done", 0)), ("RQ-20260830T120000-AB12",), 0)


def _sources(tmp_path: Path, now: datetime):
    role = tmp_path / "roles.sqlite3"
    registry = RoleRegistry(role)
    registry.claim(RoleIdentity("project_manager", RoleKind.PROJECT_MANAGER, "pm-session", "orca-run", "worktree", None, "runtime", "RQ-20260830T120000-AB12", "dispatch-pm"), observed_at=now - timedelta(seconds=5), lease_until=now + timedelta(minutes=1))
    registry.claim(RoleIdentity("gui_lead", RoleKind.DOMAIN_LEAD, "lead-session", "orca-run", "worktree", None, "runtime"), observed_at=now - timedelta(seconds=5), lease_until=now + timedelta(minutes=1))
    workflow = tmp_path / "workflow.sqlite3"
    state = WorkflowStateStore(workflow, tmp_path / "unused-events.jsonl")
    state.record(WorkflowEvent("task-start", now, EventKind.TASK_TRANSITION, EventSource.SYSTEM,
                               task_id="RQ-20260830T120000-AB12", to_state=TaskState.ACTIVE))
    events = tmp_path / "events.jsonl"
    events.write_text('{"event_id":"e1","occurred_at":"'+now.isoformat()+'","kind":"TASK_TRANSITION","source":"SYSTEM","task_id":"RQ-20260830T120000-AB12","reason_code":"STARTED"}\n', encoding="utf-8")
    return workflow, role, events


def _activity_source(tmp_path: Path, now: datetime, *records: tuple[str, str, str | None, str, bool]) -> Path:
    service = tmp_path / "workflow_controller_service.sqlite3"
    with sqlite3.connect(service) as db:
        db.execute("CREATE TABLE operation_activity (operation_id TEXT PRIMARY KEY, role_kind TEXT, session_fingerprint TEXT, task_id TEXT, state TEXT, heartbeat_at TEXT, active INTEGER, activity_digest TEXT)")
        for operation_id, role_kind, task_id, state, active in records:
            activity = OperationActivity(
                operation_id, role_kind, hashlib.sha256(operation_id.encode("utf-8")).hexdigest(), task_id,
                state, now, active,
            )
            payload = activity.to_dict()
            db.execute(
                "INSERT INTO operation_activity VALUES (?,?,?,?,?,?,?,?)",
                (payload["operation_id"], payload["role_kind"], payload["session_fingerprint"], payload["task_id"], payload["state"], payload["heartbeat_at"], int(payload["active"]), payload["activity_digest"]),
            )
    return service


def _canonical_activity_source(
    root: Path, now: datetime, *, live_generation: int | None,
    records: tuple[tuple[str, str, str | None, str, bool, int], ...],
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    service = root / "workflow_controller_service.sqlite3"
    with sqlite3.connect(service) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS operation_activity (operation_id TEXT PRIMARY KEY, role_kind TEXT, "
            "session_fingerprint TEXT, task_id TEXT, state TEXT, heartbeat_at TEXT, active INTEGER, "
            "activity_digest TEXT, generation_sequence INTEGER NOT NULL, generation_digest TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS writer_lease (service_key TEXT PRIMARY KEY, owner_id TEXT, "
            "generation_sequence INTEGER, generation_digest TEXT, acquired_at TEXT)"
        )
        db.execute("DELETE FROM writer_lease")
        db.execute("DELETE FROM operation_activity")
        digests = {generation: hashlib.sha256(f"generation-{generation}".encode()).hexdigest() for *_, generation in records}
        if live_generation is not None:
            db.execute("INSERT INTO writer_lease VALUES (?,?,?,?,?)", (
                "python_pm", "pm-live", live_generation, digests[live_generation], now.isoformat(),
            ))
        for operation_id, role_kind, task_id, state, active, generation in records:
            activity = OperationActivity(
                operation_id, role_kind, hashlib.sha256(operation_id.encode()).hexdigest(), task_id,
                state, now + timedelta(seconds=generation), active,
                generation_sequence=generation, generation_digest=digests[generation],
            )
            payload = activity.to_dict()
            db.execute(
                "INSERT INTO operation_activity VALUES (?,?,?,?,?,?,?,?,?,?)",
                (payload["operation_id"], payload["role_kind"], payload["session_fingerprint"], payload["task_id"],
                 payload["state"], payload["heartbeat_at"], int(payload["active"]), payload["activity_digest"],
                 payload["generation_sequence"], payload["generation_digest"]),
            )
    return service


def test_snapshot_reports_roles_queue_tasks_and_events(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    snapshot = MonitoringSnapshotAdapter(workflow_db=workflow, role_db=role, event_log=events, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    assert len(snapshot.pm) == len(snapshot.leads) == 1
    assert snapshot.queue.count("active") == 1
    assert snapshot.tasks[0].task_id.endswith("AB12")
    assert snapshot.events[0].reason_code == "STARTED"


def test_warning_reports_stale_heartbeat_and_missing_sources(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now - timedelta(minutes=10))
    snapshot = MonitoringSnapshotAdapter(workflow_db=workflow, role_db=role, event_log=events, queue_adapter=FakeQueue(now), stale_after_seconds=30).snapshot(observed_at=now)
    assert any(w.code == "STALE_HEARTBEAT" for w in snapshot.warnings)
    missing = MonitoringSnapshotAdapter(workflow_db=tmp_path / "none.db", role_db=tmp_path / "none2.db", event_log=tmp_path / "none.jsonl", queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    assert any(w.code.endswith("MISSING") for w in missing.warnings)


def test_valid_lead_worker_reviewer_membership_is_not_an_ownership_conflict(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    service = _activity_source(
        tmp_path, now,
        ("op-pm-one", "project_manager", None, "working", True),
        ("op-lead-one", "domain_lead", "RQ-20260830T120000-AB12", "working", True),
        ("op-worker-one", "worker", "RQ-20260830T120000-AB12", "working", True),
        ("op-reviewer-one", "reviewer", "RQ-20260830T120000-AB12", "reviewing", True),
    )
    snapshot = MonitoringSnapshotAdapter(workflow_db=workflow, event_log=events, service_db=service, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    codes = {warning.code for warning in snapshot.warnings}
    assert "DUPLICATE_PM_GENERATION" not in codes
    assert "OWNERSHIP_CONFLICT" not in codes


def test_two_live_production_pm_states_and_duplicate_leads_are_warned(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    service = _activity_source(
        tmp_path, now,
        ("op-pm-one", "project_manager", None, "working", True),
        ("op-pm-two", "project_manager", None, "idle", True),
        ("op-lead-one", "domain_lead", "RQ-20260830T120000-AB12", "working", True),
        ("op-lead-two", "domain_lead", "RQ-20260830T120000-AB12", "reviewing", True),
    )
    snapshot = MonitoringSnapshotAdapter(workflow_db=workflow, event_log=events, service_db=service, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    codes = {warning.code for warning in snapshot.warnings}
    assert "DUPLICATE_PM_GENERATION" in codes
    assert "OWNERSHIP_CONFLICT" in codes


def test_worker_membership_without_an_exclusive_lead_is_unowned(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    service = _activity_source(
        tmp_path, now,
        ("op-pm-one", "project_manager", None, "working", True),
        ("op-worker-one", "worker", "RQ-20260830T120000-AB12", "working", True),
        ("op-reviewer-one", "reviewer", "RQ-20260830T120000-AB12", "reviewing", True),
    )
    snapshot = MonitoringSnapshotAdapter(
        workflow_db=workflow, event_log=events, service_db=service,
        queue_adapter=FakeQueue(now),
    ).snapshot(observed_at=now)
    assert any(
        warning.code == "OWNERSHIP_CONFLICT" and "없습니다" in warning.message
        for warning in snapshot.warnings
    )


def test_queue_ownership_does_not_invent_pm_lead_reviewer_or_worker(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, _role, events = _sources(tmp_path, now)
    service = _activity_source(
        tmp_path, now,
        ("op-pm-idle", "project_manager", None, "idle", True),
    )
    task_id = "RQ-20260830T120000-AB12"
    queue = QueueSnapshot(
        now,
        (("new", 0), ("waiting", 0), ("ready", 0), ("active", 1),
         ("review", 0), ("blocked", 0), ("done", 0)),
        (task_id,), 0,
        (QueueTaskOwnership(
            task_id, "active", "queue_owner", "queue_lead", None, "gui", now,
            "운영 화면의 상태 표시를 바로잡습니다",
        ),),
    )

    class OwnedQueue:
        def read_snapshot(self, *, observed_at):
            return QueueSnapshot(
                observed_at, queue.state_counts, queue.active_task_ids,
                queue.compacted_count, queue.current_tasks,
            )

    snapshot = MonitoringSnapshotAdapter(
        workflow_db=workflow, event_log=events, service_db=service,
        queue_adapter=OwnedQueue(),
    ).snapshot(observed_at=now)

    assert len(snapshot.pm) == 1 and snapshot.pm[0].state == "idle"
    assert snapshot.leads == ()
    assert snapshot.reviewers == ()
    assert snapshot.workers == ()
    assert snapshot.tasks[0].owner == "queue_owner"
    assert snapshot.tasks[0].human_title == "운영 화면의 상태 표시를 바로잡습니다"
    assert any(warning.code == "LEAD_SESSION_MISSING" for warning in snapshot.warnings)


def test_service_activity_roles_are_read_only(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    service = tmp_path / "workflow_controller_service.sqlite3"
    with sqlite3.connect(service) as db:
        db.execute("CREATE TABLE operation_activity (operation_id TEXT PRIMARY KEY, role_kind TEXT, session_fingerprint TEXT, task_id TEXT, state TEXT, heartbeat_at TEXT, active INTEGER, activity_digest TEXT)")
        db.execute("INSERT INTO operation_activity VALUES (?,?,?,?,?,?,?,?)", ("op-worker", "worker", "a" * 64, "RQ-20260830T120000-AB12", "working", now.isoformat(), 1, "b" * 64))
        db.execute("INSERT INTO operation_activity VALUES (?,?,?,?,?,?,?,?)", ("op-reviewer", "reviewer", "c" * 64, None, "idle", now.isoformat(), 0, "d" * 64))
        db.execute("INSERT INTO operation_activity VALUES (?,?,?,?,?,?,?,?)", ("op-pm", "project_manager", "e" * 64, None, "working", now.isoformat(), 1, "f" * 64))
    before = service.read_bytes()
    snapshot = MonitoringSnapshotAdapter(workflow_db=workflow, role_db=role, event_log=events, service_db=service, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    assert len(snapshot.workers) == 1 and len(snapshot.reviewers) == 1
    assert not any(w.code == "DUPLICATE_PM_GENERATION" for w in snapshot.warnings)
    assert any(w.code == "OWNERSHIP_CONFLICT" and "없습니다" in w.message for w in snapshot.warnings)
    assert service.read_bytes() == before


def test_default_adapter_uses_canonical_python_pm_paths_not_legacy_registry(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    canonical = tmp_path / "data" / "runtime" / "python_pm"
    canonical.mkdir(parents=True)
    state = WorkflowStateStore(canonical / "workflow_state.sqlite3", canonical / "workflow_events.jsonl")
    state.record(WorkflowEvent(
        "canonical-start", now, EventKind.TASK_TRANSITION, EventSource.SYSTEM,
        task_id="RQ-20260830T120000-AB12", to_state=TaskState.ACTIVE,
    ))
    _activity_source(
        canonical, now,
        ("op-canonical-pm", "project_manager", None, "working", True),
        ("op-canonical-lead", "domain_lead", "RQ-20260830T120000-AB12", "working", True),
    )
    canonical_registry = RoleRegistry(canonical / "role_registry.sqlite3")
    canonical_registry.claim(
        RoleIdentity(
            "project_manager", RoleKind.PROJECT_MANAGER, "pm-session",
            "python-only", "workspace", None, "runtime",
        ),
        observed_at=now, lease_until=now + timedelta(minutes=1),
    )
    legacy = tmp_path / ".codex" / "workflow"
    legacy.mkdir(parents=True)
    (legacy / "events.jsonl").write_text("not used\n", encoding="utf-8")
    snapshot = MonitoringSnapshotAdapter(tmp_path, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    assert snapshot.tasks[0].task_id == "RQ-20260830T120000-AB12"
    assert snapshot.events[0].event_id == "canonical-start"
    assert {item.role_kind for item in (*snapshot.pm, *snapshot.leads)} == {"project_manager", "domain_lead"}
    assert not any(warning.code == "ROLE_SOURCE_MISSING" for warning in snapshot.warnings)


def test_default_adapter_projects_only_live_generation_then_one_latest_settled_pm(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    canonical = tmp_path / "data" / "runtime" / "python_pm"
    state = WorkflowStateStore(canonical / "workflow_state.sqlite3", canonical / "workflow_events.jsonl")
    state.record(WorkflowEvent(
        "generation-truth", now, EventKind.TASK_TRANSITION, EventSource.SYSTEM,
        task_id="RQ-20260830T120000-AB12", to_state=TaskState.ACTIVE,
    ))
    _canonical_activity_source(canonical, now, live_generation=2, records=(
        ("pm-one", "project_manager", None, "stopped", False, 1),
        ("lead-one", "domain_lead", "RQ-20260830T120000-AB12", "stopped", False, 1),
        ("pm-two", "project_manager", None, "working", True, 2),
        ("lead-two", "domain_lead", "RQ-20260830T120000-AB12", "working", True, 2),
    ))
    live = MonitoringSnapshotAdapter(tmp_path, queue_adapter=FakeQueue(now)).snapshot(observed_at=now + timedelta(seconds=3))
    assert [(role.generation, role.active) for role in live.pm] == [(2, True)]
    assert [(role.generation, role.active_task_id) for role in live.leads] == [(2, "RQ-20260830T120000-AB12")]
    assert not any(warning.code == "DUPLICATE_PM_GENERATION" for warning in live.warnings)

    _canonical_activity_source(canonical, now, live_generation=None, records=(
        ("pm-one", "project_manager", None, "stopped", False, 1),
        ("lead-one", "domain_lead", "RQ-20260830T120000-AB12", "stopped", False, 1),
        ("pm-two", "project_manager", None, "stopped", False, 2),
        ("lead-two", "domain_lead", "RQ-20260830T120000-AB12", "stopped", False, 2),
    ))
    settled = MonitoringSnapshotAdapter(tmp_path, queue_adapter=FakeQueue(now)).snapshot(observed_at=now + timedelta(seconds=3))
    assert [(role.generation, role.state, role.active) for role in settled.pm] == [(2, "stopped", False)]
    assert not settled.leads and not settled.workers and not settled.reviewers


def test_settled_operation_history_does_not_hide_registered_pm_session(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    service = _activity_source(
        tmp_path, now,
        ("old-pm-operation", "project_manager", None, "stopped", False),
    )

    snapshot = MonitoringSnapshotAdapter(
        workflow_db=workflow, role_db=role, event_log=events,
        service_db=service, queue_adapter=FakeQueue(now),
    ).snapshot(observed_at=now)

    assert [(item.role_key, item.active) for item in snapshot.pm] == [
        ("project_manager", True),
    ]


def test_canonical_projection_cap_never_trims_the_live_pm(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    canonical = tmp_path / "data" / "runtime" / "python_pm"
    state = WorkflowStateStore(canonical / "workflow_state.sqlite3", canonical / "workflow_events.jsonl")
    state.record(WorkflowEvent(
        "bounded-roster", now, EventKind.TASK_TRANSITION, EventSource.SYSTEM,
        task_id="RQ-20260830T120000-AB12", to_state=TaskState.ACTIVE,
    ))
    records = [
        ("pm-live", "project_manager", None, "idle", True, 7),
    ]
    records.extend(
        (
            f"lead-{index}", "domain_lead",
            f"RQ-20260830T12{index:04d}-A{index:03d}", "working", True, 7,
        )
        for index in range(60)
    )
    _canonical_activity_source(
        canonical, now, live_generation=7, records=tuple(records),
    )

    snapshot = MonitoringSnapshotAdapter(
        tmp_path, queue_adapter=FakeQueue(now),
    ).snapshot(observed_at=now + timedelta(seconds=10))

    assert [(role.generation, role.active) for role in snapshot.pm] == [(7, True)]
    assert len((*snapshot.pm, *snapshot.leads, *snapshot.workers, *snapshot.reviewers)) == 48


def test_warning_reports_unreadable_sources(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    workflow.write_bytes(b"not sqlite")
    events.write_text("not json\n", encoding="utf-8")
    snapshot = MonitoringSnapshotAdapter(workflow_db=workflow, role_db=role, event_log=events, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    codes = {warning.code for warning in snapshot.warnings}
    assert "WORKFLOW_SOURCE_UNREADABLE" in codes
    assert "EVENT_SOURCE_UNREADABLE" in codes


def test_read_only_sources_are_not_modified(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    before = {p: p.read_bytes() for p in (workflow, role, events)}
    MonitoringSnapshotAdapter(workflow_db=workflow, role_db=role, event_log=events, queue_adapter=FakeQueue(now)).snapshot(observed_at=now)
    assert before == {p: p.read_bytes() for p in (workflow, role, events)}


def test_snapshot_adds_safe_human_defaults_without_inventing_execution_roles(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    workflow, role, events = _sources(tmp_path, now)
    snapshot = MonitoringSnapshotAdapter(
        workflow_db=workflow, role_db=role, event_log=events,
        queue_adapter=FakeQueue(now),
    ).snapshot(observed_at=now)
    assert snapshot.pm_current_decision is None
    assert snapshot.pm_next_action is None
    assert snapshot.goal_summary is None
    assert snapshot.queue_action
    assert snapshot.proposal_state
    assert all(task.human_title and task.summary for task in snapshot.tasks)
    assert all(task.fix_count >= 0 for task in snapshot.tasks)
    assert all(event.human_message for event in snapshot.events)
    # Display enrichment reads existing state only; no absent worker is made up.
    assert not snapshot.workers


def test_queue_document_update_is_not_reported_as_agent_activity(tmp_path):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    task_id = "RQ-20260830T120000-AB12"
    queue = QueueSnapshot(
        now,
        (("new", 0), ("waiting", 0), ("ready", 0), ("active", 1),
         ("review", 0), ("blocked", 0), ("done", 0)),
        (task_id,), 0,
        (QueueTaskOwnership(
            task_id, "active", "queue_owner", "queue_lead", None, "gui", now,
            "문서만 있고 실행 세션은 없는 작업",
        ),),
    )

    class DocumentOnlyQueue:
        def read_snapshot(self, *, observed_at):
            return QueueSnapshot(
                observed_at, queue.state_counts, queue.active_task_ids,
                queue.compacted_count, queue.current_tasks,
            )

    snapshot = MonitoringSnapshotAdapter(
        workflow_db=tmp_path / "missing-workflow.sqlite3",
        role_db=tmp_path / "missing-roles.sqlite3",
        event_log=tmp_path / "missing-events.jsonl",
        service_db=tmp_path / "missing-service.sqlite3",
        queue_adapter=DocumentOnlyQueue(),
    ).snapshot(observed_at=now)

    assert snapshot.pm == snapshot.leads == snapshot.workers == snapshot.reviewers == ()
    assert snapshot.tasks[0].last_activity is None
    assert snapshot.tasks[0].updated_at == now
    assert {warning.code for warning in snapshot.warnings} >= {
        "PM_SESSION_MISSING", "LEAD_SESSION_MISSING",
    }
