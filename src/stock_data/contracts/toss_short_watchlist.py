"""Contract for a fixed Toss per-symbol short-selling watchlist.

This contract is intentionally not part of the executable dataset registry.
It defines the only reviewed storage boundary for a future bounded live pilot;
registration requires separate retained overlap and finality evidence.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


TOSS_SHORT_WATCHLIST_VERSION = "2026-08-20-v1"
TOSS_SHORT_WATCHLIST = (
    ("005930", "삼성전자", "KOSPI"),
    ("000660", "SK하이닉스", "KOSPI"),
)
TOSS_SHORT_SOURCE_SCOPE = "KRX_ONLY_PROVIDER_EOD"


TOSS_EQUITY_SHORT_WATCHLIST_DAILY = DatasetContract(
    name="toss_equity_short_watchlist_daily",
    version=1,
    status="reviewed_offline_fixed_watchlist_only",
    description=(
        "Toss provider-specific daily short-selling observations for the fixed "
        "2026-08-20 watchlist. This is neither an official KRX market aggregate "
        "nor a short-balance dataset and cannot be dynamically fanned out."
    ),
    source="tossinvest_open_api:getStockShortSelling",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False, description="Toss source date"),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("short_selling_volume", "int64", False, "shares"),
        ColumnContract("short_selling_amount", "int64", False, "KRW"),
        ColumnContract("short_selling_volume_rate", "float64", True, "ratio"),
        ColumnContract("short_selling_amount_rate", "float64", True, "ratio"),
        ColumnContract("source_scope", "string", False),
        ColumnContract("watchlist_version", "string", False),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_date", "string", False),
        ColumnContract("collected_at", "timestamp[ns, UTC]", False),
        ColumnContract("updated_at", "timestamp[ns, UTC]", False),
        ColumnContract(
            "availability_date",
            "string",
            False,
            description="KST date derived from the provider updatedAt field",
        ),
    ),
)
