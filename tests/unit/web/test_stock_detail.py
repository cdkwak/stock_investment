from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stock_data.research.target_prices import (
    append_target_price_vintages_atomic,
    rows_to_frame,
)
from stock_web.app import create_app
from tests.unit.web import ASGITestClient, make_project, new_temp_root


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _make_detail_project() -> Path:
    root = make_project(new_temp_root())
    master_path = "data/normalized/kr_equity_master/market=KOSPI/data.parquet"
    _write_parquet(root, master_path, pd.DataFrame({
        "symbol": ["005930", "000660"],
        "name": ["삼성전자", "SK하이닉스"],
        "market": ["KOSPI", "KOSPI"],
        "isin": ["KR7005930003", "KR7000660001"],
        "corp_no": ["1301110006246", "1344110001387"],
        "company_name": ["삼성전자 주식회사", "SK하이닉스 주식회사"],
        "security_type_code": ["010", "010"],
        "security_type_name": ["보통주", "보통주"],
        "par_value": [100.0, 5000.0],
        "issued_shares": [1_000_000_000, 728_002_365],
        "listing_date": ["1975-06-11", "1996-12-26"],
        "delisting_date": [None, None],
        "deposit_registration_date": [None, None],
        "deposit_cancellation_date": [None, None],
        "source": ["fixture", "fixture"],
        "source_date": ["2026-09-02", "2026-09-02"],
    }))

    periods = [
        (2025, "11013", "2025-03-31", 10e12, 1.0e12, .8e12, 40.0, "20250515000001", "CFS"),
        (2025, "11012", "2025-06-30", 11e12, 1.1e12, .9e12, 41.0, "20250814000001", "CFS"),
        (2025, "11014", "2025-09-30", 12e12, 1.2e12, 1.0e12, 42.0, "20251114000001", "CFS"),
        (2025, "11011", "2025-12-31", 13e12, 1.3e12, 1.1e12, 43.0, "20260331000001", "CFS"),
        (2026, "11013", "2026-03-31", 14e12, 1.4e12, 1.2e12, 44.0, "20260515000001", "CFS"),
        (2026, "11012", "2026-06-30", 15e12, 1.5e12, 1.3e12, 45.0, "20260814000001", "OFS"),
        (2026, "11012", "2026-06-30", 16e12, 2.0e12, 1.4e12, 46.0, "20260814000002", "CFS"),
        (2026, "11012", "2026-06-30", 17e12, 2.1e12, 1.5e12, 47.0, "20260814000003", "CFS"),
    ]
    _write_parquet(root, "data/normalized/kr_fundamentals_quarterly/data.parquet", pd.DataFrame([
        {
            "symbol": "005930", "bsns_year": year, "reprt_code": report,
            "fs_div": scope, "period_end": end, "revenue": revenue,
            "operating_income": operating, "net_income": net,
            "total_liabilities": ratio * 1e10, "total_equity": 1e12,
            "debt_ratio_pct": ratio, "rcept_no": receipt,
        }
        for year, report, end, revenue, operating, net, ratio, receipt, scope in periods
    ]))

    dividend_rows = []
    for observed, paid, amount in (
        ("20250930", "20251120", 100.0),
        ("20251231", "20260220", 200.0),
        ("20260331", "20260520", 300.0),
        ("20260630", "20260820", 400.0),
        ("20260930", "20261120", 500.0),
    ):
        dividend_rows.append({
            "isin": "KR7005930003", "company": "삼성전자",
            "event_type": "CASH_DIVIDEND", "security_type": "보통주",
            "dividend_record_date": observed, "cash_payment_date": paid,
            "ordinary_dividend_amount": amount,
        })
    _write_parquet(
        root, "data/normalized/kr_equity_dividend/data.parquet",
        pd.DataFrame(dividend_rows),
    )

    dates = pd.date_range(end="2026-09-02", periods=40, freq="B")
    close = [400.0 + index for index in range(len(dates))]
    _write_parquet(
        root, "data/normalized/global_etf_price_daily/year=2026/detail.parquet",
        pd.DataFrame({
            "date": dates, "symbol": "QQQ", "open": [value - 1 for value in close],
            "high": [value + 2 for value in close], "low": [value - 2 for value in close],
            "close": close, "volume": 2_000_000,
        }),
    )
    append_target_price_vintages_atomic(rows_to_frame([{
        "date": "2026-09-02", "symbol": "QQQ", "market": "US ETF",
        "source": "YAHOO_FINANCE_QUOTE_SUMMARY", "target_mean": 500.0,
        "target_high": 550.0, "target_low": 450.0, "analyst_count": 12,
        "recommendation_mean": 1.8, "currency": "USD",
        "retrieved_at": datetime(2026, 9, 2, 1, tzinfo=timezone.utc),
        "terms_ref": "docs/data/sources/TARGET_PRICE_CONSENSUS.md#yahoo-finance-us",
    }]), root / "data/normalized/research_target_price_consensus")

    flow_dates = pd.date_range(end="2026-09-02", periods=25, freq="B")
    flow_units = pd.Series(range(1, 26), dtype="int64") * 100_000_000
    _write_parquet(
        root,
        "data/normalized/kr_equity_investor_flow_daily/symbol=005930/year=2026/data.parquet",
        pd.DataFrame({
            "date": flow_dates, "symbol": ["005930"] * 25,
            "foreign_net": flow_units,
            "institution_net": -(flow_units // 2),
            "individual_net": -(flow_units * 4 // 10),
            "other_corp_net": -(flow_units // 10),
            "total_net": [0] * 25, "source": ["fixture"] * 25,
            "captured_at": pd.to_datetime(["2026-09-04T00:00:00Z"] * 25),
        }),
    )
    return root


def test_korean_stock_detail_projects_stats_company_fundamentals_and_dividends() -> None:
    root = _make_detail_project()
    payload = ASGITestClient(create_app(root)).get(
        "/api/stock-detail", params={"symbol": "005930", "market": "KOSPI"},
    ).json()

    assert payload["identity"] == {
        "symbol": "005930", "name": "삼성전자", "market": "KOSPI",
        "security_type": "보통주", "isin": "KR7005930003", "currency": "KRW",
    }
    assert payload["headline"]["price_available"] is True
    assert payload["stats"]["rsi14"] == 100.0
    assert payload["stats"]["market_cap"] == payload["headline"]["price"] * 1_000_000_000
    assert payload["company"]["listing_date"] == "1975-06-11"
    assert payload["company"]["issued_shares"] == 1_000_000_000
    assert payload["company"]["par_value"] == 100.0
    assert payload["company"]["isin"] == "KR7005930003"

    fundamentals = payload["fundamentals"]
    assert fundamentals["available"] is True
    assert len(fundamentals["rows"]) == 6
    latest = fundamentals["rows"][0]
    assert latest["fs_div"] == "CFS"
    assert latest["rcept_no"] == "20260814000003"
    assert latest["operating_margin_pct"] == 2.1e12 / 17e12 * 100
    assert fundamentals["profitable_last_4q"] is True
    assert fundamentals["revenue_trend"] == "증가"

    dividends = payload["dividends"]
    assert dividends["available"] is True
    assert [row["ordinary_dividend_amount"] for row in dividends["rows"]] == [500, 400, 300, 200]
    assert dividends["trailing_4q_sum"] == 1400
    assert dividends["dividend_yield_pct"] == 1400 / payload["headline"]["price"] * 100
    assert dividends["next_event_label"] == "다음 기준일 (예상)"
    assert dividends["next_event_value"] == "2026-12-31"
    assert dividends["next_payment_label"] == "다음 기준일 (예상) 2026-12-31"
    assert payload["stats"]["dividend_yield_pct"] == dividends["dividend_yield_pct"]

    flows = payload["investor_flows"]
    assert flows["as_of"] == "2026-09-02"
    assert len(flows["rows"]) == 10
    assert flows["rows"][0] == {
        "date": "2026-09-02", "foreign_net": 2_500_000_000,
        "institution_net": -1_250_000_000, "individual_net": -1_000_000_000,
        "other_corp_net": -250_000_000,
    }
    assert len(flows["cumulative"]["dates"]) == 20
    assert flows["cumulative"]["foreign"][0] == 600_000_000
    assert flows["cumulative"]["foreign"][-1] == 31_000_000_000
    assert flows["summary_20d"] == {
        "foreign": 31_000_000_000,
        "institution": -15_500_000_000,
        "individual": -12_400_000_000,
    }


def test_latest_dividend_without_payment_date_is_labeled_pending() -> None:
    root = _make_detail_project()
    path = root / "data/normalized/kr_equity_dividend/data.parquet"
    frame = pd.read_parquet(path)
    frame.loc[frame["dividend_record_date"].eq("20260930"), "cash_payment_date"] = None
    frame.to_parquet(path, index=False)

    dividends = ASGITestClient(create_app(root)).get(
        "/api/stock-detail", params={"symbol": "005930", "market": "KOSPI"},
    ).json()["dividends"]

    assert dividends["next_event_label"] == "지급 예정"
    assert dividends["next_event_value"] == "기준일 2026-09-30, 지급일 미공시"
    assert dividends["next_payment_label"] == "지급 예정 (기준일 2026-09-30, 지급일 미공시)"


def test_fundamental_row_marks_profit_above_revenue_for_review() -> None:
    root = _make_detail_project()
    path = root / "data/normalized/kr_fundamentals_quarterly/data.parquet"
    frame = pd.read_parquet(path)
    latest = frame["rcept_no"].eq("20260814000003")
    frame.loc[latest, "net_income"] = frame.loc[latest, "revenue"] * 1.1
    frame.to_parquet(path, index=False)

    rows = ASGITestClient(create_app(root)).get(
        "/api/stock-detail", params={"symbol": "005930", "market": "KOSPI"},
    ).json()["fundamentals"]["rows"]

    assert rows[0]["sanity_check_required"] is True
    assert all(row["sanity_check_required"] is False for row in rows[1:])


def test_us_etf_keeps_unavailable_sections_typed_and_reads_target_price() -> None:
    root = _make_detail_project()
    payload = ASGITestClient(create_app(root)).get(
        "/api/stock-detail", params={"symbol": "QQQ", "market": "US ETF"},
    ).json()

    assert payload["identity"]["symbol"] == "QQQ"
    assert payload["identity"]["security_type"] == "ETF"
    assert payload["company"] == {
        "available": False, "message": "국내 종목 기업정보만 보존되어 있습니다.",
    }
    assert payload["fundamentals"]["message"] == "미국 ETF 재무 데이터 미보존"
    assert payload["dividends"]["message"] == "배당 데이터 미보존"
    assert payload["stats"]["market_cap"] is None
    assert payload["stats"]["dividend_yield_pct"] is None
    assert payload["target_price"]["target_mean"] == 500.0
    assert payload["target_price"]["analyst_count"] == 12
    assert payload["target_price"]["as_of"] == "2026-09-02"
    assert payload["target_price"]["upside_pct"] == (500 / 439 - 1) * 100
    assert payload["investor_flows"] == {"reason": "종목별 수급은 국내 주식만 보존"}


def test_missing_korean_symbol_flow_is_typed_unavailable() -> None:
    root = _make_detail_project()

    flows = ASGITestClient(create_app(root)).get(
        "/api/stock-detail", params={"symbol": "000660", "market": "KOSPI"},
    ).json()["investor_flows"]

    assert flows == {"reason": "종목별 수급 데이터 미보존"}


def test_korean_etf_flow_is_typed_unsupported() -> None:
    root = _make_detail_project()
    _write_parquet(
        root, "data/normalized/kr_etf_master/data.parquet",
        pd.DataFrame({"symbol": ["123320"], "name": ["테스트 ETF"]}),
    )

    flows = ASGITestClient(create_app(root)).get(
        "/api/stock-detail", params={"symbol": "123320", "market": "KRX"},
    ).json()["investor_flows"]

    assert flows == {"reason": "종목별 수급은 국내 주식만 보존"}


def test_sparkline_endpoint_returns_last_thirty_closes_in_time_order() -> None:
    root = _make_detail_project()
    payload = ASGITestClient(create_app(root)).get(
        "/api/stock-sparklines", params={"symbols": "005930,QQQ"},
    ).json()["sparklines"]

    assert list(payload) == ["005930", "QQQ"]
    assert len(payload["005930"]) == len(payload["QQQ"]) == 30
    assert payload["005930"] == sorted(payload["005930"])
    assert payload["QQQ"] == list(map(float, range(410, 440)))


def test_stocks_query_is_embedded_for_direct_detail_selection() -> None:
    root = _make_detail_project()
    response = ASGITestClient(create_app(root)).get("/stocks?symbol=005930")

    assert response.status_code == 200
    assert 'data-initial-symbol="005930"' in response.text


def test_relayed_clients_can_read_both_stock_detail_endpoints() -> None:
    root = _make_detail_project()
    client = ASGITestClient(create_app(root))
    headers = {"X-Forwarded-For": "100.64.0.24"}

    detail = client.get(
        "/api/stock-detail", params={"symbol": "005930", "market": "KOSPI"},
        client_host="127.0.0.1", headers=headers,
    )
    sparklines = client.get(
        "/api/stock-sparklines", params={"symbols": "005930"},
        client_host="127.0.0.1", headers=headers,
    )

    assert detail.status_code == sparklines.status_code == 200
    assert detail.json()["identity"]["symbol"] == "005930"
    assert len(sparklines.json()["sparklines"]["005930"]) == 30
