from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from stock_data.gui.manual_account_store import (
    LocalManualAccountStore,
    ManualAccountPosition,
    ManualAccountRecord,
    ManualAccountRegistry,
)
from stock_data.gui.net_worth_service import LocalNetWorthHistoryStore
from stock_data.providers.tossinvest import (
    attach_buying_power,
    normalize_buying_power_payload,
    normalize_holdings_payload,
)
from stock_web.api import account_page, home_data
from stock_web.api.account_page import calculate_return_metrics
from stock_web.app import create_app
from tests.unit.web import ASGITestClient, new_temp_root


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _local_mock_snapshot() -> dict[str, object]:
    return {
        "schema_version": 2,
        "state": "LOCAL_MOCK",
        "as_of": "2026-09-02T07:00:00+09:00",
        "last_reconciled_at": "2026-09-02T07:01:00+09:00",
        "currency": "KRW",
        "total_assets": 10_000,
        "securities_value": 8_000,
        "cash_balance": 2_000,
        "available_cash": None,
        "realized_pnl": None,
        "unrealized_pnl": 500,
        "positions": [{
            "symbol": "SAFE", "name": "테스트 보유", "quantity": 1,
            "market_value": 8_000, "realized_pnl": None,
            "unrealized_pnl": 500,
        }],
        "asset_history": [],
    }


def _toss_buying_power_snapshot() -> dict[str, object]:
    holdings = normalize_holdings_payload(
        {"result": {
            "totalPurchaseAmount": {"krw": "0"},
            "marketValue": {
                "amount": {"krw": "0"},
                "amountAfterCost": {"krw": "0"},
            },
            "profitLoss": {
                "amount": {"krw": "0"},
                "amountAfterCost": {"krw": "0"},
                "rate": "0", "rateAfterCost": "0",
            },
            "dailyProfitLoss": {"amount": {"krw": "0"}, "rate": "0"},
            "items": [],
        }},
        collected_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    buying_power = [
        normalize_buying_power_payload(
            {"result": {"currency": "KRW", "cashBuyingPower": "24680"}},
            expected_currency="KRW",
        ),
        normalize_buying_power_payload(
            {"result": {"currency": "USD", "cashBuyingPower": "12.5"}},
            expected_currency="USD",
        ),
    ]
    return attach_buying_power(holdings, buying_power)


def _manual_post_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "accounts": [{
            "source_id": "manual:mirae",
            "label": "미래에셋",
            "account_kind": "GENERAL",
            "snapshot_date": "2026-08-30",
            "currency": "KRW",
            "cash": 100,
            "positions": [{
                "ticker": "005930", "name": "삼성전자", "quantity": 2,
                "average_cost": 800, "manual_price": None,
            }, {
                "ticker": "000660", "name": "가격없는종목", "quantity": 1,
                "average_cost": 100, "manual_price": None,
            }],
        }, {
            "source_id": "manual:us_test",
            "label": "미국 수동",
            "account_kind": "GENERAL",
            "snapshot_date": "2026-08-31",
            "currency": "USD",
            "cash": 10,
            "positions": [{
                "ticker": "AAPL", "name": "Apple", "quantity": 2,
                "average_cost": 10, "manual_price": 12,
            }],
        }],
    }


def _net_worth_post_payload() -> dict[str, object]:
    common = {
        "holder_role": "SELF", "valuation_status": "CURRENT",
    }
    return {
        "as_of_date": "2026-08-15",
        "assets": [{
            **common, "name": "거주 부동산", "asset_class": "REAL_ESTATE",
            "amount_krw": 100_000, "valuation_date": "2026-08-15",
            "valuation_method": "USER_DECLARED", "valuation_source": "USER_LOCAL",
            "uncertainty": "MEDIUM",
        }, {
            **common, "name": "예금", "asset_class": "CASH",
            "amount_krw": 5_000, "valuation_date": "2026-08-15",
            "valuation_method": "STATEMENT_VALUE",
            "valuation_source": "OFFICIAL_STATEMENT", "uncertainty": "EXACT",
        }],
        "liabilities": [{
            **common, "name": "주택 대출", "liability_class": "MORTGAGE",
            "amount_krw": 20_000, "valuation_date": "2026-08-15",
            "valuation_method": "STATEMENT_VALUE",
            "valuation_source": "OFFICIAL_STATEMENT", "uncertainty": "EXACT",
        }],
    }


