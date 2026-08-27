import json

import pytest

from stock_data.gui.manual_account_store import (
    LocalManualAccountStore,
    ManualAccountPosition,
    ManualAccountRecord,
    ManualAccountRegistry,
    manual_account_registry_payload,
    parse_manual_account_registry,
)
from stock_data.gui.account_snapshot_service import (
    AccountSnapshotState,
    LocalAccountPortfolioService,
    manual_account_registry_to_portfolio,
)


def _registry() -> ManualAccountRegistry:
    return ManualAccountRegistry((
        ManualAccountRecord(
            "manual:mirae_pension", "미래에셋 연금", "PENSION", "2026-08-26", "KRW",
            (ManualAccountPosition("Fixture ETF", "111111", 2.0, 100.0, 200.0),),
        ),
        ManualAccountRecord(
            "manual:mirae_isa", "미래에셋 ISA", "ISA", "2026-08-26", "KRW",
            (ManualAccountPosition("Fixture Fund", "222222", 1.0, None, None),),
        ),
    ))


def test_manual_account_store_round_trips_multiple_api_less_accounts_atomically(tmp_path):
    store = LocalManualAccountStore(tmp_path / "manual_accounts.json")
    store.save(_registry())
    assert store.load() == _registry()
    assert not list(tmp_path.glob("*.tmp"))


def test_manual_account_registry_rejects_private_or_unreconciled_values():
    payload = manual_account_registry_payload(_registry())
    payload["accounts"][0]["label"] = "계좌 1234-5678-9012"
    with pytest.raises(ValueError, match="private-shaped"):
        parse_manual_account_registry(payload)

    payload = manual_account_registry_payload(_registry())
    payload["accounts"][0]["positions"][0]["purchase_total"] = 201.0
    with pytest.raises(ValueError, match="reconcile"):
        parse_manual_account_registry(payload)

    payload = manual_account_registry_payload(_registry())
    payload["accounts"][0]["positions"][0]["name"] = "ETF 1234-5678-9012"
    with pytest.raises(ValueError, match="private-shaped"):
        parse_manual_account_registry(payload)


def test_invalid_existing_registry_fails_closed_without_rewrite(tmp_path):
    path = tmp_path / "manual_accounts.json"
    path.write_text(json.dumps({"schema_version": 1, "accounts": "private"}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(TypeError):
        LocalManualAccountStore(path).load()
    assert path.read_bytes() == before


def test_manual_registry_projects_purchase_basis_without_inventing_market_value():
    portfolio = manual_account_registry_to_portfolio(_registry())

    assert [entry.source_id for entry in portfolio.entries] == [
        "manual:mirae_pension", "manual:mirae_isa",
    ]
    assert portfolio.user_fund_totals == ()
    pension = portfolio.entries[0].snapshot
    assert pension.state is AccountSnapshotState.MANUAL_HOLDINGS_BASIS
    assert pension.total_assets is None
    assert pension.positions[0].purchase_amount == 200.0
    assert pension.positions[0].market_value is None
    assert not pension.include_in_user_fund_total


def test_portfolio_keeps_other_sources_available_when_manual_store_is_invalid(tmp_path):
    path = tmp_path / "manual.json"
    path.write_text('{"schema_version":1,"accounts":"invalid"}', encoding="utf-8")

    portfolio = LocalAccountPortfolioService(
        (), manual_store=LocalManualAccountStore(path),
    ).load()

    assert len(portfolio.entries) == 1
    assert portfolio.entries[0].source_id == "manual_registry_invalid"
    assert portfolio.entries[0].snapshot.reason == "MANUAL_ACCOUNT_REGISTRY_INVALID"
