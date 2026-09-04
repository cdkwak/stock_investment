from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_web.api import changes, home_data
from stock_web.app import create_app
from tests.unit.web import ASGITestClient
from tests.unit.web import make_project, new_temp_root


def _price_series(
    symbol: str, closes: list[float], *, volumes: list[float] | None = None,
) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=len(closes), freq="B"),
        "series_id": symbol,
        "basket": "TEST",
        "symbol": symbol,
        "close": closes,
        "volume": volumes if volumes is not None else [10.0] * len(closes),
    })


def _identities(*symbols: str) -> dict[str, dict[str, str]]:
    return {
        symbol: {"symbol": symbol, "name": symbol, "market": "TEST"}
        for symbol in symbols
    }


def test_two_session_diff_finds_entry_exit_and_ignores_unchanged() -> None:
    frame = pd.concat([
        _price_series("ENTRY", [100.0] * 61 + [90.0]),
        _price_series("EXIT", [100.0] * 60 + [90.0, 100.0]),
        _price_series("SAME", [100.0] * 60 + [90.0, 80.0]),
    ], ignore_index=True)
    condition = {
        "id": "day5", "name": "하루 -5% 이하 급락",
        "field": "change_pct", "op": "<=", "value": -5.0, "scope": "universe",
    }

    result = changes._changes_from_frame(
        frame,
        identities=_identities("ENTRY", "EXIT", "SAME"),
        conditions=[condition], watch_scope=set(),
        previous_rule_levels={"현금 규칙": "주의"},
        current_rule_levels={"현금 규칙": "정상"},
    )

    assert [item["symbol"] for item in result["condition_entries"]] == ["ENTRY"]
    assert [item["symbol"] for item in result["condition_exits"]] == ["EXIT"]
    assert all(item["symbol"] != "SAME" for item in [
        *result["condition_entries"], *result["condition_exits"],
    ])
    assert result["rule_changes"] == [{
        "rule": "현금 규칙", "from_level": "주의", "to_level": "정상",
    }]


def test_new_52_week_high_and_low_are_counted_only_on_the_flip() -> None:
    frame = pd.concat([
        _price_series("HIGH", [100.0] * 251 + [90.0, 110.0]),
        _price_series("LOW", [100.0] * 251 + [110.0, 80.0]),
        _price_series("STILL_HIGH", list(range(1, 254))),
    ], ignore_index=True)

    result = changes._changes_from_frame(
        frame, identities=_identities("HIGH", "LOW", "STILL_HIGH"),
        conditions=[], watch_scope=set(),
    )

    assert result["new_highs_52w"] == 1
    assert result["new_lows_52w"] == 1
    assert result["new_highs_52w_list"] == [{"symbol": "HIGH", "display": "HIGH"}]
    assert result["new_lows_52w_list"] == [{"symbol": "LOW", "display": "LOW"}]
    assert result["counts"]["new_highs_52w"] == 1
    assert result["counts"]["new_lows_52w"] == 1


def test_watchlist_scope_excludes_symbols_that_are_only_in_the_broad_universe() -> None:
    frame = pd.concat([
        _price_series("WATCHED", [100.0] * 61 + [90.0]),
        _price_series("BROAD", [100.0] * 61 + [90.0]),
    ], ignore_index=True)
    condition = {
        "id": "watch-drop", "name": "관심종목 급락",
        "field": "change_pct", "op": "<=", "value": -5.0, "scope": "watchlist",
    }

    result = changes._changes_from_frame(
        frame, identities=_identities("WATCHED", "BROAD"),
        conditions=[condition], watch_scope={"WATCHED"},
    )

    assert [item["symbol"] for item in result["condition_entries"]] == ["WATCHED"]


def test_volume_spike_uses_the_existing_twenty_session_signal_ratio() -> None:
    volumes = [10.0] * 20 + [100.0]
    frame = _price_series("SPIKE", [100.0] * len(volumes), volumes=volumes)

    result = changes._changes_from_frame(
        frame, identities=_identities("SPIKE"), conditions=[], watch_scope=set(),
    )

    assert result["volume_spikes"] == [{
        "symbol": "SPIKE", "display": "SPIKE", "ratio": pytest.approx(6.9),
    }]
    assert result["counts"]["volume_spikes"] == 1


def test_stale_symbol_is_not_diffed_against_an_older_basket_session() -> None:
    current = _price_series("CURRENT", [100.0] * 62)
    stale = _price_series("STALE", [100.0] * 60 + [90.0])
    condition = {
        "id": "drop", "name": "하루 -5% 이하 급락",
        "field": "change_pct", "op": "<=", "value": -5.0, "scope": "universe",
    }

    result = changes._changes_from_frame(
        pd.concat([current, stale], ignore_index=True),
        identities=_identities("CURRENT", "STALE"), conditions=[condition], watch_scope=set(),
    )

    assert result["as_of"] == current["date"].iloc[-1].date().isoformat()
    assert result["condition_entries"] == []


