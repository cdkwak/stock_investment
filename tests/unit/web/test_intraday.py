from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_web.api import home_data, intraday
from tests.unit.web import make_project, new_temp_root


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _envelope(observation: dict[str, object]) -> dict[str, object]:
    route_id = str(observation["route_id"])
    return {
        "circuits": {
            route_id: {
                "failure_kind": None, "generation": 0, "is_open": False,
                "safe_code": None,
            },
        },
        "decisions": {
            route_id: {
                "fallback_requests": 0, "outcome": "PRIMARY_ACCEPTED",
                "primary_requests": 1, "selected_role": "PRIMARY",
            },
        },
        "observations": [observation],
        "schema_version": 1,
    }


def _toss_observation(stamp: str, value: float) -> dict[str, object]:
    return {
        "display_only": True,
        "finality": "PROVISIONAL",
        "identity": {
            "dataset_id": "TOSS_MARKET_PRICE_SNAPSHOT",
            "market": "XKRX",
            "symbol": "KOSPI",
        },
        "interval": "snapshot",
        "pit_safe": False,
        "provider": "tossinvest_open_api",
        "provider_timestamp_utc": stamp,
        "retrieved_at_utc": stamp,
        "route_id": "toss-market-price:KOSPI:snapshot:PROVISIONAL",
        "source_route": "/api/v1/market-indicators/prices",
        "timestamp_basis": "RETRIEVAL_TIMESTAMP",
        "unit": "index points",
        "upstream_provider": "tossinvest_open_api",
        "value": value,
    }


def _write_toss_session(root: Path) -> None:
    observations = [
        ("2026-09-03T09:00:00+09:00", "2026-09-03T00:00:00+00:00", 101.0),
        ("2026-09-03T09:30:00+09:00", "2026-09-03T00:30:00+00:00", 102.0),
        ("2026-09-03T10:00:00+09:00", "2026-09-03T01:00:00+00:00", 103.0),
    ]
    windows: dict[str, object] = {}
    for window, stamp, value in observations:
        body = json.dumps(
            {"result": [{"symbol": "KOSPI", "timestamp": None, "lastPrice": str(value)}]},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        relative = (
            "data/landing/tossinvest/domestic_ur246/2026-09-03/"
            f"{window.replace(':', '')}/KOSPI/{digest}/response.json"
        )
        landing = root / relative
        landing.parent.mkdir(parents=True, exist_ok=True)
        landing.write_bytes(body)
        windows[window] = {
            "KOSPI": {
                "business_get_cap": 1,
                "fallback_count": 0,
                "landing_file": relative,
                "landing_sha256": digest,
                "oauth_cap": 1,
                "provider_timestamp_utc": stamp,
                "redirect_count": 0,
                "replay_api_calls": 0,
                "retry_count": 0,
                "route_id": "toss-market-price:KOSPI:snapshot:PROVISIONAL",
                "status": "COMPLETE",
            },
        }
    _write_json(root / "data/state/toss_domestic_ur246/2026-09-03.json", {
        "date_kst": "2026-09-03", "operation_id": "UR-246",
        "schema_version": 1, "windows": windows,
    })
    _write_json(
        root / "data/state/current_observations/toss_kospi_ur246.json",
        _envelope(_toss_observation(observations[-1][1], observations[-1][2])),
    )


def _yahoo_observation(stamp: str, value: float) -> dict[str, object]:
    return {
        "display_only": True,
        "finality": "AS_RETRIEVED",
        "identity": {
            "dataset_id": "MARKET_PRICE_CURRENT",
            "market": "CME",
            "symbol": "NQ=F",
        },
        "interval": "30m",
        "pit_safe": False,
        "provider": "YAHOO",
        "provider_timestamp_utc": stamp,
        "retrieved_at_utc": stamp,
        "route_id": "yahoo-market-current:CME:NQ=F",
        "source_route": "YAHOO_CHART_30M:NQ=F",
        "timestamp_basis": "PROVIDER_TIMESTAMP",
        "unit": "index points",
        "upstream_provider": "YAHOO_CHART_API",
        "value": value,
    }


def _write_nq_session(root: Path, points: list[tuple[str, float]]) -> None:
    current = root / "data/state/current_observations/global60m_current"
    _write_json(current / "nq_futures_current_60m.json", _envelope(
        _yahoo_observation(points[-1][0], points[-1][1]),
    ))
    _write_json(current / "nq_futures_current_60m.session.json", {
        "completed_bars_only": True,
        "interval": "30m",
        "points": [{"bar_end_utc": stamp, "value": value} for stamp, value in points],
        "provider_symbol": "NQ=F",
        "schema_version": 1,
        "series_id": "NQ_FUTURES_CURRENT_60M",
        "session_date": "2026-09-03",
        "session_end_local": None,
        "session_semantics": "FUTURES_PROVIDER_SESSION",
        "session_start_local": "18:00",
        "source_timezone": "America/New_York",
    })


def test_korean_session_maps_toss_windows_from_0900(monkeypatch) -> None:
    root = make_project(new_temp_root())
    _write_toss_session(root)
    monkeypatch.setattr(
        intraday, "_now_utc", lambda: datetime(2026, 9, 3, 1, 31, tzinfo=timezone.utc),
    )

    result = intraday.load_intraday_series(root, "KOSPI")

    assert result is not None
    assert [point["v"] for point in result["points"]] == [101.0, 102.0, 103.0]
    assert result["points"][0]["t"] == "2026-09-03T09:00:00+09:00"
    assert result["window"] == "당일 09:00~ · 10:00"
    assert result["as_of"] == "2026-09-03T10:00:00+09:00"
    assert result["source"] == "Toss 국내 30분 관측 · KOSPI"
    tile = next(item for item in home_data.build_tiles(root) if item["name"] == "KOSPI")
    assert tile["value"] == "151.80"
    assert tile["spark_kind"] == "intraday"
    # The synthetic retained close (2026-09-17) is newer than the intraday observation, so the
    # close is the truth: no 장중 note and the headline change stays close-to-close.
    assert "latest_intraday" not in tile
    assert "close_change_pct" not in tile
    assert tile["spark_kind"] == "intraday"


def test_futures_window_keeps_only_last_24_hours(monkeypatch) -> None:
    root = new_temp_root()
    _write_nq_session(root, [
        ("2026-09-02T08:00:00+00:00", 201.0),
        ("2026-09-02T10:00:00+00:00", 202.0),
        ("2026-09-03T08:30:00+00:00", 203.0),
        ("2026-09-03T09:00:00+00:00", 204.0),
        ("2026-09-03T09:30:00+00:00", 205.0),
    ])
    monkeypatch.setattr(
        intraday, "_now_utc", lambda: datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc),
    )

    result = intraday.load_intraday_series(root, "NASDAQ 100 선물")

    assert result is not None
    assert [point["v"] for point in result["points"]] == [202.0, 203.0, 204.0, 205.0]
    assert result["window"] == "24h 선물 · 18:30 KST"


