from __future__ import annotations

import pandas as pd
import pytest

from stock_data.research.core_ammunition import (
    Episode,
    classify_asset,
    cluster_level_two,
    duration_proxy_returns,
    fixed_crisis_types,
    krw_converted_value,
    measure_asset_horizons,
    peak_after_episode,
    prepare_value_series,
    quantile_values,
)


def _episode() -> Episode:
    dates = pd.bdate_range("2020-01-01", periods=8)
    return Episode(
        episode_id="US_2020-01-06",
        market="US",
        series_id="NASDAQ100",
        signal_index=3,
        signal_date=dates[3],
        hold_start_index=1,
        hold_start_date=dates[1],
        t20_date=None,
        t60_date=None,
        cycle="2020 코로나",
        cycle_type="경기침체형",
        drawdown252=-0.25,
        disp60=-0.15,
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


def test_followup_horizons_reuse_hold_start_and_market_sessions() -> None:
    episode = _episode()
    dates = pd.bdate_range("2020-01-01", periods=8)
    asset = prepare_value_series(dates, [10, 10, 11, 12, 13, 14, 15, 16])
    equity = prepare_value_series(dates, [20, 20, 18, 16, 17, 18, 19, 20])

    actual = measure_asset_horizons(
        asset,
        episode,
        equity,
        dates,
        offsets=(0, 2, 4, 5),
    )

    assert actual["date_t"] == dates[3]
    assert actual["value_t"] == pytest.approx(120.0)
    assert actual["value_2"] == pytest.approx(140.0)
    assert actual["equity_value_4"] == pytest.approx(100.0)
    assert actual["date_5"] is None
    assert actual["value_5"] is None


def test_peak_timing_marks_complete_and_right_censored_windows() -> None:
    episode = _episode()
    dates = pd.bdate_range("2020-01-01", periods=8)
    asset = prepare_value_series(dates, [10, 10, 11, 12, 14, 13, 12, 11])

    complete = peak_after_episode(asset, episode, dates, max_offset=4)
    censored = peak_after_episode(asset, episode, dates, max_offset=5)

    assert complete["full_window"] is True
    assert complete["peak_offset"] == 1
    assert complete["peak_value"] == pytest.approx(140.0)
    assert censored["full_window"] is False
    assert censored["observed_through_offset"] == 4
    assert censored["peak_offset"] == 1


def test_p10_floor_changes_only_downside_classification_criterion() -> None:
    rows = pd.DataFrame(
        {
            "value_t": [94.0] + [100.0] * 9,
            "value_60": [95.0] + [101.0] * 9,
            "equity_value_t": [90.0] * 10,
            "equity_value_60": [100.0] * 10,
        }
    )

    worst = classify_asset(rows)
    p10 = classify_asset(rows, floor_statistic="p10")

    assert worst["classification"] == "중립"
    assert p10["classification"] == "실탄"
    assert worst["share_t_ge_100"] == p10["share_t_ge_100"] == pytest.approx(0.9)
    assert p10["p10_t"] == pytest.approx(99.4)


def test_quantiles_and_fixed_crisis_rules_are_predeclared() -> None:
    rows = pd.DataFrame(
        {
            "value_t": [90.0, 100.0, 110.0, 120.0],
            "value_20": [80.0, 90.0, 100.0, 110.0],
            "value_60": [70.0, 80.0, 90.0, 100.0],
        }
    )

    quantiles = quantile_values(rows)

    assert quantiles["t"]["p25"] == pytest.approx(97.5)
    assert quantiles["60"]["p75"] == pytest.approx(92.5)
    assert fixed_crisis_types(0.01, -0.51) == {
        "ten_year_rule": "인플레형",
        "two_year_first_rule": "침체형",
    }
    assert fixed_crisis_types(0.0, -0.5) == {
        "ten_year_rule": "침체형",
        "two_year_first_rule": "인플레형",
    }
