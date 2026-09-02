from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stock_data.gui.manual_account_store import (
    LocalManualAccountStore,
    ManualAccountPosition,
    ManualAccountRecord,
    ManualAccountRegistry,
)
from stock_data.gui.net_worth_service import LocalNetWorthHistoryStore
from stock_web.api import home_data
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
    assert "투자 자산" in client.get("/account").text
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
    assert account["day_change_pct"] is None
    assert "투자 자산만 기준" in account["footnote"]
    assert {source["name"] for source in account["sources"]} >= {
        "Toss", "KB", "미래에셋", "미국 수동", "부동산", "예금·현금", "주택담보대출",
    }