def test_guest_mode_filters_every_change_to_public_watchlist_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    config = root / "config/public_watchlist.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "schema_version": 1,
        "items": [{
            "market": "US ETF", "symbol": "PUBLIC", "name": "Public ETF",
            "security_type": "ETF",
        }],
    }), encoding="utf-8")
    frame = pd.concat([
        _price_series("PUBLIC", [100.0] * 20 + [100.0], volumes=[10.0] * 20 + [100.0]),
        _price_series("PRIVATE", [100.0] * 20 + [100.0], volumes=[10.0] * 20 + [100.0]),
    ], ignore_index=True)
    monkeypatch.setattr(changes, "_dataset_signature", lambda *args, **kwargs: "sig")
    monkeypatch.setattr(changes, "_rule_levels", lambda *args, **kwargs: {})
    monkeypatch.setattr(changes, "_load_price_frame", lambda *args, **kwargs: frame)
    monkeypatch.setattr(
        changes, "_read_cache",
        lambda *args, **kwargs: pytest.fail("guest mode must not read the user cache"),
    )
    monkeypatch.setattr(
        changes, "_write_cache",
        lambda *args, **kwargs: pytest.fail("guest mode must not write the user cache"),
    )
    monkeypatch.setattr(
        changes, "load_conditions",
        lambda *_args: pytest.fail("guest mode must not read user conditions"),
    )

    result = changes.build_changes(root, public_mode=True)

    assert [item["symbol"] for item in result["volume_spikes"]] == ["PUBLIC"]
    assert result["condition_entries"] == []
    assert result["condition_exits"] == []


def test_private_universe_unions_kospi200_holdings_watchlists_and_registered_us(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    condition = {"id": "all", "scope": "universe"}
    monkeypatch.setattr(changes, "_kospi200_members", lambda _root: {
        "K200": {"symbol": "K200", "name": "K200", "market": "KOSPI"},
    })
    monkeypatch.setattr(changes, "_market_cap_proxy", lambda _root: pytest.fail("exact membership wins"))
    monkeypatch.setattr(changes, "_registered_us_identities", lambda: {
        "USREG": {"symbol": "USREG", "name": "USREG", "market": "US ETF"},
    })
    monkeypatch.setattr(changes, "_held_identities", lambda _root: {
        "HELD": {"symbol": "HELD", "name": "HELD", "market": "US"},
    })
    monkeypatch.setattr(changes, "_private_watchlist_identities", lambda _root: {
        "WATCH": {"symbol": "WATCH", "name": "WATCH", "market": "US ETF"},
    })
    monkeypatch.setattr(changes, "_master_names", lambda *_args: {})
    monkeypatch.setattr(changes, "load_conditions", lambda _root: {"conditions": [condition]})

    identities, conditions, watch_scope = changes._collect_context(root, public_mode=False)

    assert set(identities) == {"K200", "USREG", "HELD", "WATCH"}
    assert conditions == [condition]
    assert watch_scope == {"HELD", "WATCH"}


def test_market_cap_fallback_selects_the_latest_top_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    frame = pd.DataFrame({
        "date": [pd.Timestamp("2026-09-03")] + [pd.Timestamp("2026-09-04")] * 201,
        "market": ["KOSPI"] * 202,
        "symbol": ["OLD"] + [f"{index:06d}" for index in range(201)],
        "market_cap": [10**20] + list(range(1, 202)),
    })
    monkeypatch.setattr(changes, "_recent_paths", lambda *_args, **_kwargs: [Path("fixture.parquet")])
    monkeypatch.setattr(changes, "_read_paths", lambda *_args, **_kwargs: frame)

    result = changes._market_cap_proxy(root)

    assert len(result) == 200
    assert "OLD" not in result
    assert "000000" not in result
    assert "000200" in result


def test_home_payload_exposes_changes_section_shape_for_the_new_strip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_project(new_temp_root())
    expected = changes._empty_payload()
    expected["as_of"] = "2026-09-02"
    monkeypatch.setattr(home_data, "build_changes", lambda *_args, **_kwargs: expected)
    home_data._HOME_CACHE.clear()

    payload = home_data.build_home_payload(root)

    assert payload["sections"]["changes"] == expected
    assert set(expected) == {
        "as_of", "rule_changes", "condition_entries", "condition_exits",
        "new_highs_52w", "new_lows_52w", "new_highs_52w_list",
        "new_lows_52w_list", "volume_spikes", "counts",
    }


def test_changes_get_route_uses_the_same_public_mode_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    expected = changes._empty_payload()
    observed: list[bool] = []
    monkeypatch.setattr(
        changes, "build_changes",
        lambda _root, public_mode=False: observed.append(public_mode) or expected,
    )

    response = ASGITestClient(create_app(root)).get("/api/changes")

    assert response.status_code == 200
    assert response.json() == expected
    assert observed == [True]
