from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.research.condition_backtest import (
    CONDITION_COLUMNS,
    HORIZONS,
    add_condition_events,
    attach_volatility_index,
    build_forward_outcomes,
    compute_signals,
    summarize_condition_events,
    wilder_rsi,
)


def _prices(close: np.ndarray | list[float], *, start: str = "2000-01-03") -> pd.DataFrame:
    values = np.asarray(close, dtype="float64")
    return pd.DataFrame(
        {
            "date": pd.bdate_range(start, periods=len(values)),
            "series_id": "SYNTH",
            "basket": "KR",
            "close": values,
            "volume": np.arange(len(values), dtype="float64") + 100.0,
        }
    )


def test_wilder_rsi_known_monotonic_and_flat_values() -> None:
    rising = wilder_rsi(pd.Series(np.arange(1.0, 17.0)))
    flat = wilder_rsi(pd.Series(np.full(16, 100.0)))

    assert rising.iloc[:14].isna().all()
    assert rising.iloc[14] == pytest.approx(100.0)
    assert flat.iloc[14] == pytest.approx(50.0)


def test_crash_recovery_has_known_rolling_drawdown() -> None:
    close = np.r_[np.full(252, 100.0), 70.0, 85.0]
    signals = compute_signals(_prices(close))

    assert signals.loc[251, "drawdown252"] == pytest.approx(0.0)
    assert signals.loc[252, "drawdown252"] == pytest.approx(-0.30)
    assert signals.loc[253, "drawdown252"] == pytest.approx(-0.15)
    assert bool(signals.loc[252, "event_drawdown252_le_m30"])


def test_event_detection_requires_crossing_and_prior_valid_state() -> None:
    frame = pd.DataFrame(
        {
            "series_id": ["A"] * 5,
            "rsi14": [np.nan, 31.0, 29.0, 28.0, 32.0],
            "disp60": [np.nan, -0.09, -0.11, -0.12, -0.08],
            "drawdown252": [np.nan, -0.29, -0.31, -0.32, -0.28],
        }
    )
    result = add_condition_events(frame)

    assert result["event_rsi14_le_30"].tolist() == [False, False, True, False, False]
    assert result["event_disp60_le_m10"].tolist() == [False, False, True, False, False]
    assert result["event_drawdown252_le_m30"].tolist() == [False, False, True, False, False]
    assert result["event_all_three"].tolist() == [False, False, True, False, False]
    assert all(result[column].dtype == bool for column in CONDITION_COLUMNS.values())


def test_signals_do_not_change_when_only_future_prices_change() -> None:
    x = np.arange(420, dtype="float64")
    close = 100.0 + 0.06 * x + 12.0 * np.sin(x / 12.0)
    original = _prices(close)
    changed = original.copy()
    changed.loc[301:, "close"] *= np.linspace(0.5, 2.0, len(changed) - 301)

    left = compute_signals(original).loc[:300]
    right = compute_signals(changed).loc[:300]

    pd.testing.assert_frame_equal(
        left[[
            "rsi14",
            "ma60",
            "disp60",
            "high252",
            "drawdown252",
            "realized_volatility_20d",
            "bollinger_percent_b20",
            "volume_ratio20",
        ]],
        right[[
            "rsi14",
            "ma60",
            "disp60",
            "high252",
            "drawdown252",
            "realized_volatility_20d",
            "bollinger_percent_b20",
            "volume_ratio20",
        ]],
    )


def test_forward_return_true_path_drawdown_and_series_end_cutoff() -> None:
    outcomes = build_forward_outcomes(_prices([100.0, 120.0, 90.0, 110.0]), horizons=(2,))

    assert len(outcomes) == 2
    first = outcomes.iloc[0]
    assert first["forward_return"] == pytest.approx(-0.10)
    assert first["forward_max_drawdown"] == pytest.approx(-0.25)
    assert first["forward_realized_volatility"] == pytest.approx(
        np.std([0.20, -0.25], ddof=1) * np.sqrt(252.0)
    )
    assert first["outcome_end_date"] == "2000-01-05"
    assert outcomes["observation_date"].tolist() == ["2000-01-03", "2000-01-04"]


def test_default_horizons_add_90_and_keep_existing_extras() -> None:
    assert HORIZONS == (5, 20, 60, 90, 120)


def test_volatility_index_join_is_same_date_only_without_forward_fill() -> None:
    signals = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-02"]),
            "series_id": ["KOSPI200", "KOSPI200", "NASDAQ100"],
            "basket": ["KR", "KR", "US_TECH"],
        }
    )
    volatility = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-02", "2020-01-02"]),
            "vol_index_id": ["VKOSPI", "VIX"],
            "vol_index_close": [20.0, 15.0],
            "vol_index_percentile252": [0.9, 0.1],
            "dataset_source": ["kr_vkospi_daily", "fred_vix_daily"],
        }
    )

    result = attach_volatility_index(signals, volatility)

    kr_missing = result.loc[
        result["series_id"].eq("KOSPI200")
        & result["date"].eq(pd.Timestamp("2020-01-03"))
    ].iloc[0]
    assert pd.isna(kr_missing["vol_index_close"])
    assert result.loc[result["series_id"].eq("NASDAQ100"), "vol_index_id"].iloc[0] == "VIX"


def test_basket_comparison_uses_the_same_series_unconditional_baseline() -> None:
    outcomes = pd.DataFrame(
        {
            "observation_date": ["2000-01-03", "2000-01-04", "2000-01-03", "2000-01-04"],
            "outcome_end_date": ["2000-01-10"] * 4,
            "series_id": ["A", "A", "B", "B"],
            "basket": ["KR"] * 4,
            "horizon": [5] * 4,
            "forward_return": [1.0, 1.0, -0.2, -0.4],
            "forward_max_drawdown": [-0.1] * 4,
        }
    )
    events = outcomes.loc[[2]].copy()
    events["rule"] = "RSI14≤30"
    summary = summarize_condition_events(events, outcomes)
    selected = summary.loc[
        summary["rule"].eq("RSI14≤30")
        & summary["horizon"].eq(5)
        & summary["basket"].eq("KR")
    ].iloc[0]

    assert selected["baseline_mean"] == pytest.approx(-0.30)
    assert selected["difference_vs_baseline"] == pytest.approx(0.10)
