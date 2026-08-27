from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pandas as pd
import pytest

from market_backtest.holdout import CoverageHoldout
from market_backtest.market_regime_validation import (
    MARKET_REGIME_HORIZONS,
    MARKET_REGIME_MAX_HORIZON,
    MARKET_REGIME_VALIDATION_STATUS,
    MARKET_REGIME_VALIDATION_VERSION,
    MarketRegimeCandidate,
    build_market_regime_labels,
    evaluate_predefined_market_regimes,
)


def holdout() -> CoverageHoldout:
    return CoverageHoldout(
        policy_id="SYNTHETIC_MARKET_REGIME_UNTOUCHED",
        coverage_start="2020-01-02",
        coverage_end="2026-12-31",
        holdout_start="2025-01-01",
        development_observations=900,
        holdout_observations=252,
        results_reviewed=False,
    )


def source(rows: int = 720) -> pd.DataFrame:
    dates = pd.bdate_range("2021-01-04", periods=rows)
    close = 100.0 * np.cumprod(np.full(rows, 1.0005, dtype="float64"))
    return pd.DataFrame({
        "observation_date": dates.strftime("%Y-%m-%d"),
        "ticker": "SYNTHETIC",
        "date_semantics": "RETAINED_TRADING_SESSION",
        "close": close,
    })


def labels() -> pd.DataFrame:
    return build_market_regime_labels(source(), holdout())


def features() -> pd.DataFrame:
    source_frame = source()
    dates = pd.to_datetime(source_frame["observation_date"])
    rows = len(source_frame)
    even = np.arange(rows) % 2 == 0
    return pd.DataFrame({
        "observation_date": source_frame["observation_date"],
        "ticker": "SYNTHETIC",
        "date_semantics": "RETAINED_TRADING_SESSION",
        "usable_from": (
            dates + pd.offsets.BDay(1)
        ).dt.strftime("%Y-%m-%dT09:00:00+09:00"),
        "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "price_axis_state": np.where(even, "OVERSOLD", "NEUTRAL"),
        "valuation_axis_state": np.where(even, "LOW", "MID"),
        "earnings_axis_state": np.where(even, "REVISING_UP", "STABLE"),
        "price_axis_pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "valuation_axis_pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "earnings_axis_pit_status": "PIT_SAFE_EOD_T_PLUS_1",
    })


def candidates() -> tuple[MarketRegimeCandidate, ...]:
    return (
        MarketRegimeCandidate(
            "OVERSOLD_LOW_UP", "OVERSOLD", "LOW", "REVISING_UP", 10,
        ),
        MarketRegimeCandidate(
            "NEUTRAL_MID_STABLE", "NEUTRAL", "MID", "STABLE", 10,
        ),
    )


def evaluate(**overrides):
    options = {
        "minimum_train": 100,
        "test_size": 50,
        "purge": MARKET_REGIME_MAX_HORIZON,
        "embargo": 5,
    }
    options.update(overrides)
    return evaluate_predefined_market_regimes(
        features(), labels(), candidates(), holdout(), **options,
    )


def test_long_horizon_labels_use_fixed_sessions_and_true_path_drawdown():
    frame = source()
    result = build_market_regime_labels(frame, holdout())

    assert len(result) == len(frame) - MARKET_REGIME_MAX_HORIZON
    assert result.columns.tolist() == [
        "observation_date", "ticker", "date_semantics",
        "forward_return_63d", "forward_max_drawdown_63d",
        "forward_return_126d", "forward_max_drawdown_126d",
        "forward_return_252d", "forward_max_drawdown_252d",
        "label_available_at", "label_version",
    ]
    first = result.iloc[0]
    close = frame["close"].to_numpy(dtype="float64")
    for horizon in MARKET_REGIME_HORIZONS:
        assert first[f"forward_return_{horizon}d"] == pytest.approx(
            close[horizon] / close[0] - 1.0
        )
        assert first[f"forward_max_drawdown_{horizon}d"] == pytest.approx(0.0)
    assert first["label_available_at"] == (
        frame["observation_date"].iloc[252] + "T15:30:00+09:00"
    )


def test_true_path_drawdown_uses_running_peak_not_only_initial_close():
    frame = source()
    frame.loc[10, "close"] = frame.loc[9, "close"] * 0.80
    result = build_market_regime_labels(frame, holdout())
    path = frame["close"].to_numpy(dtype="float64")[:64]
    expected = np.min(path / np.maximum.accumulate(path) - 1.0)

    assert result.loc[0, "forward_max_drawdown_63d"] == pytest.approx(expected)
    assert expected < 0.0


def test_label_builder_rejects_holdout_before_reading_close_values():
    frame = source()
    frame.loc[len(frame) - 1, "observation_date"] = "2025-01-02"
    frame["close"] = frame["close"].astype("object")
    frame.loc[len(frame) - 1, "close"] = object()

    with pytest.raises(ValueError, match="crosses the untouched holdout"):
        build_market_regime_labels(frame, holdout())


def test_predefined_three_axis_study_is_purged_ordered_and_selects_no_winner():
    result = evaluate()

    assert result.contract_version == MARKET_REGIME_VALIDATION_VERSION
    assert result.status == MARKET_REGIME_VALIDATION_STATUS
    assert result.horizons == MARKET_REGIME_HORIZONS
    assert result.purge_sessions == 252
    assert result.embargo_sessions == 5
    assert result.folds >= 1 and result.test_observations > 0
    assert result.winner_selected is False
    assert [item.candidate.candidate_id for item in result.results] == [
        "OVERSOLD_LOW_UP", "NEUTRAL_MID_STABLE",
    ]
    assert not hasattr(result, "winner") and not hasattr(result, "ranking")
    assert all(item.availability == "EVALUATED" for item in result.results)
    assert all(
        tuple(metric.horizon_sessions for metric in item.horizons)
        == MARKET_REGIME_HORIZONS
        for item in result.results
    )


