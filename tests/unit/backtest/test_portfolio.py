from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import market_backtest
import numpy as np
import pandas as pd
import pytest

from market_backtest.holdout import CoverageHoldout
from market_backtest.portfolio import (
    CLOSE_PROXY_V1,
    INSTRUMENT_CLAIM,
    KOSPI200_FROZEN_HOLDOUT_V1,
    PORTFOLIO_STATUS,
    PortfolioAssumptions,
    PortfolioSimulation,
    simulate_kospi200_risk_off_portfolio,
)


def holdout() -> CoverageHoldout:
    return KOSPI200_FROZEN_HOLDOUT_V1


def prices(
    close: tuple[float, ...] = (100.0, 100.0, 110.0, 99.0),
    *,
    start: str = "2020-01-02",
) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": close,
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def signals(
    price_frame: pd.DataFrame,
    risk_off: tuple[bool, ...] | None = None,
    *,
    first_price_index: int = 0,
) -> pd.DataFrame:
    observation_dates = price_frame["date"].iloc[first_price_index:-1].reset_index(
        drop=True
    )
    execution_dates = price_frame["date"].iloc[first_price_index + 1:].reset_index(
        drop=True
    )
    decisions = risk_off or tuple(False for _ in range(len(observation_dates)))
    assert len(decisions) == len(observation_dates)
    return pd.DataFrame({
        "observation_date": observation_dates,
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
        "usable_from": execution_dates + "T09:00:00+09:00",
        "source_dataset": "kr_kospi200_index_daily",
        "source_contract_version": pd.Series(
            [1] * len(observation_dates), dtype="int64",
        ),
        "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "risk_off_signal": pd.Series(decisions, dtype="bool"),
        "risk_score": pd.Series(
            [2 if decision else 0 for decision in decisions], dtype="int64",
        ),
        "signal_version": pd.Series(
            [1] * len(observation_dates), dtype="int64",
        ),
    })


def simulate(
    price_frame: pd.DataFrame,
    risk_off: tuple[bool, ...] | None = None,
    *,
    first_price_index: int = 0,
) -> PortfolioSimulation:
    return simulate_kospi200_risk_off_portfolio(
        price_frame,
        signals(
            price_frame, risk_off, first_price_index=first_price_index,
        ),
        holdout(),
    )


def test_close_proxy_result_is_typed_immutable_and_explicitly_non_executable():
    result = simulate(prices(), (False, False, False))

    assert result.status == PORTFOLIO_STATUS == "DEVELOPMENT_ONLY_CLOSE_PROXY"
    assert result.instrument_claim == INSTRUMENT_CLAIM == "NOT_EXECUTABLE_INSTRUMENT"
    assert result.assumptions == CLOSE_PROXY_V1
    assert isinstance(result.ledger, tuple)
    assert result.ledger[-1].target_position == 1
    assert result.ledger[-1].units > 0.0
    with pytest.raises(FrozenInstanceError):
        result.ledger[-1].nav = 2.0


def test_t_signal_executes_only_at_next_retained_final_close():
    result = simulate(prices((100.0, 200.0)), (False,))

    initial, execution = result.ledger
    assert initial.date == "2020-01-02"
    assert initial.nav == 1.0
    assert execution.date == "2020-01-03"
    assert execution.signal_observation_date == initial.date
    assert execution.usable_from == "2020-01-03T09:00:00+09:00"
    assert execution.position_before == 0
    assert execution.target_position == 1
    assert execution.gross_portfolio_return == 0.0
    assert execution.nav == pytest.approx(1.0 / 1.001)
    assert result.metrics.total_return < 0.0


def test_current_risk_off_signal_cannot_avoid_prior_interval_loss():
    result = simulate(prices((100.0, 100.0, 50.0)), (False, True))

    buy, sell = result.ledger[1:]
    assert buy.target_position == 1
    assert sell.position_before == 1
    assert sell.target_position == 0
    assert sell.market_return == pytest.approx(-0.5)
    assert sell.gross_portfolio_return == pytest.approx(-0.5)
    assert sell.nav_before_cost == pytest.approx(buy.nav * 0.5)


