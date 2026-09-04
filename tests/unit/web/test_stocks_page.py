from __future__ import annotations

from pathlib import Path
from time import perf_counter

import pandas as pd
import pytest

from stock_data.gui.watchlist_service import LocalWatchlistService
from stock_web.api import home_data, stocks_page, symbol_resolver
from stock_web.api import scanner as scanner_api
from stock_web.api.scanner import build_scanner
from stock_web.api.stocks_page import (
    build_stocks_page_data,
    evaluate_conditions,
    save_conditions,
    search_stocks,
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
    fundamentals_path = root / "data/normalized/kr_fundamentals_quarterly/data.parquet"
    fundamentals_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "symbol": ["000660"] * 7,
        "bsns_year": [2025, 2025, 2025, 2025, 2026, 2026, 2026],
        "reprt_code": ["11013", "11012", "11014", "11011", "11013", "11012", "11012"],
        "fs_div": ["CFS"] * 7,
        "period_end": pd.to_datetime([
            "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
            "2026-03-31", "2026-06-30", "2026-06-30",
        ]),
        "revenue": [10, 11, 12, 13, 14, 15, 99],
        "operating_income": [1, 1, 1, 1, 1, 1, -99],
        "net_income": [1, 1, 1, -1, 1, 1, -99],
        "debt_ratio_pct": [40, 41, 42, 43, 44, 42.25, 999],
        "rcept_no": [
            "20250515000001", "20250814000001", "20251114000001",
            "20260331000001", "20260515000001", "20260814000001",
            "20260903000002",
        ],
        # All rows were collected after the 09-02 price basis. Availability
        # must still follow the earlier disclosure dates encoded in rcept_no.
        "retrieved_at": pd.to_datetime(["2026-09-03T06:00:00Z"] * 7),
    }).to_parquet(fundamentals_path, index=False)
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
        "as_of": "2026-08-14",
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


def test_watchlist_accepts_skhy_registry_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(new_temp_root())
    monkeypatch.setattr(symbol_resolver, "_global_equity_registry", lambda: {
        "SKHY": {
            "korean_name": "SK하이닉스(ADR)", "official_exchange": "NASDAQ",
            "security_type": "DEPOSITARY_RECEIPT", "underlying_kr_symbol": "000660",
            "expected_currency": "USD",
        },
    })
    symbol_resolver._SYMBOL_INDEX_CACHE.clear()
    stocks_page._SEARCH_INDEX_CACHE.clear()
    dates = pd.date_range("2026-07-13", periods=39, freq="B")
    target = root / "data/normalized/global_equity_price_daily/symbol=SKHY/year=2026/data.parquet"
    target.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": dates, "symbol": ["SKHY"] * 39, "source_ticker": ["SKHY"] * 39,
        "open": [160.0] * 39, "high": [165.0] * 39, "low": [159.0] * 39,
        "close": [163.679993] * 39, "adjusted_close": [163.679993] * 39,
        "volume": [1_000_000] * 39, "currency": ["USD"] * 39,
        "exchange": ["NASDAQ"] * 39, "provider": ["fixture"] * 39,
        "retrieved_at": pd.to_datetime(["2026-09-04T00:00:00Z"] * 39),
        "adjustment_status": ["RAW_AND_ADJUSTED_RETAINED"] * 39,
    }).to_parquet(target, index=False)
    client = ASGITestClient(create_app(root))

    search = client.get("/api/stocks/search", params={"q": "하이닉스 ADR"}).json()
    added = client.post(
        "/api/watchlist/items",
        json={"list_id": "favorites", "market": "US 주식", "symbol": "SKHY"},
        client_host="127.0.0.1",
    )

    assert search["matches"][0]["symbol"] == "SKHY"
    assert search["matches"][0]["source"] == "global_equity_registry"
    assert added.status_code == 200
    assert added.json()["lists"][0]["items"][0]["market"] == "US 주식"
    assert added.json()["lists"][0]["items"][0]["security_type"] == "ADR"
    row = client.get("/api/stocks").json()["table"][0]
    assert row["price_display"] == "163.68"
    assert row["ma60_display"] == "— (상장 60일 미만)"
    assert row["disp60_display"] == "— (상장 60일 미만)"


