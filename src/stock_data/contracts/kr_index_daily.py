from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_INDEX_DAILY = DatasetContract(
    name="kr_index_daily",
    version=2,
    status="active",
    description="Daily OHLCV and market aggregates for broad Korean equity indices.",
    source="pykrx",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "symbol"),
    sort_key=("date", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("market", "string", False),
        ColumnContract("open", "float64", False, "index_point"),
        ColumnContract("high", "float64", False, "index_point"),
        ColumnContract("low", "float64", False, "index_point"),
        ColumnContract("close", "float64", False, "index_point"),
        ColumnContract("volume", "int64", False),
        ColumnContract("trading_value", "int64", False),
        ColumnContract("market_cap", "int64", False),
        ColumnContract("source", "string", False),
    ),
)