def test_old_bar_during_open_session_is_labeled_delayed(monkeypatch) -> None:
    root = new_temp_root()
    _write_nq_session(root, [
        ("2026-09-03T09:00:00+00:00", 301.0),
        ("2026-09-03T09:30:00+00:00", 302.0),
        ("2026-09-03T10:00:00+00:00", 303.0),
    ])
    monkeypatch.setattr(
        intraday, "_now_utc", lambda: datetime(2026, 9, 3, 14, 1, tzinfo=timezone.utc),
    )

    result = intraday.load_intraday_series(root, "NASDAQ 100 선물")

    assert result is not None
    assert result["window"] == "장중 · 갱신 지연"


def test_missing_store_keeps_daily_sparkline(monkeypatch) -> None:
    root = make_project(new_temp_root())
    monkeypatch.setattr(
        intraday, "_now_utc", lambda: datetime(2026, 9, 3, 1, 31, tzinfo=timezone.utc),
    )

    assert intraday.load_intraday_series(root, "KOSPI") is None
    tile = next(item for item in home_data.build_tiles(root) if item["name"] == "KOSPI")

    assert tile["spark_kind"] == "daily"
    assert tile["window"] == "최근 30일 마감"
    assert len(tile["spark"]) == 30


def test_fx_intraday_becomes_headline_against_previous_kst_session(monkeypatch) -> None:
    root = make_project(new_temp_root())

    def retained_intraday(_root: Path, tile_key: str) -> dict[str, object] | None:
        if tile_key != "USD/KRW":
            return None
        return {
            "points": [
                {"t": "2026-09-02T23:30:00+09:00", "v": 1_380.0},
                {"t": "2026-09-03T00:30:00+09:00", "v": 1_390.0},
                {"t": "2026-09-03T01:30:00+09:00", "v": 1_400.0},
            ],
            "window": "최근 24h · 01:30 KST", "source": "Yahoo · KRW=X",
        }

    monkeypatch.setattr(home_data, "load_intraday_series", retained_intraday)
    tile = next(item for item in home_data.build_tiles(root) if item["name"] == "USD/KRW")

    assert tile["value"] == "1,400.00"
    assert tile["change_pct"] == pytest.approx((1_400 / 1_380 - 1) * 100)
    assert tile["window"] == "24h · 01:30 KST"
    assert tile["sub_note"].startswith("FRED 확정 ")
    assert "latest_intraday" not in tile


def test_tile_source_notes_and_intraday_difference_threshold(monkeypatch) -> None:
    root = make_project(new_temp_root())
    daily_kospi = next(item for item in home_data.build_tiles(root) if item["name"] == "KOSPI")
    daily_value = float(str(daily_kospi["value"]).replace(",", ""))

    def retained_intraday(_root: Path, tile_key: str) -> dict[str, object] | None:
        if tile_key == "KOSPI":
            latest = daily_value * 1.0004
            return {
                "points": [
                    {"t": "2026-09-03T09:00:00+09:00", "v": daily_value},
                    {"t": "2026-09-03T09:30:00+09:00", "v": daily_value},
                    {"t": "2026-09-03T10:00:00+09:00", "v": latest},
                ],
                "window": "당일 09:00~ · 10:00", "source": "Toss",
            }
        if tile_key in {"VIX", "미국 10Y"}:
            value = 15.6 if tile_key == "VIX" else 4.1
            return {
                "points": [
                    {"t": "2026-09-02T23:00:00+09:00", "v": value - 0.2},
                    {"t": "2026-09-03T00:30:00+09:00", "v": value - 0.1},
                    {"t": "2026-09-03T01:30:00+09:00", "v": value},
                ],
                "window": "최근 24h · 01:30 KST", "source": "Yahoo",
            }
        return None

    monkeypatch.setattr(home_data, "load_intraday_series", retained_intraday)
    tiles = home_data.build_tiles(root)
    kospi = next(item for item in tiles if item["name"] == "KOSPI")
    vix = next(item for item in tiles if item["name"] == "VIX")
    treasury = next(item for item in tiles if item["name"] == "미국 10Y")

    assert "latest_intraday" not in kospi
    assert vix["sub_note"].startswith("FRED 마감 ")
    assert "장중 ^VIX 15.6" in vix["sub_note"]
    assert "^TNX 지수" in treasury["sub_note"]