def test_search_index_ranks_exact_then_prefix_by_latest_market_cap() -> None:
    root = make_project(new_temp_root())
    master_path = root / "data/normalized/kr_equity_master/market=KOSPI/data.parquet"
    master = pd.read_parquet(master_path)
    additions = pd.DataFrame({
        "symbol": ["000001", "000002", "000003"],
        "name": ["삼성스팩10호", "삼성스팩11호", "삼성스팩12호"],
        "market": ["KOSPI"] * 3,
        "isin": ["KR7000000001", "KR7000000002", "KR7000000003"],
        "listing_date": ["2025-01-01"] * 3,
        "delisting_date": [None] * 3,
        "security_type_name": ["보통주"] * 3,
    })
    pd.concat([master, additions], ignore_index=True).to_parquet(master_path, index=False)
    cap_path = root / "data/normalized/kr_equity_market_cap_daily/market=KOSPI/year=2026/data.parquet"
    cap_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "date": [pd.Timestamp("2026-09-02")] * 4,
        "symbol": ["005930", "000001", "000002", "000003"],
        "market_cap": [500_000_000_000_000, 100, 300, 200],
    }).to_parquet(cap_path, index=False)

    prefix = search_stocks(root, "삼성")["matches"]
    exact_name = search_stocks(root, "삼성스팩10호")["matches"]
    exact_symbol = search_stocks(root, "000003")["matches"]

    assert [item["symbol"] for item in prefix[:4]] == ["005930", "000002", "000003", "000001"]
    assert exact_name[0]["symbol"] == "000001"
    assert exact_symbol[0]["symbol"] == "000003"


def test_search_index_includes_full_kr_etf_universe_with_ranking_and_warm_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_project(new_temp_root())
    path = root / "data/normalized/kr_etf_universe_daily/source_date=2026-09-04/data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "source_date": ["2026-09-04"] * 4,
        "symbol": ["0015B0", "0099A0", "0088A0", "0077A0"],
        "name": [
            "KoAct 미국나스닥성장기업액티브",
            "성장기업 ETF",
            "미국 성장기업 인컴 ETF",
            "0015B0 추종 ETF",
        ],
        "full_name": [
            "삼성 KoAct 미국나스닥성장기업액티브증권상장지수투자신탁",
            "성장기업 상장지수투자신탁",
            "미국 성장기업 인컴 상장지수투자신탁",
            "0015B0 추종 상장지수투자신탁",
        ],
        "isin": ["KR70015B0001", "KR70099A0001", "KR70088A0001", "KR70077A0001"],
        "listing_date": ["2025-02-25", "2024-01-02", "2024-01-03", "2024-01-04"],
        "underlying_index": ["NASDAQ Growth", "Growth", "Growth Income", "Code Tracking"],
        "market": ["KRX"] * 4,
        "security_type": ["ETF"] * 4,
        "listing_status": ["LISTED_AT_SOURCE_DATE"] * 4,
    }).to_parquet(path, index=False)

    exact = search_stocks(root, "0015B0")["matches"]
    ranked = search_stocks(root, "성장기업")["matches"]
    full_name = search_stocks(root, "삼성 KoAct")["matches"]

    assert exact[0]["symbol"] == "0015B0"
    assert exact[0]["name"] == "KoAct 미국나스닥성장기업액티브"
    assert exact[0]["market"] == "KRX"
    assert exact[0]["security_type"] == "ETF"
    assert exact[0]["listing_date"] == "2025-02-25"
    assert exact[0]["source"] == "kr_etf_universe"
    assert exact[1]["symbol"] == "0077A0"
    assert ranked[0]["symbol"] == "0099A0"
    assert {item["symbol"] for item in ranked[1:3]} == {"0015B0", "0088A0"}
    assert full_name[0]["symbol"] == "0015B0"

    monkeypatch.setattr(
        stocks_page.pd, "read_parquet",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("warm search rebuilt")),
    )
    started = perf_counter()
    warm = search_stocks(root, "KoAct")
    elapsed = perf_counter() - started
    assert warm["matches"][0]["symbol"] == "0015B0"
    assert elapsed < 0.2


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
    shared_script = (
        Path(__file__).parents[3] / "src/stock_web/static/app.js"
    ).read_text(encoding="utf-8")

    assert "선택: ${selectedSearch.name} ${selectedSearch.symbol} → 목록 '${list.name}'에 추가" in script
    assert 'target.id === "add-selected-stock"' in script
    assert "flash-new" in script
    assert "SIIndicators.rsiWilder" in script
    assert "function rsiPoints" not in script
    assert script.count("Wilder 지수이동평균 방식") == 2
    assert "sidebarSearchSequence" in script
    assert "sequence !== sidebarSearchSequence" in script
    assert "query.length < 2" in script
    assert "), 350);" in script
    assert 'xMode: "index"' in script
    assert 'type: "custom"' in script
    assert 'type: "custom"' in shared_script
    assert 'options.xMode === "index"' in shared_script
    assert '정식 종가 기준 (${String(result.as_of).slice(5)}) · 잠정 미포함' in script


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
