import numpy as np
import pandas as pd
import pytest

from stock_data.derived.option_walls import (
    EXTREME_MONEYNESS,
    LIMITED_STATUS,
    NO_OPEN_INTEREST,
    NO_NEAR_WINDOW_OI,
    VERIFIED_STATUS,
    MoneynessWarningPolicy,
    OptionWallError,
    compute_front_month_wall,
    compute_option_walls,
    compute_wall_distance,
    get_option_wall_histogram,
    join_kospi200_daily_index,
)


def _row(date, maturity, strike, side, oi, volume, segment="OFFICIAL"):
    return {
        "date": date, "maturity_month": maturity, "strike": strike,
        "call_put": side, "open_interest": oi, "volume": volume,
        "bridge_segment": segment, "session": "REGULAR_DAY", "source": "source",
    }


def test_walls_split_date_maturity_and_side_with_pcr_and_changes():
    frame = pd.DataFrame([
        _row("2019-12-30", "2020-01", 100, "CALL", 4, 10, "LEGACY"),
        _row("2019-12-30", "2020-01", 110, "CALL", 8, 20, "LEGACY"),
        _row("2019-12-30", "2020-01", 90, "PUT", 12, 30, "LEGACY"),
        _row("2020-01-02", "2020-01", 100, "CALL", 10, 40),
        _row("2020-01-02", "2020-01", 90, "PUT", 5, 20),
        _row("2020-01-02", "2020-02", 120, "CALL", 2, 3),
        _row("2020-01-02", "2020-02", 80, "PUT", 3, 6),
        _row("2020-01-03", "2020-01", 105, "CALL", 13, 50),
        _row("2020-01-03", "2020-01", 95, "PUT", 7, 30),
    ])
    result = compute_option_walls(frame)
    first = result.iloc[0]
    assert first.call_wall_strike == 110
    assert first.put_wall_strike == 90
    assert first.total_call_oi == 12
    assert first.oi_put_call_ratio == 1
    assert first.analysis_status == LIMITED_STATUS
    modern = result.loc[(result.date == pd.Timestamp("2020-01-03"))].iloc[0]
    assert modern.analysis_status == VERIFIED_STATUS
    assert modern.call_wall_oi_change_1d == 3
    assert modern.put_wall_strike_change_1d == 5


def test_zero_oi_tie_preserves_all_candidates_and_uses_lowest_strike():
    frame = pd.DataFrame([
        _row("2026-01-02", "2026-01", 100, "CALL", 0, 5),
        _row("2026-01-02", "2026-01", 105, "CALL", 0, 7),
        _row("2026-01-02", "2026-01", 90, "PUT", 1, 2),
    ])
    row = compute_option_walls(frame).iloc[0]
    assert bool(row.call_wall_tie)
    assert row.call_wall_candidate_count == 2
    assert row.call_wall_candidate_strikes == "100|105"
    assert row.call_wall_status == NO_OPEN_INTEREST
    assert np.isnan(row.call_wall_strike)
    assert np.isnan(row.call_wall_volume)
    assert not np.isnan(row.volume_put_call_ratio)


def test_null_key_and_provider_boundary_fail_closed():
    null_frame = pd.DataFrame([_row("2026-01-02", "2026-01", None, "CALL", 1, 1)])
    with pytest.raises(OptionWallError, match="null option identity"):
        compute_option_walls(null_frame)
    boundary = pd.DataFrame([
        _row("2026-01-02", "2026-01", 100, "CALL", 1, 1, "A"),
        _row("2026-01-02", "2026-01", 90, "PUT", 1, 1, "B"),
    ])
    with pytest.raises(OptionWallError, match="provider boundary"):
        compute_option_walls(boundary)


def test_front_month_distance_and_histogram_interfaces():
    options = pd.DataFrame([
        _row("2026-01-02", "2025-12", 90, "CALL", 1, 1),
        _row("2026-01-02", "2025-12", 80, "PUT", 1, 1),
        _row("2026-01-02", "2026-02", 110, "CALL", 5, 2),
        _row("2026-01-02", "2026-02", 90, "PUT", 6, 3),
        _row("2026-01-02", "2026-03", 120, "CALL", 2, 2),
        _row("2026-01-02", "2026-03", 80, "PUT", 2, 3),
    ])
    walls = compute_option_walls(options)
    front = compute_front_month_wall(walls)
    assert front.maturity_month.tolist() == ["2026-02"]
    distance = compute_wall_distance(front.assign(underlying_price=100.0)).iloc[0]
    assert distance.call_wall_distance == 10
    assert distance.call_wall_distance_pct == pytest.approx(10)
    assert distance.put_wall_distance_pct == pytest.approx(-10)
    histogram = get_option_wall_histogram(options, "2026-01-02", "2026-02")
    assert set(histogram.call_put) == {"CALL", "PUT"}
    assert histogram.open_interest.sum() == 11


