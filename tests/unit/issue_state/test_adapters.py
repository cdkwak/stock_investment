from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy

import pytest

from issue_state.adapters import (
    adapt_health_v2, adapt_runtime_diagnostic, adapt_scheduler_occurrence,
    adapt_update_event,
)
from stock_data.orchestration.update_event_log import (
    EventState, ReasonCode, TriggerType, UpdateEvent,
)
from stock_data.orchestration.daily_operations import DATASET_UNIVERSE


EVIDENCE = "artifacts/source.json@sha256:" + "a" * 64


def health_payload() -> dict[str, object]:
    rows = []
    for dataset, spec in DATASET_UNIVERSE.items():
        rows.append({
            "api_calls": 0, "automation_enabled": spec.automation_enabled,
            "automation_policy": "MANUAL_GATE", "blocker": None,
            "calendar": "PROVIDER_PUBLICATION", "calendar_source": "typed-dataset-policy",
            "calendar_version": "1", "dataset": dataset, "expected": None,
            "expected_lag_policy": "MANUAL", "finality": "UNKNOWN",
            "freshness": "UNKNOWN", "gap_status": "CALENDAR_RESOLVED", "grain": "DAILY",
            "last_run": None, "last_success": None, "latest": None,
            "market_expected_latest": None, "missing_dates": None,
            "observation_calendar": "PROVIDER_PUBLICATION", "operational": "READY_WITH_LIMITS",
            "pit": "PIT_BLOCKED", "pre_network_noop": False,
            "provider_availability_policy": "MANUAL_OBSERVATION", "refresh": "GAP_FILL",
            "role": "SOURCE", "runtime_coverage": "NOT_PROBED",
            "scheduler_lane": "MANUAL", "scheduler_management": "MANUAL_ONLY",
            "source": "typed-source",
            "display_consumer_eligibility": spec.display_consumer_eligibility.value,
            "display_consumer_reason": spec.display_consumer_reason.value,
            "research_consumer_eligibility": spec.research_consumer_eligibility.value,
            "research_consumer_reason": spec.research_consumer_reason.value,
            "predictive_consumer_eligibility": spec.predictive_consumer_eligibility.value,
            "predictive_consumer_reason": spec.predictive_consumer_reason.value,
        })
    target = next(row for row in rows if row["dataset"] == "kr_equity_canonical_universe_daily")
    target.update(freshness="STALE", operational="BLOCKED", latest="2026-08-24", expected="2026-08-25")
    return {
        "actionable_incident_count": 1, "as_of": "2026-08-26T00:00:00Z",
        "automation_enabled_count": sum(spec.automation_enabled for spec in DATASET_UNIVERSE.values()),
        "core_operation_missing": [], "core_operations_count": 0,
        "core_reference_time": "2026-08-26T00:00:00Z", "dataset_count": len(rows),
        "datasets": rows, "dimension_summary": {}, "generated_at": "2026-08-26T00:00:00Z",
        "operations_registry_count": 0, "run_id": "health-20260826",
        "runtime_coverage_failure_count": 0, "runtime_coverage_failures": {},
        "runtime_coverage_validated_count": 0, "schema_version": 2,
    }


def test_runtime_adapter_projects_no_exception_or_frame_content() -> None:
    payload = {
        "schema": "runtime-diagnostic/v1", "event_id": "a" * 32,
        "occurred_at": "2026-08-26T00:00:00+00:00", "domain": "GUI",
        "kind": "UNHANDLED", "session_id": "b" * 32, "run_id": None,
        "code": "GUI_LOAD_FAILED", "stage": "DASHBOARD",
        "exception_classes": ["RuntimeError"], "frames": ["src/app.py:10"],
        "artifacts": [],
    }
    projected = adapt_runtime_diagnostic(payload, evidence=EVIDENCE)[0]
    assert projected.target_id == "gui:dashboard"
    assert "RuntimeError" not in repr(projected)
    assert "src/app.py" not in repr(projected)


