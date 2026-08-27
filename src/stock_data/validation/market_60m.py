from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION


GLOBAL_CONTINUOUS_IDENTITIES = frozenset({
    ("KOSPI_CURRENT_60M", "INDEX", "^KS11"),
    ("KOSDAQ_CURRENT_60M", "INDEX", "^KQ11"),
    ("USD_KRW_60M", "FOREX", "KRW=X"),
    ("UST2_FUTURES_60M", "FUTURE_CONTINUOUS", "ZT=F"),
    ("UST10_FUTURES_60M", "FUTURE_CONTINUOUS", "ZN=F"),
    ("UST30_FUTURES_60M", "FUTURE_CONTINUOUS", "ZB=F"),
    ("SP500_CURRENT_60M", "INDEX", "^GSPC"),
    ("NASDAQ_CURRENT_60M", "INDEX", "^IXIC"),
    ("NQ_FUTURES_CURRENT_60M", "FUTURE_CONTINUOUS", "NQ=F"),
    ("SOXX_CURRENT_60M", "ETF", "SOXX"),
    ("GOLD_CURRENT_60M", "FUTURE_CONTINUOUS", "GC=F"),
    ("WTI_CURRENT_60M", "FUTURE_CONTINUOUS", "CL=F"),
    ("BITCOIN_CURRENT_60M", "CRYPTOCURRENCY", "BTC-USD"),
})


@dataclass(frozen=True)
class SessionCompleteness:
    expected_bars: int
    observed_bars: int
    missing_bars: tuple[pd.Timestamp, ...]
    duplicate_bars: tuple[pd.Timestamp, ...]
    unexpected_bars: tuple[pd.Timestamp, ...]
    status: str


def audit_session_bars(
    observed_bar_starts: Sequence[datetime], expected_bar_starts: Sequence[datetime],
) -> SessionCompleteness:
    """Compare one provider/symbol/date/session without hiding duplicates."""
    observed = tuple(pd.Timestamp(value).tz_convert("UTC") for value in observed_bar_starts)
    expected = tuple(pd.Timestamp(value).tz_convert("UTC") for value in expected_bar_starts)
    observed_set, expected_set = set(observed), set(expected)
    duplicates = tuple(sorted({value for value in observed if observed.count(value) > 1}))
    missing = tuple(sorted(expected_set - observed_set))
    unexpected = tuple(sorted(observed_set - expected_set))
    return SessionCompleteness(
        expected_bars=len(expected), observed_bars=len(observed),
        missing_bars=missing, duplicate_bars=duplicates, unexpected_bars=unexpected,
        status="COMPLETE" if not missing and not duplicates and not unexpected else "INCOMPLETE",
    )