def test_explicit_pit_safe_kospi200_join_and_configurable_warning():
    options = pd.DataFrame([
        _row("2026-01-02", "2026-01", 130, "CALL", 5, 2),
        _row("2026-01-02", "2026-01", 90, "PUT", 6, 3),
    ])
    walls = compute_front_month_wall(compute_option_walls(options))
    index = pd.DataFrame({
        "date": ["2026-01-02"], "symbol": ["KOSPI200"],
        "close": [100.0], "source": ["official_krx"],
    })
    joined = join_kospi200_daily_index(
        walls, index, dataset_name="kr_kospi200_index_daily", symbol="KOSPI200",
        pit_status="PIT_SAFE_EOD_T_PLUS_1", warning_policy=MoneynessWarningPolicy(25.0),
    ).iloc[0]
    assert joined.underlying_price == 100
    assert joined.call_wall_distance_pct == pytest.approx(30)
    assert joined.call_wall_warning == EXTREME_MONEYNESS
    assert joined.put_wall_warning is None
    with pytest.raises(OptionWallError, match="PIT_SAFE_EOD_T_PLUS_1"):
        join_kospi200_daily_index(
            walls, index, dataset_name="x", symbol="KOSPI200", pit_status="UNVERIFIED"
        )


def test_near_wall_ignores_far_stale_max_oi_and_keeps_all_strike_wall():
    options = pd.DataFrame([
        _row("2026-09-02", "2026-09", 1597.5, "CALL", 90_000, 0),
        _row("2026-09-02", "2026-09", 1050.0, "CALL", 8_000, 200),
        _row("2026-09-02", "2026-09", 700.0, "PUT", 80_000, 0),
        _row("2026-09-02", "2026-09", 1000.0, "PUT", 9_000, 250),
    ])
    index = pd.DataFrame({
        "date": ["2026-09-02"], "symbol": ["KOSPI200"],
        "close": [1030.0], "source": ["official_krx"],
    })

    joined = join_kospi200_daily_index(
        compute_front_month_wall(compute_option_walls(options)),
        index,
        dataset_name="kr_kospi200_index_daily",
        symbol="KOSPI200",
        pit_status="PIT_SAFE_EOD_T_PLUS_1",
    ).iloc[0]

    assert joined.call_wall_strike == 1597.5
    assert joined.put_wall_strike == 700.0
    assert joined.near_call_wall_strike == 1050.0
    assert joined.near_put_wall_strike == 1000.0
    assert joined.near_call_wall_distance_pct == pytest.approx(1.94174757)
    assert joined.near_put_wall_distance_pct == pytest.approx(-2.91262136)


def test_near_wall_reports_no_oi_inside_window():
    options = pd.DataFrame([
        _row("2026-09-02", "2026-09", 1597.5, "CALL", 90_000, 0),
        _row("2026-09-02", "2026-09", 1030.0, "CALL", 0, 1),
        _row("2026-09-02", "2026-09", 700.0, "PUT", 80_000, 0),
    ])
    index = pd.DataFrame({
        "date": ["2026-09-02"], "symbol": ["KOSPI200"],
        "close": [1030.0], "source": ["official_krx"],
    })

    joined = join_kospi200_daily_index(
        compute_front_month_wall(compute_option_walls(options)),
        index,
        dataset_name="kr_kospi200_index_daily",
        symbol="KOSPI200",
        pit_status="PIT_SAFE_EOD_T_PLUS_1",
    ).iloc[0]

    assert joined.near_call_wall_status == NO_NEAR_WINDOW_OI
    assert joined.near_put_wall_status == NO_NEAR_WINDOW_OI
    assert np.isnan(joined.near_call_wall_strike)
    assert np.isnan(joined.near_put_wall_strike)