def _write_searchable_account_etfs(root: Path) -> None:
    _write_parquet(
        root,
        "data/normalized/kr_etf_universe_daily/source_date=2026-09-04/data.parquet",
        pd.DataFrame({
            "source_date": ["2026-09-04"] * 4,
            "symbol": ["0015B0", "100001", "100002", "100003"],
            "name": [
                "KoAct 미국나스닥성장기업액티브",
                "미국 성장 1호", "미국 성장 2호", "미국 성장 3호",
            ],
            "full_name": [None] * 4,
            "isin": [None] * 4,
            "listing_date": ["2025-02-25", "2024-01-01", "2024-01-02", "2024-01-03"],
            "underlying_index": [None] * 4,
            "market": ["KRX"] * 4,
            "security_type": ["ETF"] * 4,
            "listing_status": ["LISTED_AT_SOURCE_DATE"] * 4,
        }),
    )


def _account_project() -> Path:
    root = new_temp_root()
    _write_parquet(
        root,
        "data/normalized/kr_equity_master/market=KOSPI/data.parquet",
        pd.DataFrame({
            "symbol": ["005930"], "name": ["삼성전자"], "market": ["KOSPI"],
            "isin": ["KR7005930003"], "listing_date": ["1975-06-11"],
            "delisting_date": [None], "security_type_name": ["보통주"],
        }),
    )
    _write_parquet(
        root,
        "data/normalized/kr_equity_master/market=KOSDAQ/data.parquet",
        pd.DataFrame({
            "symbol": ["035720"], "name": ["카카오"], "market": ["KOSDAQ"],
            "isin": ["KR7035720002"], "listing_date": ["1999-11-11"],
            "delisting_date": [None], "security_type_name": ["보통주"],
        }),
    )
    _write_parquet(
        root,
        "data/normalized/kr_equity_price_daily/market=KOSPI/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-09-01")],
            "symbol": ["005930"], "close": [1_000],
        }),
    )
    _write_parquet(
        root,
        "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({"date": [pd.Timestamp("2026-09-01")], "dexkous": [1_300.0]}),
    )
    _write_parquet(
        root,
        "data/normalized/bok_ecos_usd_krw_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-09-03")],
            "rate_krw_per_usd": [1_400.0],
        }),
    )
    snapshot = root / "data/normalized/toss_account_snapshot/latest.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(_local_mock_snapshot(), ensure_ascii=False), encoding="utf-8")

    # Establish the legacy/shared store before the web extension is posted.
    manual_path = root / "artifacts/local_user/manual_accounts.json"
    LocalManualAccountStore(manual_path).save(ManualAccountRegistry((
        ManualAccountRecord(
            "manual:mirae", "미래에셋", "GENERAL", "2026-08-30", "KRW",
            (ManualAccountPosition("삼성전자", "005930", 2, 800, 1_600),),
        ),
    )))
    return root


def test_account_totals_use_local_prices_fx_and_exclude_unpriced_holdings() -> None:
    root = _account_project()
    client = ASGITestClient(create_app(root))

    manual_post = client.post(
        "/api/manual/accounts", json=_manual_post_payload(), client_host="127.0.0.1",
    )
    net_worth_post = client.post(
        "/api/net-worth", json=_net_worth_post_payload(), client_host="::1",
    )
    response = client.get("/api/account")

    assert manual_post.status_code == 200
    assert net_worth_post.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    # Toss 10,000 + KRW manual (100 cash + 2*1,000) + USD (10 cash + 2*12)*1,400.
    assert payload["summary"]["invest_total_krw"] == 59_700
    # Other assets 105,000 - liabilities 20,000 are added to investment assets.
    assert payload["summary"]["net_worth_krw"] == 144_700
    assert payload["summary"]["fx_krw_per_usd"] == 1_400
    assert payload["summary"]["fx_as_of"] == "2026-09-03"
    assert payload["summary"]["fx_as_of_label"] == "09-03"
    assert payload["summary"]["fx_source"] == "BOK 매매기준율 09-03"
    assert payload["summary"]["broker_reported_pnl_krw"] == 500
    assert set(payload["return_metrics"]) == {"1M", "3M", "YTD", "ALL"}
    assert payload["cash_flows"]["entries"] == []
    assert payload["total_asset_history"]
    assert next(row for row in payload["rows"] if row["name"] == "Toss")["as_of_label"] == "09-02 07:00"
    assert payload["manual_accounts"]["unpriced_count"] == 1
    unpriced = next(
        position
        for account in payload["manual_accounts"]["accounts"]
        for position in account["valued_positions"]
        if position["ticker"] == "000660"
    )
    assert unpriced["included"] is False
    assert unpriced["market_value_krw"] is None
    assert "평가 불가" in unpriced["note"]

    # The compatible KRW account is still written and validated by the shared store.
    stored = LocalManualAccountStore(
        root / "artifacts/local_user/manual_accounts.json"
    ).load()
    assert [account.source_id for account in stored.accounts] == ["manual:mirae"]
    assert len(LocalNetWorthHistoryStore(root / "data/local/net_worth_history").load_history()) == 1


