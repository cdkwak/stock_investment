from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from issue_state.model import (
    IssueEvent, aggregate_events, release_suppression, suppress_issue,
)


SCRIPT = Path("scripts/maintenance/sync_issue_state.py").resolve()
QUEUE_SCRIPT = Path("scripts/request_queue.py").resolve()
PRODUCTION_POLICY = Path("artifacts/issue_state/escalation_policy.json").resolve()


def load_module():
    spec = importlib.util.spec_from_file_location("sync_issue_state", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_event(event_id: str, occurred_at: str) -> dict[str, object]:
    return {
        "schema": "runtime-diagnostic/v1", "event_id": event_id,
        "occurred_at": occurred_at, "domain": "GUI", "kind": "UNHANDLED",
        "session_id": "b" * 32, "run_id": None, "code": "GUI_LOAD_FAILED",
        "stage": "DASHBOARD", "exception_classes": ["RuntimeError"],
        "frames": ["src/app.py:10"], "artifacts": [],
    }


def policy() -> dict[str, object]:
    return {
        "schema": "escalation-policy/v1", "policies": [{
            "policy_id": "GUI_REPEAT", "revision": 1, "enabled": True,
            "effective_from": "2026-08-25T00:00:00Z", "effective_until": None,
            "fingerprint": None,
            "stable_code": "GUI_LOAD_FAILED", "target_kind": "COMPONENT",
            "target_id": "gui:dashboard", "minimum_severity": "ERROR",
            "all_of": [{"kind": "occurrence_count", "operator": "gte", "value": 2}],
            "discovery_rate": {"max_count": 1, "window_seconds": 86400},
            "cooldown_seconds": 3600,
            "queue_fingerprint": "issue-state:{fingerprint}",
            "discovery_template": {
                "title": "Repeated local GUI load failure",
                "symptom": "Typed local GUI failure repeated",
                "impact": "Dashboard status may be unavailable",
                "suspected_scope": "src/stock_data/gui",
                "reproduce": "Inspect sanitized local evidence",
                "priority_hint": "P1",
            },
        }],
    }


def issue_event(identity: str, occurred_at: str, outcome: str = "FAILURE") -> IssueEvent:
    return IssueEvent(
        source_schema="runtime-diagnostic/v1", source_event_id=identity,
        occurred_at=occurred_at, stable_code="GUI_LOAD_FAILED", domain="GUI",
        target_kind="COMPONENT", target_id="gui:dashboard", outcome=outcome,
        severity="ERROR" if outcome == "FAILURE" else "INFO",
        retryability="NOT_RETRYABLE",
    )


def test_production_policy_requires_two_post_activation_scheduler_failures(
    tmp_path: Path,
) -> None:
    module = load_module()
    rows = module.load_policies(PRODUCTION_POLICY)
    assert len(rows) == 3
    row = next(
        item for item in rows
        if item["policy_id"] == "KR_MARKET_0910_REPEATED_FAILURE"
    )
    assert row["policy_id"] == "KR_MARKET_0910_REPEATED_FAILURE"
    assert row["stable_code"] == "SCHEDULER_OCCURRENCE_FAILURE"
    assert row["target_kind"] == "SCHEDULER_LANE"
    assert row["target_id"] == "kr_market_daily:0910"
    assert row["all_of"] == [
        {"kind": "occurrence_count", "operator": "gte", "value": 2},
    ]

    def scheduler_event(identity: str, occurred_at: str, outcome: str) -> IssueEvent:
        return IssueEvent(
            source_schema="scheduler-occurrence/v1", source_event_id=identity,
            occurred_at=occurred_at, stable_code="SCHEDULER_OCCURRENCE_FAILURE",
            domain="DATA", target_kind="SCHEDULER_LANE",
            target_id="kr_market_daily:0910", outcome=outcome,
            severity="ERROR" if outcome == "FAILURE" else "INFO",
            retryability="AUTHORIZED_OPERATION_REQUIRED",
            evidence=(
                "data/state/provider_scheduler/kr_market_daily_occurrences/"
                f"{identity}.json@sha256:{identity}",
            ),
        )

    project = tmp_path / "project"
    project.mkdir(parents=True)
    subprocess.run(
        [sys.executable, str(QUEUE_SCRIPT), "--root", str(project / "artifacts/request_queue"), "init"],
        cwd=project, check=True, capture_output=True, text=True,
    )

    first = scheduler_event("a" * 64, "2026-08-26T04:19:00Z", "FAILURE")
    records = aggregate_events((), (first,))
    assert len(records) == 1
    assert module.matching_policy(records[0], rows) is None

    second = scheduler_event("c" * 64, "2026-08-26T04:20:00Z", "FAILURE")
    records = aggregate_events(records, (second,))
    assert records[0].occurrence_count == 2
    assert module.matching_policy(records[0], rows) is row
    decision, queue_fingerprint, recurrence = module.queue_decision(
        records[0], row, project / "artifacts/request_queue",
    )
    assert decision == "DISCOVER"
    assert queue_fingerprint is not None
    assert recurrence is None
    discovered = module.discover(project, records[0], row, queue_fingerprint, recurrence)
    assert discovered.startswith("RQ-")
    assert len(list((project / "artifacts/request_queue/inbox/new").glob("*/META.json"))) == 1
    assert module.queue_decision(
        records[0], row, project / "artifacts/request_queue",
    )[0] == "DUPLICATE_ACTIVE"

    suppressed = suppress_issue(
        records[0], suppression_id="scheduled-maintenance",
        reason_code="SCHEDULED_MAINTENANCE", actor="local.operator",
        started_at="2026-08-26T04:20:30Z", expires_at="2026-08-26T05:20:30Z",
        evidence="artifacts/issue_state/suppressions/scheduled.json@sha256:" + "e" * 64,
    )
    assert module.matching_policy(suppressed, rows) is None

    success = scheduler_event("d" * 64, "2026-08-26T04:21:00Z", "SUCCESS")
    records = aggregate_events(records, (success,))
    assert records[0].state == "RECOVERED"
    assert module.matching_policy(records[0], rows) is None


def test_production_policy_escalates_only_repeated_health_failures_for_managed_datasets(
) -> None:
    module = load_module()
    rows = module.load_policies(PRODUCTION_POLICY)
    stale_policy = next(
        item for item in rows
        if item["policy_id"] == "MANAGED_DATASET_REPEATED_STALE"
    )

    def health_event(identity: str, target: str, occurred_at: str) -> IssueEvent:
        return IssueEvent(
            source_schema="health-artifact/v2", source_event_id=identity,
            occurred_at=occurred_at, stable_code="HEALTH_STALE", domain="DATA",
            target_kind="DATASET", target_id=target, outcome="FAILURE",
            severity="ERROR", retryability="NOT_RETRYABLE", freshness="STALE",
        )

    managed = aggregate_events((), (
        health_event("managed-first", "fred_vix_daily", "2026-08-26T13:21:00Z"),
        health_event("managed-second", "fred_vix_daily", "2026-08-26T13:22:00Z"),
    ))[0]
    unmanaged = aggregate_events((), (
        health_event(
            "manual-first", "bok_ecos_kr_treasury_yield_source_observation",
            "2026-08-26T13:21:00Z",
        ),
        health_event(
            "manual-second", "bok_ecos_kr_treasury_yield_source_observation",
            "2026-08-26T13:22:00Z",
        ),
    ))[0]

    assert module.matching_policy(managed, rows) is stale_policy
    assert module.matching_policy(unmanaged, rows) is None


def write_queue_meta(root: Path, state: str, fingerprint: str, task_id: str = "RQ-TEST") -> None:
    directory = root / ("inbox" if state in {"new", "ready"} else "") / state / "task"
    directory.mkdir(parents=True)
    (directory / "META.json").write_text(json.dumps({
        "fingerprint": fingerprint, "id": task_id, "state": state,
        "created_at": "2026-08-26T00:00:00Z",
        "completed_at": "2026-08-26T00:30:00Z" if state == "done" else None,
    }), encoding="utf-8")


def write_completed_index(root: Path, entries: list[dict[str, object]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = root / "COMPLETED_INDEX.json"
    path.write_text(json.dumps({
        "schema_version": 1, "entries": entries, "entries_sha256": digest,
    }), encoding="utf-8")
    return path


def test_thresholded_discovery_creates_one_inbox_item_and_replay_is_idempotent(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "project"
    project.mkdir(parents=True)
    subprocess.run(
        [sys.executable, str(QUEUE_SCRIPT), "--root", str(project / "artifacts/request_queue"), "init"],
        cwd=project, check=True, capture_output=True, text=True,
    )
    source_root = project / "artifacts/runtime_logs/application"
    source_root.mkdir(parents=True)
    first = source_root / "first.json"
    second = source_root / "second.json"
    first.write_text(json.dumps(runtime_event("a" * 32, "2026-08-26T00:00:00Z")), encoding="utf-8")
    second.write_text(json.dumps(runtime_event("c" * 32, "2026-08-26T00:01:00Z")), encoding="utf-8")
    policy_path = project / "artifacts/issue_state/escalation_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(json.dumps(policy()), encoding="utf-8")
    store = project / "artifacts/issue_state/v1/issues.json"

    below = module.sync(project, (first,), store_path=store, policy_path=policy_path, enable_discovery=True)
    assert below["decisions"] == []
    threshold = module.sync(project, (first, second), store_path=store, policy_path=policy_path, enable_discovery=True)
    assert threshold["decisions"][0]["task"].startswith("RQ-")
    assert threshold["decisions"][0]["policy_revision"] == 1
    assert len(threshold["decisions"][0]["issue_snapshot_sha256"]) == 64
    replay = module.sync(project, (first, second), store_path=store, policy_path=policy_path, enable_discovery=True)
    assert replay["decisions"][0]["decision"] == "DUPLICATE_ACTIVE"
    assert len(list((project / "artifacts/request_queue/inbox/new").glob("*/META.json"))) == 1


def test_no_policy_means_no_discovery_and_provider_calls_zero(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "project"
    source = project / "artifacts/runtime_logs/application/event.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(runtime_event("a" * 32, "2026-08-26T00:00:00Z")), encoding="utf-8")
    result = module.sync(
        project, (source,), store_path=project / "artifacts/issue_state/v1/issues.json",
        policy_path=None, enable_discovery=True,
    )
    assert result["provider_calls"] == 0
    assert result["decisions"] == []


def test_alternate_store_root_and_unallowlisted_input_fail_closed(tmp_path: Path) -> None:
    module = load_module()
    project = tmp_path / "project"
    project.mkdir()
    source = project / "outside.json"
    source.write_text(json.dumps(runtime_event("a" * 32, "2026-08-26T00:00:00Z")), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlisted"):
        module.sync(
            project, (source,), store_path=project / "artifacts/issue_state/v1/issues.json",
            policy_path=None, enable_discovery=False,
        )
    allowed = project / "artifacts/runtime_logs/application/event.json"
    allowed.parent.mkdir(parents=True)
    allowed.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical"):
        module.sync(
            project, (allowed,), store_path=project / "alternate/issues.json",
            policy_path=None, enable_discovery=False,
        )


@pytest.mark.parametrize("state", ("new", "ready", "active", "review", "blocked"))
def test_all_mutable_queue_states_deduplicate_an_exact_policy_fingerprint(
    tmp_path: Path, state: str,
) -> None:
    module = load_module()
    record = aggregate_events((), (
        issue_event("event-1", "2026-08-26T02:00:00Z"),
        issue_event("event-2", "2026-08-26T02:01:00Z"),
    ))[0]
    row = policy()["policies"][0]
    fingerprint = row["queue_fingerprint"].replace("{fingerprint}", record.fingerprint)
    queue_root = tmp_path / "queue"
    write_queue_meta(queue_root, state, fingerprint)

    assert module.queue_decision(record, row, queue_root) == (
        "DUPLICATE_ACTIVE", None, None,
    )


def test_done_and_digest_validated_completed_index_have_distinct_recurrence_rules(
    tmp_path: Path,
) -> None:
    module = load_module()
    row = policy()["policies"][0]
    row["discovery_rate"] = {"max_count": 10, "window_seconds": 86400}
    row["cooldown_seconds"] = 60
    record = aggregate_events((), (
        issue_event("failure-1", "2026-08-26T00:00:00Z"),
        issue_event("success-1", "2026-08-26T00:01:00Z", "SUCCESS"),
        issue_event("failure-2", "2026-08-26T03:00:00Z"),
    ))[0]
    base = row["queue_fingerprint"].replace("{fingerprint}", record.fingerprint)

    done_root = tmp_path / "done-queue"
    write_queue_meta(done_root, "done", base, "RQ-DONE")
    assert module.queue_decision(record, row, done_root) == (
        "RECURRENCE_REVIEW_REQUIRED", None, None,
    )

    compacted_root = tmp_path / "compacted-queue"
    index = write_completed_index(compacted_root, [{
        "fingerprint": base, "id": "RQ-COMPACTED",
        "completed_at": "2026-08-26T00:30:00Z",
    }])
    decision, recurrence_fingerprint, identity = module.queue_decision(
        record, row, compacted_root,
    )
    assert decision == "DISCOVER_RECURRENCE"
    assert recurrence_fingerprint and len(recurrence_fingerprint) == 64
    assert identity == {
        "completed_task_id": "RQ-COMPACTED", "recovery_epoch": 2,
        "schema": "queue-recurrence/v1",
        "stable_issue_fingerprint": record.fingerprint,
    }

    write_queue_meta(compacted_root, "new", recurrence_fingerprint, "RQ-RECURRENCE")
    assert module.queue_decision(record, row, compacted_root) == (
        "DUPLICATE_RECURRENCE", None, None,
    )
    payload = json.loads(index.read_text(encoding="utf-8"))
    payload["entries_sha256"] = "0" * 64
    index.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        module.queue_decision(record, row, compacted_root)


def test_recurrence_cooldown_and_rate_limit_are_both_enforced(tmp_path: Path) -> None:
    module = load_module()
    record = aggregate_events((), (
        issue_event("failure-1", "2026-08-26T00:00:00Z"),
        issue_event("success-1", "2026-08-26T00:01:00Z", "SUCCESS"),
        issue_event("failure-2", "2026-08-26T02:00:00Z"),
    ))[0]
    row = policy()["policies"][0]
    base = row["queue_fingerprint"].replace("{fingerprint}", record.fingerprint)

    cooldown_root = tmp_path / "cooldown"
    write_completed_index(cooldown_root, [{
        "fingerprint": base, "id": "RQ-OLD",
        "completed_at": "2026-08-26T01:30:00Z",
    }])
    assert module.queue_decision(record, row, cooldown_root)[0] == "COOLDOWN_ACTIVE"

    rate_root = tmp_path / "rate"
    write_completed_index(rate_root, [{
        "fingerprint": base, "id": "RQ-OLD",
        "completed_at": "2026-08-26T00:00:00Z",
    }])
    assert module.queue_decision(record, row, rate_root)[0] == "RATE_LIMITED"


def test_occurrence_threshold_counts_only_the_active_recovery_epoch() -> None:
    module = load_module()
    row = policy()["policies"][0]
    first_recurrence = aggregate_events((), (
        issue_event("failure-1", "2026-08-26T00:00:00Z"),
        issue_event("success-1", "2026-08-26T00:01:00Z", "SUCCESS"),
        issue_event("failure-2", "2026-08-26T01:00:00Z"),
    ))[0]
    assert first_recurrence.occurrence_count == 2
    assert first_recurrence.previous_epochs[0]["occurrence_count"] == 1
    assert module.matching_policy(first_recurrence, (row,)) is None

    repeated_recurrence = aggregate_events((first_recurrence,), (
        issue_event("failure-3", "2026-08-26T01:01:00Z"),
    ))[0]
    assert module.matching_policy(repeated_recurrence, (row,)) is row


def test_suppression_release_requires_a_new_snapshot_before_policy_match() -> None:
    module = load_module()
    row = policy()["policies"][0]
    record = aggregate_events((), (
        issue_event("event-1", "2026-08-26T00:00:00Z"),
        issue_event("event-2", "2026-08-26T00:01:00Z"),
    ))[0]
    suppressed = suppress_issue(
        record, suppression_id="maintenance-1", reason_code="KNOWN_MAINTENANCE",
        started_at="2026-08-26T00:01:00Z", expires_at="2026-08-27T00:01:00Z",
        actor="local.operator", evidence="artifacts/runtime_logs/application/event.json@sha256:" + "a" * 64,
    )
    released = release_suppression(
        suppressed, released_at="2026-08-26T00:02:00Z",
        reason_code="MAINTENANCE_COMPLETE", actor="local.operator",
    )
    assert module.matching_policy(released, (row,)) is None

    later = aggregate_events((released,), (
        issue_event("event-3", "2026-08-26T00:03:00Z"),
    ))[0]
    assert module.matching_policy(later, (row,)) is row


def test_malformed_allowlisted_input_and_policy_fail_closed_and_outputs_are_private_free(
    tmp_path: Path,
) -> None:
    module = load_module()
    project = tmp_path / "project"
    source_root = project / "artifacts/runtime_logs/application"
    source_root.mkdir(parents=True)
    malformed = source_root / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    store = project / "artifacts/issue_state/v1/issues.json"
    with pytest.raises(json.JSONDecodeError):
        module.sync(project, (malformed,), store_path=store, policy_path=None, enable_discovery=False)
    assert not store.exists()

    first = source_root / "first.json"
    second = source_root / "second.json"
    first.write_text(json.dumps(runtime_event("a" * 32, "2026-08-26T00:00:00Z")), encoding="utf-8")
    second.write_text(json.dumps(runtime_event("c" * 32, "2026-08-26T00:01:00Z")), encoding="utf-8")
    policy_path = project / "artifacts/issue_state/escalation_policy.json"
    policy_path.parent.mkdir(parents=True)
    malformed_policy = policy()
    malformed_policy["policies"][0]["unknown"] = True
    policy_path.write_text(json.dumps(malformed_policy), encoding="utf-8")
    with pytest.raises(ValueError, match="fields"):
        module.load_policies(policy_path)

    unallowlisted_group = policy()
    unallowlisted_group["policies"][0]["target_id"] = (
        "group=automation-enabled-datasets"
    )
    policy_path.write_text(json.dumps(unallowlisted_group), encoding="utf-8")
    with pytest.raises(ValueError, match="not allowlisted"):
        module.load_policies(policy_path)

    absolute_path_policy = policy()
    absolute_path_policy["policies"][0]["discovery_template"]["reproduce"] = (
        "C:/Users/example/private.txt"
    )
    policy_path.write_text(json.dumps(absolute_path_policy), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        module.load_policies(policy_path)

    uri_texts = (
        "urn:example:private", "file:C:/Users/example/private.txt",
        *("a" * length + ":private" for length in (32, 33, 200)),
    )
    for uri_text in uri_texts:
        uri_policy = policy()
        uri_policy["policies"][0]["discovery_template"]["reproduce"] = uri_text
        policy_path.write_text(json.dumps(uri_policy), encoding="utf-8")
        with pytest.raises(ValueError, match="unsafe"):
            module.load_policies(policy_path)

    traversal_policy = policy()
    traversal_policy["policies"][0]["discovery_template"]["suspected_scope"] = (
        "src/../private"
    )
    policy_path.write_text(json.dumps(traversal_policy), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        module.load_policies(policy_path)

    result = module.sync(
        project, (first, second), store_path=store,
        policy_path=None, enable_discovery=False,
    )
    retained = store.read_text(encoding="utf-8")
    serialized_result = json.dumps(result, sort_keys=True)
    forbidden = ("authorization", "bearer", "token", "secret", "password", "account", "holding", "balance", "order", "http://", "https://")
    assert all(term not in retained.lower() for term in forbidden)
    assert all(term not in serialized_result.lower() for term in forbidden)
