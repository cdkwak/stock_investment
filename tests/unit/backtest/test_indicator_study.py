from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pandas as pd
import pytest

from market_backtest.holdout import CoverageHoldout
from market_backtest.indicator_study import (
    INDICATOR_STUDY_CONTRACT_VERSION,
    INDICATOR_STUDY_STATUS,
    IndicatorCandidate,
    evaluate_predefined_indicators,
)


def holdout() -> CoverageHoldout:
    return CoverageHoldout(
        policy_id="SYNTHETIC_UNTOUCHED",
        coverage_start="2019-01-01",
        coverage_end="2021-12-31",
        holdout_start="2021-01-01",
        development_observations=6,
        holdout_observations=2,
        results_reviewed=False,
    )


def features() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=7)
    return pd.DataFrame({
        "observation_date": dates.strftime("%Y-%m-%d"),
        "ticker": "SYNTHETIC",
        "date_semantics": "RETAINED_TRADING_SESSION",
        "usable_from": (
            dates + pd.offsets.BDay(1)
        ).strftime("%Y-%m-%dT09:00:00+09:00"),
        "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "rsi_14": (10.0, 20.0, 50.0, 70.0, 80.0, 90.0, 95.0),
    })


def labels() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=6)
    return pd.DataFrame({
        "observation_date": dates.strftime("%Y-%m-%d"),
        "ticker": "SYNTHETIC",
        "date_semantics": "RETAINED_TRADING_SESSION",
        "label_available_at": (
            dates + pd.offsets.BDay(60)
        ).strftime("%Y-%m-%dT15:30:00+09:00"),
        "label_version": pd.Series([1] * 6, dtype="int64"),
        "forward_return_20d": (0.20, 0.10, 0.00, -0.05, -0.10, -0.20),
        "forward_max_drawdown_20d": (-0.05, -0.04, -0.08, -0.10, -0.15, -0.25),
    })


def candidates() -> tuple[IndicatorCandidate, ...]:
    return (
        IndicatorCandidate("RSI14_LOW_30", "rsi_14", "LOW", 30.0, 20, 2),
        IndicatorCandidate("RSI14_HIGH_70", "rsi_14", "HIGH", 70.0, 20, 2),
    )


def test_predefined_candidates_are_descriptive_ordered_and_select_no_winner():
    result = evaluate_predefined_indicators(
        features(), labels(), candidates(), holdout(),
    )

    assert result.contract_version == INDICATOR_STUDY_CONTRACT_VERSION
    assert result.status == INDICATOR_STUDY_STATUS
    assert result.winner_selected is False
    assert [row.candidate.candidate_id for row in result.results] == [
        "RSI14_LOW_30", "RSI14_HIGH_70",
    ]
    assert not hasattr(result, "winner")
    assert not hasattr(result, "ranking")


def test_low_and_high_conditions_compute_known_conditional_statistics():
    low, high = evaluate_predefined_indicators(
        features(), labels(), candidates(), holdout(),
    ).results

    assert low.availability == high.availability == "EVALUATED"
    assert low.metrics.signal_observations == 2
    assert low.metrics.signal_rate == pytest.approx(2.0 / 6.0)
    assert low.metrics.conditional_mean_return == pytest.approx(0.15)
    assert low.metrics.conditional_median_return == pytest.approx(0.15)
    assert low.metrics.conditional_positive_rate == 1.0
    assert low.metrics.conditional_mean_max_drawdown == pytest.approx(-0.045)
    assert high.metrics.signal_observations == 3
    assert high.metrics.conditional_mean_return == pytest.approx(-0.35 / 3.0)
    assert high.metrics.conditional_positive_rate == 0.0


def test_unconditional_baseline_and_difference_are_explicit():
    low = evaluate_predefined_indicators(
        features(), labels(), candidates(), holdout(),
    ).results[0]

    assert low.metrics.unconditional_mean_return == pytest.approx(-0.05 / 6.0)
    assert low.metrics.unconditional_median_return == pytest.approx(-0.025)
    assert low.metrics.unconditional_positive_rate == pytest.approx(2.0 / 6.0)
    assert low.metrics.conditional_mean_return_difference == pytest.approx(
        0.15 - (-0.05 / 6.0)
    )


def test_insufficient_signal_count_is_typed_without_partial_metrics():
    sparse = (replace(candidates()[0], minimum_signal_observations=3),)
    result = evaluate_predefined_indicators(
        features(), labels(), sparse, holdout(),
    ).results[0]

    assert result.availability == "INSUFFICIENT_SIGNAL_OBSERVATIONS"
    assert result.metrics.signal_observations == 2
    assert result.metrics.conditional_mean_return is None
    assert result.metrics.conditional_median_return is None
    assert result.metrics.conditional_positive_rate is None
    assert result.metrics.conditional_mean_max_drawdown is None
    assert result.metrics.conditional_mean_return_difference is None


def test_result_and_nested_candidates_are_immutable_and_deterministic():
    first = evaluate_predefined_indicators(
        features(), labels(), candidates(), holdout(),
    )
    second = evaluate_predefined_indicators(
        features(), labels(), candidates(), holdout(),
    )

    assert first == second
    assert isinstance(first.results, tuple)
    with pytest.raises(FrozenInstanceError):
        first.results[0].availability = "OTHER"


