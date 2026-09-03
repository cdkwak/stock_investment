from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from stock_data.gui.watchlist_service import LocalWatchlistService
from stock_web.api import home_data
from stock_web.api import scanner as scanner_api
from stock_web.api.scanner import build_scanner
from stock_web.api.stocks_page import (
    build_stocks_page_data,
    evaluate_conditions,
    save_conditions,
)
from stock_web.app import create_app
from tests.unit.web import ASGITestClient, make_project, new_temp_root


def _condition_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "conditions": [{
            "id": "low-rsi", "name": "RSI 낮음", "field": "rsi14",
            "op": "<=", "value": 30, "scope": "watchlist",
        }],
    }


def test_watchlist_add_reorder_remove_endpoints_use_local_store() -> None:
    root = make_project(new_temp_root())
    client = ASGITestClient(create_app(root))

    first = client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "005930"},
        client_host="127.0.0.1",
    )
    second = client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "000660"},
        client_host="::1",
    )
    moved = client.post(
        "/api/watchlist/items/move",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "000660", "offset": -1},
        client_host="127.0.0.1",
    )
    created = client.post(
        "/api/watchlists", json={"action": "create", "name": "배당주"},
        client_host="127.0.0.1",
    )
    created_id = created.json()["lists"][-1]["list_id"]
    renamed = client.post(
        "/api/watchlists",
        json={"action": "rename", "list_id": created_id, "name": "장기 관찰"},
        client_host="127.0.0.1",
    )
    removed = client.delete(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "005930"},
        client_host="127.0.0.1",
    )

    assert first.status_code == second.status_code == moved.status_code == 200
    assert created.status_code == renamed.status_code == removed.status_code == 200
    assert renamed.json()["lists"][-1]["name"] == "장기 관찰"
    assert [item["symbol"] for item in moved.json()["lists"][0]["items"]] == ["000660", "005930"]
    stored = LocalWatchlistService(root / "artifacts/local_user/watchlists.json").load()
    assert [item.identity.symbol for item in stored.default_list.items] == ["000660"]
    stocks = client.get("/api/stocks").json()
    assert stocks["table"][0]["price_available"] is True
    assert stocks["table"][0]["volume20_multiple"] == 2.0


def test_conditions_are_evaluated_saved_and_used_for_watchlist_flags() -> None:
    conditions = _condition_payload()["conditions"]
    assert [item["id"] for item in evaluate_conditions(
        {"rsi14": 29.5}, conditions, scope="watchlist",
    )] == ["low-rsi"]
    assert evaluate_conditions({"rsi14": None}, conditions, scope="watchlist") == []
    assert evaluate_conditions({"rsi14": 29.5}, conditions, scope="universe") == []

    root = make_project(new_temp_root())
    client = ASGITestClient(create_app(root))
    assert client.post(
        "/api/watch-conditions", json=_condition_payload(), client_host="127.0.0.1",
    ).status_code == 200
    assert client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "000660"},
        client_host="127.0.0.1",
    ).status_code == 200

    row = client.get("/api/stocks").json()["table"][0]
    assert row["flag"] == "RSI 낮음"
    assert row["condition_matches"][0]["observed"] <= 30
    assert home_data.build_watchlist(root)["rows"][0]["flag"] == "RSI 낮음"


def test_scanner_finds_one_oversold_symbol_in_tiny_exact_universe_and_caches() -> None:
    root = make_project(new_temp_root())

    result = build_scanner(root)
    cached = build_scanner(root)

    assert result["status"] == "READY"
    assert result["scanned_instruments"] == 3
    assert result["count"] == 1
    assert result["candidates"][0]["symbol"] == "000660"
    assert result["candidates"][0]["rsi14"] == pytest.approx(0.5433431733168419)
    assert result["fundamental_columns"] == []
    assert "없어 표시하지 않습니다" in result["fundamentals_note"]
    assert cached == result
    assert (root / "artifacts/local_user/scanner_cache.json").is_file()

    save_conditions(root, {
        "schema_version": 1,
        "conditions": [{
            "id": "nonnegative", "name": "등락률 비음수", "field": "change_pct",
            "op": ">=", "value": 0, "scope": "universe",
        }],
    })
    expanded = build_scanner(root)
    assert expanded["count"] == 3
    assert {item["symbol"] for item in expanded["candidates"]} == {"000660", "005930", "035720"}
    assert "사용자 전체시장 조건" in expanded["rule"]


