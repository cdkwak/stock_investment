from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType

from stock_data.contracts.base import ColumnContract, DatasetContract


class EndpointWindowPolicy(StrEnum):
    """Accepted response-window checks for registered global index symbols."""

    STRICT_EXCHANGE = "strict_exchange"
    PROVIDER_NATIVE = "provider_native"


# Symbols absent from this override registry retain the strict exchange-window
# policy. DX-Y.NYB is an ICE dollar-index route whose Yahoo daily labels do not
# share the XNYS cash-calendar endpoints used to bound the request.
GLOBAL_INDEX_ENDPOINT_WINDOW_OVERRIDES = MappingProxyType({
    "DOLLAR_INDEX": EndpointWindowPolicy.PROVIDER_NATIVE,
})


# Earliest contract-supported explicit daily-history windows for bounded
# futures onboarding.  Yahoo's range=max route is deliberately not used because
# it can silently coerce long histories to monthly granularity.
GLOBAL_FUTURES_BACKFILL_STARTS = MappingProxyType({
    "GOLD": date(2000, 8, 30),
})

FRED_TREASURY_YIELD_EXT_STARTS = MappingProxyType({
    "DGS3": date(1962, 1, 2),
    "DGS5": date(1962, 1, 2),
    "DTB3": date(1954, 1, 4),
})


GLOBAL_INDEX_REGISTRY = MappingProxyType({
    "SP500": {
        "source_ticker": "^GSPC", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": None, "accepted_yahoo_exchanges": (),
        "provider": "yahoo_chart_api",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
    },
    "NASDAQ_COMPOSITE": {
        "source_ticker": "^IXIC", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": None, "accepted_yahoo_exchanges": (),
        "provider": "yahoo_chart_api",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC",
    },
    "NASDAQ100": {
        "source_ticker": "^NDX", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": None, "accepted_yahoo_exchanges": (),
        "provider": "yahoo_chart_api",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX",
    },
    "SOX": {
        "source_ticker": "^SOX", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": None,
        "accepted_yahoo_exchanges": ("NIM", "NGM", "NMS", "NASDAQ"),
        "provider": "yahoo_chart_api",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX",
    },
    "DOW_JONES": {
        "source_ticker": "^DJI", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": None, "accepted_yahoo_exchanges": (),
        "provider": "yahoo_chart_api",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EDJI",
    },
    "DOLLAR_INDEX": {
        "source_ticker": "DX-Y.NYB", "instrument_type": "INDEX",
        "index_basis": "NOT_APPLICABLE",
        "expected_currency": None, "accepted_yahoo_exchanges": ("NYB", "ICE"),
        "provider": "yahoo_chart_api",
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB",
    },
    "NIKKEI225": {
        "source_ticker": "^N225", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": "JPY", "accepted_yahoo_exchanges": ("OSA",),
        "timezone": "Asia/Tokyo", "exchange_calendar": "XTKS",
        "provider": "yahoo_chart_api", "ohlc_fill_from_close": True,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EN225",
    },
    "TAIEX": {
        "source_ticker": "^TWII", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": "TWD", "accepted_yahoo_exchanges": ("TAI",),
        "timezone": "Asia/Taipei", "exchange_calendar": "XTAI",
        "provider": "yahoo_chart_api", "ohlc_fill_from_close": True,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII",
    },
    "EURO_STOXX50": {
        "source_ticker": "^STOXX50E", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": "EUR", "accepted_yahoo_exchanges": ("ZRH",),
        "timezone": "Europe/Zurich", "exchange_calendar": "XETR",
        "provider": "yahoo_chart_api", "ohlc_fill_from_close": True,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5ESTOXX50E",
    },
    "CAC40": {
        "source_ticker": "^FCHI", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": "EUR", "accepted_yahoo_exchanges": ("PAR",),
        "timezone": "Europe/Paris", "exchange_calendar": "XPAR",
        "provider": "yahoo_chart_api", "ohlc_fill_from_close": True,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EFCHI",
    },
    "HANG_SENG": {
        "source_ticker": "^HSI", "instrument_type": "INDEX",
        "index_basis": "PRICE",
        "expected_currency": "HKD", "accepted_yahoo_exchanges": ("HKG",),
        "timezone": "Asia/Hong_Kong", "exchange_calendar": "XHKG",
        "provider": "yahoo_chart_api", "ohlc_fill_from_close": True,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EHSI",
    },
    "DAX": {
        "source_ticker": "^GDAXI", "instrument_type": "INDEX",
        "index_basis": "TOTAL_RETURN",
        "expected_currency": "EUR", "accepted_yahoo_exchanges": ("GER",),
        "timezone": "Europe/Berlin", "exchange_calendar": "XETR",
        "provider": "yahoo_chart_api", "ohlc_fill_from_close": True,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EGDAXI",
    },
    "VIX9D": {
        "source_ticker": "VIX9D", "instrument_type": "INDEX",
        "index_basis": "NOT_APPLICABLE",
        "expected_currency": None, "provider": "cboe_index_history_csv",
        "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv",
    },
    "VIX3M": {
        "source_ticker": "VIX3M", "instrument_type": "INDEX",
        "index_basis": "NOT_APPLICABLE",
        "expected_currency": None, "provider": "cboe_index_history_csv",
        "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
    },
    "VIX6M": {
        "source_ticker": "VIX6M", "instrument_type": "INDEX",
        "index_basis": "NOT_APPLICABLE",
        "expected_currency": None, "provider": "cboe_index_history_csv",
        "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX6M_History.csv",
    },
    "SKEW": {
        "source_ticker": "SKEW", "instrument_type": "INDEX",
        "index_basis": "NOT_APPLICABLE",
        "expected_currency": None, "provider": "cboe_index_history_csv",
        "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv",
    },
})
GLOBAL_INDEX_DAILY_SYMBOLS = tuple(GLOBAL_INDEX_REGISTRY)