def test_toss_buying_power_is_not_rendered_as_cash_balance() -> None:
    root = _account_project()
    snapshot_path = root / "data/normalized/toss_account_snapshot/latest.json"
    snapshot_path.write_text(
        json.dumps(_toss_buying_power_snapshot(), ensure_ascii=False), encoding="utf-8",
    )
    client = ASGITestClient(create_app(root))

    response = client.get("/api/account")

    assert response.status_code == 200
    payload = response.json()
    toss = next(row for row in payload["rows"] if row["source_id"] == "toss_self")
    assert toss["included"] is True
    assert toss["value_krw"] is not None
    assert toss["cash_krw"] is None
    assert toss["cash_note"] == "현금 미확인"
    assert payload["summary"]["cash_krw"] is None
    assert payload["summary"]["cash_complete"] is False
    assert payload["summary"]["cash_note"] == "현금 미확인"
    account_javascript = client.get("/static/account.js").text
    assert '${money(row.cash_krw)}' in account_javascript
    assert 'value === null || value === undefined ? "—"' in account_javascript
    assert 'title="${esc(cashTitle)}"' in account_javascript


def test_fx_history_prefers_bok_and_uses_fred_only_for_missing_dates() -> None:
    root = new_temp_root()
    _write_parquet(
        root,
        "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": pd.to_datetime(["2026-08-28", "2026-09-01", "2026-09-02"]),
            "dexkous": [1_300.0, 1_310.0, 1_320.0],
        }),
    )
    _write_parquet(
        root,
        "data/normalized/bok_ecos_usd_krw_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": pd.to_datetime(["2026-09-01", "2026-09-03"]),
            "rate_krw_per_usd": [1_410.0, 1_430.0],
        }),
    )

    history = account_page._fx_history(root)

    assert [(row["date"], row["source_label"]) for row in history] == [
        ("2026-08-28", "FRED"),
        ("2026-09-01", "BOK 매매기준율"),
        ("2026-09-02", "FRED"),
        ("2026-09-03", "BOK 매매기준율"),
    ]
    assert history[1]["value"] == 1_410.0
    assert account_page._latest_fx(root) == (
        1_430.0, "2026-09-03", "BOK 매매기준율 09-03",
    )


def test_latest_fx_falls_back_to_newer_fred_date_when_bok_lacks_it() -> None:
    root = new_temp_root()
    _write_parquet(
        root,
        "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({"date": [pd.Timestamp("2026-09-02")], "dexkous": [1_320.0]}),
    )
    _write_parquet(
        root,
        "data/normalized/bok_ecos_usd_krw_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-09-01")],
            "rate_krw_per_usd": [1_410.0],
        }),
    )

    assert account_page._latest_fx(root) == (1_320.0, "2026-09-02", "FRED 09-02")