def test_scanner_liquidity_filters_and_fundamental_annotations_are_queryable(
    monkeypatch,
) -> None:
    root = make_project(new_temp_root())
    save_conditions(root, {
        "schema_version": 1,
        "conditions": [{
            "id": "nonnegative", "name": "등락률 비음수", "field": "change_pct",
            "op": ">=", "value": 0, "scope": "universe",
        }],
    })
    monkeypatch.setattr(scanner_api, "liquidity_snapshot", lambda project_root, as_of: pd.DataFrame({
        "symbol": ["000660", "005930", "035720"],
        "avg_value_20d": [2_000_000_000.0, 500_000_000.0, 1_000_000_000.0],
        "market_cap": [200_000_000_000, 500_000_000_000, 100_000_000_000],
    }))
    monkeypatch.setattr(scanner_api, "fundamental_health", lambda project_root, as_of: pd.DataFrame({
        "symbol": ["000660"],
        "debt_ratio_pct": [42.25],
        "op_income_positive_4q": [True],
        "net_income_positive_4q": [False],
        "revenue_trend": ["INCREASING"],
        "fundamentals_as_of": [pd.Timestamp("2026-09-01T06:00:00+00:00")],
    }))
    client = ASGITestClient(create_app(root))

    filtered = client.get("/api/scanner").json()
    unfiltered = client.get("/api/scanner", params={"all": 1}).json()
    overridden = client.get(
        "/api/scanner", params={"min_value": 0, "min_cap": 0},
    ).json()

    assert filtered["count"] == 2
    assert {item["symbol"] for item in filtered["candidates"]} == {"000660", "035720"}
    assert filtered["filters"] == {
        "avg_value_20d_min": 1_000_000_000.0,
        "market_cap_min": 100_000_000_000.0,
        "applied": True,
    }
    assert "20일 평균 거래대금" in filtered["liquidity_note"]
    assert filtered["fundamentals_coverage"] == {
        "available": 1,
        "total": 2,
        "as_of": "2026-09-01T06:00:00+00:00",
    }
    health = next(item for item in filtered["candidates"] if item["symbol"] == "000660")
    missing = next(item for item in filtered["candidates"] if item["symbol"] == "035720")
    assert health["avg_value_20d"] == 2_000_000_000.0
    assert health["debt_ratio_pct"] == 42.25
    assert health["op_income_positive_4q"] is True
    assert health["net_income_positive_4q"] is False
    assert health["revenue_trend"] == "INCREASING"
    assert health["value_trap_state"] == "NOT_FLAGGED"
    assert all(missing[column] is None for column in scanner_api.FUNDAMENTAL_HEALTH_COLUMNS)
    assert missing["value_trap_state"] == "UNAVAILABLE"
    assert unfiltered["count"] == 3
    assert unfiltered["filters"]["applied"] is False
    assert "필터 해제" in unfiltered["liquidity_note"]
    assert overridden["count"] == 3
    assert overridden["filters"]["applied"] is True


def test_scanner_missing_filter_helpers_degrade_without_failing(monkeypatch) -> None:
    root = make_project(new_temp_root())

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("retained partition missing")

    monkeypatch.setattr(scanner_api, "liquidity_snapshot", unavailable)
    monkeypatch.setattr(scanner_api, "fundamental_health", unavailable)

    result = build_scanner(root)

    assert result["status"] == "READY"
    assert result["count"] == 1
    assert result["filters"]["applied"] is False
    assert result["liquidity_note"] == "유동성 데이터 없음 · 필터 미적용"
    assert result["fundamentals_coverage"] == {"available": 0, "total": 1, "as_of": None}
    assert result["candidates"][0]["avg_value_20d"] is None
    assert result["candidates"][0]["market_cap"] is None