def test_flat_cash_long_cash_path_charges_exact_self_financing_ten_basis_points():
    result = simulate(prices((100.0, 100.0, 100.0)), (False, True))
    buy, sell = result.ledger[1:]
    cost_rate = CLOSE_PROXY_V1.one_way_transaction_cost_rate

    assert buy.transaction_cost / buy.trade_notional == pytest.approx(cost_rate)
    assert sell.transaction_cost / sell.trade_notional == pytest.approx(cost_rate)
    assert buy.nav == pytest.approx(1.0 / (1.0 + cost_rate))
    assert sell.nav == pytest.approx((1.0 - cost_rate) / (1.0 + cost_rate))
    assert result.metrics.trade_count == 2
    assert result.metrics.transaction_cost_paid == pytest.approx(
        buy.transaction_cost + sell.transaction_cost
    )


def test_every_ledger_row_conserves_cash_asset_and_transaction_cost():
    result = simulate(
        prices((100.0, 90.0, 110.0, 80.0, 120.0)),
        (False, False, True, False),
    )

    for row in result.ledger:
        assert row.cash + row.asset_value == pytest.approx(row.nav)
        assert row.nav == pytest.approx(row.nav_before_cost - row.transaction_cost)
        assert row.asset_value == pytest.approx(row.units * row.close)
        if row.trade_notional:
            assert row.turnover == pytest.approx(
                row.trade_notional / row.nav_before_cost
            )


def test_known_metrics_drawdown_volatility_turnover_and_exposure_match():
    result = simulate(prices(), (False, False, False))
    rate = CLOSE_PROXY_V1.one_way_transaction_cost_rate
    expected_nav = 0.99 / (1.0 + rate)
    expected_returns = np.array([-rate / (1.0 + rate), 0.10, -0.10])

    assert result.metrics.observations == 4
    assert result.metrics.intervals == 3
    assert result.metrics.ending_nav == pytest.approx(expected_nav)
    assert result.metrics.total_return == pytest.approx(expected_nav - 1.0)
    assert result.metrics.annualized_return == pytest.approx(
        expected_nav ** (252.0 / 3.0) - 1.0
    )
    assert result.metrics.annualized_volatility == pytest.approx(
        expected_returns.std(ddof=1) * np.sqrt(252.0)
    )
    assert result.metrics.max_drawdown == pytest.approx(-0.10)
    assert result.metrics.trade_count == 1
    assert result.metrics.total_turnover == pytest.approx(1.0 / (1.0 + rate))
    assert result.metrics.average_long_exposure == pytest.approx(2.0 / 3.0)


def test_all_cash_path_has_zero_returns_exposure_costs_and_drawdown():
    result = simulate(prices((100.0, 80.0, 120.0)), (True, True))

    assert result.metrics.ending_nav == 1.0
    assert result.metrics.total_return == 0.0
    assert result.metrics.annualized_return == 0.0
    assert result.metrics.annualized_volatility == 0.0
    assert result.metrics.max_drawdown == 0.0
    assert result.metrics.trade_count == 0
    assert result.metrics.total_turnover == 0.0
    assert result.metrics.average_long_exposure == 0.0
    assert result.metrics.transaction_cost_paid == 0.0


def test_price_lookback_prefix_is_allowed_but_not_included_in_ledger():
    price_frame = prices((80.0, 90.0, 100.0, 110.0, 120.0))
    result = simulate(
        price_frame, (False, False), first_price_index=2,
    )

    assert [row.date for row in result.ledger] == price_frame["date"].iloc[2:].tolist()
    assert result.metrics.observations == 3


