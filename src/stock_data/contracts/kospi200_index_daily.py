from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_KOSPI200_INDEX_DAILY = DatasetContract(
    name="kr_kospi200_index_daily",
    version=1,
    status="active",
    description="Daily official KOSPI200 spot-index OHLCV observations for ticker 1028.",
    source="pykrx:get_index_ohlcv:1028",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date",),
    sort_key=("date",),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("ticker", "string", False),
        ColumnContract("open", "float64", False, "index_point"),
        ColumnContract("high", "float64", False, "index_point"),
        ColumnContract("low", "float64", False, "index_point"),
        ColumnContract("close", "float64", False, "index_point"),
        ColumnContract("volume", "int64", False),
        ColumnContract("trading_value", "int64", False),
        ColumnContract("market_cap", "int64", False),
        ColumnContract("ohlc_status", "string", False),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("date_semantics", "string", False),
    ),
)
