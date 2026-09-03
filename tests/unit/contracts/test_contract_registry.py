from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.contracts.global_etf import (
    GLOBAL_ETF_DAILY_SYMBOLS,
    GLOBAL_ETF_REGISTRY,
    global_etf_leverage_multiple,
)
from stock_data.contracts.kr_etf import infer_kr_etf_leverage_multiple


def test_contract_registry_has_unique_names_and_layer_formats() -> None:
    assert len(CONTRACTS)==len(set(CONTRACTS))
    assert all(contract.storage_format=="parquet" for contract in CONTRACTS.values())
    assert all(contract.layer in {"normalized","derived","published"} for contract in CONTRACTS.values())
    assert not any("label" in contract.column_names for contract in CONTRACTS.values())


def test_market_60m_contract_is_provider_specific_and_session_safe() -> None:
    contract = CONTRACTS["market_price_60m_observation"]
    assert contract.frequency == "intraday"
    assert contract.primary_key == ("provider", "symbol", "bar_start")
    assert {"session", "actual_duration_minutes", "fallback_used", "fallback_reason"} <= set(
        contract.column_names
    )


def test_market_15m_active_contract_is_delayed_and_timezone_explicit() -> None:
    contract = CONTRACTS[MARKET_PRICE_15M_OBSERVATION.name]
    assert contract.status == "active"
    assert contract.frequency == "intraday"
    assert contract.primary_key == ("provider", "series_id", "bar_start")
    assert {"source_timezone", "display_timezone", "data_availability"} <= set(
        contract.column_names
    )
    assert "not licensed realtime" in contract.description


def test_global_daily_contracts_keep_symbol_identity_and_futures_semantics() -> None:
    index = CONTRACTS["global_index_price_daily"]
    etf = CONTRACTS["global_etf_price_daily"]
    futures = CONTRACTS["global_commodity_futures_daily"]

    assert index.primary_key == ("date", "symbol")
    assert {"source_ticker", "open", "high", "low", "close", "volume"} <= set(
        index.column_names
    )
    assert {"adjusted_close", "currency", "exchange", "provider"} <= set(
        etf.column_names
    )
    assert {"source_ticker", "asset", "ohlc_status"} <= set(futures.column_names)
    assert "dollar-index continuous futures" in futures.description


def test_global_etf_registry_is_contract_owned_and_exposure_explicit() -> None:
    assert GLOBAL_ETF_DAILY_SYMBOLS == (
        "SOXX", "EWY", "SOXL", "TQQQ", "QLD", "TLT", "QQQ", "SPY",
    )
    assert {
        symbol: global_etf_leverage_multiple(symbol)
        for symbol in GLOBAL_ETF_DAILY_SYMBOLS
    } == {
        "SOXX": 1, "EWY": 1, "SOXL": 3, "TQQQ": 3,
        "QLD": 2, "TLT": 1, "QQQ": 1, "SPY": 1,
    }
    assert all(
        entry["cadence"] == "GLOBAL_DAILY"
        and entry["automation_enabled"] is True
        and entry["expected_currency"] == "USD"
        for entry in GLOBAL_ETF_REGISTRY.values()
    )


def test_korean_etf_contracts_and_name_only_leverage_rule_are_registered() -> None:
    master = CONTRACTS["kr_etf_master"]
    price = CONTRACTS["kr_etf_price_daily"]
    assert master.primary_key == ("market", "symbol")
    assert {"listing_status", "listing_date", "leverage_multiple"} <= set(master.column_names)
    assert price.primary_key == ("date", "symbol")
    assert {"open", "high", "low", "close", "volume", "trading_value", "nav"} <= set(
        price.column_names
    )
    assert infer_kr_etf_leverage_multiple("TIGER 레버리지") == 2
    assert infer_kr_etf_leverage_multiple("TIGER 200 IT 레버리지") == 2
    assert infer_kr_etf_leverage_multiple("TIGER 인버스2X") == -2
    assert infer_kr_etf_leverage_multiple("TIGER 200") == 1