def validate_market_price_60m(dataframe: pd.DataFrame) -> None:
    if list(dataframe.columns) != list(MARKET_PRICE_60M_OBSERVATION.column_names) or dataframe.empty:
        raise ValueError("60m schema invalid or empty")
    if dataframe.duplicated(list(MARKET_PRICE_60M_OBSERVATION.primary_key)).any():
        raise ValueError("duplicate 60m observation key")
    identity = dataframe[[
        "market_date", "market", "symbol", "asset_type", "bar_start", "bar_end",
        "timezone", "session", "interval", "provider", "provider_symbol",
        "adjustment_status", "retrieved_at", "fallback_used",
    ]]
    if identity.isna().any().any():
        raise ValueError("60m identity/provenance is null")
    if not dataframe["interval"].eq("60m").all():
        raise ValueError("60m observations require the 60m interval")
    sessions = set(dataframe["session"].astype(str))
    if not sessions.issubset({"REGULAR", "GLOBAL_CONTINUOUS"}):
        raise ValueError("unsupported 60m session")
    continuous = dataframe["session"].eq("GLOBAL_CONTINUOUS")
    if continuous.any():
        identities = set(zip(
            dataframe.loc[continuous, "symbol"].astype(str),
            dataframe.loc[continuous, "asset_type"].astype(str),
            dataframe.loc[continuous, "provider_symbol"].astype(str),
        ))
        if not identities.issubset(GLOBAL_CONTINUOUS_IDENTITIES):
            raise ValueError("global-continuous 60m identity is not allowlisted")
        if not dataframe.loc[continuous, "adjustment_status"].eq(
            "PROVIDER_UNADJUSTED_INTRADAY_DELAYED"
        ).all():
            raise ValueError("global-continuous 60m rows must retain delayed provenance")
    starts = pd.to_datetime(dataframe["bar_start"], utc=True, errors="coerce")
    ends = pd.to_datetime(dataframe["bar_end"], utc=True, errors="coerce")
    retrieved = pd.to_datetime(dataframe["retrieved_at"], utc=True, errors="coerce")
    if starts.isna().any() or ends.isna().any() or retrieved.isna().any() or not (ends > starts).all():
        raise ValueError("60m timestamps are invalid")
    if (ends > retrieved).any():
        raise ValueError("60m bar end exceeds retrieval time")
    declared_timezones: dict[str, ZoneInfo] = {}
    for timezone_name in dataframe["timezone"].astype(str).unique():
        try:
            declared_timezones[timezone_name] = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("60m timezone is not a valid IANA zone") from None
    market_dates = pd.to_datetime(
        dataframe["market_date"], errors="coerce"
    )
    if market_dates.isna().any():
        raise ValueError("60m market date is invalid")
    expected_market_dates = pd.Series(
        (
            start.tz_convert(declared_timezones[timezone_name]).date()
            for start, timezone_name in zip(
                starts, dataframe["timezone"].astype(str), strict=True,
            )
        ),
        index=dataframe.index,
    )
    if not market_dates.dt.date.eq(expected_market_dates).all():
        raise ValueError("60m market date differs from local bar start")
    durations = (ends - starts).dt.total_seconds().div(60)
    declared = pd.to_numeric(dataframe["actual_duration_minutes"], errors="coerce")
    if declared.isna().any() or not declared.eq(durations).all() or not declared.between(1, 60).all():
        raise ValueError("60m actual duration differs from timestamps")
    numeric = dataframe[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    prices = numeric[["open", "high", "low", "close"]]
    if prices.isna().any().any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("60m OHLC contains missing or infinite values")
    if ((prices.high < prices.low) | ~prices.open.between(prices.low, prices.high)
            | ~prices.close.between(prices.low, prices.high)).any():
        raise ValueError("60m OHLC relationship is invalid")
    if (numeric.volume.dropna() < 0).any():
        raise ValueError("60m volume is negative")
    fallback = dataframe["fallback_used"].astype(bool)
    if dataframe.loc[fallback, "fallback_reason"].isna().any() or dataframe.loc[~fallback, "fallback_reason"].notna().any():
        raise ValueError("60m fallback reason/status mismatch")


def select_complete_session_provider(
    observations: pd.DataFrame,
    *,
    expected_bar_starts: Mapping[tuple[str, object], Sequence[datetime]],
    provider_priority: Sequence[str],
) -> pd.DataFrame:
    """Select one complete provider per symbol/date; never stitch individual bars."""
    validate_market_price_60m(observations)
    selected = []
    for (symbol, market_date), expected_values in sorted(expected_bar_starts.items()):
        expected = {pd.Timestamp(value).tz_convert("UTC") for value in expected_values}
        choice = None
        for provider in provider_priority:
            candidate = observations.loc[
                observations["symbol"].eq(symbol)
                & observations["market_date"].eq(market_date)
                & observations["provider"].eq(provider)
            ].copy()
            observed = set(pd.to_datetime(candidate["bar_start"], utc=True))
            if observed == expected:
                choice = candidate
                break
        if choice is None:
            raise ValueError(f"no complete 60m provider session: {symbol} {market_date}")
        used_fallback = choice["provider"].iloc[0] != provider_priority[0]
        choice["fallback_used"] = used_fallback
        choice["fallback_reason"] = "PRIMARY_SESSION_INCOMPLETE" if used_fallback else None
        selected.append(choice)
    result = pd.concat(selected, ignore_index=True).sort_values(
        ["market_date", "symbol", "bar_start"], kind="stable"
    ).reset_index(drop=True)
    validate_market_price_60m(result)
    return result


__all__ = [
    "SessionCompleteness", "audit_session_bars", "select_complete_session_provider",
    "validate_market_price_60m",
]