# index_basis (2026-09-05, vault + review): what an index measures. PRICE = price index
# (dividends excluded: ^GSPC, ^NDX, ^N225, ^STOXX50E, ^FCHI, KOSPI in kr_index_daily);
# TOTAL_RETURN = performance index with dividends reinvested (^GDAXI); NOT_APPLICABLE =
# volatility/dollar gauges. Charts that normalise several indices to a common base must
# refuse to mix PRICE with TOTAL_RETURN — the rule lives here, not only in a spec document.
INDEX_BASES = frozenset({"PRICE", "TOTAL_RETURN", "NOT_APPLICABLE"})


def index_basis(symbol: str) -> str:
    """Return the registry's index_basis for a global index symbol (KeyError if unknown)."""
    return str(GLOBAL_INDEX_REGISTRY[symbol]["index_basis"])


def assert_same_index_basis(symbols: tuple[str, ...] | list[str]) -> str:
    """Refuse to mix PRICE with TOTAL_RETURN on one normalised axis; return the shared basis.

    NOT_APPLICABLE gauges (VIX family, dollar index) — and non-index series such as yields,
    which never enter this registry — may sit next to either kind, so they are ignored here.
    Returns "PRICE" / "TOTAL_RETURN", or "NOT_APPLICABLE" when no return-bearing index is
    present.
    """
    return_bases = {
        index_basis(symbol) for symbol in symbols
        if index_basis(symbol) in {"PRICE", "TOTAL_RETURN"}
    }
    if len(return_bases) > 1:
        raise ValueError(
            "indices measure different things and cannot share one normalised axis: "
            + ", ".join(f"{symbol}={index_basis(symbol)}" for symbol in symbols)
        )
    return next(iter(return_bases)) if return_bases else "NOT_APPLICABLE"
GLOBAL_INDEX_SYMBOLS_BY_PROVIDER = MappingProxyType({
    provider: tuple(
        symbol for symbol, spec in GLOBAL_INDEX_REGISTRY.items()
        if spec["provider"] == provider
    )
    for provider in ("yahoo_chart_api", "cboe_index_history_csv")
})


def global_index_endpoint_window(symbol: str) -> EndpointWindowPolicy:
    return GLOBAL_INDEX_ENDPOINT_WINDOW_OVERRIDES.get(
        symbol, EndpointWindowPolicy.STRICT_EXCHANGE,
    )


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
        "and semiconductor benchmarks. Provider identity must match each symbol's "
        "registered Yahoo chart or Cboe public daily-history source before normalization."
    ),
    source="registered_global_index_provider", layer="normalized", storage_format="parquet", frequency="daily",
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

FRED_TREASURY_YIELD_EXT_DAILY = DatasetContract(
    name="fred_treasury_yield_ext_daily", version=1, status="active",
    description=(
        "Additional observed U.S. Treasury rates from FRED: DGS3 and DGS5 "
        "constant-maturity yields plus DTB3, the 3-month Treasury bill "
        "secondary-market discount rate used as a cash-yield proxy. Values are "
        "percent, and source holiday/non-observation markers remain nullable."
    ),
    source="fred", layer="normalized", storage_format="parquet", frequency="daily",
    timezone=None, primary_key=("date",), sort_key=("date",), partition_by=("year",),
    columns=(
        ColumnContract(
            "date", "date32", False,
            description="FRED observation date; rows may contain holiday nulls.",
        ),
        ColumnContract(
            "dgs3", "float64", True, "percent",
            "3-year Treasury constant-maturity rate, investment basis.",
        ),
        ColumnContract(
            "dgs5", "float64", True, "percent",
            "5-year Treasury constant-maturity rate, investment basis.",
        ),
        ColumnContract(
            "dtb3", "float64", True, "percent",
            "3-month Treasury bill secondary-market rate, discount basis; cash-yield proxy.",
        ),
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


US_VIX_TERM_STRUCTURE_DAILY = DatasetContract(
    name="us_vix_term_structure_daily", version=1, status="active",
    description=(
        "VIX 기간구조: 1개월/3개월 비율 < 1 = 콘탱고(평온), > 1 = "
        "백워데이션(공포). FRED VIXCLS와 Cboe 기간 지수를 날짜별로 "
        "결합한 재현 가능한 파생 데이터셋."
    ),
    source="fred_vix_daily+global_index_price_daily", layer="derived",
    storage_format="parquet", frequency="daily", timezone=None,
    primary_key=("date",), sort_key=("date",), partition_by=("year",), columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("vix", "float64", True, "index_points"),
        ColumnContract("vix9d", "float64", True, "index_points"),
        ColumnContract("vix3m", "float64", True, "index_points"),
        ColumnContract("vix6m", "float64", True, "index_points"),
        ColumnContract("skew", "float64", True, "index_points"),
        ColumnContract("ratio_1m_3m", "float64", True, "ratio"),
        ColumnContract("ratio_9d_1m", "float64", True, "ratio"),
        ColumnContract("regime", "string", True),
        ColumnContract("pct_rank_252", "float64", True, "fraction"),
    ),
)
