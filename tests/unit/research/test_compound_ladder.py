from __future__ import annotations

import json
from pathlib import Path

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
from scripts.research.run_compound_backtest import (
    _independent_cycle_counts,
    _parser,
    _plateau,
    _require_product_share,
    validate_base_sweep_payload,
)


def _dates(n: int) -> pd.Series:
    return pd.Series(pd.bdate_range("2020-01-01", periods=n))


def _spec(*, levels: int = 2, base_exposure: float = 1.0, product_share: float = 1.0) -> LadderSpec:
    return LadderSpec(
        drawdown_threshold=-0.20,
        disp60_threshold=-0.10,
        product_share_at_max=product_share,
        levels=levels,
        base_exposure=base_exposure,
    )


def test_known_drawdown_level_path_and_next_session_execution() -> None:
    signals = pd.DataFrame(
        {
            "date": _dates(5),
            "drawdown252": [-0.05, -0.21, -0.21, -0.05, np.nan],
            "disp60": [-0.02, -0.05, -0.11, -0.11, -0.11],
        }
    )
    result = ladder_levels(signals, _spec(levels=2))
    assert result["observed_level"].tolist() == [0, 1, 2, 1, pd.NA]
    assert result["executable_level"].tolist() == [pd.NA, 0, 1, 2, 1]


def test_rule_six_rejects_each_undecided_ladder_value() -> None:
    with pytest.raises(ValueError, match="drawdown_threshold is undecided.*⑥"):
        LadderSpec()
    with pytest.raises(ValueError, match="disp60_threshold is undecided.*⑥"):
        LadderSpec(drawdown_threshold=-0.20)
    with pytest.raises(ValueError, match="product_share_at_max is undecided.*⑥"):
        LadderSpec(drawdown_threshold=-0.20, disp60_threshold=-0.10)


def test_product_share_knob_caps_top_weight_and_exposes_effective_exposure() -> None:
    result = simulate_account(
        _dates(4),
        pd.Series(np.zeros(4)),
        pd.Series([np.nan, 0.0, 1.0, 2.0]),
        underlying_returns=pd.Series(np.zeros(4)),
        spec=_spec(levels=2, base_exposure=1.0, product_share=0.5),
        leverage_multiple=3,
        exit_variant="a",
        transaction_cost=0.0,
    )
    assert result.curve["product_weight"].tolist() == pytest.approx([0.0, 0.0, 0.25, 0.5])
    assert result.curve["core_weight"].tolist() == pytest.approx([1.0, 1.0, 0.75, 0.5])
    assert result.curve["effective_exposure"].tolist() == pytest.approx([1.0, 1.0, 1.5, 2.0])
    assert result.effective_exposure_max == pytest.approx(2.0)


def test_execution_at_next_close_earns_only_following_return() -> None:
    returns = pd.Series([0.0, 0.10, 0.10, 0.0])
    levels = pd.Series([np.nan, 1.0, 1.0, 0.0])
    result = simulate_account(
        _dates(4),
        returns,
        levels,
        underlying_returns=pd.Series(np.zeros(4)),
        spec=_spec(levels=1, base_exposure=0.0),
        leverage_multiple=1,
        transaction_cost=0.0,
    )
    assert result.curve["wealth"].tolist() == pytest.approx([1.0, 1.0, 1.1, 1.1])
    assert result.trades["date"].tolist() == [_dates(4).iloc[1], _dates(4).iloc[3]]


def test_exit_a_reverse_score_partially_sells() -> None:
    returns = pd.Series([0.0, 0.0, 0.10, 0.10])
    levels = pd.Series([np.nan, 2.0, 1.0, 0.0])
    result = simulate_account(
        _dates(4),
        returns,
        levels,
        underlying_returns=pd.Series(np.zeros(4)),
        spec=_spec(levels=2, base_exposure=0.0),
        leverage_multiple=1,
        transaction_cost=0.0,
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
        underlying_returns=pd.Series(np.zeros(n)),
        spec=_spec(levels=1, base_exposure=0.0),
        leverage_multiple=1,
        exit_variant=variant,
        transaction_cost=0.0,
    )
    assert result.trades.iloc[0]["target_weight"] == 1.0
    assert result.trades.iloc[1]["target_weight"] == 0.0
    assert result.trades.iloc[1]["date"] == _dates(n).iloc[holding + 1]


def test_fixed_period_core_uses_max_exposure_then_returns_to_one() -> None:
    n = 64
    result = simulate_account(
        _dates(n),
        pd.Series(np.zeros(n)),
        pd.Series([np.nan, 1.0] + [1.0] * (n - 2)),
        underlying_returns=pd.Series(np.zeros(n)),
        spec=_spec(levels=2, base_exposure=1.0),
        leverage_multiple=3,
        exit_variant="b60",
        transaction_cost=0.0,
    )
    assert result.curve["exposure"].iloc[1:61].tolist() == pytest.approx([3.0] * 60)
    assert result.curve["exposure"].iloc[61] == pytest.approx(1.0)


