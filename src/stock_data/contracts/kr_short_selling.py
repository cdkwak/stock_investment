from stock_data.contracts.base import ColumnContract, DatasetContract


KR_SHORT_SELLING_TRADING_DAILY = DatasetContract(
    name="kr_short_selling_trading_daily", version=1, status="draft_blocked",
    description="Per-symbol daily short-selling volume and trading value.",
    source="pykrx_1.2.8_contract_unverified_live", layer="normalized",
    storage_format="parquet", frequency="daily", timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"), partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("short_volume", "int64", False, "shares"),
        ColumnContract("short_trading_value", "int64", False, "KRW"),
    ),
)

KR_SHORT_SELLING_BALANCE_DAILY = DatasetContract(
    name="kr_short_selling_balance_daily", version=1, status="draft_blocked",
    description="Per-symbol daily reported short balance and reference totals.",
    source="pykrx_1.2.8_contract_unverified_live", layer="normalized",
    storage_format="parquet", frequency="daily", timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"), partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("short_balance", "int64", False, "shares"),
        ColumnContract("shares_outstanding", "int64", False, "shares"),
        ColumnContract("short_balance_value", "int64", False, "KRW"),
        ColumnContract("market_cap", "int64", False, "KRW"),
        ColumnContract("short_balance_ratio", "float64", False, "percent"),
    ),
)

KR_SHORT_SELLING_INVESTOR_DAILY = DatasetContract(
    name="kr_short_selling_investor_daily", version=1, status="draft_blocked",
    description="Daily market-level short selling by investor class and metric.",
    source="pykrx_1.2.8_contract_unverified_live", layer="normalized",
    storage_format="parquet", frequency="daily", timezone="Asia/Seoul",
    primary_key=("date", "market", "investor_type", "metric"),
    sort_key=("date", "market", "metric", "investor_type"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("investor_type", "string", False),
        ColumnContract("metric", "string", False, description="volume or trading_value"),
        ColumnContract("value", "int64", False, description="shares or KRW per metric"),
    ),
)
