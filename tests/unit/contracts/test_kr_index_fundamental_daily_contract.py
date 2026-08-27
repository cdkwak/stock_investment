from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)


def test_contract_fixes_identity_provenance_and_partitions() -> None:
    contract = KR_INDEX_FUNDAMENTAL_DAILY
    assert contract.version == 1
    assert contract.layer == "normalized"
    assert contract.primary_key == ("date", "index_code")
    assert contract.partition_by == ("market", "year")
    assert contract.column_names == (
        "date", "index_code", "market", "close", "weighted_per",
        "weighted_pbr", "dividend_yield", "source",
        "source_response_sha256",
    )
    nullable = {column.name: column.nullable for column in contract.columns}
    assert nullable["weighted_per"] is True
    assert nullable["weighted_pbr"] is True
    assert nullable["dividend_yield"] is True
    assert nullable["source_response_sha256"] is False
