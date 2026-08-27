"""BOK ECOS Korean Treasury source-observation contract."""

from stock_data.contracts.base import ColumnContract, DatasetContract


BOK_ECOS_TREASURY_TENORS = ("2Y", "3Y", "5Y", "10Y", "20Y", "30Y")


BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION = DatasetContract(
    name="bok_ecos_kr_treasury_yield_source_observation",
    version=1,
    status="active",
    description=(
        "Immutable observations distributed by BOK ECOS for KOFIA final-quotation "
        "Korean government-bond yields. Separate from Toss OHLC candles; historical "
        "publication and revision timing remain unknown because the official "
        "StatisticSearch response contract exposes neither publication timestamps "
        "nor preliminary/revision state."
    ),
    source="bok_ecos:StatisticSearch:817Y002",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("capture_id", "source_item_code", "source_item_ordinal"),
    sort_key=("date", "maturity_years", "captured_at_utc", "source_item_ordinal"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("tenor", "string", False),
        ColumnContract("maturity_years", "int64", False, "years"),
        ColumnContract("yield_percent", "decimal(9,3)", False, "annual_percent"),
        ColumnContract("source_agency", "string", False),
        ColumnContract("distributor", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_table_code", "string", False),
        ColumnContract("source_table_name", "string", False),
        ColumnContract("source_item_code", "string", False),
        ColumnContract("source_item_name", "string", False),
        ColumnContract("source_unit_name", "string", False),
        ColumnContract("source_cycle", "string", False),
        ColumnContract("capture_id", "string", False),
        ColumnContract("captured_at_utc", "timestamp[ns, UTC]", False),
        ColumnContract("landing_response_sha256", "string", False),
        ColumnContract("source_item_ordinal", "int64", False),
        ColumnContract("published_at_utc", "timestamp[ns, UTC]", True),
        ColumnContract("revision_id", "string", True),
        ColumnContract("availability_status", "string", False),
    ),
)


BOK_ECOS_TREASURY_CONTRACTS = (
    BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION,
)
