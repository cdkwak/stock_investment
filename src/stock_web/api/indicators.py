"""Pure pandas technical indicators for the local Market chart.

The functions in this module are provider-free and never mutate their input.
Indicators are calculated after candle resampling so weekly and monthly views
describe weekly and monthly bars rather than sampled daily calculations.
"""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


INDICATOR_NAMES = frozenset({
    "ma5", "ma20", "ma60", "ma120", "bollinger", "volume",
    "rsi14", "macd", "stochastic",
})
DEFAULT_INDICATORS = ("ma5", "ma20", "ma60", "ma120", "volume")


def normalize_indicators(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Return unique, allowlisted indicator ids in caller order."""
    if value is None:
        values = DEFAULT_INDICATORS
    elif isinstance(value, str):
        values = (part.strip().lower() for part in value.split(","))
    else:
        values = (str(part).strip().lower() for part in value)
    return tuple(dict.fromkeys(item for item in values if item in INDICATOR_NAMES))


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI with an explicit arithmetic seed and recursive smoothing."""
    values = pd.to_numeric(close, errors="coerce").astype(float)
    delta = values.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    average_gain = pd.Series(np.nan, index=values.index, dtype=float)
    average_loss = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) <= period:
        return average_gain

    average_gain.iloc[period] = gains.iloc[1:period + 1].mean()
    average_loss.iloc[period] = losses.iloc[1:period + 1].mean()
    for offset in range(period + 1, len(values)):
        average_gain.iloc[offset] = (
            average_gain.iloc[offset - 1] * (period - 1) + gains.iloc[offset]
        ) / period
        average_loss.iloc[offset] = (
            average_loss.iloc[offset - 1] * (period - 1) + losses.iloc[offset]
        ) / period

    denominator = average_gain + average_loss
    rsi = 100.0 * average_gain / denominator
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100.0)
    rsi = rsi.mask((average_gain == 0) & (average_loss > 0), 0.0)
    return rsi.mask((average_gain == 0) & (average_loss == 0), 50.0)


def calculate_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Add MA, Bollinger, RSI, MACD, and stochastic columns to OHLCV bars."""
    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce").astype(float)
    high = pd.to_numeric(out.get("high", close), errors="coerce").astype(float)
    low = pd.to_numeric(out.get("low", close), errors="coerce").astype(float)

    for window in (5, 20, 60, 120):
        out[f"ma{window}"] = close.rolling(window, min_periods=window).mean()

    out["bollinger_mid"] = out["ma20"]
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    out["bollinger_upper"] = out["bollinger_mid"] + 2.0 * deviation
    out["bollinger_lower"] = out["bollinger_mid"] - 2.0 * deviation
    out["rsi14"] = _wilder_rsi(close, 14)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_histogram"] = out["macd"] - out["macd_signal"]

    lowest = low.rolling(14, min_periods=14).min()
    highest = high.rolling(14, min_periods=14).max()
    spread = (highest - lowest).replace(0.0, np.nan)
    out["stochastic_k"] = (close - lowest) / spread * 100.0
    out["stochastic_d"] = out["stochastic_k"].rolling(3, min_periods=3).mean()
    return out


def resample_ohlcv(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Resample daily bars to actual last-session weekly/monthly OHLCV bars."""
    if interval == "1d":
        return frame.sort_values("date").reset_index(drop=True).copy()
    if interval not in {"1w", "1M"}:
        raise ValueError(f"지원하지 않는 봉 간격입니다: {interval}")

    daily = frame.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date", "close"]).sort_values("date")
    for column in ("open", "high", "low"):
        if column not in daily:
            daily[column] = daily["close"]
    if "volume" not in daily:
        daily["volume"] = np.nan
    periods = daily["date"].dt.to_period("W-FRI" if interval == "1w" else "M")

    rows: list[dict[str, object]] = []
    for _period, group in daily.groupby(periods, sort=True):
        numeric_volume = pd.to_numeric(group["volume"], errors="coerce")
        rows.append({
            "date": group["date"].iloc[-1],
            "open": pd.to_numeric(group["open"], errors="coerce").iloc[0],
            "high": pd.to_numeric(group["high"], errors="coerce").max(),
            "low": pd.to_numeric(group["low"], errors="coerce").min(),
            "close": pd.to_numeric(group["close"], errors="coerce").iloc[-1],
            "volume": numeric_volume.sum(min_count=1),
        })
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


__all__ = [
    "DEFAULT_INDICATORS", "INDICATOR_NAMES", "calculate_indicators",
    "normalize_indicators", "resample_ohlcv",
]
