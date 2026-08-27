from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION


YAHOO_15M_IDENTITIES = {
    "NQ=F": ("CME", "FUTURE_CONTINUOUS", "GLOBAL_CONTINUOUS"),
    "^IXIC": ("US_INDEX", "INDEX", "REGULAR"),
    "^GSPC": ("US_INDEX", "INDEX", "REGULAR"),
    "^VIX": ("CBOE", "VOLATILITY_INDEX", "REGULAR"),
    "^FVX": ("CBOE", "TREASURY_YIELD_INDEX", "REGULAR"),
    "^TNX": ("CBOE", "TREASURY_YIELD_INDEX", "REGULAR"),
    "^TYX": ("CBOE", "TREASURY_YIELD_INDEX", "REGULAR"),
}
YAHOO_15M_GRID_OFFSET_MINUTES = {
    series_id: 5 if series_id in {"^FVX", "^TNX", "^TYX"} else 0
    for series_id in YAHOO_15M_IDENTITIES
}
DELAYED_CLASSIFICATION = "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME"


@dataclass(frozen=True)
class BarCompleteness:
    expected_bars: int
    observed_bars: int
    missing_bars: tuple[pd.Timestamp, ...]
    unexpected_bars: tuple[pd.Timestamp, ...]
    status: str


def yahoo_native_15m_grid_aligned(series_id: str, value: object) -> bool:
    """Return whether a timestamp matches its reviewed identity-bound grid."""

    expected_offset = YAHOO_15M_GRID_OFFSET_MINUTES.get(str(series_id))
    if expected_offset is None:
        return False
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return False
    if timestamp.tzinfo is None:
        return False
    minute_ns = 60 * 1_000_000_000
    if timestamp.value % minute_ns:
        return False
    interval_ns = 15 * 60 * 1_000_000_000
    return timestamp.value % interval_ns == expected_offset * minute_ns


def audit_market_15m_bars(
    observed: Sequence[datetime], expected: Sequence[datetime]
) -> BarCompleteness:
    observed_values = tuple(pd.Timestamp(value).tz_convert("UTC") for value in observed)
    expected_values = tuple(pd.Timestamp(value).tz_convert("UTC") for value in expected)
    observed_set, expected_set = set(observed_values), set(expected_values)
    missing = tuple(sorted(expected_set - observed_set))
    unexpected = tuple(sorted(observed_set - expected_set))
    duplicates = len(observed_values) != len(observed_set)
    return BarCompleteness(
        expected_bars=len(expected_values),
        observed_bars=len(observed_values),
        missing_bars=missing,
        unexpected_bars=unexpected,
        status="COMPLETE" if not missing and not unexpected and not duplicates else "INCOMPLETE",
    )


def validate_market_price_15m(dataframe: pd.DataFrame) -> None:
    contract = MARKET_PRICE_15M_OBSERVATION
    if dataframe.empty or list(dataframe.columns) != list(contract.column_names):
        raise ValueError("15m schema invalid or empty")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate 15m observation key")
    required = [
        "market_date", "market", "series_id", "provider_symbol", "instrument_type",
        "bar_start", "bar_end", "source_timezone", "display_timezone", "session",
        "interval", "provider", "data_availability", "retrieved_at",
    ]
    if dataframe[required].isna().any().any():
        raise ValueError("15m identity/provenance is null")
    if not dataframe["interval"].eq("15m").all():
        raise ValueError("15m observations require native 15m interval")
    if not dataframe["provider"].eq("yahoo_chart_api").all():
        raise ValueError("15m provider differs")
    if not dataframe["data_availability"].eq(DELAYED_CLASSIFICATION).all():
        raise ValueError("15m delayed availability classification differs")

    for row in dataframe.itertuples(index=False):
        identity = YAHOO_15M_IDENTITIES.get(str(row.series_id))
        if identity is None or str(row.provider_symbol) != str(row.series_id):
            raise ValueError("15m identity is not allowlisted")
        if (str(row.market), str(row.instrument_type), str(row.session)) != identity:
            raise ValueError("15m identity semantics differ")
        try:
            source_zone = ZoneInfo(str(row.source_timezone))
            ZoneInfo(str(row.display_timezone))
        except ZoneInfoNotFoundError as error:
            raise ValueError("15m timezone is invalid") from error
        start = pd.Timestamp(row.bar_start)
        market_date = pd.Timestamp(row.market_date).date()
        if start.tzinfo is None or start.tz_convert(source_zone).date() != market_date:
            raise ValueError("15m market date differs from source timezone")

    starts = pd.to_datetime(dataframe["bar_start"], utc=True, errors="coerce")
    ends = pd.to_datetime(dataframe["bar_end"], utc=True, errors="coerce")
    retrieved = pd.to_datetime(dataframe["retrieved_at"], utc=True, errors="coerce")
    if starts.isna().any() or ends.isna().any() or retrieved.isna().any():
        raise ValueError("15m timestamps are invalid")
    if not (ends - starts).dt.total_seconds().eq(15 * 60).all():
        raise ValueError("15m bar duration differs")
    if not all(
        yahoo_native_15m_grid_aligned(series_id, start)
        for series_id, start in zip(dataframe["series_id"], starts, strict=True)
    ):
        raise ValueError("15m bar start is off-grid")
    if not (ends <= retrieved).all():
        raise ValueError("live-forming 15m bar is retained")

    numeric = dataframe[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    prices = numeric[["open", "high", "low", "close"]]
    if prices.isna().any().any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("15m OHLC contains missing or infinite values")
    if ((prices.high < prices.low) | ~prices.open.between(prices.low, prices.high)
            | ~prices.close.between(prices.low, prices.high)).any():
        raise ValueError("15m OHLC relationship is invalid")
    if (numeric.volume.dropna() < 0).any():
        raise ValueError("15m volume is negative")


__all__ = [
    "BarCompleteness", "DELAYED_CLASSIFICATION", "YAHOO_15M_IDENTITIES",
    "YAHOO_15M_GRID_OFFSET_MINUTES", "audit_market_15m_bars",
    "validate_market_price_15m", "yahoo_native_15m_grid_aligned",
]
