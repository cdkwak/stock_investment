from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER,
    KR_EQUITY_PRICE_DAILY,
)


def test_equity_contracts_are_separate_normalized_parquet_datasets() -> None:
    contracts = (
        KR_EQUITY_PRICE_DAILY,
        KR_EQUITY_MARKET_CAP_DAILY,
        KR_EQUITY_MASTER,
    )
    assert all(contract.status in {"draft","active"} for contract in contracts)
    assert all(contract.layer == "normalized" for contract in contracts)
    assert all(contract.storage_format == "parquet" for contract in contracts)
    assert KR_EQUITY_PRICE_DAILY.column_names == (
        "date", "market", "symbol", "open", "high", "low", "close",
        "volume", "trading_value",
    )
    assert KR_EQUITY_MARKET_CAP_DAILY.column_names == (
        "date", "market", "symbol", "market_cap", "shares_outstanding",
    )
    assert KR_EQUITY_MASTER.column_names == (
        "symbol", "name", "market", "isin", "corp_no", "company_name",
        "security_type_code", "security_type_name", "par_value", "issued_shares",
        "listing_date", "delisting_date", "deposit_registration_date",
        "deposit_cancellation_date", "source", "source_date",
    )


def test_daily_equity_contracts_use_market_year_partitions() -> None:
    assert KR_EQUITY_PRICE_DAILY.partition_by == ("market", "year")
    assert KR_EQUITY_MARKET_CAP_DAILY.partition_by == ("market", "year")
    assert KR_EQUITY_MASTER.partition_by == ("market",)
    assert KR_EQUITY_MASTER.source == "data_go_kr_stock_issuance+daily_source_identity"