def test_health_adapter_emits_failure_and_exact_success_closures() -> None:
    payload = health_payload()
    events = adapt_health_v2(payload, evidence=EVIDENCE)
    assert {event.stable_code for event in events} == {
        "HEALTH_STALE", "HEALTH_UNKNOWN", "HEALTH_OPERATIONAL_BLOCKED",
    }
    target = "kr_equity_canonical_universe_daily"
    assert next(event for event in events if event.stable_code == "HEALTH_STALE" and event.target_id == target).outcome == "FAILURE"
    assert next(event for event in events if event.stable_code == "HEALTH_UNKNOWN" and event.target_id == target).outcome == "SUCCESS"

    fabricated = deepcopy(payload)
    fabricated["datasets"][0]["dataset"] = "made_up_dataset"
    with pytest.raises(ValueError, match="identity"):
        adapt_health_v2(fabricated, evidence=EVIDENCE)

    missing = deepcopy(payload)
    del missing["datasets"][0]["research_consumer_reason"]
    with pytest.raises(ValueError, match="row differs"):
        adapt_health_v2(missing, evidence=EVIDENCE)

    forged = deepcopy(payload)
    forged["datasets"][0]["predictive_consumer_eligibility"] = "ELIGIBLE"
    with pytest.raises(ValueError, match="consumer eligibility differs"):
        adapt_health_v2(forged, evidence=EVIDENCE)

    cross_dataset = deepcopy(payload)
    first, second = cross_dataset["datasets"][:2]
    first["display_consumer_reason"] = second["display_consumer_reason"]
    if first["display_consumer_reason"] == payload["datasets"][0]["display_consumer_reason"]:
        first["display_consumer_reason"] = "DISPLAY_NOT_CONTRACTED"
    with pytest.raises(ValueError, match="consumer eligibility differs"):
        adapt_health_v2(cross_dataset, evidence=EVIDENCE)


def test_scheduler_adapter_ignores_claim_and_projects_terminal_only() -> None:
    claim = {
        "schema_version": 1, "bundle": "KR_MARKET_DAILY", "scheduled_slot": "09:10",
        "scheduled_for": "2026-08-26T09:10:00+09:00", "status": "CLAIMED_BEFORE_LANES",
    }
    assert adapt_scheduler_occurrence(claim, evidence=EVIDENCE) == ()
    scheduler_evidence = "data/state/provider_scheduler/kr_market_daily_occurrences/20260826T001000Z-0910.json@sha256:" + "a" * 64
    health = {"status": "FAIL", "error_type": "SchedulerHealthProjectionError"}
    terminal = {
        **claim, "status": "DEGRADED", "occurrence_status": "TERMINAL_FAILURE",
        "scheduler_process_status": "FAIL_AFTER_INDEPENDENT_LANES", "terminal_exit_code": 1,
        "started_at_utc": "2026-08-26T00:10:00Z",
        "finished_at_utc": datetime(2026, 8, 26, 0, 10, tzinfo=timezone.utc).isoformat(),
        "eligible_lanes": ["SHORT_SELLING_DAILY"], "api_calls": 0,
        "health_projection": health,
        "occurrence_receipt": "data/state/provider_scheduler/kr_market_daily_occurrences/20260826T001000Z-0910.json",
        "outcomes": [{
            "lane": "SHORT_SELLING_DAILY", "status": "FAIL", "advancement_status": "FAILED",
            "api_calls": 0, "scheduled_slot": "09:10",
            "scheduled_for": "2026-08-26T09:10:00+09:00", "started_at_utc": "2026-08-26T00:10:00Z",
            "result": {"scheduler_process_status": "FAIL_AFTER_HEALTH", "health_projection": health},
        }],
    }
    event = adapt_scheduler_occurrence(terminal, evidence=scheduler_evidence)[0]
    assert event.outcome == "FAILURE"
    assert event.target_id == "kr_market_daily:0910"
    changed = deepcopy(terminal)
    changed["api_calls"] = 1
    with pytest.raises(ValueError, match="totals"):
        adapt_scheduler_occurrence(changed, evidence=scheduler_evidence)
    contradictory = deepcopy(terminal)
    contradictory["outcomes"][0]["result"]["scheduler_process_status"] = "FAIL"
    with pytest.raises(ValueError, match="contradicts"):
        adapt_scheduler_occurrence(contradictory, evidence=scheduler_evidence)

    lane_failure = deepcopy(terminal)
    passing_health = {
        "status": "PASS", "dataset_count": 80,
        "runtime_coverage_validated_count": 21,
        "runtime_coverage_failure_count": 0,
    }
    lane_failure["health_projection"] = passing_health
    lane_failure["outcomes"][0]["result"] = {
        "scheduler_process_status": "FAIL", "health_projection": passing_health,
    }
    assert adapt_scheduler_occurrence(
        lane_failure, evidence=scheduler_evidence,
    )[0].outcome == "FAILURE"


def test_update_success_closes_each_failure_fingerprint_without_copying_message() -> None:
    started = UpdateEvent.started(
        run_id="route-20260826", job_route="yahoo-current",
        logical_dataset="yahoo_market_current", trigger_type=TriggerType.SCHEDULED,
        requested_scope={"symbol": "redacted"}, at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        message="private native message",
    )
    completed = started.terminal(
        state=EventState.SUCCEEDED, reason_code=ReasonCode.COMPLETED,
        at=datetime(2026, 8, 26, 0, 1, tzinfo=timezone.utc),
        message="private success message",
    )
    events = adapt_update_event(completed.to_dict(), evidence=EVIDENCE)
    assert len(events) == 5
    assert {event.outcome for event in events} == {"SUCCESS"}
    assert "private" not in repr(events)
    assert "requested_scope" not in repr(events)
