from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.research.compound_ladder import (
    LadderSpec,
    ladder_levels,
    simulate_account,
    simulate_baseline,
    simulate_grid_metrics,
    validate_grid_row,
    with_baseline_comparison,
)
from stock_data.research.leveraged_product import (
    price_from_returns,
    synthetic_daily_returns,
)


def _dates(n: int) -> pd.Series:
    return pd.Series(pd.bdate_range("2020-01-01", periods=n))


def test_known_drawdown_level_path_and_next_session_execution() -> None:
    signals = pd.DataFrame(
        {
            "date": _dates(5),
            "drawdown252": [-0.05, -0.21, -0.21, -0.05, np.nan],
            "disp60": [-0.02, -0.05, -0.11, -0.11, -0.11],
        }
    )
    result = ladder_levels(signals, LadderSpec(levels=2))
    assert result["observed_level"].tolist() == [0, 1, 2, 1, pd.NA]
    assert result["executable_level"].tolist() == [pd.NA, 0, 1, 2, 1]


def test_execution_at_next_close_earns_only_following_return() -> None:
    returns = pd.Series([0.0, 0.10, 0.10, 0.0])
    levels = pd.Series([np.nan, 1.0, 1.0, 0.0])
    result = simulate_account(
        _dates(4), returns, levels, spec=LadderSpec(levels=1), transaction_cost=0.0
    )
    assert result.curve["wealth"].tolist() == pytest.approx([1.0, 1.0, 1.1, 1.1])
    assert result.trades["date"].tolist() == [_dates(4).iloc[1], _dates(4).iloc[3]]


def test_exit_a_reverse_score_partially_sells() -> None:
    returns = pd.Series([0.0, 0.0, 0.10, 0.10])
    levels = pd.Series([np.nan, 2.0, 1.0, 0.0])
    result = simulate_account(
        _dates(4), returns, levels, spec=LadderSpec(levels=2), transaction_cost=0.0
    )
    # Full exposure earns +10%, then half exposure earns the next +10%.
    assert result.curve["wealth"].iloc[-1] == pytest.approx(1.155)
    assert result.trades["target_weight"].tolist() == pytest.approx([1.0, 0.5, 0.0])


@pytest.mark.parametrize(("variant", "holding"), [("b60", 60), ("b120", 120)])
def test_fixed_period_exit_returns_to_base_after_n_sessions(variant: str, holding: int) -> None:
    n = holding + 3
    levels = pd.Series([np.nan, 1.0] + [1.0] * (n - 2))
    result = simulate_account(
        _dates(n),
        pd.Series(np.zeros(n)),
        levels,
        spec=LadderSpec(levels=1),
        exit_variant=variant,
        transaction_cost=0.0,
    )
    assert result.trades.iloc[0]["target_weight"] == 1.0
    assert result.trades.iloc[1]["target_weight"] == 0.0
    assert result.trades.iloc[1]["date"] == _dates(n).iloc[holding + 1]


def test_profit_exit_sells_original_thirds_at_each_30_percent_gain() -> None:
    prices = pd.Series([1.0, 1.0, 1.30, 1.60, 1.90])
    returns = prices.pct_change(fill_method=None).fillna(0.0)
    levels = pd.Series([np.nan, 1.0, 1.0, 1.0, 1.0])
    result = simulate_account(
        _dates(5),
        returns,
        levels,
        spec=LadderSpec(levels=1),
        exit_variant="c",
        transaction_cost=0.0,
    )
    assert result.trades["action"].tolist() == [
        "profit_entry",
        "profit_take_1",
        "profit_take_2",
        "profit_take_3",
    ]
    assert result.curve["wealth"].iloc[-1] == pytest.approx(1.6)
    assert result.curve["weight"].iloc[-1] == pytest.approx(0.0)


def test_never_sell_enters_once_and_holds_forever() -> None:
    returns = pd.Series([0.0, 0.0, 0.10, -0.10, 0.10])
    levels = pd.Series([np.nan, 1.0, 0.0, 1.0, 0.0])
    result = simulate_account(
        _dates(5),
        returns,
        levels,
        spec=LadderSpec(levels=2),
        exit_variant="d",
        transaction_cost=0.0,
    )
    assert len(result.trades) == 1
    assert result.curve["wealth"].iloc[-1] == pytest.approx(1.089)