def test_account_posts_are_loopback_only_and_pages_and_get_apis_render() -> None:
    root = _account_project()
    client = ASGITestClient(create_app(root))
    original = (root / "artifacts/local_user/manual_accounts.json").read_bytes()

    refused_manual = client.post("/api/manual/accounts", json=_manual_post_payload())
    refused_net_worth = client.post("/api/net-worth", json=_net_worth_post_payload())

    assert refused_manual.status_code == 403
    assert refused_net_worth.status_code == 403
    assert (root / "artifacts/local_user/manual_accounts.json").read_bytes() == original
    assert not (root / "data/local/net_worth_history").exists()
    assert client.get("/account").status_code == 200
    account_page_html = client.get("/account").text
    assert "투자 자산" in account_page_html
    assert "입출금 기록" in account_page_html
    assert "lightweight-charts" not in account_page_html
    assert '/static/app.js' in account_page_html
    assert '/static/account.css' in account_page_html
    assert "계좌 관측 또는 환율이 3거래일 넘게 오래된 날" in account_page_html
    assert 'class="account-wide-grid"' in account_page_html
    assert 'class="return-metrics dense"' in account_page_html
    assert 'id="account-toast"' in account_page_html
    assert 'role="status"' in account_page_html
    for status_id in ("manual-status", "net-worth-status", "cash-flow-status", "journal-status"):
        assert f'id="{status_id}"' in account_page_html
    assert 'class="card account-input-panel"' in account_page_html
    assert "최근 저장 시도" in account_page_html
    assert account_page_html.index("진짜 투자 수익") < account_page_html.index("계좌별")
    assert account_page_html.index("계좌별") < account_page_html.index("순자산 타임라인")
    assert account_page_html.index("최근 자산·부채 구성") < account_page_html.index("입력 (수동 계좌")
    assert account_page_html.index("입력 (수동 계좌") < account_page_html.index("최근 저장 시도")
    app_css = client.get("/static/app.css").text
    assert ".page {" in app_css
    assert "max-width: 1760px" in app_css
    assert "margin: 0 auto" in app_css
    account_css = client.get("/static/account.css").text
    account_javascript = client.get("/static/account.js").text
    assert ".card-head b" in account_css
    assert "grid-template-columns: repeat(6, minmax(0, 1fr))" in account_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in account_css
    assert "white-space: nowrap" in account_css
    assert ".source-mobile-meta" in account_css
    assert "manual-name-search" in account_javascript
    assert "manualSearchSequence" in account_javascript
    assert "events.slice(0, journalVisibleRows)" in account_javascript
    assert "window.setTimeout(() => { toast.hidden = true; }, 8000)" in account_javascript
    assert client.get("/api/manual/accounts").status_code == 200
    assert client.get("/api/net-worth").status_code == 200


def test_manual_accounts_resolve_unique_names_and_hint_ambiguous_or_missing_names() -> None:
    root = _account_project()
    _write_searchable_account_etfs(root)
    client = ASGITestClient(create_app(root))
    base = _manual_post_payload()
    base["accounts"] = [{
        **base["accounts"][0],
        "positions": [{
            "ticker": "", "name": "KoAct 미국나스닥성장기업액티브",
            "quantity": 2, "average_cost": 10_000, "manual_price": None,
        }],
    }]

    resolved = client.post(
        "/api/manual/accounts", json=base, client_host="127.0.0.1",
    )
    assert resolved.status_code == 200
    saved = resolved.json()["accounts"][0]
    assert saved["positions"][0]["ticker"] == "0015B0"
    assert saved["positions"][0]["name"] == "KoAct 미국나스닥성장기업액티브"
    assert saved["currency"] == "KRW"

    kr_stock_payload = {
        **base,
        "accounts": [{
            **base["accounts"][0],
            "positions": [{
                "ticker": "", "name": "삼성전자", "quantity": 1,
                "average_cost": 70_000, "manual_price": None,
            }],
        }],
    }
    response = client.post(
        "/api/manual/accounts", json=kr_stock_payload, client_host="127.0.0.1",
    )
    assert response.status_code == 200
    assert response.json()["accounts"][0]["positions"][0]["ticker"] == "005930"

    us_payload = {
        **base,
        "accounts": [{
            **base["accounts"][0],
            "currency": "KRW",
            "positions": [{
                "ticker": "", "name": "SPDR S&P 500 ETF Trust",
                "quantity": 1, "average_cost": 500, "manual_price": 510,
            }],
        }],
    }
    response = client.post(
        "/api/manual/accounts", json=us_payload, client_host="127.0.0.1",
    )
    assert response.status_code == 200
    assert response.json()["accounts"][0]["positions"][0]["ticker"] == "SPY"
    assert response.json()["accounts"][0]["currency"] == "USD"

    ambiguous = {
        **base,
        "accounts": [{
            **base["accounts"][0],
            "positions": [{
                **base["accounts"][0]["positions"][0], "name": "미국 성장",
            }],
        }],
    }
    response = client.post(
        "/api/manual/accounts", json=ambiguous, client_host="127.0.0.1",
    )
    assert response.status_code == 400
    assert response.json()["error"] == (
        "종목코드를 고르세요: 100001 미국 성장 1호 · 100002 미국 성장 2호 · 100003 미국 성장 3호"
    )

    missing = {
        **base,
        "accounts": [{
            **base["accounts"][0],
            "positions": [{
                **base["accounts"][0]["positions"][0], "name": "존재하지 않는 종목",
            }],
        }],
    }
    response = client.post(
        "/api/manual/accounts", json=missing, client_host="127.0.0.1",
    )
    assert response.status_code == 400
    assert response.json()["error"] == (
        "종목명을 찾을 수 없습니다. 종목코드를 직접 입력하거나 검색 결과에서 고르세요."
    )


