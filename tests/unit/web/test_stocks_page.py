from __future__ import annotations

from pathlib import Path

from stock_data.gui.watchlist_service import LocalWatchlistService
from stock_web.api import home_data
from stock_web.api.scanner import build_scanner
from stock_web.api.stocks_page import evaluate_conditions, save_conditions
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
    assert result["candidates"][0]["rsi14"] <= 30
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
    assert 'id="add-selected-stock"' in page.text
    assert "검색 결과에서 종목을 선택하세요." in page.text


def test_stocks_search_selection_and_new_row_flash_are_client_side() -> None:
    script = (
        Path(__file__).parents[3] / "src/stock_web/static/stocks.js"
    ).read_text(encoding="utf-8")

    assert "선택: ${selectedSearch.name} ${selectedSearch.symbol} → 목록 '${list.name}'에 추가" in script
    assert 'target.id === "add-selected-stock"' in script
    assert "flash-new" in script


def test_home_symbol_query_is_embedded_for_chart_preselection() -> None:
    root = make_project(new_temp_root())
    response = ASGITestClient(create_app(root)).get("/?symbol=005930")

    assert response.status_code == 200
    assert 'data-initial-symbol="005930"' in response.text
