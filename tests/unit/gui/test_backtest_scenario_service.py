from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import socket

import pandas as pd
import pytest

from market_backtest.indicator_strategy import (
    ThresholdBandPolicy,
    compare_threshold_band_to_matched_hold,
)
from market_backtest.portfolio import KOSPI200_FROZEN_HOLDOUT_V1
from stock_data.gui.backtest_scenario_service import (
    SCENARIO_ADAPTER_VERSION,
    SCENARIO_ID,
    SCENARIO_INPUT_VERSION,
    BacktestScenarioError,
    BacktestScenarioInputs,
    BacktestScenarioService,
)


def _inputs(*, rsi: list[float] | None = None) -> BacktestScenarioInputs:
    sessions = tuple(
        value.date().isoformat()
        for value in pd.bdate_range("2020-01-02", periods=30)
    )
    feature_dates = sessions[:-1]
    values = rsi or ([20.0] * 20 + [80.0] * 9)
    market = pd.DataFrame({
        "session_date": pd.Series(sessions, dtype="object"),
        "open": pd.Series(
            [100.0 + index for index in range(len(sessions))], dtype="float64",
        ),
        "close": pd.Series(
            [100.5 + index for index in range(len(sessions))], dtype="float64",
        ),
        "instrument_id": pd.Series(["KRX:1028"] * len(sessions), dtype="object"),
        "currency": pd.Series(["KRW"] * len(sessions), dtype="object"),
    })
    features = pd.DataFrame({
        "observation_date": pd.Series(feature_dates, dtype="object"),
        "ticker": pd.Series(["1028"] * len(feature_dates), dtype="object"),
        "date_semantics": pd.Series(
            ["KRX_TRADING_DATE_DAILY_FINAL"] * len(feature_dates),
            dtype="object",
        ),
        "instrument_id": pd.Series(
            ["KRX:1028"] * len(feature_dates), dtype="object",
        ),
        "usable_from": pd.Series(
            [f"{sessions[index + 1]}T09:00:00+09:00" for index in range(29)],
            dtype="object",
        ),
        "pit_status": pd.Series(
            ["PIT_SAFE_EOD_T_PLUS_1"] * len(feature_dates), dtype="object",
        ),
        "rsi_14": pd.Series(values, dtype="float64"),
    })
    label_dates = feature_dates[:-1]
    labels = pd.DataFrame({
        "observation_date": pd.Series(label_dates, dtype="object"),
        "ticker": pd.Series(["1028"] * len(label_dates), dtype="object"),
        "date_semantics": pd.Series(
            ["KRX_TRADING_DATE_DAILY_FINAL"] * len(label_dates), dtype="object",
        ),
        "label_available_at": pd.Series(
            ["2020-06-01T15:30:00+09:00"] * len(label_dates), dtype="object",
        ),
        "label_version": pd.Series([1] * len(label_dates), dtype="int64"),
        "forward_return_20d": pd.Series(
            [0.01 + index / 10_000 for index in range(len(label_dates))],
            dtype="float64",
        ),
        "forward_max_drawdown_20d": pd.Series(
            [-0.02 - index / 10_000 for index in range(len(label_dates))],
            dtype="float64",
        ),
    })
    return BacktestScenarioInputs(
        SCENARIO_INPUT_VERSION,
        SCENARIO_ID,
        market,
        features,
        labels,
        KOSPI200_FROZEN_HOLDOUT_V1,
    )