def test_write_audit_records_200_400_403_without_private_payload_content() -> None:
    root = _account_project()
    client = ASGITestClient(create_app(root))
    valid = _net_worth_post_payload()
    valid["assets"][0]["name"] = "감사비밀자산"
    valid["assets"][0]["amount_krw"] = 987_654_321

    assert client.post(
        "/api/net-worth", json=valid, client_host="127.0.0.1",
    ).status_code == 200
    assert client.post(
        "/api/net-worth", json={"as_of_date": "bad", "assets": [], "liabilities": []},
        client_host="::1",
    ).status_code == 400
    assert client.post(
        "/api/net-worth", json={"account": "123-456-789012", "amount": 11223344},
        client_host="127.0.0.1", headers={"x-forwarded-for": "100.64.0.8"},
    ).status_code == 403

    audit_path = root / "artifacts/local_user/web_write_audit.jsonl"
    raw = audit_path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw.splitlines()]
    assert [line["status"] for line in lines] == [200, 400, 403]
    assert [line["client_kind"] for line in lines] == ["loopback", "loopback", "relayed"]
    assert set(lines[0]) == {"ts", "path", "client_kind", "status", "error_code", "row_counts"}
    assert lines[0]["row_counts"] == {"assets": 2, "liabilities": 1}
    for private_text in ("감사비밀자산", "987654321", "123-456-789012", "11223344"):
        assert private_text not in raw
    recent = client.get("/api/account").json()["recent_write_attempts"]
    assert [line["status"] for line in recent] == [403, 400, 200]


def test_home_account_keeps_total_alias_and_adds_split_fields() -> None:
    root = _account_project()
    client = ASGITestClient(create_app(root))
    assert client.post(
        "/api/manual/accounts", json=_manual_post_payload(), client_host="127.0.0.1",
    ).status_code == 200
    assert client.post(
        "/api/net-worth", json=_net_worth_post_payload(), client_host="127.0.0.1",
    ).status_code == 200
    home_data._HOME_CACHE.clear()

    account = home_data.build_account(root)

    assert account["total_krw"] == account["invest_total_krw"] == 59_700
    assert account["net_worth_krw"] == 144_700
    assert account["net_worth_as_of"] == "2026-08-15"
    assert account["net_worth_as_of_label"] == "08-15"
    assert account["day_change_pct"] is None
    assert account["footnote"] == "입출금은 내 계좌 페이지에서 기록 · 기록이 없으면 변동 전체를 손익으로 간주"
    assert {source["name"] for source in account["sources"]} >= {
        "Toss", "KB", "미래에셋", "미국 수동", "부동산", "예금·현금", "주택담보대출",
    }


