from __future__ import annotations

import json
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


def _account_project() -> Path:
    root = new_temp_root()
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
    # Toss 10,000 + KRW manual (100 cash + 2*1,000) + USD (10 cash + 2*12)*1,300.
    assert payload["summary"]["invest_total_krw"] == 56_300
    # Other assets 105,000 - liabilities 20,000 are added to investment assets.
    assert payload["summary"]["net_worth_krw"] == 141_300
    assert payload["summary"]["fx_krw_per_usd"] == 1_300
    assert payload["summary"]["fx_as_of"] == "2026-09-01"
    assert payload["summary"]["fx_as_of_label"] == "09-01"
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
    assert client.get("/api/manual/accounts").status_code == 200
    assert client.get("/api/net-worth").status_code == 200


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

    assert account["total_krw"] == account["invest_total_krw"] == 56_300
    assert account["net_worth_krw"] == 141_300
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


def test_total_asset_series_forward_fills_and_marks_sources_stale_after_three_days() -> None:
    combined = account_page._combine_total_asset_series(
        [{
            "source_id": "toss_self:KRW", "currency": "KRW",
            "points": [{"date": "2026-09-01", "value": 100}, {"date": "2026-09-06", "value": 110}],
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
