from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from market_backtest.holdout import CoverageHoldout
from market_backtest.indicator_strategy import (
    MATCHED_HOLD_CONTRACT_VERSION,
    MATCHED_HOLD_STATUS,
    THRESHOLD_BAND_CONTRACT_VERSION,
    THRESHOLD_BAND_STATUS,
    ThresholdBandPolicy,
    compare_threshold_band_to_matched_hold,
    simulate_predefined_threshold_band,
)


def holdout() -> CoverageHoldout:
    return CoverageHoldout(
        policy_id="SYNTHETIC_UNTOUCHED",
        coverage_start="2019-01-01",
        coverage_end="2021-12-31",
        holdout_start="2021-01-01",
        development_observations=5,
        holdout_observations=2,
        results_reviewed=False,
    )


def market() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=5)
    return pd.DataFrame({
        "session_date": dates.strftime("%Y-%m-%d"),
        "open": (100.0, 110.0, 100.0, 90.0, 120.0),
        "close": (105.0, 100.0, 95.0, 100.0, 125.0),
        "instrument_id": "KRX:069500",
        "currency": "KRW",
    })


def features(values: tuple[float, ...] = (20.0, 25.0, 50.0, 80.0)) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=5)
    return pd.DataFrame({
        "observation_date": dates[:-1].strftime("%Y-%m-%d"),
        "instrument_id": "KRX:069500",
        "usable_from": dates[1:].strftime("%Y-%m-%dT09:00:00+09:00"),
        "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "rsi_14": values,
    })


def policy() -> ThresholdBandPolicy:
    return ThresholdBandPolicy(
        "RSI14_30_70", "rsi_14", 30.0, 70.0,
    )


def test_threshold_band_enters_and_exits_only_on_predefined_crossing_states():
    result = simulate_predefined_threshold_band(
        market(), features(), policy(), holdout(),
    )

    assert result.contract_version == THRESHOLD_BAND_CONTRACT_VERSION
    assert result.status == THRESHOLD_BAND_STATUS
    assert [row.reason for row in result.decisions] == [
        "ENTER_AT_OR_BELOW", "EXIT_AT_OR_ABOVE",
    ]
    assert [row.observation_date for row in result.decisions] == [
        "2020-01-02", "2020-01-07",
    ]
    assert [row.trade_side for row in result.execution.ledger] == [
        "NONE", "BUY", "NONE", "NONE", "SELL",
    ]
    assert result.execution.ledger[1].fill_price == 110.0
    assert result.execution.ledger[4].fill_price == 120.0


def test_hysteresis_does_not_repeat_buy_or_sell_inside_the_same_state():
    result = simulate_predefined_threshold_band(
        market(), features((10.0, 20.0, 80.0, 90.0)), policy(), holdout(),
    )

    assert [row.target_long for row in result.decisions] == [True, False]
    assert result.execution.metrics.trade_count == 2


def test_no_transition_is_a_valid_all_cash_scenario():
    result = simulate_predefined_threshold_band(
        market(), features((40.0, 45.0, 50.0, 60.0)), policy(), holdout(),
    )

    assert result.decisions == ()
    assert result.execution.metrics.ending_nav == 1.0
    assert result.execution.metrics.trade_count == 0


def test_matched_hold_enters_at_the_exact_same_first_fill_and_never_exits():
    comparison = compare_threshold_band_to_matched_hold(
        market(), features(), policy(), holdout(),
    )

    assert comparison.contract_version == MATCHED_HOLD_CONTRACT_VERSION
    assert comparison.status == MATCHED_HOLD_STATUS
    assert comparison.availability == "EVALUATED"
    assert comparison.winner_selected is False
    assert comparison.entry_observation_date == "2020-01-02"
    assert comparison.entry_usable_from == "2020-01-03T09:00:00+09:00"
    assert comparison.baseline is not None
    assert [row.trade_side for row in comparison.baseline.ledger] == [
        "NONE", "BUY", "NONE", "NONE", "NONE",
    ]
    assert comparison.baseline.ledger[1].fill_price == (
        comparison.strategy.execution.ledger[1].fill_price
    )


