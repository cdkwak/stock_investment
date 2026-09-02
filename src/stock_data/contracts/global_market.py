from dataclasses import dataclass

from stock_data.contracts.base import ColumnContract, DatasetContract


@dataclass(frozen=True)
class FredFxSeriesIdentity:
    """Provider-native identity for one column in the retained FRED FX dataset."""

    series_id: str
    column: str
    display_pair: str
    official_unit: str
    frequency: str
    seasonal_adjustment: str
    release: str
    quote_convention: str
    display_scale: float


FRED_DEXJPUS_IDENTITY = FredFxSeriesIdentity(
    series_id="DEXJPUS",
    column="dexjpus",
    display_pair="USD/JPY",
    official_unit="Japanese Yen to One U.S. Dollar",
    frequency="Daily",
    seasonal_adjustment="Not Seasonally Adjusted",
    release="H.10 Foreign Exchange Rates",
    quote_convention="JPY_PER_USD",
    display_scale=1.0,
)


GLOBAL_INDEX_PRICE_DAILY = DatasetContract(
    name="global_index_price_daily", version=1, status="active",
    description=(
        "Daily OHLCV for explicitly registered overseas indices, including broad-market "
        "and semiconductor benchmarks. Provider identity and daily granularity must match "
        "the registered Yahoo ticker before normalization."
    ),
    source="yahoo_chart_api", layer="normalized", storage_format="parquet", frequency="daily",
    timezone="source_exchange", primary_key=("date", "symbol"), sort_key=("date", "symbol"),
    partition_by=("symbol", "year"), columns=(
        ColumnContract("date", "date32", False), ColumnContract("symbol", "string", False),
        ColumnContract("source_ticker", "string", False), ColumnContract("open", "float64", False),
        ColumnContract("high", "float64", False), ColumnContract("low", "float64", False),
        ColumnContract("close", "float64", False), ColumnContract("volume", "int64", True),
    ),
)

GLOBAL_COMMODITY_FUTURES_DAILY = DatasetContract(
    name="global_commodity_futures_daily", version=1, status="active",
    description=(
        "Yahoo vendor-continuous market-futures daily OHLCV. The legacy dataset name "
        "is retained for compatibility and includes explicitly registered commodity, "
        "equity-index, and dollar-index continuous futures. Not spot prices, "
        "individual-expiry contracts, official "
        "settlements, or historical provider vintages."
    ),
    source="yahoo_chart_api", layer="normalized", storage_format="parquet", frequency="daily",
    timezone="America/New_York", primary_key=("date", "symbol"), sort_key=("date", "symbol"),
    partition_by=("symbol", "year"), columns=(
        ColumnContract("date", "date32", False), ColumnContract("symbol", "string", False),
        ColumnContract("source_ticker", "string", False), ColumnContract("asset", "string", False),
        ColumnContract("open", "float64", True), ColumnContract("high", "float64", True),
        ColumnContract("low", "float64", True), ColumnContract("close", "float64", True),
        ColumnContract("volume", "int64", True), ColumnContract("ohlc_status", "string", False),
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
    description=(
        "Observed USD exchange-rate series from FRED's H.10 release. Provider "
        "values retain their native quote direction; DEXJPUS is JPY per one USD "
        "and must not be inverted or rescaled."
    ),
    source="fred", layer="normalized", storage_format="parquet", frequency="daily", timezone=None,
    primary_key=("date",), sort_key=("date",), partition_by=("year",), columns=(
        ColumnContract("date", "date32", False),
        ColumnContract(
            "dexkous", "float64", True, "South Korean Won to One U.S. Dollar",
            "FRED DEXKOUS provider-native daily H.10 observation.",
        ),
        ColumnContract(
            "dexjpus", "float64", True, FRED_DEXJPUS_IDENTITY.official_unit,
            "FRED DEXJPUS provider-native daily, not-seasonally-adjusted H.10 "
            "observation; display as USD/JPY without reciprocal or 100-unit scaling.",
        ),
    ),
)

FRED_VIX_DAILY = DatasetContract(
    name="fred_vix_daily", version=1, status="active",
    description=(
        "Daily VIX close observations distributed by FRED as VIXCLS. "
        "This FRED lineage is distinct from the license-blocked direct Cboe Landing route."
    ),
    source="fred_vixcls", layer="normalized", storage_format="parquet", frequency="daily",
    timezone=None, primary_key=("date",), sort_key=("date",), partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("vixcls", "float64", True, "index_points"),
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
