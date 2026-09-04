from __future__ import annotations

import pandas as pd
import pytest

from stock_data.research.core_ammunition import (
    classify_asset,
    cluster_level_two,
    duration_proxy_returns,
    krw_converted_value,
)


def test_duration_proxy_matches_hand_computed_yield_path() -> None:
    yields = pd.Series([5.00, 4.90, 5.00], dtype="float64")

    actual = duration_proxy_returns(yields, duration=2.0)

    assert actual.iloc[0] == 0.0
    assert actual.iloc[1] == pytest.approx(-2.0 * (-0.001) + 0.05 / 252.0)
    assert actual.iloc[2] == pytest.approx(-2.0 * 0.001 + 0.049 / 252.0)


def test_episode_clustering_ignores_retriggers_within_120_sessions() -> None:
    dates = pd.bdate_range("2020-01-01", periods=127)
    levels = pd.Series(0, index=range(127), dtype="Int64")
    levels.iloc[[5, 6, 125, 126]] = 2
    ladder = pd.DataFrame(
        {
            "date": dates,
            "observed_level": levels,
            "drawdown252": -0.21,
            "disp60": -0.11,
        }
    )

    episodes = cluster_level_two(
        ladder,
        market="KR",
        series_id="KOSPI",
        start_date="2020-01-01",
    )

    assert [episode.signal_index for episode in episodes] == [5, 126]
    assert episodes[0].hold_start_index == 4
    assert episodes[1].hold_start_index == 124


def test_classification_requires_both_ammunition_thresholds() -> None:
    rows = pd.DataFrame(
        {
            "value_t": [100.0, 101.0, 102.0, 96.0],
            "value_60": [102.0, 103.0, 104.0, 98.0],
            "equity_value_t": [90.0, 91.0, 92.0, 93.0],
            "equity_value_60": [100.0, 101.0, 102.0, 103.0],
        }
    )

    result = classify_asset(rows)

    assert result["classification"] == "실탄"
    assert result["share_t_ge_100"] == pytest.approx(0.75)
    assert result["worst_t"] == 96.0


def test_classification_marks_falling_asset_as_buy_target_when_recovery_beats_equity() -> None:
    rows = pd.DataFrame(
        {
            "value_t": [94.0, 98.0, 99.0],
            "value_60": [110.0, 112.0, 113.0],
            "equity_value_t": [88.0, 90.0, 91.0],
            "equity_value_60": [98.0, 100.0, 101.0],
        }
    )

    result = classify_asset(rows)

    assert result["classification"] == "매수 대상"
    assert result["share_recovery_beats_equity"] == 1.0


def test_krw_conversion_rises_when_krw_per_usd_rises() -> None:
    weaker_won = krw_converted_value(100.0, start_fx=1300.0, target_fx=1400.0)
    stronger_won = krw_converted_value(100.0, start_fx=1300.0, target_fx=1200.0)

    assert weaker_won == pytest.approx(107.6923076923)
    assert stronger_won == pytest.approx(92.3076923077)
