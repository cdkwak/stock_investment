from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from stock_data.orchestration.workflow_control import (
    EventKind,
    EventSource,
    Priority,
    QueueAdapterError,
    RequestQueueStatusAdapter,
    ReviewOutcome,
    SanitizedJsonlLedger,
    TaskState,
    WorkflowEvent,
    WorkflowEventConflictError,
    WorkflowStateStore,
    build_digest,
    render_digest,
    render_state_projection,
    stable_fingerprint,
)
from stock_data.orchestration.workflow_control.state import WorkflowStateError
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleKind,
    RoleRegistry,
    RoleState,
    StaleRoleGeneration,
)


UTC = timezone.utc
T0 = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
TASK_A = "RQ-20260829T003900-24A5"
TASK_B = "RQ-20260829T003912-025B"


def test_app_coordination_lead_replacement_is_generation_fenced_and_preserves_assignment(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry(tmp_path / "roles.sqlite3")
    registry.claim(
        RoleIdentity(
            "project_manager", RoleKind.PROJECT_MANAGER, "pm-session",
            "transport-disabled", "stock-investment-rev1-main", None,
            "codex-cli-owned-v1",
        ),
        observed_at=T0, lease_until=T0 + timedelta(hours=1),
    )
    original = registry.claim(
        RoleIdentity(
            "lead_infra", RoleKind.DOMAIN_LEAD, "app-session-old",
            "transport-disabled", "stock-investment-rev1-main", None,
            "codex-app-local", active_task_id=TASK_A,
            active_dispatch_id="dispatch-a", parent_role_key="project_manager",
        ),
        observed_at=T0, lease_until=T0 + timedelta(hours=1),
    )
    replacement = registry.replace_app_coordination_lead_session(
        "lead_infra", expected_generation=original.generation,
        expected_session_id="app-session-old", replacement_session_id="app-session-new",
        expected_task_id=TASK_A, expected_dispatch_id="dispatch-a",
        expected_parent_role_key="project_manager",
        expected_runtime_id="codex-app-local",
        expected_worktree_id="stock-investment-rev1-main",
        observed_at=T0 + timedelta(minutes=1), lease_until=T0 + timedelta(hours=2),
    )
    assert replacement.generation == original.generation + 1
    assert replacement.identity.active_task_id == TASK_A
    assert replacement.identity.active_dispatch_id == "dispatch-a"
    assert replacement.identity.parent_role_key == "project_manager"
    assert replacement.identity.codex_session_id == "app-session-new"
    assert replacement.state is RoleState.ACTIVE
    with pytest.raises(StaleRoleGeneration):
        registry.replace_app_coordination_lead_session(
            "lead_infra", expected_generation=original.generation,
            expected_session_id="app-session-old", replacement_session_id="app-session-next",
            expected_task_id=TASK_A, expected_dispatch_id="dispatch-a",
            expected_parent_role_key="project_manager",
            expected_runtime_id="codex-app-local",
            expected_worktree_id="stock-investment-rev1-main",
            observed_at=T0 + timedelta(minutes=2), lease_until=T0 + timedelta(hours=3),
        )
    with sqlite3.connect(registry.path) as connection:
        connection.execute(
            "UPDATE role_registry SET parent_role_key = 'changed_parent' WHERE role_key = 'lead_infra'"
        )
    with pytest.raises(StaleRoleGeneration, match="replacement identity changed"):
        registry.replace_app_coordination_lead_session(
            "lead_infra", expected_generation=replacement.generation,
            expected_session_id="app-session-new", replacement_session_id="app-session-after-parent-race",
            expected_task_id=TASK_A, expected_dispatch_id="dispatch-a",
            expected_parent_role_key="project_manager",
            expected_runtime_id="codex-app-local",
            expected_worktree_id="stock-investment-rev1-main",
            observed_at=T0 + timedelta(minutes=3), lease_until=T0 + timedelta(hours=4),
        )
    assert registry.get("lead_infra").identity.codex_session_id == "app-session-new"
    cli_owned = registry.claim(
        RoleIdentity(
            "lead_cli", RoleKind.DOMAIN_LEAD, "cli-session-old",
            "transport-disabled", "stock-investment-rev1-main", None,
            "codex-cli-owned-v1", active_task_id=TASK_B,
            active_dispatch_id="dispatch-cli", parent_role_key="project_manager",
        ), observed_at=T0, lease_until=T0 + timedelta(hours=1),
    )
    with pytest.raises(StaleRoleGeneration, match="replacement identity changed"):
        registry.replace_app_coordination_lead_session(
            "lead_cli", expected_generation=cli_owned.generation,
            expected_session_id="cli-session-old", replacement_session_id="app-session-newer",
            expected_task_id=TASK_B, expected_dispatch_id="dispatch-cli",
            expected_parent_role_key="project_manager",
            expected_runtime_id="codex-app-local",
            expected_worktree_id="stock-investment-rev1-main",
            observed_at=T0 + timedelta(minutes=2), lease_until=T0 + timedelta(hours=3),
        )


def transition(
    event_id: str,
    at: datetime,
    task_id: str,
    to_state: TaskState,
    *,
    from_state: TaskState | None = None,
) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id,
        occurred_at=at,
        kind=EventKind.TASK_TRANSITION,
        source=EventSource.QUEUE,
        task_id=task_id,
        from_state=from_state,
        to_state=to_state,
        priority=Priority.P1,
        domain="infra",
        reason_code="QUEUE_TRANSITION",
    )


