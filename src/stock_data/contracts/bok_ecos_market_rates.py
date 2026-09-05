"""BOK ECOS Korean daily corporate-bond and overnight call-rate contract."""

from stock_data.contracts.base import ColumnContract, DatasetContract


BOK_ECOS_KR_MARKET_RATE_DAILY = DatasetContract(
    name="bok_ecos_kr_market_rate_daily",
    version=1,
    status="active",
    description=(
        "Separate BOK ECOS daily observations for the 3-year AA- corporate-bond "
        "yield and overnight all-transactions call rate. The series are never "
        "spliced into Korean Treasury yields; publication/revision finality and "
        "predictive point-in-time use remain unverified."
    ),
    source="bok_ecos:StatisticSearch:817Y002:010300000,010101000",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "series"),
    sort_key=("date", "series"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("series", "string", False),
        ColumnContract("rate_percent", "float64", False, "percent"),
        ColumnContract("item_code", "string", False),
        ColumnContract("stat_code", "string", False),
        ColumnContract("unit", "string", False),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
    ),
)


BOK_ECOS_MARKET_RATE_CONTRACTS = (BOK_ECOS_KR_MARKET_RATE_DAILY,)


__all__ = ["BOK_ECOS_KR_MARKET_RATE_DAILY", "BOK_ECOS_MARKET_RATE_CONTRACTS"]
