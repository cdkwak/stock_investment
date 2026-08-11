from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.legacy_market_investor import KR_MARKET_INVESTOR_NET_PURCHASE_DAILY
from stock_data.contracts.tossinvest_historical import KR_MARKET_INVESTOR_TRADING_DAILY
from stock_data.published import investor_bridge
from stock_data.published.investor_bridge import build_investor_bridge
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.investor_bridge import validate_investor_bridge
from stock_data.validation.legacy_market_investor import validate_legacy_market_investor_net_purchase
from stock_data.validation.tossinvest_historical import validate_toss_historical


def _legacy() -> pd.DataFrame:
    return pd.DataFrame([[
        "2014-06-30", "KOSPI", -10, 0, 3, 7, 0,
        "legacy_stock_investment_pykrx_1.2.8", "MDCSTAT02202", "legacy_pre_a001_only",
    ]], columns=KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names)


def _toss() -> pd.DataFrame:
    row = {column: 0 for column in KR_MARKET_INVESTOR_TRADING_DAILY.column_names}
    row.update({
        "date": "2014-07-01", "market": "KOSPI",
        "individual_buy_amount": 30, "individual_sell_amount": 20,
        "foreigner_buy_amount": 10, "foreigner_sell_amount": 20,
        "institution_buy_amount": 5, "institution_sell_amount": 5,
        "other_corporation_buy_amount": 0, "other_corporation_sell_amount": 0,
        "institution_financial_investment_buy_amount": 5,
        "institution_financial_investment_sell_amount": 5,
        "source": "tossinvest_open_api", "source_operation": "getMarketIndicatorInvestorTrading",
        "source_date": "2014-07-01",
        "collected_at": pd.Timestamp("2026-08-11T00:00:00Z"),
        "updated_at": pd.Timestamp("2014-07-01T06:00:00Z"),
        "availability_date": "2014-07-01",
    })
    return pd.DataFrame([row], columns=KR_MARKET_INVESTOR_TRADING_DAILY.column_names)


def test_build_bridge_preserves_provider_units_and_boundary(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(investor_bridge, "EXPECTED_LEGACY_ROWS", 1)
    monkeypatch.setattr(investor_bridge, "EXPECTED_TOSS_ROWS", 1)
    legacy_root = tmp_path / "data/normalized/kr_market_investor_net_purchase_daily"
    toss_root = tmp_path / "data/normalized/kr_market_investor_trading_daily"
    write_dataset_atomic(_legacy(), legacy_root, KR_MARKET_INVESTOR_NET_PURCHASE_DAILY, validate_legacy_market_investor_net_purchase)
    write_dataset_atomic(
        _toss(), toss_root, KR_MARKET_INVESTOR_TRADING_DAILY,
        lambda frame: validate_toss_historical(frame, KR_MARKET_INVESTOR_TRADING_DAILY),
    )

    state = build_investor_bridge(project_root=tmp_path)

    assert state["rows"] == 2
    assert state["api_calls"] == 0
    root = tmp_path / "data/published/kr_market_investor_net_purchase_bridge_daily"
    restored = read_dataset(root, investor_bridge.KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY, validate_investor_bridge)
    assert restored["value_unit"].tolist() == ["unit_unknown", "KRW"]
    assert restored["availability_date"].tolist()[0] is None
    assert restored["total_net_purchase"].tolist() == [0, 0]
    assert restored.loc[1, [
        "institution_net_purchase", "other_corporation_net_purchase",
        "individual_net_purchase", "foreign_net_purchase",
    ]].tolist() == [0, 0, 10, -10]


def test_bridge_validator_rejects_invented_legacy_unit():
    frame = pd.DataFrame([{
        "date": "2014-06-30", "market": "KOSPI",
        "institution_net_purchase": -10, "other_corporation_net_purchase": 0,
        "individual_net_purchase": 3, "foreign_net_purchase": 7,
        "total_net_purchase": 0, "value_unit": "KRW",
        "source_dataset": "kr_market_investor_net_purchase_daily",
        "source_provider": "legacy_stock_investment_pykrx_1.2.8",
        "source_operation": "MDCSTAT02202", "provider_segment": "legacy_pre_a001",
        "availability_date": None,
        "predictive_use_status": "blocked_unknown_unit_and_availability",
    }])
    with pytest.raises(ValueError, match="unit must remain unknown"):
        validate_investor_bridge(frame)


def test_bridge_validator_rejects_cross_segment_provenance():
    frame = pd.DataFrame([{
        "date": "2014-07-01", "market": "KOSPI",
        "institution_net_purchase": 0, "other_corporation_net_purchase": 0,
        "individual_net_purchase": 10, "foreign_net_purchase": -10,
        "total_net_purchase": 0, "value_unit": "KRW",
        "source_dataset": "kr_market_investor_net_purchase_daily",
        "source_provider": "tossinvest_open_api",
        "source_operation": "getMarketIndicatorInvestorTrading",
        "provider_segment": "toss_a001", "availability_date": "2014-07-01",
        "predictive_use_status": "eligible_from_availability_date",
    }])
    with pytest.raises(ValueError, match="Toss bridge provenance is invalid"):
        validate_investor_bridge(frame)