def test_profit_exit_sells_original_thirds_at_each_30_percent_gain() -> None:
    prices = pd.Series([1.0, 1.0, 1.30, 1.60, 1.90])
    returns = prices.pct_change(fill_method=None).fillna(0.0)
    levels = pd.Series([np.nan, 1.0, 1.0, 1.0, 1.0])
    result = simulate_account(
        _dates(5),
        returns,
        levels,
        underlying_returns=pd.Series(np.zeros(5)),
        spec=_spec(levels=1, base_exposure=0.0),
        leverage_multiple=1,
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
        underlying_returns=pd.Series(np.zeros(5)),
        spec=_spec(levels=2, base_exposure=0.0),
        leverage_multiple=1,
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
        underlying_returns=pd.Series(np.zeros(3)),
        spec=_spec(levels=1, base_exposure=1.0),
        leverage_multiple=2,
        transaction_cost=0.001,
    )
    # Each core/product switch contains a core sell and a product buy (or vice versa).
    assert strategy.trades.iloc[1]["cost"] > 0.0019
    assert strategy.metrics["full"]["transaction_cost"] > 0.0048


def test_chaining_reinvests_cash_from_first_episode() -> None:
    returns = pd.Series([0.0, 0.0, 0.10, 0.0, 0.10, 0.0])
    levels = pd.Series([np.nan, 1.0, 0.0, 1.0, 0.0, 0.0])
    result = simulate_account(
        _dates(6),
        returns,
        levels,
        underlying_returns=pd.Series(np.zeros(6)),
        spec=_spec(levels=1, base_exposure=0.0),
        leverage_multiple=1,
        transaction_cost=0.0,
    )
    assert result.curve["wealth"].iloc[-1] == pytest.approx(1.21)
    assert len(result.cycles) == 2


@pytest.mark.parametrize("variant", ["a", "b60", "b120", "c", "d"])
def test_k1_core_strategy_is_exactly_the_baseline(variant: str) -> None:
    underlying = pd.Series([0.0, 0.10, -0.05, 0.02, 0.01])
    deliberately_different_product = pd.Series([0.0, 0.30, -0.20, 0.15, -0.10])
    levels = pd.Series([np.nan, 1.0, 2.0, 0.0, 1.0])
    baseline = simulate_baseline(_dates(5), underlying, transaction_cost=0.001)
    strategy = simulate_account(
        _dates(5),
        deliberately_different_product,
        levels,
        underlying_returns=underlying,
        spec=_spec(levels=2, base_exposure=1.0),
        leverage_multiple=1,
        exit_variant=variant,
        transaction_cost=0.001,
    )
    pd.testing.assert_frame_equal(strategy.curve, baseline.curve)
    assert strategy.metrics["holdout"] == baseline.metrics["holdout"]
    assert strategy.metrics["full"] == baseline.metrics["full"]


def test_two_level_core_overlay_has_hand_computed_wealth_and_exposure() -> None:
    underlying = pd.Series([0.0, 0.0, 0.10, 0.10, 0.0])
    product = pd.Series([0.0, 0.0, 0.20, 0.20, 0.0])
    levels = pd.Series([np.nan, 1.0, 2.0, 1.0, 0.0])
    result = simulate_account(
        _dates(5),
        product,
        levels,
        underlying_returns=underlying,
        spec=_spec(levels=2, base_exposure=1.0),
        leverage_multiple=2,
        exit_variant="a",
        transaction_cost=0.0,
    )
    assert result.curve["wealth"].tolist() == pytest.approx([1.0, 1.0, 1.15, 1.38, 1.38])
    assert result.curve["exposure"].tolist() == pytest.approx([1.0, 1.5, 2.0, 1.5, 1.0])


def test_two_level_ladder_interpolates_product_fraction_above_one_x_base() -> None:
    result = simulate_account(
        _dates(5),
        pd.Series(np.zeros(5)),
        pd.Series([np.nan, 0.0, 1.0, 2.0, 0.0]),
        underlying_returns=pd.Series(np.zeros(5)),
        spec=_spec(levels=2, base_exposure=1.5),
        leverage_multiple=3,
        exit_variant="a",
        transaction_cost=0.0,
    )
    # f0=(1.5-1)/(3-1)=0.25; f(level)=f0+(1-f0)*level/2.
    assert result.curve["product_weight"].tolist() == pytest.approx(
        [0.25, 0.25, 0.625, 1.0, 0.25]
    )
    assert result.curve["exposure"].tolist() == pytest.approx(
        [1.5, 1.5, 2.25, 3.0, 1.5]
    )


