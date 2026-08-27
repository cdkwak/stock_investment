from datetime import date, datetime, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from scripts.maintenance import run_bok_ecos_treasury_finality_observation as scheduler
from stock_data.orchestration.bok_ecos_treasury_daily import (
    CANONICAL_TENORS,
    DATASET_ID,
    DailyPlanAction,
    ExactDateReview,
    FinalityObservationAction,
    FINALITY_OBSERVATION_POLICY,
    POLICY,
    plan_daily_operation,
    plan_finality_observation_occurrence,
    validate_atomic_scope_dates,
)


TARGET = date(2026, 8, 14)


def _checkpoint(*, status="SUCCEEDED", target="2026-08-14", scopes=CANONICAL_TENORS):
    return {
        "dataset": DATASET_ID,
        "target_date": target,
        "status": status,
        "accepted_scopes": list(scopes),
    }


def test_policy_is_exact_six_call_retry_zero_and_fail_closed() -> None:
    assert POLICY.scopes == CANONICAL_TENORS
    assert POLICY.max_data_calls == 6
    assert POLICY.retry_budget == 0
    assert POLICY.finality_policy.value == "UNKNOWN"
    assert POLICY.atomic_promotion_scope == "ALL_SIX_TENORS_AND_CHECKPOINT"
    plan = plan_daily_operation(target_date=TARGET, retained_latest=date(2026, 8, 13))
    assert plan.action is DailyPlanAction.REVIEW_REQUIRED
    assert plan.pre_network_noop and plan.max_data_calls == 0


def test_finality_observation_policy_is_diagnostic_only_and_never_auto_final() -> None:
    policy = FINALITY_OBSERVATION_POLICY
    assert policy.scopes == CANONICAL_TENORS
    assert policy.max_statistic_search_calls_per_batch == 6
    assert policy.retry_budget == 0 and policy.landing_first
    assert policy.normalized_writes == 0
    assert policy.official_ui_marker_separate
    assert policy.observation_window_kst == "17:00-18:00"
    assert policy.required_batches_before_review == 3
    assert not policy.automatic_expected_latest
    assert not policy.automatic_finality_claim


def test_only_explicit_review_makes_an_exact_date_collection_candidate() -> None:
    plan = plan_daily_operation(
        target_date=TARGET,
        retained_latest=date(2026, 8, 13),
        exact_date_review=ExactDateReview.OPERATOR_REVIEWED,
    )
    assert plan.action is DailyPlanAction.COLLECT_EXACT_DATE
    assert plan.max_data_calls == 6 and plan.retry_budget == 0


def test_successful_same_date_replay_is_pre_network_api_zero() -> None:
    plan = plan_daily_operation(
        target_date=TARGET, retained_latest=TARGET, checkpoint=_checkpoint(),
    )
    assert plan.action is DailyPlanAction.NOOP_ALREADY_SUCCEEDED
    assert plan.pre_network_noop and plan.max_data_calls == 0


def test_checkpoint_cannot_hide_missing_promoted_target() -> None:
    plan = plan_daily_operation(
        target_date=TARGET,
        retained_latest=date(2026, 8, 13),
        checkpoint=_checkpoint(),
    )
    assert plan.action is DailyPlanAction.CHECKPOINT_CONFLICT
    assert plan.pre_network_noop


def test_atomic_candidate_requires_all_six_exact_date_scopes() -> None:
    valid = {tenor: (TARGET,) for tenor in CANONICAL_TENORS}
    validate_atomic_scope_dates(valid, target_date=TARGET)
    with pytest.raises(ValueError, match="six canonical tenors"):
        validate_atomic_scope_dates(dict(list(valid.items())[:-1]), target_date=TARGET)
    invalid = dict(valid)
    invalid["30Y"] = (date(2026, 8, 13),)
    with pytest.raises(ValueError, match="exactly the target date"):
        validate_atomic_scope_dates(invalid, target_date=TARGET)


def test_finality_scheduler_plan_is_window_bounded_and_stops_at_review_gate() -> None:
    outside = plan_finality_observation_occurrence(
        observation_time=datetime(2026, 8, 27, 16, 59, tzinfo=ZoneInfo("Asia/Seoul")),
        retained_batch_count=1,
    )
    assert outside.action is FinalityObservationAction.NOOP_OUTSIDE_WINDOW
    assert outside.pre_network_noop

    eligible = plan_finality_observation_occurrence(
        observation_time=datetime(2026, 8, 27, 17, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        retained_batch_count=1,
    )
    assert eligible.action is FinalityObservationAction.OBSERVE_OR_REPLAY
    assert eligible.max_statistic_search_calls == 6
    assert eligible.max_official_ui_calls == 1

    complete = plan_finality_observation_occurrence(
        observation_time=datetime(2026, 8, 29, 17, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        retained_batch_count=3,
    )
    assert complete.action is FinalityObservationAction.NOOP_REVIEW_GATE_REACHED
    assert complete.pre_network_noop


def _write_finality_state(root, *, batch_count: int) -> None:
    path = root / scheduler.STATE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1,
        "batch_count": batch_count,
        "batches": [{} for _ in range(batch_count)],
    }), encoding="utf-8")


def test_scheduled_bok_observation_noops_before_credentials_outside_window(
    tmp_path, monkeypatch,
) -> None:
    _write_finality_state(tmp_path, batch_count=1)
    monkeypatch.setattr(
        scheduler, "load_dotenv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("env loaded")),
    )
    report = scheduler.run_scheduled_observation(
        tmp_path,
        observation_time=datetime(2026, 8, 27, 0, 30, tzinfo=timezone.utc),
        operation=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("called")),
    )
    assert report["status"] == "PASS"
    assert report["observation_status"] == "NOOP_OUTSIDE_WINDOW"
    assert report["api_calls"] == 0


def test_scheduled_bok_observation_runs_one_bounded_batch_and_writes_safe_receipt(
    tmp_path, monkeypatch,
) -> None:
    metadata = tmp_path / scheduler.METADATA_RELATIVE
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("{}", encoding="utf-8")
    _write_finality_state(tmp_path, batch_count=1)
    monkeypatch.setattr(scheduler, "load_dotenv", lambda *_args, **_kwargs: True)

    def operation(**kwargs):
        assert kwargs["range_start_date"] is None
        _write_finality_state(tmp_path, batch_count=2)
        return {
            "status": "FINALITY_OBSERVATION_COMPLETE",
            "statistic_search_calls": 6,
            "official_ui_calls": 1,
            "selected_date": "20260827",
            "comparison_status": "SAME",
            "state_status": "PUBLICATION_FINALITY_UNKNOWN",
        }

    report = scheduler.run_scheduled_observation(
        tmp_path,
        observation_time=datetime(2026, 8, 27, 17, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        operation=operation,
    )
    assert report["status"] == "PASS" and report["api_calls"] == 7
    assert report["batch_count_before"] == 1 and report["batch_count_after"] == 2
    retained = json.loads((tmp_path / scheduler.LOG_RELATIVE).read_text(encoding="utf-8"))
    assert retained == report
    assert "metadata" not in json.dumps(retained).lower()