def test_modified_dietz_and_twr_match_hand_calculated_cash_flow_example() -> None:
    history = [
        {"t": "2026-01-01", "v": 100.0, "partial": False},
        {"t": "2026-01-06", "v": 150.0, "partial": False},
        {"t": "2026-01-11", "v": 165.0, "partial": False},
    ]
    flows = [{
        "id": "flow_mid", "date": "2026-01-06", "amount_krw": 50,
        "account": "현금", "memo": "중간 입금",
    }]

    metric = calculate_return_metrics(
        history, flows, broker_reported_pnl_krw=-10.0,
    )["ALL"]

    assert metric["net_flows_krw"] == 50
    assert metric["true_pnl_krw"] == 15
    # Dietz: 15 / (100 + 0.5*50) = 12%; TWR: 1.0 * 165/150 - 1 = 10%.
    assert metric["return_pct_modified_dietz"] == 12.0
    assert metric["return_pct_twr"] == pytest.approx(10.0)
    assert metric["broker_reported_pnl_krw"] == -10.0


def test_return_window_without_a_pre_start_observation_has_korean_reason() -> None:
    metrics = calculate_return_metrics(
        [
            {"t": "2026-08-26", "v": 100.0, "partial": False},
            {"t": "2026-09-03", "v": 110.0, "partial": False},
        ],
        [],
        broker_reported_pnl_krw=None,
    )

    assert metrics["1M"] == {
        "window": "1M", "reason": "관측 시작 08-26 이후만 계산 가능",
    }
    assert metrics["ALL"]["true_pnl_krw"] == 10.0


def test_total_asset_series_marks_accounts_stale_after_three_sessions() -> None:
    combined = account_page._combine_total_asset_series(
        [{
            "source_id": "toss_self:KRW", "currency": "KRW",
            "points": [{"date": "2026-09-01", "value": 100}, {"date": "2026-09-08", "value": 110}],
        }, {
            "source_id": "manual:mirae", "currency": "KRW",
            "points": [{"date": "2026-09-02", "value": 50}],
        }],
        [],
    )

    assert combined[0] == {
        "t": "2026-09-01", "v": 100.0, "total_invest_krw": 100.0,
        "partial": True, "observed": True,
    }
    assert next(point for point in combined if point["t"] == "2026-09-04")["partial"] is False
    assert combined[-1]["v"] == 160.0
    assert combined[-1]["partial"] is True


def test_total_asset_series_marks_fx_stale_only_after_three_sessions() -> None:
    account_points = [
        {"date": day, "value": 10.0}
        for day in (
            "2026-08-28", "2026-08-31", "2026-09-01", "2026-09-02",
            "2026-09-03", "2026-09-04",
        )
    ]
    combined = account_page._combine_total_asset_series(
        [{"source_id": "usd", "currency": "USD", "points": account_points}],
        [{"date": "2026-08-28", "value": 1_300.0}],
    )

    assert next(point for point in combined if point["t"] == "2026-09-02")["partial"] is False
    assert next(point for point in combined if point["t"] == "2026-09-03")["partial"] is True


def test_account_payload_selects_all_for_short_history_and_labels_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    monkeypatch.setattr(account_page, "build_api_account_data", lambda _root: {
        "rows": [], "total_krw": 0.0, "cash_krw": 0.0, "cash_complete": True,
        "broker_reported_pnl_krw": None,
    })
    monkeypatch.setattr(account_page, "build_manual_account_data", lambda _root: {
        "rows": [], "accounts": [], "total_krw": 0.0, "cash_krw": 0.0,
        "unpriced_count": 0,
    })
    monkeypatch.setattr(account_page, "build_net_worth_data", lambda _root: {
        "rows": [], "exists": False, "timeline": [], "complete": False,
    })
    monkeypatch.setattr(account_page, "build_cash_flow_data", lambda _root: {
        "schema_version": 1, "entries": [], "monthly_subtotals": [],
    })
    monkeypatch.setattr(account_page, "_total_asset_components", lambda _root, _manual: [{
        "source_id": "synthetic", "currency": "KRW",
        "points": [
            {"date": "2026-08-26", "value": 100.0},
            {"date": "2026-09-03", "value": 110.0},
        ],
    }])
    monkeypatch.setattr(account_page, "_fx_history", lambda _root: [])
    monkeypatch.setattr(account_page, "_kospi_benchmark", lambda _root, _history: [])

    payload = account_page.build_account_page_data(root)

    assert payload["return_period"] == {
        "default_window": "ALL", "all_label": "전체 (08-26~)",
    }
    assert payload["chart_labels"] == {
        "primary": "총자산", "benchmark": "KOSPI (시작값 맞춤)",
    }


