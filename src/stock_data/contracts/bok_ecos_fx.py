"""BOK ECOS official daily USD/KRW reference-rate contract.

The table/item identity is configured from the ECOS StatisticSearch route
requested by the project.  Publication time, revision behavior, and historical
point-in-time finality remain UNVERIFIED because the client-rendered official
guide details could not be opened in this implementation environment.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


BOK_ECOS_USD_KRW_DAILY = DatasetContract(
    name="bok_ecos_usd_krw_daily",
    version=1,
    status="active",
    description=(
        "Official BOK ECOS 731Y001 daily KRW-per-USD reference-rate observations "
        "for item 0000001. Eligible for current display and account valuation; "
        "publication/revision timing is unverified, so predictive/backtest use is blocked."
    ),
    source="bok_ecos:StatisticSearch:731Y001:0000001",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date",),
    sort_key=("date",),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("rate_krw_per_usd", "float64", False, "KRW per USD"),
        ColumnContract("item_code", "string", False),
        ColumnContract("stat_code", "string", False),
        ColumnContract("unit", "string", False),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
    ),
)


BOK_ECOS_FX_CONTRACTS = (BOK_ECOS_USD_KRW_DAILY,)


__all__ = ["BOK_ECOS_FX_CONTRACTS", "BOK_ECOS_USD_KRW_DAILY"]
