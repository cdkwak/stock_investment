from stock_data.contracts.base import ColumnContract, DatasetContract


GLOBAL_ETF_PRICE_DAILY = DatasetContract(
    name="global_etf_price_daily", version=1, status="active",
    description=(
        "Daily provider OHLCV and separately retained adjusted close for explicitly "
        "registered exchange-traded funds. ETFs are never represented as indices."
    ),
    source="yahoo_chart_api", layer="normalized", storage_format="parquet", frequency="daily",
    timezone="source_exchange", primary_key=("date", "symbol"), sort_key=("date", "symbol"),
    partition_by=("symbol", "year"), columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("source_ticker", "string", False),
        ColumnContract("open", "float64", False),
        ColumnContract("high", "float64", False),
        ColumnContract("low", "float64", False),
        ColumnContract("close", "float64", False),
        ColumnContract("adjusted_close", "float64", False),
        ColumnContract("volume", "int64", True),
        ColumnContract("currency", "string", False),
        ColumnContract("exchange", "string", False),
        ColumnContract("provider", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
        ColumnContract("adjustment_status", "string", False),
    ),
)