def test_study_reports_return_drawdown_and_unconditional_opportunity_context():
    result = evaluate().results[0]

    assert result.signal_observations > 0
    assert 0.0 < result.signal_rate < 1.0
    for metric in result.horizons:
        assert metric.conditional_mean_return > 0.0
        assert metric.conditional_positive_rate == 1.0
        assert metric.conditional_mean_max_drawdown == pytest.approx(0.0)
        assert metric.unconditional_mean_return > 0.0
        assert metric.unconditional_mean_max_drawdown == pytest.approx(0.0)
        assert metric.conditional_mean_return_difference == pytest.approx(0.0)
        assert metric.conditional_mean_max_drawdown_difference == pytest.approx(0.0)


def test_every_axis_is_required_and_missing_earnings_is_never_imputed():
    feature_frame = features()
    feature_frame.loc[0, "earnings_axis_state"] = "UNAVAILABLE"

    with pytest.raises(ValueError, match="complete typed states"):
        evaluate_predefined_market_regimes(
            feature_frame, labels(), candidates(), holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )


def test_pit_blocked_earnings_state_and_axis_status_are_rejected():
    with pytest.raises(ValueError, match="candidate is invalid"):
        MarketRegimeCandidate(
            "BLOCKED_CANDIDATE", "OVERSOLD", "LOW", "PIT_BLOCKED", 1,
        )
    with pytest.raises(ValueError, match="candidate is invalid"):
        MarketRegimeCandidate(
            "LIMITED_CANDIDATE", "OVERSOLD", "LOW", "PIT_LIMITED", 1,
        )
    blocked_state = features()
    blocked_state["earnings_axis_state"] = "PIT_BLOCKED"
    blocked_candidate = (MarketRegimeCandidate(
        "BLOCKED_CASE", "OVERSOLD", "LOW", "REVISING_UP", 1,
    ),)
    with pytest.raises(ValueError, match="complete typed states"):
        evaluate_predefined_market_regimes(
            blocked_state, labels(), blocked_candidate, holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )

    blocked_status = features()
    blocked_status["earnings_axis_pit_status"] = "PIT_BLOCKED"
    with pytest.raises(ValueError, match="earnings_axis_pit_status"):
        evaluate_predefined_market_regimes(
            blocked_status, labels(), candidates(), holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )

    limited_state = features()
    limited_state["earnings_axis_state"] = "PIT_LIMITED"
    with pytest.raises(ValueError, match="complete typed states"):
        evaluate_predefined_market_regimes(
            limited_state, labels(), candidates(), holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )


def test_outcomes_are_forbidden_from_features_and_purge_must_cover_12_months():
    feature_frame = features()
    feature_frame["forward_return_63d"] = 999.0
    with pytest.raises(ValueError, match="outcome namespace"):
        evaluate_predefined_market_regimes(
            feature_frame, labels(), candidates(), holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )
    with pytest.raises(ValueError, match="walk-forward policy"):
        evaluate(purge=251)


def test_holdout_crossing_is_rejected_before_axis_or_outcome_values_are_read():
    feature_frame = features()
    label_frame = labels()
    feature_frame.loc[len(feature_frame) - 1, "observation_date"] = "2025-01-02"
    label_frame.loc[len(label_frame) - 1, "observation_date"] = "2025-01-02"
    feature_frame["earnings_axis_state"] = feature_frame[
        "earnings_axis_state"
    ].astype("object")
    label_frame["forward_return_252d"] = label_frame[
        "forward_return_252d"
    ].astype("object")
    feature_frame.loc[len(feature_frame) - 1, "earnings_axis_state"] = object()
    label_frame.loc[len(label_frame) - 1, "forward_return_252d"] = object()

    with pytest.raises(ValueError, match="crosses the untouched holdout"):
        evaluate_predefined_market_regimes(
            feature_frame, label_frame, candidates(), holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )


def test_label_availability_must_match_the_252nd_retained_session():
    label_frame = labels()
    label_frame["label_available_at"] = (
        label_frame["observation_date"] + "T15:30:00+09:00"
    )

    with pytest.raises(ValueError, match="252nd retained session"):
        evaluate_predefined_market_regimes(
            features(), label_frame, candidates(), holdout(),
            minimum_train=100, test_size=50, purge=252, embargo=5,
        )


def test_insufficient_candidate_is_typed_numeric_free_and_result_is_immutable():
    sparse = (MarketRegimeCandidate(
        "ABSENT_COMBINATION", "OVERBOUGHT", "HIGH", "REVISING_DOWN", 5,
    ),)
    result = evaluate_predefined_market_regimes(
        features(), labels(), sparse, holdout(),
        minimum_train=100, test_size=50, purge=252, embargo=5,
    )
    item = result.results[0]

    assert item.availability == "INSUFFICIENT_SIGNAL_OBSERVATIONS"
    assert item.signal_observations == 0
    assert all(metric.conditional_mean_return is None for metric in item.horizons)
    assert all(
        metric.conditional_mean_max_drawdown is None for metric in item.horizons
    )
    with pytest.raises(FrozenInstanceError):
        item.availability = "OTHER"
