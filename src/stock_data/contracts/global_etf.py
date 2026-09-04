from types import MappingProxyType
from typing import Mapping

from stock_data.contracts.base import ColumnContract, DatasetContract


GLOBAL_ETF_PRICE_DAILY = DatasetContract(
    name="global_etf_price_daily", version=1, status="active",
    description=(
        "Daily provider OHLCV and separately retained adjusted close for explicitly "
        "registered exchange-traded funds. Provider ticker, ETF type, currency, exchange, "
        "and daily granularity are validated before normalization. ETFs are never "
        "represented as indices."
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


# This is the single symbol/identity authority for the Yahoo daily ETF lane.
# A symbol absent here must be rejected before a provider request is attempted.
GLOBAL_ETF_REGISTRY: Mapping[str, Mapping[str, object]] = MappingProxyType({
    "SOXX": MappingProxyType({
        "source_ticker": "SOXX", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "iShares",
        "official_fund_name": "iShares Semiconductor ETF",
        "official_exchange": "NASDAQ", "official_cusip": "464287523",
        "official_identity_url": "https://www.ishares.com/us/products/239705/SOXX",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "EWY": MappingProxyType({
        "source_ticker": "EWY", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "iShares",
        "official_fund_name": "iShares MSCI South Korea ETF",
        "official_exchange": "NYSE Arca",
        "official_identity_url": (
            "https://www.ishares.com/us/products/239681/"
            "ishares-msci-south-korea-etf"
        ),
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("PCX", "NYSEArca", "NYSE Arca"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "SOXL": MappingProxyType({
        "source_ticker": "SOXL", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "Direxion",
        "official_fund_name": "Direxion Daily Semiconductor Bull 3X Shares",
        "official_exchange": "NYSE Arca",
        "official_identity_url": "https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("PCX", "NYSEArca", "NYSE Arca"),
        "leverage_multiple": 3,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "TQQQ": MappingProxyType({
        "source_ticker": "TQQQ", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "ProShares",
        "official_fund_name": "ProShares UltraPro QQQ",
        "official_exchange": "NASDAQ",
        "official_identity_url": "https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 3,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "QLD": MappingProxyType({
        "source_ticker": "QLD", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "ProShares",
        "official_fund_name": "ProShares Ultra QQQ",
        "official_exchange": "NYSE Arca",
        "official_identity_url": "https://www.proshares.com/our-etfs/leveraged-and-inverse/qld",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("PCX", "NYSEArca", "NYSE Arca"),
        "leverage_multiple": 2,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "TLT": MappingProxyType({
        "source_ticker": "TLT", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "iShares",
        "official_fund_name": "iShares 20+ Year Treasury Bond ETF",
        "official_exchange": "NASDAQ",
        "official_identity_url": "https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "QQQ": MappingProxyType({
        "source_ticker": "QQQ", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "Invesco",
        "official_fund_name": "Invesco QQQ Trust, Series 1",
        "official_exchange": "NASDAQ",
        "official_identity_url": "https://www.invesco.com/qqq-etf/en/home.html",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "SPY": MappingProxyType({
        "source_ticker": "SPY", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "State Street Global Advisors",
        "official_fund_name": "SPDR S&P 500 ETF Trust",
        "official_exchange": "NYSE Arca",
        "official_identity_url": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("PCX", "NYSEArca", "NYSE Arca"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "SGOV": MappingProxyType({
        "source_ticker": "SGOV", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "iShares",
        "official_fund_name": "iShares 0-3 Month Treasury Bond ETF",
        "official_exchange": "NYSE Arca", "official_cusip": "46436E718",
        "official_identity_url": "https://www.ishares.com/us/products/314116/ishares-0-3-month-treasury-bond-etf",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("PCX", "NYSEArca", "NYSE Arca", "NYQ", "NYSE"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "VGLT": MappingProxyType({
        "source_ticker": "VGLT", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "Vanguard",
        "official_fund_name": "Vanguard Long-Term Treasury ETF",
        "official_exchange": "NASDAQ", "official_cusip": "92206C870",
        "official_identity_url": "https://investor.vanguard.com/investment-products/etfs/profile/vglt",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "VNQ": MappingProxyType({
        "source_ticker": "VNQ", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "Vanguard",
        "official_fund_name": "Vanguard Real Estate ETF",
        "official_exchange": "NYSE Arca", "official_cusip": "922908553",
        "official_identity_url": "https://investor.vanguard.com/investment-products/etfs/profile/vnq",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("PCX", "NYSEArca", "NYSE Arca"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "IEF": MappingProxyType({
        "source_ticker": "IEF", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "iShares",
        "official_fund_name": "iShares 7-10 Year Treasury Bond ETF",
        "official_exchange": "NASDAQ", "official_cusip": "464287440",
        "official_identity_url": "https://www.ishares.com/us/products/239456/ishares-710-year-treasury-bond-etf",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
    "SHY": MappingProxyType({
        "source_ticker": "SHY", "provider": "yahoo_chart_api",
        "instrument_type": "ETF", "issuer": "iShares",
        "official_fund_name": "iShares 1-3 Year Treasury Bond ETF",
        "official_exchange": "NASDAQ", "official_cusip": "464287457",
        "official_identity_url": "https://www.ishares.com/us/products/239452/ishares-13-year-treasury-bond-etf",
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "leverage_multiple": 1,
        "cadence": "GLOBAL_DAILY", "freshness_policy": "reviewed_us_completed_session",
        "validation": "global_etf_price_daily_v1", "automation_enabled": True,
    }),
})

GLOBAL_ETF_DAILY_SYMBOLS = tuple(GLOBAL_ETF_REGISTRY)


def global_etf_leverage_multiple(symbol: str) -> int:
    """Return the explicit exposure multiple for a registered daily ETF."""

    key = str(symbol).strip().upper()
    try:
        value = GLOBAL_ETF_REGISTRY[key]["leverage_multiple"]
    except KeyError as error:
        raise ValueError(f"unregistered global ETF: {key}") from error
    if type(value) is not int or value < 1:
        raise ValueError(f"invalid leverage multiple for global ETF: {key}")
    return value


__all__ = [
    "GLOBAL_ETF_DAILY_SYMBOLS", "GLOBAL_ETF_PRICE_DAILY", "GLOBAL_ETF_REGISTRY",
    "global_etf_leverage_multiple",
]