def test_outcome_namespace_is_forbidden_from_features():
    feature_frame = features()
    feature_frame["forward_return_20d"] = 100.0

    with pytest.raises(ValueError, match="outcome namespace"):
        evaluate_predefined_indicators(
            feature_frame, labels(), candidates(), holdout(),
        )


def test_holdout_rows_are_rejected_before_indicator_or_outcome_values_are_read():
    feature_frame = features()
    label_frame = labels()
    feature_frame.loc[6, "observation_date"] = "2021-01-04"
    label_frame.loc[5, "observation_date"] = "2021-01-04"
    feature_frame["rsi_14"] = feature_frame["rsi_14"].astype("object")
    label_frame["forward_return_20d"] = label_frame[
        "forward_return_20d"
    ].astype("object")
    feature_frame.loc[6, "rsi_14"] = "DO_NOT_INSPECT"
    label_frame.loc[5, "forward_return_20d"] = "DO_NOT_INSPECT"

    with pytest.raises(ValueError, match="untouched holdout"):
        evaluate_predefined_indicators(
            feature_frame, label_frame, candidates(), holdout(),
        )


def test_label_whose_outcome_becomes_available_in_holdout_is_rejected():
    label_frame = labels()
    label_frame.loc[5, "label_available_at"] = "2021-01-04T15:30:00+09:00"

    with pytest.raises(ValueError, match="before holdout"):
        evaluate_predefined_indicators(
            features(), label_frame, candidates(), holdout(),
        )


@pytest.mark.parametrize(
    "usable_from",
    [
        "2020-01-02T09:00:00+09:00",
        "2020-01-06T09:00:00+09:00",
        "2020-01-03T09:00:00",
        "invalid",
    ],
)
def test_feature_must_be_available_after_observation_with_timezone(usable_from):
    feature_frame = features()
    feature_frame.loc[0, "usable_from"] = usable_from

    with pytest.raises(ValueError, match="usable_from"):
        evaluate_predefined_indicators(
            feature_frame, labels(), candidates(), holdout(),
        )


def test_feature_and_label_identity_or_retained_date_mismatch_fails_closed():
    label_frame = labels()
    label_frame["ticker"] = "OTHER"
    with pytest.raises(ValueError, match="identity differs"):
        evaluate_predefined_indicators(
            features(), label_frame, candidates(), holdout(),
        )

    label_frame = labels()
    label_frame.loc[0, "observation_date"] = "2019-12-31"
    with pytest.raises(ValueError, match="exact retained feature date"):
        evaluate_predefined_indicators(
            features(), label_frame, candidates(), holdout(),
        )


def test_invalid_numeric_values_versions_and_pit_status_fail_closed():
    feature_frame = features()
    feature_frame.loc[0, "rsi_14"] = float("nan")
    with pytest.raises(ValueError, match="real numeric and finite"):
        evaluate_predefined_indicators(
            feature_frame, labels(), candidates(), holdout(),
        )

    label_frame = labels()
    label_frame["label_version"] = 2
    with pytest.raises(ValueError, match="label_version"):
        evaluate_predefined_indicators(
            features(), label_frame, candidates(), holdout(),
        )

    feature_frame = features()
    feature_frame["pit_status"] = "PIT_LIMITED"
    with pytest.raises(ValueError, match="PIT_SAFE"):
        evaluate_predefined_indicators(
            feature_frame, labels(), candidates(), holdout(),
        )


def test_finite_inputs_that_overflow_a_derived_metric_fail_closed():
    label_frame = labels()
    label_frame["forward_return_20d"] = float.fromhex("0x1.fffffffffffffp+1023")

    with pytest.raises(ValueError, match="metrics must remain finite"):
        evaluate_predefined_indicators(
            features(), label_frame, candidates(), holdout(),
        )


def test_candidate_definition_is_strict_and_ids_are_unique():
    with pytest.raises(ValueError, match="candidate"):
        IndicatorCandidate("BAD", "rsi_14", "LOW", float("nan"), 20, 2)

    duplicate = (candidates()[0], candidates()[0])
    with pytest.raises(ValueError, match="ids must be unique"):
        evaluate_predefined_indicators(
            features(), labels(), duplicate, holdout(),
        )


def test_malformed_holdout_coverage_fails_closed():
    malformed = CoverageHoldout(
        policy_id="SYNTHETIC_UNTOUCHED",
        coverage_start="2021-06-01",
        coverage_end="2021-12-31",
        holdout_start="2021-01-01",
        development_observations=6,
        holdout_observations=2,
        results_reviewed=False,
    )

    with pytest.raises(ValueError, match="coverage"):
        evaluate_predefined_indicators(
            features(), labels(), candidates(), malformed,
        )

    too_late_start = CoverageHoldout(
        policy_id="SYNTHETIC_UNTOUCHED",
        coverage_start="2020-01-03",
        coverage_end="2021-12-31",
        holdout_start="2021-01-01",
        development_observations=6,
        holdout_observations=2,
        results_reviewed=False,
    )
    with pytest.raises(ValueError, match="within coverage"):
        evaluate_predefined_indicators(
            features(), labels(), candidates(), too_late_start,
        )