def test_search_endpoint_combines_korean_catalog_and_static_us_etfs() -> None:
    root = make_project(new_temp_root())
    client = ASGITestClient(create_app(root))

    korean = client.get("/api/stocks/search", params={"q": "삼성"})
    etf = client.get("/api/stocks/search", params={"q": "EWY"})

    assert korean.status_code == etf.status_code == 200
    assert korean.json()["matches"][0]["symbol"] == "005930"
    assert any(item["symbol"] == "EWY" for item in etf.json()["matches"])
    assert client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "US ETF", "symbol": "EWY"},
        client_host="127.0.0.1",
    ).status_code == 200
    row = client.get("/api/stocks").json()["table"][0]
    assert row["symbol"] == "EWY"
    assert row["price_available"] is False
    assert row["unavailable_reason"] == "로컬 가격 없음"


def test_stocks_posts_refuse_non_loopback_and_page_renders() -> None:
    root = make_project(new_temp_root())
    client = ASGITestClient(create_app(root))

    refused_item = client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "005930"},
    )
    refused_conditions = client.post("/api/watch-conditions", json=_condition_payload())

    assert refused_item.status_code == refused_conditions.status_code == 403
    assert not (root / "artifacts/local_user/watchlists.json").exists()
    assert not (root / "artifacts/local_user/watch_conditions.json").exists()
    page = client.get("/stocks")
    assert page.status_code == 200
    assert "관심종목 관리" in page.text
    assert "과매도 스캐너" in page.text
    assert 'id="scanner-min-value"' in page.text
    assert 'id="scanner-min-cap"' in page.text
    assert 'id="scanner-fundamentals-only"' in page.text
    assert 'id="add-selected-stock"' in page.text
    assert "검색 결과에서 종목을 선택하세요." in page.text


def test_stocks_search_selection_and_new_row_flash_are_client_side() -> None:
    script = (
        Path(__file__).parents[3] / "src/stock_web/static/stocks.js"
    ).read_text(encoding="utf-8")

    assert "선택: ${selectedSearch.name} ${selectedSearch.symbol} → 목록 '${list.name}'에 추가" in script
    assert 'target.id === "add-selected-stock"' in script
    assert "flash-new" in script
    assert "SIIndicators.rsiWilder" in script
    assert "function rsiPoints" not in script
    assert script.count("Wilder 지수이동평균 방식") == 2


def test_home_symbol_query_is_embedded_for_chart_preselection() -> None:
    root = make_project(new_temp_root())
    response = ASGITestClient(create_app(root)).get("/?symbol=005930")

    assert response.status_code == 200
    assert 'data-initial-symbol="005930"' in response.text


def test_stocks_page_labels_provisional_basis_and_conditions_use_same_day_close() -> None:
    root = make_project(new_temp_root())
    path = (
        root / "data/normalized/kr_equity_price_provisional_daily/"
        "market=KOSPI/year=2026/data.parquet"
    )
    path.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": [pd.Timestamp("2026-09-03")],
        "market": ["KOSPI"], "symbol": ["005930"],
        "open": [195], "high": [205], "low": [190], "close": [200],
        "volume": [2_000_000], "trading_value": [400_000_000],
        "source": ["pykrx"],
        "source_operation": ["stock.get_market_ohlcv_by_ticker"],
        "source_date": [pd.Timestamp("2026-09-03")],
        "provisional": [True],
        "observed_at": [pd.Timestamp("2026-09-03T11:31:00Z")],
    }).to_parquet(path, index=False)
    client = ASGITestClient(create_app(root))
    assert client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "KOSPI", "symbol": "005930"},
        client_host="127.0.0.1",
    ).status_code == 200
    save_conditions(root, {
        "schema_version": 1,
        "conditions": [{
            "id": "same-day-jump", "name": "당일 상승",
            "field": "change_pct", "op": ">=", "value": 10,
            "scope": "watchlist",
        }],
    })

    row = build_stocks_page_data(root)["table"][0]

    assert row["as_of"] == "2026-09-03"
    assert row["price_basis"] == "provisional"
    assert row["provisional_dates"] == ["2026-09-03"]
    assert row["price"] == 200
    assert row["flag"] == "당일 상승"
