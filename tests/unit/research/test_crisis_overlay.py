from __future__ import annotations

import json

import pandas as pd
import pytest

from stock_data.research.core_ammunition import (
    Episode,
    measure_asset_horizons,
    prepare_value_series,
)
from stock_data.research.crisis_overlay import (
    NORMALISATION_LABELS,
    OFFSETS,
    SCHEMA_VERSION,
    aligned_core_path,
    round_payload,
    select_first_cycle_episodes,
    validate_overlay_payload,
)


def _episode(
    signal_date: str, *, market: str = "KR", cycle: str = "2008–09 금융위기",
    signal_index: int = 10,
) -> Episode:
    signal = pd.Timestamp(signal_date)
    return Episode(
        episode_id=f"{market}_{signal:%Y-%m-%d}", market=market,
        series_id="KOSPI" if market == "KR" else "NASDAQ100",
        signal_index=signal_index, signal_date=signal,
        hold_start_index=0, hold_start_date=signal - pd.offsets.BDay(signal_index),
        t20_date=signal + pd.offsets.BDay(20),
        t60_date=signal + pd.offsets.BDay(60),
        cycle=cycle,
        cycle_type="인플레형" if cycle.startswith("2022") else "경기침체형",
        drawdown252=-.25, disp60=-.12,
    )


def test_episode_selection_keeps_first_level_two_per_market_cycle() -> None:
    cycles = ("2008–09 금융위기", "2022 인플레")
    episodes = [
        _episode("2008-09-17", market="US"),
        _episode("2022-03-14", market="US", cycle="2022 인플레"),
        _episode("2008-01-23", market="US"),
        _episode("2008-01-22", market="KR"),
        _episode("2008-07-21", market="KR"),
        _episode("2004-05-17", market="KR", cycle="기타 (2004)"),
    ]

    selected = select_first_cycle_episodes(episodes, cycles)

    assert [(row.market, row.signal_date.strftime("%Y-%m-%d")) for row in selected] == [
        ("KR", "2008-01-22"), ("US", "2008-01-23"), ("US", "2022-03-14"),
    ]


def test_both_normalisations_reuse_core_valuation_at_t_20_60() -> None:
    dates = pd.date_range("2008-01-01", periods=100, freq="B")
    episode = _episode(str(dates[10].date()), signal_index=10)
    asset = prepare_value_series(dates, [80.0 + index * .7 for index in range(100)])
    equity = prepare_value_series(dates, [100.0 + index for index in range(100)])
    core = measure_asset_horizons(
        asset, episode, equity, dates, offsets=(0, 20, 60),
    )

    signal_path, real_dates, signal_mark = aligned_core_path(
        asset, episode, equity, dates, offsets=(-10, 0, 20, 60), basis="signal",
    )
    hold_path, hold_dates, hold_mark = aligned_core_path(
        asset, episode, equity, dates, offsets=(-10, 0, 20, 60), basis="hold_start",
    )

    assert signal_mark == pytest.approx(core["value_t"])
    assert hold_mark == pytest.approx(core["value_t"])
    assert signal_path[1:] == pytest.approx([
        100.0,
        100.0 * core["value_20"] / core["value_t"],
        100.0 * core["value_60"] / core["value_t"],
    ])
    assert hold_path == pytest.approx([
        100.0, core["value_t"], core["value_20"], core["value_60"],
    ])
    assert real_dates == hold_dates == [
        dates[0].strftime("%Y-%m-%d"),
        core["date_t"].strftime("%Y-%m-%d"),
        core["date_20"].strftime("%Y-%m-%d"),
        core["date_60"].strftime("%Y-%m-%d"),
    ]


def test_hold_start_basis_keeps_core_table_t_value_88_11() -> None:
    dates = pd.date_range("2022-07-01", periods=80, freq="B")
    episode = _episode(str(dates[10].date()), signal_index=10)
    asset = prepare_value_series(dates, [100.0 - 1.189 * min(index, 10) for index in range(80)])
    equity = prepare_value_series(dates, [100.0] * 80)

    path, _dates, signal_mark = aligned_core_path(
        asset, episode, equity, dates, offsets=(-10, 0), basis="hold_start",
    )

    assert path == pytest.approx([100.0, 88.11])
    assert signal_mark == pytest.approx(88.11)


def test_overlay_json_shape_rounding_and_size_contract() -> None:
    length = len(OFFSETS)
    episode_id = "KR_2008-01-22"
    signal_path = [100.126] * length
    signal_path[-OFFSETS[0]] = 100.0
    hold_path = [100.126] * length
    hold_path[-OFFSETS[0] - 10] = 100.0
    ladder_item = {
        "2008–09 금융위기": signal_path,
        "median": signal_path,
        "worst": "2008–09 금융위기",
        "signal_dates": {"2008–09 금융위기": "2008-01-22"},
        "dates": {"2008–09 금융위기": ["2008-01-22"] * length},
        "levels": {"2008–09 금융위기": [2] * length},
        "hold_start_offsets": {"2008–09 금융위기": -10},
    }
    payload = round_payload({
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-09-05T00:00:00+00:00",
        "episodes": [{
            "id": episode_id, "market": "KR", "cycle": "2008–09 금융위기",
            "type": "recession-type", "signal_date": "2008-01-22",
            "hold_start_date": "2008-01-08", "hold_start_offset": -10,
            "is_holdout": False,
        }],
        "assets": [{"id": "equity_reference", "label": "KOSPI"}],
        "normalisations": [
            {"id": basis, "label": label}
            for basis, label in NORMALISATION_LABELS.items()
        ],
        "series": {
            "hold_start": {episode_id: {"equity_reference": hold_path}},
            "signal": {episode_id: {"equity_reference": signal_path}},
        },
        "signal_values": {episode_id: {"equity_reference": 80.126}},
        "dates": {episode_id: ["2008-01-22"] * length},
        "yields": {episode_id: [4.126] * length},
        "levels": {episode_id: [2] * length},
        "ladder": {
            basis: {
                "KR": {"KOSPI": ladder_item},
                "US_TECH": {"NASDAQ100": ladder_item},
                "SEMIS": {"SOX": ladder_item},
            }
            for basis in NORMALISATION_LABELS
        },
    })

    validate_overlay_payload(payload)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    assert payload["series"]["signal"][episode_id]["equity_reference"][0] == 100.13
    assert payload["signal_values"][episode_id]["equity_reference"] == 80.13
    assert len(body) < 3_000_000
