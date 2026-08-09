from stock_data.contracts.base import ColumnContract, DatasetContract


GLOBAL_INDEX_PRICE_DAILY = DatasetContract(
    name="global_index_price_daily", version=1, status="active",
    description="Daily OHLCV for explicitly configured overseas indices.",
    source="yahoo_chart_api", layer="normalized", storage_format="parquet", frequency="daily",
    timezone="source_exchange", primary_key=("date", "symbol"), sort_key=("date", "symbol"),
    partition_by=("symbol", "year"), columns=(
        ColumnContract("date", "date32", False), ColumnContract("symbol", "string", False),
        ColumnContract("source_ticker", "string", False), ColumnContract("open", "float64", False),
        ColumnContract("high", "float64", False), ColumnContract("low", "float64", False),
        ColumnContract("close", "float64", False), ColumnContract("volume", "int64", True),
    ),
)

FRED_TREASURY_YIELD_DAILY = DatasetContract(
    name="fred_treasury_yield_daily", version=1, status="active",
    description="Observed U.S. Treasury constant maturity rates from FRED.",
    source="fred", layer="normalized", storage_format="parquet", frequency="daily", timezone=None,
    primary_key=("date",), sort_key=("date",), partition_by=("year",), columns=(
        ColumnContract("date", "date32", False), ColumnContract("dgs2", "float64", True),
        ColumnContract("dgs10", "float64", True), ColumnContract("dgs30", "float64", True),
    ),
)

FRED_USD_FX_DAILY = DatasetContract(
    name="fred_usd_fx_daily", version=1, status="active",
    description="Observed USD exchange-rate series from FRED.",
    source="fred", layer="normalized", storage_format="parquet", frequency="daily", timezone=None,
    primary_key=("date",), sort_key=("date",), partition_by=("year",), columns=(
        ColumnContract("date", "date32", False), ColumnContract("dexkous", "float64", True),
        ColumnContract("dexjpus", "float64", True),
    ),
)

US_TREASURY_SPREAD_DAILY = DatasetContract(
    name="us_treasury_spread_daily", version=1, status="active",
    description="Treasury term spreads reproducibly derived from normalized FRED yields.",
    source="fred_treasury_yield_daily", layer="derived", storage_format="parquet",
    frequency="daily", timezone=None, primary_key=("date",), sort_key=("date",),
    partition_by=("year",), columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("spread_10y_2y", "float64", True),
        ColumnContract("spread_30y_2y", "float64", True),
    ),
)
