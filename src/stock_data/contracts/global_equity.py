from types import MappingProxyType
from typing import Mapping

from stock_data.contracts.base import ColumnContract, DatasetContract


GLOBAL_EQUITY_PRICE_DAILY = DatasetContract(
    name="global_equity_price_daily", version=1, status="active",
    description=(
        "Daily provider OHLCV and separately retained adjusted close for explicitly "
        "registered U.S. equities and depositary receipts. Provider ticker, instrument "
        "type, currency, exchange, and daily granularity are validated before normalization."
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


# Single identity authority for the Yahoo daily U.S. equity lane.  ADR ratio is
# intentionally unknown and must remain None until authoritative evidence exists.
GLOBAL_EQUITY_REGISTRY: Mapping[str, Mapping[str, object]] = MappingProxyType({
    "SKHY": MappingProxyType({
        "source_ticker": "SKHY",
        "provider": "yahoo_chart_api",
        "instrument_type": "EQUITY",
        "security_type": "DEPOSITARY_RECEIPT",
        "official_name": "SK hynix Inc. ADR",
        "korean_name": "SK하이닉스(ADR)",
        "official_exchange": "NASDAQ",
        "isin": "US78392B2060",
        "underlying_kr_symbol": "000660",
        "adr_ratio": None,
        "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NMS", "NGM", "NASDAQ", "NasdaqGM"),
        "cadence": "GLOBAL_DAILY",
        "automation_enabled": True,
    }),
})

GLOBAL_EQUITY_DAILY_SYMBOLS = tuple(GLOBAL_EQUITY_REGISTRY)


__all__ = [
    "GLOBAL_EQUITY_DAILY_SYMBOLS",
    "GLOBAL_EQUITY_PRICE_DAILY",
    "GLOBAL_EQUITY_REGISTRY",
]