def test_matched_hold_reports_exact_relative_return_drawdown_and_costs():
    comparison = compare_threshold_band_to_matched_hold(
        market(), features(), policy(), holdout(),
    )
    assert comparison.baseline is not None
    assert comparison.metrics is not None
    strategy = comparison.strategy.execution.metrics
    baseline = comparison.baseline.metrics
    metrics = comparison.metrics

    assert metrics.ending_nav_difference == pytest.approx(
        strategy.ending_nav - baseline.ending_nav
    )
    assert metrics.total_return_difference == pytest.approx(
        strategy.total_return - baseline.total_return
    )
    assert metrics.strategy_max_drawdown == strategy.max_drawdown
    assert metrics.baseline_max_drawdown == baseline.max_drawdown
    assert metrics.annualized_volatility_difference == pytest.approx(
        strategy.annualized_volatility - baseline.annualized_volatility
    )
    assert metrics.strategy_total_turnover == strategy.total_turnover
    assert metrics.baseline_total_turnover == baseline.total_turnover
    assert metrics.incremental_transaction_cost == pytest.approx(
        strategy.transaction_cost_paid - baseline.transaction_cost_paid
    )
    assert not hasattr(comparison, "winner")
    assert not hasattr(comparison, "ranking")


def test_matched_hold_without_entry_is_typed_unavailable_not_a_fake_baseline():
    comparison = compare_threshold_band_to_matched_hold(
        market(),
        features((40.0, 45.0, 50.0, 60.0)),
        policy(),
        holdout(),
    )

    assert comparison.availability == "NO_ENTRY_OBSERVATION"
    assert comparison.entry_observation_date is None
    assert comparison.entry_usable_from is None
    assert comparison.baseline is None
    assert comparison.metrics is None


def test_matched_hold_comparison_is_deterministic_and_immutable():
    first = compare_threshold_band_to_matched_hold(
        market(), features(), policy(), holdout(),
    )
    second = compare_threshold_band_to_matched_hold(
        market(), features(), policy(), holdout(),
    )

    assert first == second
    assert first.metrics is not None
    with pytest.raises(FrozenInstanceError):
        first.metrics.ending_nav_difference = 0.0


def test_result_is_immutable_and_deterministic():
    first = simulate_predefined_threshold_band(
        market(), features(), policy(), holdout(),
    )
    second = simulate_predefined_threshold_band(
        market(), features(), policy(), holdout(),
    )

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.decisions[0].target_long = False


@pytest.mark.parametrize(
    "usable_from",
    [
        "2020-01-02T09:00:00+09:00",
        "2020-01-06T09:00:00+09:00",
        "2020-01-03T09:00:00",
    ],
)
def test_feature_usable_clock_must_equal_exact_next_retained_session(usable_from):
    feature_frame = features()
    feature_frame.loc[0, "usable_from"] = usable_from

    with pytest.raises(ValueError, match="usable_from"):
        simulate_predefined_threshold_band(
            market(), feature_frame, policy(), holdout(),
        )


def test_holdout_is_rejected_before_price_or_indicator_values_are_inspected():
    market_frame = market()
    feature_frame = features()
    market_frame.loc[4, "session_date"] = "2021-01-04"
    feature_frame.loc[3, "observation_date"] = "2021-01-01"
    market_frame["open"] = market_frame["open"].astype("object")
    feature_frame["rsi_14"] = feature_frame["rsi_14"].astype("object")
    market_frame.loc[4, "open"] = "DO_NOT_INSPECT"
    feature_frame.loc[3, "rsi_14"] = "DO_NOT_INSPECT"

    with pytest.raises(ValueError, match="untouched holdout"):
        simulate_predefined_threshold_band(
            market_frame, feature_frame, policy(), holdout(),
        )


