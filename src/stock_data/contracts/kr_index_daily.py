from __future__ import annotations

from types import MappingProxyType

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_INDEX_TICKERS = MappingProxyType({
    "KOSPI": "1001",
    "KOSDAQ": "2001",
    "KOSPI200_IT": "1155",
})
KR_INDEX_MARKETS = MappingProxyType({
    "KOSPI": "KOSPI",
    "KOSDAQ": "KOSDAQ",
    "KOSPI200_IT": "KOSPI",
})


KR_INDEX_DAILY = DatasetContract(
    name="kr_index_daily",
    version=2,
    status="active",
    description=(
        "Daily OHLCV and market aggregates for registered Korean equity indices, "
        "including KOSPI 200 Information Technology."
    ),
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