def test_fixed_scenario_returns_immutable_views_without_mutating_inputs():
    inputs = _inputs()
    before = tuple(
        frame.copy(deep=True)
        for frame in (inputs.market, inputs.features, inputs.labels)
    )

    view = BacktestScenarioService().evaluate(inputs)

    assert view.contract_version == SCENARIO_ADAPTER_VERSION
    assert view.scenario_id == "RSI14_30_70"
    assert view.winner_selected is False
    assert view.recommendation_provided is False
    assert tuple(row.candidate_id for row in view.conditional) == (
        "RSI14_LOW_30", "RSI14_HIGH_70",
    )
    assert tuple(row.threshold for row in view.conditional) == (30.0, 70.0)
    assert view.conditional[0].availability == "EVALUATED"
    assert view.execution.contract_version == "historical-next-open/v1"
    assert view.execution.trade_count == 2
    assert view.matched_hold.availability == "EVALUATED"
    assert view.matched_hold.total_return_difference is not None
    accepted = compare_threshold_band_to_matched_hold(
        inputs.market,
        inputs.features,
        ThresholdBandPolicy("RSI14_30_70", "rsi_14", 30.0, 70.0),
        KOSPI200_FROZEN_HOLDOUT_V1,
    )
    assert view.matched_hold.entry_observation_date == (
        accepted.entry_observation_date
    )
    assert view.matched_hold.entry_usable_from == accepted.entry_usable_from
    assert view.execution.transaction_cost_paid == (
        accepted.strategy.execution.metrics.transaction_cost_paid
    )
    assert view.matched_hold.incremental_transaction_cost == (
        accepted.metrics.incremental_transaction_cost
    )
    for frame, snapshot in zip(
        (inputs.market, inputs.features, inputs.labels), before, strict=True,
    ):
        pd.testing.assert_frame_equal(frame, snapshot)
    with pytest.raises(FrozenInstanceError):
        view.status = "changed"


@pytest.mark.parametrize(
    "mutate, message",
    (
        (lambda value: replace(value, contract_version="v2"), "identity"),
        (lambda value: replace(value, scenario_id="RSI14_20_80"), "identity"),
        (
            lambda value: replace(
                value, features=value.features.assign(extra_parameter=1),
            ),
            "schema",
        ),
    ),
)
def test_scenario_accepts_only_exact_version_identity_and_fixed_schema(
    mutate, message,
):
    with pytest.raises(BacktestScenarioError, match=message):
        BacktestScenarioService().evaluate(mutate(_inputs()))


def test_holdout_date_rejects_before_any_numeric_or_identity_value_access():
    class NumericProbe:
        touched = False

        def __float__(self):
            type(self).touched = True
            raise AssertionError("sealed row numeric value was inspected")

    holdout_date = KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
    probe = NumericProbe()
    market = pd.DataFrame({
        "session_date": [holdout_date],
        "open": [probe],
        "close": [probe],
        "instrument_id": [probe],
        "currency": [probe],
    })
    features = pd.DataFrame({
        "observation_date": [holdout_date],
        "ticker": [probe],
        "date_semantics": [probe],
        "instrument_id": [probe],
        "usable_from": [probe],
        "pit_status": [probe],
        "rsi_14": [probe],
    })
    labels = pd.DataFrame({
        "observation_date": [holdout_date],
        "ticker": [probe],
        "date_semantics": [probe],
        "label_available_at": [probe],
        "label_version": [probe],
        "forward_return_20d": [probe],
        "forward_max_drawdown_20d": [probe],
    })
    inputs = BacktestScenarioInputs(
        SCENARIO_INPUT_VERSION, SCENARIO_ID, market, features, labels,
    )

    with pytest.raises(BacktestScenarioError, match="untouched holdout"):
        BacktestScenarioService().evaluate(inputs)

    assert NumericProbe.touched is False


@pytest.mark.parametrize("case", ("identity", "clock"))
def test_development_identity_and_usable_clock_fail_closed(case):
    inputs = _inputs()
    features = inputs.features.copy(deep=True)
    if case == "identity":
        features.loc[0, "instrument_id"] = "KRX:OTHER"
    else:
        features.loc[0, "usable_from"] = "2020-01-03T15:30:00+09:00"

    with pytest.raises(BacktestScenarioError):
        BacktestScenarioService().evaluate(replace(inputs, features=features))


def test_no_entry_and_insufficient_signals_are_typed_and_numeric_free():
    view = BacktestScenarioService().evaluate(_inputs(rsi=[50.0] * 29))

    assert all(
        row.availability == "INSUFFICIENT_SIGNAL_OBSERVATIONS"
        and row.conditional_mean_return is None
        and row.mean_return_difference is None
        for row in view.conditional
    )
    assert view.execution.trade_count == 0
    assert view.matched_hold.availability == "NO_ENTRY_OBSERVATION"
    assert view.matched_hold.ending_nav_difference is None
    assert view.matched_hold.total_return_difference is None


def test_scenario_service_is_provider_free(monkeypatch):
    def forbidden_socket(*_args, **_kwargs):
        raise AssertionError("provider call attempted")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    view = BacktestScenarioService().evaluate(_inputs())
    assert view.status == "DEVELOPMENT_ONLY_FIXED_SCENARIO"