@pytest.mark.parametrize(("multiple", "exit_variant"), [(2, "a"), (2, "d"), (3, "a"), (3, "d")])
def test_base_equal_to_product_multiple_makes_ladder_identical_to_permanent_hold(
    multiple: int,
    exit_variant: str,
) -> None:
    product = pd.Series([0.0, 0.08, -0.04, 0.05, -0.02])
    underlying = pd.Series([0.0, 0.03, -0.01, 0.02, -0.01])
    ladder = simulate_account(
        _dates(5),
        product,
        pd.Series([np.nan, 1.0, 2.0, 0.0, 1.0]),
        underlying_returns=underlying,
        spec=_spec(levels=2, base_exposure=float(multiple)),
        leverage_multiple=multiple,
        exit_variant=exit_variant,
        transaction_cost=0.001,
    )
    permanent = simulate_account(
        _dates(5),
        product,
        pd.Series(np.zeros(5)),
        underlying_returns=underlying,
        spec=_spec(levels=2, base_exposure=float(multiple)),
        leverage_multiple=multiple,
        exit_variant="a",
        transaction_cost=0.001,
    )
    pd.testing.assert_frame_equal(
        ladder.curve.drop(columns="executable_level"),
        permanent.curve.drop(columns="executable_level"),
    )
    for period in ("holdout", "full"):
        assert ladder.metrics[period]["final_wealth_multiple"] == pytest.approx(
            permanent.metrics[period]["final_wealth_multiple"]
        )
        assert ladder.metrics[period]["max_drawdown"] == pytest.approx(
            permanent.metrics[period]["max_drawdown"]
        )
        assert ladder.metrics[period]["transaction_cost"] == pytest.approx(
            permanent.metrics[period]["transaction_cost"]
        )


def test_base_exposure_must_not_exceed_product_multiple() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        simulate_account(
            _dates(3),
            pd.Series(np.zeros(3)),
            pd.Series(np.zeros(3)),
            underlying_returns=pd.Series(np.zeros(3)),
            spec=_spec(levels=2, base_exposure=2.1),
            leverage_multiple=2,
        )
    with pytest.raises(ValueError, match=r"\[0.0, 3.0\]"):
        _spec(base_exposure=3.1)


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


def test_retained_negative_short_rate_keeps_the_financing_formula() -> None:
    close = pd.Series([100.0, 100.0])
    zero_rate = synthetic_daily_returns(
        close,
        leverage_multiple=2,
        annual_expense_ratio=0.0,
        annual_short_rate=0.0,
    )
    negative_rate = synthetic_daily_returns(
        close,
        leverage_multiple=2,
        annual_expense_ratio=0.0,
        annual_short_rate=-0.0005,
    )
    assert negative_rate.iloc[1] - zero_rate.iloc[1] == pytest.approx(0.0005 / 252)


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
        underlying_returns=pd.Series(np.zeros(4)),
        spec=_spec(levels=1, base_exposure=0.0),
        leverage_multiple=1,
        transaction_cost=0.0,
    )
    assert strategy.curve["wealth"].tolist() == pytest.approx([1.0, 1.0, 1.0, 1.0])
    fast = simulate_grid_metrics(
        dates,
        returns,
        pd.Series([np.nan, 1.0, 0.0, 1.0]),
        underlying_returns=pd.Series(np.zeros(4)),
        spec=_spec(levels=1, base_exposure=0.0),
        leverage_multiple=1,
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
        "base_exposure": 1.0,
        "product_share_at_max": 0.5,
        "effective_exposure_max": 1.5,
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


def test_plateau_matches_retained_kr_kospi_summary() -> None:
    root = Path(__file__).parents[3]
    rows = json.loads(
        (root / "artifacts/research/compound_ladder/grid_kr_kospi.json").read_text(
            encoding="utf-8",
        )
    )
    summary = json.loads(
        (root / "artifacts/research/compound_ladder/summary.json").read_text(
            encoding="utf-8",
        )
    )
    retained = next(
        item for item in summary["baskets"]["KR"] if item["underlying"] == "KOSPI"
    )["plateau"]
    recalculated = {
        item["surface"]: item
        for item in _plateau(
            rows,
            drawdown_threshold=-0.20,
            disp60_threshold=-0.10,
        )
    }

    assert set(recalculated) == {item["surface"] for item in retained}
    for expected in retained:
        actual = recalculated[expected["surface"]]
        assert actual["best_x"] == expected["best_x"]
        assert actual["best_y"] == expected["best_y"]
        assert (
            actual["best_fit_relative_to_baseline"]
            == expected["best_fit_relative_to_baseline"]
        )
        assert actual["neighbour_count"] == expected["neighbour_count"]
        assert actual["neighbourhood_mean"] == expected["neighbourhood_mean"]


def test_base_sweep_file_schema_requires_comparisons_references_and_thresholds() -> None:
    metric = {"final_wealth_multiple": 1.2, "max_drawdown": -0.3}
    comparison = {
        "ladder_on_base": dict(metric),
        "permanent_base": dict(metric),
        "baseline_1x": dict(metric),
        "ladder_to_baseline_ratio": 1.0,
        "ladder_to_permanent_ratio": 1.0,
    }
    payload = {
        "schema_version": 1,
        "experiment": "compound-ladder/base-exposure-sweep-v1",
        "development_only": True,
        "api_calls": 0,
        "quick": False,
        "basket": "KR",
        "underlying": "KOSPI",
        "parameters": {},
        "input_manifest_sha256": "0" * 64,
        "input_manifest": [],
        "calibration": {},
        "independent_cycle_count": {"fit": 1, "holdout": 1, "full": 2},
        "references": [{
            "row_kind": "permanent_base",
            "leverage_multiple": 2,
            "base_exposure": 1.0,
            "periods": {period: dict(metric) for period in ("fit", "holdout", "full")},
        }],
        "rows": [{
            "row_kind": "ladder_on_base",
            "leverage_multiple": 2,
            "base_exposure": 1.0,
            "exit": "a",
            "periods": {
                period: {
                    key: dict(value) if isinstance(value, dict) else value
                    for key, value in comparison.items()
                }
                for period in ("fit", "holdout", "full")
            },
        }],
        "thresholds": [
            {
                "leverage_multiple": multiple,
                "exit": exit_variant,
                "period": period,
                "smallest_base_exposure": None,
                "beats_permanent_at_threshold": None,
                "ladder_to_baseline_ratio": None,
                "ladder_to_permanent_ratio": None,
            }
            for multiple in (2, 3)
            for exit_variant in ("a", "d")
            for period in ("fit", "holdout", "full")
        ],
        "runtime_seconds": 0.1,
    }
    validate_base_sweep_payload(payload)
    del payload["rows"][0]["periods"]["fit"]["ladder_to_permanent_ratio"]
    with pytest.raises(ValueError, match="comparison metrics"):
        validate_base_sweep_payload(payload)


def test_base_sweep_independent_cycles_split_signal_clusters_at_ninety_day_gaps() -> None:
    frame = pd.DataFrame({
        "date": pd.to_datetime([
            "2015-01-02",
            "2015-01-05",
            "2015-05-01",
            "2016-01-04",
            "2016-01-05",
        ])
    })
    levels = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0])
    assert _independent_cycle_counts(frame, levels) == {"fit": 2, "holdout": 1, "full": 3}