@pytest.mark.parametrize(
    "usable_from",
    [
        "2020-01-02T09:00:00+09:00",
        "2020-01-03T09:00:01+09:00",
        "2020-01-03T00:00:00+00:00",
        "2020-01-03 09:00:00+09:00",
        None,
    ],
)
def test_usable_clock_must_be_exact_next_retained_date_at_0900(usable_from):
    price_frame = prices((100.0, 100.0))
    signal_frame = signals(price_frame, (False,))
    signal_frame.loc[0, "usable_from"] = usable_from

    with pytest.raises(ValueError, match="usable_from"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


def test_signal_dates_cannot_skip_or_leave_an_unexplained_retained_gap():
    price_frame = prices((100.0, 101.0, 102.0, 103.0))
    signal_frame = signals(price_frame, (False, False, False)).drop(index=1)

    with pytest.raises(ValueError, match="contiguous retained-price suffix"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


@pytest.mark.parametrize(
    ("artifact", "column", "value"),
    [
        ("price", "ticker", "KOSPI200"),
        ("price", "date_semantics", "CALENDAR_DATE"),
        ("signal", "ticker", "KOSPI200"),
        ("signal", "date_semantics", "CALENDAR_DATE"),
        ("signal", "source_dataset", "other"),
        ("signal", "source_contract_version", 2),
        ("signal", "pit_status", "PIT_LIMITED"),
        ("signal", "signal_version", 2),
    ],
)
def test_canonical_identity_and_lineage_are_exact(artifact, column, value):
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    target = price_frame if artifact == "price" else signal_frame
    target[column] = value

    with pytest.raises(ValueError):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


@pytest.mark.parametrize(
    "bad_close",
    ["100", True, complex(100, 1), np.nan, np.inf, 0.0, -1.0],
)
def test_close_rejects_coercion_nonfinite_and_nonpositive_values(bad_close):
    price_frame = prices((100.0, 101.0))
    if isinstance(bad_close, (str, bool, complex)):
        price_frame["close"] = price_frame["close"].astype("object")
    price_frame.loc[0, "close"] = bad_close

    with pytest.raises(ValueError, match="close"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signals(prices((100.0, 101.0)), (False,)), holdout(),
        )


@pytest.mark.parametrize(
    "decisions",
    [
        pd.Series(["False"], dtype="object"),
        pd.Series([False], dtype="object"),
        pd.Series([0], dtype="int64"),
        pd.Series([None], dtype="object"),
        pd.Series([pd.NA], dtype="boolean"),
    ],
)
def test_risk_off_signal_rejects_non_exact_boolean_dtype(decisions):
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    signal_frame["risk_off_signal"] = decisions

    with pytest.raises(ValueError, match="non-null boolean"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


@pytest.mark.parametrize(
    "scores",
    [
        pd.Series([1.0], dtype="float64"),
        pd.Series([True], dtype="bool"),
        pd.Series([-1], dtype="int64"),
        pd.Series([5], dtype="int64"),
        pd.Series([pd.NA], dtype="Int64"),
    ],
)
def test_risk_score_rejects_wrong_type_missing_or_range(scores):
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    signal_frame["risk_score"] = scores

    with pytest.raises(ValueError, match="risk_score"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


@pytest.mark.parametrize("artifact", ["price", "signal"])
def test_duplicate_columns_are_rejected_without_implicit_selection(artifact):
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    if artifact == "price":
        price_frame = pd.concat(
            [price_frame, price_frame.loc[:, ["close"]]], axis=1,
        )
    else:
        signal_frame = pd.concat(
            [signal_frame, signal_frame.loc[:, ["risk_off_signal"]]], axis=1,
        )

    with pytest.raises(ValueError, match="schema/content"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


@pytest.mark.parametrize("artifact", ["price", "signal"])
@pytest.mark.parametrize("column", ["forward_return_20d", "label_available_at"])
def test_labels_and_outcomes_are_forbidden_from_portfolio_inputs(artifact, column):
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    target = price_frame if artifact == "price" else signal_frame
    target[column] = 0.0

    with pytest.raises(ValueError, match="outcome namespace"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


@pytest.mark.parametrize(
    "bad_dates",
    [
        ["2020-1-02", "2020-01-03"],
        ["2020-01-03", "2020-01-02"],
        ["2020-01-02", "2020-01-02"],
        [pd.Timestamp("2020-01-02"), "2020-01-03"],
        [pd.Timestamp("2020-01-02").date(), "2020-01-03"],
    ],
)
def test_price_dates_reject_noncanonical_unsorted_and_duplicate_keys(bad_dates):
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    price_frame["date"] = bad_dates

    with pytest.raises(ValueError, match="price date|price dates"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


class PoisonValue:
    def __float__(self):
        raise AssertionError("holdout value was inspected")

    def __bool__(self):
        raise AssertionError("holdout value was inspected")

    def __eq__(self, _other):
        raise AssertionError("holdout value was inspected")


def test_poisoned_price_holdout_row_is_rejected_before_close_is_inspected():
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    price_frame.loc[len(price_frame)] = {
        "date": holdout().holdout_start,
        "close": PoisonValue(),
        "ticker": PoisonValue(),
        "date_semantics": PoisonValue(),
    }

    with pytest.raises(ValueError, match="before untouched holdout"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


def test_poisoned_signal_holdout_row_is_rejected_before_decision_is_inspected():
    price_frame = prices((100.0, 101.0))
    signal_frame = signals(price_frame, (False,))
    signal_frame.loc[len(signal_frame)] = {
        "observation_date": holdout().holdout_start,
        "ticker": PoisonValue(),
        "date_semantics": PoisonValue(),
        "usable_from": PoisonValue(),
        "source_dataset": PoisonValue(),
        "source_contract_version": PoisonValue(),
        "pit_status": PoisonValue(),
        "risk_off_signal": PoisonValue(),
        "risk_score": PoisonValue(),
        "signal_version": PoisonValue(),
    }

    with pytest.raises(ValueError, match="before untouched holdout"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signal_frame, holdout(),
        )


def test_inputs_remain_unchanged_and_repeated_results_are_identical():
    price_frame = prices((100.0, 90.0, 110.0, 95.0))
    signal_frame = signals(price_frame, (False, True, False))
    price_before = price_frame.copy(deep=True)
    signal_before = signal_frame.copy(deep=True)

    first = simulate_kospi200_risk_off_portfolio(
        price_frame, signal_frame, holdout(),
    )
    second = simulate_kospi200_risk_off_portfolio(
        price_frame, signal_frame, holdout(),
    )

    assert first == second
    pd.testing.assert_frame_equal(price_frame, price_before)
    pd.testing.assert_frame_equal(signal_frame, signal_before)


def test_only_frozen_close_proxy_assumptions_are_accepted():
    price_frame = prices((100.0, 101.0))
    with pytest.raises(ValueError, match="fixed CLOSE_PROXY_V1"):
        simulate_kospi200_risk_off_portfolio(
            price_frame,
            signals(price_frame, (False,)),
            holdout(),
            replace(CLOSE_PROXY_V1, one_way_transaction_cost_rate=0.0),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_nav", 1),
        ("cash_yield_rate", 0),
        ("cash_yield_rate", np.float64(0.0)),
        ("one_way_transaction_cost_rate", np.float64(0.001)),
        ("long_position", True),
        ("cash_position", False),
        ("cash_position", 0.0),
        ("annualization_sessions", np.int64(252)),
        ("leverage_allowed", np.bool_(False)),
        ("leverage_allowed", 0),
        ("shorting_allowed", 0),
        ("forced_liquidation", 0),
    ],
)
def test_fixed_assumptions_require_exact_builtin_types(field, value):
    values = {
        item: getattr(CLOSE_PROXY_V1, item)
        for item in PortfolioAssumptions.__dataclass_fields__
    }
    values[field] = value

    with pytest.raises(ValueError, match="portfolio assumptions are invalid"):
        PortfolioAssumptions(**values)


def test_simulator_revalidates_assumption_types_after_illicit_mutation():
    price_frame = prices((100.0, 101.0))
    assumptions = PortfolioAssumptions()
    object.__setattr__(assumptions, "long_position", True)

    with pytest.raises(ValueError, match="portfolio assumptions are invalid"):
        simulate_kospi200_risk_off_portfolio(
            price_frame,
            signals(price_frame, (False,)),
            holdout(),
            assumptions,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_id", "UNTOUCHED_FINAL_6_CALENDAR_YEARS"),
        ("coverage_start", "1990-01-04"),
        ("coverage_end", "2030-08-14"),
        ("holdout_start", "2027-08-17"),
        ("development_observations", 8224),
        ("holdout_observations", 1221),
        ("results_reviewed", 0),
    ],
)
def test_portfolio_rejects_any_substitute_holdout_identity(field, value):
    price_frame = prices((100.0, 101.0))
    substitute = replace(holdout(), **{field: value})

    with pytest.raises(ValueError, match="(?i)holdout|fixed KOSPI200 frozen slice"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signals(price_frame, (False,)), substitute,
        )


def test_later_spoofed_holdout_cannot_reclassify_2022_rows_as_development():
    price_frame = prices((100.0, 101.0), start="2022-01-03")
    substitute = replace(holdout(), holdout_start="2025-01-01")

    with pytest.raises(ValueError, match="fixed KOSPI200 frozen slice"):
        simulate_kospi200_risk_off_portfolio(
            price_frame, signals(price_frame, (False,)), substitute,
        )


@pytest.mark.parametrize(
    "close",
    [
        (1e-308, 1e308),
        (1.0, 1.0, 1_000.0),
    ],
)
def test_nonfinite_derived_ledger_or_metrics_fail_closed(close):
    price_frame = prices(close)

    with pytest.raises(ValueError, match="must remain finite"):
        simulate(
            price_frame,
            tuple(False for _ in range(len(price_frame) - 1)),
        )


def test_package_root_exports_portfolio_status_claim_and_frozen_holdout():
    assert market_backtest.PORTFOLIO_STATUS is PORTFOLIO_STATUS
    assert market_backtest.INSTRUMENT_CLAIM is INSTRUMENT_CLAIM
    assert market_backtest.KOSPI200_FROZEN_HOLDOUT_V1 is holdout()
