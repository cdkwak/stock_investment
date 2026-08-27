"""LS t8428 surrounding-funds source-observation contract."""

from stock_data.contracts.base import ColumnContract, DatasetContract


LS_T8428_SURROUNDING_FUNDS_SOURCE_OBSERVATION = DatasetContract(
    name="ls_t8428_surrounding_funds_source_observation",
    version=1,
    status="implementation_ready_source_observation_only",
    description=(
        "Official LS t8428 daily source observations. Monetary fields whose official "
        "labels end in _억원 use KRW 100 million. Source date is preserved, but "
        "publication/revision timing is unknown, so predictive use is blocked."
    ),
    source="ls_openapi:t8428:/stock/investinfo:gubun=1:upcode=001",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("capture_id", "date"),
    sort_key=("date", "captured_at_utc"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("index_level", "decimal(18,2)", True),
        ColumnContract("index_change_sign", "string", True),
        ColumnContract("index_change", "decimal(18,2)", True),
        ColumnContract("index_change_percent", "decimal(18,2)", True),
        ColumnContract("market_volume_source", "int64", True),
        ColumnContract("turnover_source", "decimal(18,2)", True),
        ColumnContract("customer_deposits", "int64", True, "krw_100_million"),
        ColumnContract("customer_deposit_change", "int64", True, "krw_100_million"),
        ColumnContract("receivables", "int64", True, "krw_100_million"),
        ColumnContract("credit_balance", "int64", True, "krw_100_million"),
        ColumnContract("futures_deposits", "int64", True, "krw_100_million"),
        ColumnContract("equity_fund_balance", "int64", True, "krw_100_million"),
        ColumnContract("mixed_equity_fund_balance", "int64", True, "krw_100_million"),
        ColumnContract("mixed_bond_fund_balance", "int64", True, "krw_100_million"),
        ColumnContract("bond_fund_balance", "int64", True, "krw_100_million"),
        ColumnContract("legacy_short_bond_filler_source", "int64", True),
        ColumnContract("mmf_balance", "int64", True, "krw_100_million"),
        ColumnContract("capture_id", "string", False),
        ColumnContract("captured_at_utc", "timestamp[ns, UTC]", False),
        ColumnContract("landing_response_sha256", "string", False),
        ColumnContract("source_row_ordinal", "int64", False),
        ColumnContract("availability_status", "string", False),
    ),
)


LS_T8428_CONTRACTS = (LS_T8428_SURROUNDING_FUNDS_SOURCE_OBSERVATION,)