def test_sqlite_record_and_jsonl_replay_are_idempotent_and_order_independent(
    tmp_path: Path,
) -> None:
    store = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    active = transition(
        "event-active", T0 + timedelta(hours=2), TASK_A, TaskState.ACTIVE,
        from_state=TaskState.READY,
    )
    ready = transition("event-ready", T0, TASK_A, TaskState.READY)

    assert store.record(active)
    assert store.record(ready)
    assert not store.record(active)
    assert not store.record(ready)
    assert store.event_count() == 2
    assert store.replay_jsonl() == 0
    assert store.task_snapshots()[0].state is TaskState.ACTIVE
    assert len((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_crash_reconciliation_imports_existing_ledger_line_without_duplication(
    tmp_path: Path,
) -> None:
    event = transition("event-ledger-first", T0, TASK_A, TaskState.READY)
    ledger = SanitizedJsonlLedger(tmp_path / "events.jsonl")
    assert ledger.append(event).appended
    store = WorkflowStateStore(tmp_path / "state.sqlite3", ledger.path)

    assert store.record(event)
    assert store.event_count() == 1
    assert len(ledger.path.read_text(encoding="utf-8").splitlines()) == 1


def test_conflicting_event_id_rolls_back_machine_truth_and_ledger(tmp_path: Path) -> None:
    store = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    original = transition("event-conflict", T0, TASK_A, TaskState.READY)
    conflicting = transition("event-conflict", T0, TASK_A, TaskState.BLOCKED)
    store.record(original)
    before = (tmp_path / "events.jsonl").read_bytes()

    with pytest.raises(WorkflowEventConflictError, match="different machine truth"):
        store.record(conflicting)

    assert store.event_count() == 1
    assert store.task_snapshots()[0].state is TaskState.READY
    assert (tmp_path / "events.jsonl").read_bytes() == before


def test_schema_migration_is_atomic_on_an_incompatible_partial_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE events(event_id, occurred_at)")
        connection.execute(
            "INSERT INTO events(event_id, occurred_at) VALUES (?, ?)",
            ("sentinel", "2026-08-28T12:00:00.000Z"),
        )
    before = database.read_bytes()

    with pytest.raises(WorkflowStateError, match="schema shape is incompatible"):
        WorkflowStateStore(database, tmp_path / "events.jsonl")

    assert database.read_bytes() == before
    assert not (tmp_path / "events.jsonl").exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(events)")
        )
        indexes = tuple(connection.execute("PRAGMA index_list(events)"))
        rows = tuple(connection.execute("SELECT event_id, occurred_at FROM events"))
    assert tables == {"events"}
    assert columns == ("event_id", "occurred_at")
    assert indexes == ()
    assert rows == (("sentinel", "2026-08-28T12:00:00.000Z"),)


def test_schema_migration_creates_complete_usable_v1_machine_truth(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = WorkflowStateStore(database, tmp_path / "events.jsonl")

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT name, version FROM schema_metadata"
        ).fetchall() == [("workflow-control-state", 1)]
        event_columns = tuple(
            (row[1], row[2], row[3], row[4], row[5])
            for row in connection.execute("PRAGMA table_info(events)")
        )
        assert event_columns == (
            ("event_id", "TEXT", 0, None, 1),
            ("occurred_at", "TEXT", 1, None, 0),
            ("kind", "TEXT", 1, None, 0),
            ("source", "TEXT", 1, None, 0),
            ("task_id", "TEXT", 0, None, 0),
            ("payload_json", "TEXT", 1, None, 0),
            ("ledger_written", "INTEGER", 1, "0", 0),
        )
        assert tuple(
            row[2]
            for row in connection.execute(
                "PRAGMA index_info(events_occurred_at_idx)"
            )
        ) == ("occurred_at", "event_id")
        assert tuple(
            (row[2], row[3], row[4], row[5], row[6], row[7])
            for row in connection.execute("PRAGMA foreign_key_list(tasks)")
        ) == (
            ("events", "last_event_id", "event_id", "NO ACTION", "NO ACTION", "NONE"),
        )
        schema_sql = {
            row[0]: "".join(row[1].lower().split())
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "check(version>=1)" in schema_sql["schema_metadata"]
        assert "check(ledger_writtenin(0,1))" in schema_sql["events"]

    event = transition("post-migration", T0, TASK_A, TaskState.READY)
    assert store.record(event)
    assert store.events() == (event,)
    snapshot = store.task_snapshots()[0]
    assert snapshot.task_id == TASK_A
    assert snapshot.state is TaskState.READY
    assert snapshot.last_event_id == event.event_id


def test_schema_migration_rejects_matching_columns_without_required_constraint(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                task_id TEXT,
                payload_json TEXT NOT NULL,
                ledger_written INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    before = database.read_bytes()

    with pytest.raises(WorkflowStateError, match="schema shape is incompatible"):
        WorkflowStateStore(database, tmp_path / "events.jsonl")

    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'schema_metadata'"
        ).fetchone()[0] == 0
        assert "events_occurred_at_idx" not in {
            row[1] for row in connection.execute("PRAGMA index_list(events)")
        }


@pytest.mark.parametrize(
    "mutation",
    (
        "DROP INDEX events_occurred_at_idx",
        "DELETE FROM schema_metadata WHERE name = 'workflow-control-state'",
    ),
)
def test_existing_v1_missing_required_index_or_metadata_fails_without_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / mutation.split()[0].lower() / "state.sqlite3"
    WorkflowStateStore(database, database.with_suffix(".jsonl"))
    with sqlite3.connect(database) as connection:
        connection.execute(mutation)
    before = database.read_bytes()

    with pytest.raises(WorkflowStateError, match="schema|metadata"):
        WorkflowStateStore(database, database.with_suffix(".jsonl"))

    assert database.read_bytes() == before


def test_source_projection_excludes_secrets_transcripts_and_direct_identifiers(
    tmp_path: Path,
) -> None:
    source = {
        "event_id": "DIRECT-EVENT-ID",
        "occurred_at": "2026-08-28T12:00:00.000Z",
        "kind": "SESSION_STARTED",
        "source": "ORCA",
        "session_fingerprint": stable_fingerprint("raw-terminal-session"),
        "reason_code": "SESSION_BOOT",
        "secret": "TOP-SECRET",
        "access_token": "TOKEN-VALUE",
        "account_id": "DIRECT-ACCOUNT",
        "terminal_handle": "term-private",
        "prompt": "private prompt",
        "transcript": "private transcript",
        "payload": {"body": "private response"},
    }
    event = WorkflowEvent.from_source(source)
    store = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    store.record(event)

    line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    persisted = json.loads(line)
    assert set(persisted) == set(event.to_dict())
    assert event.event_id.startswith("source-")
    assert event.event_id != source["event_id"]
    assert all(
        forbidden not in line
        for forbidden in (
            "DIRECT-EVENT-ID", "TOP-SECRET", "TOKEN-VALUE", "DIRECT-ACCOUNT",
            "term-private",
            "private prompt", "private transcript", "private response",
            "raw-terminal-session",
        )
    )


def test_source_projection_ids_are_idempotent_and_digest_collisions_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "event_id": "raw-event-one",
        "occurred_at": "2026-08-28T12:00:00.000999Z",
        "kind": "SESSION_STARTED",
        "source": "ORCA",
        "session_fingerprint": stable_fingerprint("session-one"),
        "reason_code": "SESSION_BOOT",
        "prompt": "first forbidden value",
    }
    first = WorkflowEvent.from_source(source)
    replay = WorkflowEvent.from_source(
        {**source, "event_id": "raw-event-two", "prompt": "second forbidden value"}
    )
    assert first == replay
    assert first.occurred_at == T0
    idempotent_store = WorkflowStateStore(
        tmp_path / "idempotent" / "state.sqlite3",
        tmp_path / "idempotent" / "events.jsonl",
    )
    assert idempotent_store.record(first)
    assert not idempotent_store.record(replay)

    legacy_payload = first.to_dict()
    legacy_payload["event_id"] = "legacy-event-id"
    assert WorkflowEvent.from_dict(legacy_payload).event_id == "legacy-event-id"

    monkeypatch.setattr(
        "stock_data.orchestration.workflow_control.contracts.stable_fingerprint",
        lambda _value: "0" * 64,
    )
    collision_a = WorkflowEvent.from_source(source)
    collision_b = WorkflowEvent.from_source({**source, "reason_code": "SESSION_RESTART"})
    assert collision_a.event_id == collision_b.event_id
    store = WorkflowStateStore(
        tmp_path / "collision" / "state.sqlite3",
        tmp_path / "collision" / "events.jsonl",
    )
    assert store.record(collision_a)
    with pytest.raises(WorkflowEventConflictError, match="different machine truth"):
        store.record(collision_b)


def test_state_markdown_projection_is_deterministic(tmp_path: Path) -> None:
    first = WorkflowStateStore(tmp_path / "first.sqlite3", tmp_path / "first.jsonl")
    second = WorkflowStateStore(tmp_path / "second.sqlite3", tmp_path / "second.jsonl")
    events = (
        transition("b-active", T0 + timedelta(hours=2), TASK_B, TaskState.ACTIVE),
        transition("a-ready", T0 + timedelta(hours=1), TASK_A, TaskState.READY),
    )
    first.replay(events)
    second.replay(reversed(events))

    rendered = render_state_projection(first.task_snapshots(), as_of=T0 + timedelta(hours=3))
    assert rendered == render_state_projection(
        second.task_snapshots(), as_of=T0 + timedelta(hours=3)
    )
    assert rendered.index(TASK_A) < rendered.index(TASK_B)
    assert "workflow-control-state-projection/v1" in rendered


def test_overnight_digest_metrics_and_markdown_are_deterministic() -> None:
    recurrence = stable_fingerprint("same-escalation-class")
    session = stable_fingerprint("one-session")
    events = [
        transition("a-ready", T0, TASK_A, TaskState.READY),
        transition(
            "a-active", T0 + timedelta(minutes=5), TASK_A, TaskState.ACTIVE,
            from_state=TaskState.READY,
        ),
        transition(
            "a-done", T0 + timedelta(minutes=20), TASK_A, TaskState.DONE,
            from_state=TaskState.ACTIVE,
        ),
        WorkflowEvent(
            event_id="review-failed", occurred_at=T0 + timedelta(minutes=6),
            kind=EventKind.REVIEW_RESULT, source=EventSource.QUEUE,
            task_id=TASK_A, outcome=ReviewOutcome.FAILED,
            reason_code="REVIEW_DECISION",
        ),
        WorkflowEvent(
            event_id="rework", occurred_at=T0 + timedelta(minutes=7),
            kind=EventKind.REWORK_REQUESTED, source=EventSource.QUEUE,
            task_id=TASK_A, reason_code="REVIEW_REWORK",
        ),
        WorkflowEvent(
            event_id="escalation-1", occurred_at=T0 + timedelta(minutes=8),
            kind=EventKind.ESCALATION, source=EventSource.ORCA,
            recurrence_fingerprint=recurrence, reason_code="WORKER_ESCALATION",
        ),
        WorkflowEvent(
            event_id="escalation-2", occurred_at=T0 + timedelta(minutes=9),
            kind=EventKind.ESCALATION, source=EventSource.ORCA,
            recurrence_fingerprint=recurrence, reason_code="WORKER_ESCALATION",
        ),
        WorkflowEvent(
            event_id="queue-idle", occurred_at=T0 + timedelta(minutes=10),
            kind=EventKind.QUEUE_SNAPSHOT, source=EventSource.QUEUE,
            runnable_count=3, active_worker_count=0,
            reason_code="QUEUE_STATUS_COMPACT",
        ),
        WorkflowEvent(
            event_id="session-start", occurred_at=T0 + timedelta(minutes=11),
            kind=EventKind.SESSION_STARTED, source=EventSource.ORCA,
            session_fingerprint=session, reason_code="SESSION_BOOT",
        ),
    ]
    kwargs = {"window_start": T0, "window_end": T0 + timedelta(hours=8)}
    digest = build_digest(events, **kwargs)
    rendered = render_digest(digest)

    assert digest.metrics.throughput == 1
    assert digest.metrics.wait_count == 1
    assert digest.metrics.mean_wait_ms == 300_000
    assert digest.metrics.review_failure_count == 1
    assert digest.metrics.rework_count == 1
    assert digest.metrics.repeated_escalation_groups == 1
    assert digest.metrics.repeated_escalation_count == 1
    assert digest.metrics.runnable_idle_observations == 1
    assert digest.metrics.session_start_count == 1
    assert rendered == render_digest(build_digest(reversed(events), **kwargs))
    assert "workflow-control-digest/v1" in rendered


def test_digest_wait_uses_canonical_milliseconds_before_and_after_persistence(
    tmp_path: Path,
) -> None:
    raw_ready_at = T0 + timedelta(microseconds=999)
    raw_active_at = T0 + timedelta(seconds=1, microseconds=998)
    assert int((raw_active_at - raw_ready_at).total_seconds() * 1000) == 999
    ready = transition("sub-ms-ready", raw_ready_at, TASK_A, TaskState.READY)
    active = transition(
        "sub-ms-active",
        raw_active_at,
        TASK_A,
        TaskState.ACTIVE,
        from_state=TaskState.READY,
    )
    kwargs = {"window_start": T0, "window_end": T0 + timedelta(hours=1)}
    before = build_digest((ready, active), **kwargs)

    store = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    assert store.replay((ready, active)) == 2
    after = build_digest(store.events(), **kwargs)

    assert before == after
    assert before.metrics.wait_count == 1
    assert before.metrics.mean_wait_ms == 1_000
    assert before.metrics.max_wait_ms == 1_000


def test_queue_adapter_invokes_only_read_only_compact_status(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "request_queue.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "new=2 waiting=1 ready=3 active=1 review=4 blocked=5 done=6 compacted=7\n"
                "active=P1-RQ-20260829T003900-24A5-sanitized-workflow\n"
            ),
            stderr="",
        )

    snapshot = RequestQueueStatusAdapter(tmp_path, runner=runner).read_snapshot(
        observed_at=T0
    )

    command, kwargs = calls[0]
    assert command[-2:] == ["status", "--compact"]
    assert not ({"claim", "checkpoint", "submit", "review-pass"} & set(command))
    assert kwargs["check"] is False and kwargs["capture_output"] is True
    assert snapshot.count("ready") == 3
    assert snapshot.active_task_ids == (TASK_A,)
    observation = snapshot.to_event()
    assert observation.runnable_count == 3
    assert observation.active_worker_count == 1