def test_base_exposure_cli_is_opt_in_and_accepts_list_or_comma_groups() -> None:
    assert _parser().parse_args([]).base_exposures is None
    assert _parser().parse_args([]).drawdown_threshold is None
    assert _parser().parse_args([]).disp60_threshold is None
    assert _parser().parse_args([]).product_share_at_max is None
    with pytest.raises(ValueError, match="product_share_at_max is undecided.*⑥"):
        _require_product_share(None)
    parsed = _parser().parse_args([
        "--drawdown-threshold", "-0.20",
        "--disp60-threshold", "-0.10",
        "--product-share-at-max", "0.5",
        "--base-exposures", "1.0", "1.3,1.5",
    ])
    assert parsed.base_exposures == [(1.0,), (1.3, 1.5)]
    assert parsed.product_share_at_max == 0.5
    assert parsed.drawdown_threshold == -0.20
    assert parsed.disp60_threshold == -0.10


@pytest.mark.parametrize("variant", ["a", "b60", "b120", "c", "d"])
def test_fast_grid_metrics_match_detailed_account(variant: str) -> None:
    returns = pd.Series([0.0, 0.0, 0.10, 0.20, -0.05, 0.30, 0.0])
    underlying = pd.Series([0.0, 0.0, 0.05, 0.10, -0.025, 0.15, 0.0])
    levels = pd.Series([np.nan, 2.0, 1.0, 0.0, 2.0, 1.0, 0.0])
    spec = _spec(levels=2)
    detailed = simulate_account(
        _dates(len(returns)),
        returns,
        levels,
        underlying_returns=underlying,
        spec=spec,
        leverage_multiple=2,
        exit_variant=variant,
        transaction_cost=0.001,
    )
    fast = simulate_grid_metrics(
        _dates(len(returns)),
        returns,
        levels,
        underlying_returns=underlying,
        spec=spec,
        leverage_multiple=2,
        exit_variant=variant,
        transaction_cost=0.001,
    )
    assert fast["full"]["final_wealth_multiple"] == pytest.approx(
        detailed.metrics["full"]["final_wealth_multiple"]
    )
    assert fast["full"]["transaction_cost"] == pytest.approx(
        detailed.metrics["full"]["transaction_cost"]
    )