def test_instrument_mismatch_and_missing_market_date_fail_closed():
    feature_frame = features()
    feature_frame["instrument_id"] = "OTHER"
    with pytest.raises(ValueError, match="identity differs"):
        simulate_predefined_threshold_band(
            market(), feature_frame, policy(), holdout(),
        )

    feature_frame = features()
    feature_frame.loc[0, "observation_date"] = "2019-12-31"
    with pytest.raises(ValueError, match="exact retained"):
        simulate_predefined_threshold_band(
            market(), feature_frame, policy(), holdout(),
        )


def test_feature_on_final_market_session_cannot_create_a_no_fill_decision():
    market_frame = market()
    feature_frame = features()
    final = pd.DataFrame({
        "observation_date": [market_frame.loc[4, "session_date"]],
        "instrument_id": ["KRX:069500"],
        "usable_from": ["2020-01-10T09:00:00+09:00"],
        "pit_status": ["PIT_SAFE_EOD_T_PLUS_1"],
        "rsi_14": [20.0],
    })
    feature_frame = pd.concat([feature_frame, final], ignore_index=True)

    with pytest.raises(ValueError, match="no next retained"):
        simulate_predefined_threshold_band(
            market_frame, feature_frame, policy(), holdout(),
        )


def test_outcome_columns_nonfinite_values_and_non_pit_features_fail_closed():
    feature_frame = features()
    feature_frame["forward_return_20d"] = 1.0
    with pytest.raises(ValueError, match="outcome namespace"):
        simulate_predefined_threshold_band(
            market(), feature_frame, policy(), holdout(),
        )

    feature_frame = features()
    feature_frame.loc[0, "rsi_14"] = float("nan")
    with pytest.raises(ValueError, match="real numeric and finite"):
        simulate_predefined_threshold_band(
            market(), feature_frame, policy(), holdout(),
        )

    feature_frame = features()
    feature_frame["pit_status"] = "PIT_LIMITED"
    with pytest.raises(ValueError, match="PIT_SAFE"):
        simulate_predefined_threshold_band(
            market(), feature_frame, policy(), holdout(),
        )


def test_policy_is_strict_and_never_allows_an_initial_unpriced_position():
    with pytest.raises(ValueError, match="policy"):
        ThresholdBandPolicy("BAD", "rsi_14", 70.0, 30.0)
    with pytest.raises(ValueError, match="policy"):
        ThresholdBandPolicy("BAD", "rsi_14", 30.0, 70.0, initial_long=True)


def test_malformed_or_reviewed_holdout_policy_fails_closed():
    with pytest.raises(ValueError, match="untouched"):
        CoverageHoldout(
            policy_id="SYNTHETIC_UNTOUCHED",
            coverage_start="2019-01-01",
            coverage_end="2021-12-31",
            holdout_start="2021-01-01",
            development_observations=5,
            holdout_observations=2,
            results_reviewed=True,
        )

    malformed = CoverageHoldout(
        policy_id="SYNTHETIC_UNTOUCHED",
        coverage_start="2021-06-01",
        coverage_end="2021-12-31",
        holdout_start="2021-01-01",
        development_observations=5,
        holdout_observations=2,
        results_reviewed=False,
    )
    with pytest.raises(ValueError, match="identity"):
        simulate_predefined_threshold_band(
            market(), features(), policy(), malformed,
        )

    too_late_start = CoverageHoldout(
        policy_id="SYNTHETIC_UNTOUCHED",
        coverage_start="2020-01-03",
        coverage_end="2021-12-31",
        holdout_start="2021-01-01",
        development_observations=5,
        holdout_observations=2,
        results_reviewed=False,
    )
    with pytest.raises(ValueError, match="within coverage"):
        simulate_predefined_threshold_band(
            market(), features(), policy(), too_late_start,
        )