def test_queue_adapter_failure_does_not_expose_subprocess_stderr(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "request_queue.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture", encoding="utf-8")

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 2, stdout="", stderr="access_token=DO-NOT-LEAK"
        )

    with pytest.raises(QueueAdapterError) as captured:
        RequestQueueStatusAdapter(tmp_path, runner=runner).read_snapshot(observed_at=T0)

    assert "DO-NOT-LEAK" not in str(captured.value)


def test_queue_adapter_reads_bounded_human_title_from_current_document(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "request_queue.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture", encoding="utf-8")
    task_dir = (
        tmp_path / "artifacts" / "request_queue" / "active"
        / f"P1-{TASK_A}-operations-dashboard"
    )
    task_dir.mkdir(parents=True)
    (task_dir / "META.json").write_text(json.dumps({
        "id": TASK_A,
        "state": "active",
        "owner": "gui_lead",
        "lead_owner": "gui_lead",
        "domain": "gui",
        "title": "실제 세션과 작업 문서 상태를 구분합니다",
        "updated_at": T0.isoformat(),
    }), encoding="utf-8")

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0,
            stdout=(
                "new=0 waiting=0 ready=0 active=1 review=0 blocked=0 done=0 compacted=0\n"
                f"active=P1-{TASK_A}-operations-dashboard\n"
            ),
            stderr="",
        )

    snapshot = RequestQueueStatusAdapter(tmp_path, runner=runner).read_snapshot(
        observed_at=T0,
    )

    assert snapshot.current_tasks[0].title == "실제 세션과 작업 문서 상태를 구분합니다"
