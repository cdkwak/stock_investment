from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY


def test_contract_is_explicit_and_versioned() -> None:
    assert KR_INDEX_DAILY.name == "kr_index_daily"
    assert KR_INDEX_DAILY.version == 2
    assert KR_INDEX_DAILY.layer == "normalized"
    assert KR_INDEX_DAILY.storage_format == "parquet"
    assert KR_INDEX_DAILY.partition_by == ("market", "year")
    assert KR_INDEX_DAILY.primary_key == ("date", "symbol")
    assert KR_INDEX_DAILY.timezone == "Asia/Seoul"
    assert KR_INDEX_DAILY.column_names == (
        "date", "symbol", "market", "open", "high", "low", "close",
        "volume", "trading_value", "market_cap", "source",
    )