def test_transaction_cost_is_paid_on_each_one_way_trade() -> None:
    baseline = simulate_baseline(_dates(2), pd.Series([0.0, 0.0]), transaction_cost=0.001)
    assert baseline.curve["wealth"].iloc[0] == pytest.approx(1.0 / 1.001)
    assert baseline.metrics["full"]["final_wealth_multiple"] == pytest.approx(1.0 / 1.001)
    strategy = simulate_account(
        _dates(3),
        pd.Series([0.0, 0.0, 0.0]),
        pd.Series([np.nan, 1.0, 0.0]),
        spec=LadderSpec(levels=1),
        transaction_cost=0.001,
    )
    assert strategy.metrics["full"]["transaction_cost"] > 0.0019


def test_chaining_reinvests_cash_from_first_episode() -> None:
    returns = pd.Series([0.0, 0.0, 0.10, 0.0, 0.10, 0.0])
    levels = pd.Series([np.nan, 1.0, 0.0, 1.0, 0.0, 0.0])
    result = simulate_account(
        _dates(6), returns, levels, spec=LadderSpec(levels=1), transaction_cost=0.0
    )
    assert result.curve["wealth"].iloc[-1] == pytest.approx(1.21)
    assert len(result.cycles) == 2


def test_daily_reset_two_x_path_is_point_nine_six_not_point_nine_nine() -> None:
    close = pd.Series([100.0, 110.0, 99.0])
    returns = synthetic_daily_returns(
        close,
        leverage_multiple=2,
        annual_expense_ratio=0.0,
        annual_short_rate=0.0,
    )
    assert price_from_returns(returns).iloc[-1] == pytest.approx(0.96)
    assert close.iloc[-1] / close.iloc[0] == pytest.approx(0.99)


def test_zero_nav_is_terminal_and_never_divides_by_zero() -> None:
    dates = _dates(4)
    returns = pd.Series([0.0, -1.0, 0.0, 0.0])
    baseline = simulate_baseline(dates, returns, transaction_cost=0.0)
    assert baseline.metrics["full"]["final_wealth_multiple"] == 0.0
    assert baseline.metrics["full"]["max_drawdown"] == -1.0
    strategy = simulate_account(
        dates,
        returns,
        pd.Series([np.nan, 1.0, 0.0, 1.0]),
        spec=LadderSpec(levels=1),
        transaction_cost=0.0,
    )
    assert strategy.curve["wealth"].tolist() == pytest.approx([1.0, 1.0, 1.0, 1.0])
    fast = simulate_grid_metrics(
        dates,
        returns,
        pd.Series([np.nan, 1.0, 0.0, 1.0]),
        spec=LadderSpec(levels=1),
        exit_variant="c",
        transaction_cost=0.0,
    )
    assert fast["full"]["final_wealth_multiple"] == 1.0


def test_grid_row_schema_and_baseline_comparison() -> None:
    metrics = {
        period: {"final_wealth_multiple": 1.2, "cagr": 0.1, "max_drawdown": -0.2}
        for period in ("fit", "holdout", "full")
    }
    baseline = {
        period: {"final_wealth_multiple": 1.1, "cagr": 0.08, "max_drawdown": -0.3}
        for period in ("fit", "holdout", "full")
    }
    compared = with_baseline_comparison(metrics, baseline)
    row = {
        "row_kind": "strategy",
        "basket": "KR",
        "underlying": "KOSPI200",
        "drawdown_threshold": -0.20,
        "disp60_threshold": -0.10,
        "levels": 2,
        "leverage_multiple": 2,
        "exit": "a",
        "cost_enabled": True,
        **compared,
    }
    validate_grid_row(row)
    assert row["holdout"]["relative_to_baseline"] == pytest.approx(1.2 / 1.1)
    broken = dict(row)
    del broken["exit"]
    with pytest.raises(ValueError, match="missing fields"):
        validate_grid_row(broken)


@pytest.mark.parametrize("variant", ["a", "b60", "b120", "c", "d"])
def test_fast_grid_metrics_match_detailed_account(variant: str) -> None:
    returns = pd.Series([0.0, 0.0, 0.10, 0.20, -0.05, 0.30, 0.0])
    levels = pd.Series([np.nan, 2.0, 1.0, 0.0, 2.0, 1.0, 0.0])
    spec = LadderSpec(levels=2)
    detailed = simulate_account(
        _dates(len(returns)),
        returns,
        levels,
        spec=spec,
        exit_variant=variant,
        transaction_cost=0.001,
    )
    fast = simulate_grid_metrics(
        _dates(len(returns)),
        returns,
        levels,
        spec=spec,
        exit_variant=variant,
        transaction_cost=0.001,
    )
    assert fast["full"]["final_wealth_multiple"] == pytest.approx(
        detailed.metrics["full"]["final_wealth_multiple"]
    )
    assert fast["full"]["transaction_cost"] == pytest.approx(
        detailed.metrics["full"]["transaction_cost"]
    )
