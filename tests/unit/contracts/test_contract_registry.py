from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.contracts.global_etf import (
    GLOBAL_ETF_DAILY_SYMBOLS,
    GLOBAL_ETF_REGISTRY,
    global_etf_leverage_multiple,
)
from stock_data.contracts.global_equity import (
    GLOBAL_EQUITY_DAILY_SYMBOLS,
    GLOBAL_EQUITY_PRICE_DAILY,
    GLOBAL_EQUITY_REGISTRY,
)
from stock_data.contracts.global_market import (
    GLOBAL_INDEX_DAILY_SYMBOLS,
    GLOBAL_INDEX_REGISTRY,
    GLOBAL_INDEX_SYMBOLS_BY_PROVIDER,
)
from stock_data.contracts.kr_etf import infer_kr_etf_leverage_multiple
from stock_data.providers.yahoo import GLOBAL_MARKET_60M_REGISTRY


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


def test_global_index_registry_includes_vix_term_structure_identities() -> None:
    assert GLOBAL_INDEX_DAILY_SYMBOLS[-4:] == ("VIX9D", "VIX3M", "VIX6M", "SKEW")
    assert GLOBAL_INDEX_SYMBOLS_BY_PROVIDER["cboe_index_history_csv"] == (
        "VIX9D", "VIX3M", "VIX6M", "SKEW",
    )
    for symbol in GLOBAL_INDEX_DAILY_SYMBOLS[-4:]:
        spec = GLOBAL_INDEX_REGISTRY[symbol]
        assert spec["source_ticker"] == symbol
        assert spec["provider"] == "cboe_index_history_csv"
        assert spec["source_url"].endswith(f"/{symbol}_History.csv")
        assert "ohlc_fill_from_close" not in spec
    derived = CONTRACTS["us_vix_term_structure_daily"]
    assert derived.layer == "derived"
    assert derived.primary_key == ("date",)
    assert derived.column_names == (
        "date", "vix", "vix9d", "vix3m", "vix6m", "skew",
        "ratio_1m_3m", "ratio_9d_1m", "regime", "pct_rank_252",
    )


def test_yahoo_current_30m_registry_has_four_new_exact_identities() -> None:
    assert {
        series_id: {
            "provider_symbol": spec["provider_symbol"],
            "market": spec["market"],
            "instrument_type": spec["instrument_type"],
            "expected_currency": spec["expected_currency"],
            "accepted_yahoo_exchanges": spec["accepted_yahoo_exchanges"],
        }
        for series_id, spec in GLOBAL_MARKET_60M_REGISTRY.items()
        if series_id in {
            "SP500_FUTURES_CURRENT_60M", "DOW_FUTURES_CURRENT_60M",
            "SOX_CURRENT_60M", "DOLLAR_INDEX_CURRENT_60M",
        }
    } == {
        "SP500_FUTURES_CURRENT_60M": {
            "provider_symbol": "ES=F", "market": "CME", "instrument_type": "FUTURE",
            "expected_currency": "USD", "accepted_yahoo_exchanges": ("CME",),
        },
        "DOW_FUTURES_CURRENT_60M": {
            "provider_symbol": "YM=F", "market": "CBOT", "instrument_type": "FUTURE",
            "expected_currency": "USD", "accepted_yahoo_exchanges": ("CBT", "CBOT"),
        },
        "SOX_CURRENT_60M": {
            "provider_symbol": "^SOX", "market": "XNAS", "instrument_type": "INDEX",
            "expected_currency": "USD",
            "accepted_yahoo_exchanges": ("NIM", "NGM", "NMS", "NASDAQ"),
        },
        "DOLLAR_INDEX_CURRENT_60M": {
            "provider_symbol": "DX-Y.NYB", "market": "ICE", "instrument_type": "INDEX",
            "expected_currency": "USD", "accepted_yahoo_exchanges": ("NYB", "ICE"),
        },
    }


def test_global_etf_registry_is_contract_owned_and_exposure_explicit() -> None:
    assert GLOBAL_ETF_DAILY_SYMBOLS == (
        "SOXX", "EWY", "SOXL", "TQQQ", "QLD", "TLT", "QQQ", "SPY", "SGOV", "VGLT",
    )
    assert {
        symbol: global_etf_leverage_multiple(symbol)
        for symbol in GLOBAL_ETF_DAILY_SYMBOLS
    } == {
        "SOXX": 1, "EWY": 1, "SOXL": 3, "TQQQ": 3,
        "QLD": 2, "TLT": 1, "QQQ": 1, "SPY": 1, "SGOV": 1, "VGLT": 1,
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


def test_korean_equity_investor_flow_contract_is_registered_at_symbol_date_grain() -> None:
    contract = CONTRACTS["kr_equity_investor_flow_daily"]
    assert contract.primary_key == ("date", "symbol")
    assert contract.partition_by == ("symbol", "year")
    assert contract.column_names == (
        "date", "symbol", "foreign_net", "institution_net", "individual_net",
        "other_corp_net", "total_net", "source", "captured_at",
    )
    assert all(
        column.dtype == "int64" and column.unit == "KRW"
        for column in contract.columns[2:7]
    )


def test_first_global_equity_registry_entry_is_exact_skhy_adr_identity() -> None:
    assert GLOBAL_EQUITY_DAILY_SYMBOLS == ("SKHY",)
    assert GLOBAL_EQUITY_PRICE_DAILY.column_names == CONTRACTS[
        "global_etf_price_daily"
    ].column_names
    assert GLOBAL_EQUITY_PRICE_DAILY.partition_by == ("symbol", "year")
    assert dict(GLOBAL_EQUITY_REGISTRY["SKHY"]) == {
        "source_ticker": "SKHY",
        "provider": "yahoo_chart_api",
        "instrument_type": "EQUITY",
        "security_type": "DEPOSITARY_RECEIPT",
        "official_name": "SK hynix Inc. ADR",
        "korean_name": "SK하이닉스(ADR)",
        "official_exchange": "NASDAQ",
        "isin": "US78392B2060",
        "underlying_kr_symbol": "000660",
        "adr_ratio": None,
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "cadence": "GLOBAL_DAILY",
        "automation_enabled": True,
    }