def test_twr_defers_a_flow_until_the_next_genuine_valuation() -> None:
    history = [
        {"t": "2026-01-01", "v": 100.0, "partial": False, "observed": True},
        {"t": "2026-01-02", "v": 100.0, "partial": False, "observed": False},
        {"t": "2026-01-03", "v": 100.0, "partial": False, "observed": False},
        {"t": "2026-01-04", "v": 100.0, "partial": False, "observed": False},
        {"t": "2026-01-05", "v": 165.0, "partial": False, "observed": True},
    ]
    flows = [{
        "id": "flow_mid", "date": "2026-01-03", "amount_krw": 50,
        "account": "현금", "memo": "중간 입금",
    }]

    metric = calculate_return_metrics(
        history, flows, broker_reported_pnl_krw=None,
    )["ALL"]

    assert metric["true_pnl_krw"] == 15.0
    assert metric["return_pct_twr"] == pytest.approx(10.0)
    assert account_page._daily_true_change(history, flows) is None


def test_cash_flow_crud_is_atomic_newest_first_and_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _account_project()
    client = ASGITestClient(create_app(root))
    flow_path = root / "artifacts/local_user/cash_flows.json"
    real_replace = account_page.os.replace
    replacements: list[tuple[Path, Path]] = []

    def observed_replace(source: object, target: object) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(account_page.os, "replace", observed_replace)
    entry = {
        "date": "2026-09-01", "amount_krw": 50_000_000,
        "account": "Toss", "memo": "투자금 입금",
    }
    assert client.post("/api/cash-flows", json=entry).status_code == 403
    assert not flow_path.exists()

    created = client.post(
        "/api/cash-flows", json=entry, client_host="127.0.0.1",
    )
    assert created.status_code == 200
    flow_id = created.json()["entries"][0]["id"]
    assert replacements[-1][1] == flow_path
    assert replacements[-1][0].parent == flow_path.parent
    assert not list(flow_path.parent.glob("*.tmp"))

    edited = client.post(
        "/api/cash-flows",
        json={**entry, "id": flow_id, "amount_krw": -10_000_000, "date": "2026-09-02"},
        client_host="::1",
    )
    assert edited.status_code == 200
    assert edited.json()["entries"] == [{
        **entry, "id": flow_id, "amount_krw": -10_000_000, "date": "2026-09-02",
    }]
    assert edited.json()["monthly_subtotals"] == [{"month": "2026-09", "amount_krw": -10_000_000}]

    second = client.post(
        "/api/cash-flows",
        json={"date": "2026-10-01", "amount_krw": 2_000_000, "account": "KB", "memo": "추가 입금"},
        client_host="127.0.0.1",
    )
    assert second.status_code == 200
    second_id = second.json()["entries"][0]["id"]
    assert [row["date"] for row in second.json()["entries"]] == ["2026-10-01", "2026-09-02"]

    assert client.delete("/api/cash-flows", json={"id": flow_id}).status_code == 403
    deleted = client.delete(
        "/api/cash-flows", json={"id": flow_id}, client_host="127.0.0.1",
    )
    assert deleted.status_code == 200
    assert [row["id"] for row in deleted.json()["entries"]] == [second_id]
    assert client.delete(
        "/api/cash-flows", json={"id": second_id}, client_host="127.0.0.1",
    ).status_code == 200
    assert json.loads(flow_path.read_text(encoding="utf-8")) == {
        "schema_version": 1, "entries": [],
    }


def test_trade_journal_name_search_and_side_specific_price_labels_are_rendered() -> None:
    root = new_temp_root()
    html = ASGITestClient(create_app(root)).get("/account").text
    script = (
        Path(__file__).parents[3] / "src/stock_web/static/account.js"
    ).read_text(encoding="utf-8")

    assert 'id="journal-search-results"' in html
    assert 'role="combobox"' in html
    assert "const JOURNAL_PRICE_LABELS" in script
    assert 'label: "매수 단가 (원/주)"' in script
    assert 'label: "매도 단가 (원/주)"' in script
    assert 'label: "주당 배당금 (세전)"' in script
    assert 'label: "단가 (선택)"' in script
    assert "journalSearchSequence" in script
    assert "sequence !== journalSearchSequence" in script
    assert "}, 350);" in script
    assert "/api/stocks/search?q=" in script
