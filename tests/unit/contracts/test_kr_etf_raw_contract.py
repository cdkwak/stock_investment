from stock_data.contracts.kr_etf_raw import (
    KR_ETF_OHLCV_DAILY_RAW,
    KR_ETF_RAW_CONTRACTS,
    KR_ETF_UNIVERSE_DAILY_RAW,
)
from stock_data.contracts.registry import CONTRACTS


def test_etf_raw_contracts_remain_unregistered_until_semantic_gates_close() -> None:
    assert len(KR_ETF_RAW_CONTRACTS) == 2
    for contract in KR_ETF_RAW_CONTRACTS:
        # The semantic kr_etf_universe_daily contract (contracts/kr_etf.py) is registered and
        # collected daily; the RAW contract objects themselves must stay out of the registry.
        assert CONTRACTS.get(contract.name) is not contract
        assert contract.status == (
            "contract_only_raw_finality_revision_delisting_review_required"
        )
        assert contract.layer == "raw"
        assert contract.storage_format == "shared_landing_json_reference"
        assert contract.timezone == "Asia/Seoul"
        assert contract.primary_key == ("market_date", "issue_short_code")


def test_universe_contract_is_exact_date_membership_not_an_interval() -> None:
    contract = KR_ETF_UNIVERSE_DAILY_RAW
    assert contract.partition_by == ("market_date",)
    assert {
        "issue_code", "security_group_code", "issue_name", "close_raw",
        "nav_per_security_raw", "market_cap_raw", "total_net_assets_raw",
        "listed_securities_raw", "source_row_ordinal", "landing_path",
        "landing_sha256", "finality_status", "revision_status",
        "delisting_status",
    } <= set(contract.column_names)
    assert not ({"effective_from", "effective_to", "delisting_date"} & set(contract.column_names))


def test_ohlcv_contract_preserves_provider_native_units_and_shared_identity() -> None:
    contract = KR_ETF_OHLCV_DAILY_RAW
    columns = {column.name: column for column in contract.columns}
    for field in ("open_raw", "high_raw", "low_raw", "close_raw", "nav_per_security_raw"):
        assert columns[field].dtype == "string"
        assert columns[field].unit == "KRW_per_ETF_security"
    assert columns["accumulated_volume_raw"].unit == "ETF_securities"
    assert columns["accumulated_trading_value_raw"].unit == "KRW"
    assert columns["landing_sha256"].unit == "sha256_hex"
    assert not ({"adjusted_close", "available_at", "revision_id"} & set(contract.column_names))


def test_both_logical_contracts_require_the_same_evidence_columns() -> None:
    evidence = {
        "source_row_ordinal", "landing_path", "landing_sha256", "captured_at_utc",
        "finality_status", "revision_status", "delisting_status",
    }
    assert evidence <= set(KR_ETF_UNIVERSE_DAILY_RAW.column_names)
    assert evidence <= set(KR_ETF_OHLCV_DAILY_RAW.column_names)
