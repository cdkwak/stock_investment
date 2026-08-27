from __future__ import annotations

from datetime import datetime
from math import isfinite

import numpy as np
import pandas as pd


RSI14_FEATURE_VERSION = 1
RSI14_COLUMN = "rsi_14"
RSI14_PIT_STATUS = "PIT_SAFE_EOD_T_PLUS_1"
RSI14_SOURCE_DATASET = "kr_kospi200_index_daily"


def _dates(values: pd.Series) -> pd.Series:
    parsed: list[datetime] = []
    for value in values:
        if type(value) is not str:
            raise ValueError("RSI source dates must be canonical YYYY-MM-DD")
        try:
            item = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("RSI source dates must be canonical YYYY-MM-DD") from None
        if item.strftime("%Y-%m-%d") != value:
            raise ValueError("RSI source dates must be canonical YYYY-MM-DD")
        parsed.append(item)
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_gain == 0.0 and average_loss == 0.0:
        return 50.0
    if average_loss == 0.0:
        return 100.0
    if average_gain == 0.0:
        return 0.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def build_wilder_rsi14(source: pd.DataFrame) -> pd.DataFrame:
    """Build deterministic Wilder RSI14 with exact next-retained-session use."""
    required = {"date", "close", "ticker", "date_semantics"}
    if not isinstance(source, pd.DataFrame) or source.empty or not required.issubset(source.columns):
        raise ValueError("RSI source schema/content is invalid")
    if source.columns.has_duplicates:
        raise ValueError("RSI source columns must be unique")
    forbidden = {
        column for column in source.columns
        if type(column) is str and column.startswith(
            ("forward_", "future_", "label_", "outcome_")
        )
    }
    if forbidden:
        raise ValueError(f"outcome namespace is forbidden in RSI input: {sorted(forbidden)}")
    frame = source.reset_index(drop=True)
    dates = _dates(frame["date"])
    if dates.empty or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("RSI source dates must be unique and sorted")
    close = frame["close"]
    if (
        pd.api.types.is_bool_dtype(close.dtype)
        or pd.api.types.is_complex_dtype(close.dtype)
        or not pd.api.types.is_numeric_dtype(close.dtype)
    ):
        raise ValueError("RSI close must be real numeric, finite, and positive")
    values = close.to_numpy(dtype="float64", na_value=np.nan)
    if not np.isfinite(values).all() or (values <= 0.0).any():
        raise ValueError("RSI close must be real numeric, finite, and positive")
    for column, expected in (
        ("ticker", "1028"),
        ("date_semantics", "KRX_TRADING_DATE_DAILY_FINAL"),
    ):
        series = frame[column]
        if not series.map(lambda value: type(value) is str).all() or not series.eq(expected).all():
            raise ValueError("RSI source identity/date semantics differ")
    if len(values) < 16:
        return pd.DataFrame(columns=(
            "observation_date", "ticker", "date_semantics", "instrument_id",
            "observation_time", "available_at", "usable_from", RSI14_COLUMN,
            "source_dataset", "source_contract_version", "feature_version",
            "pit_status",
        ))

    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(gains[:14].mean())
    average_loss = float(losses[:14].mean())
    rsi = np.full(len(values), np.nan, dtype="float64")
    rsi[14] = _rsi_value(average_gain, average_loss)
    for index in range(15, len(values)):
        average_gain = (average_gain * 13.0 + float(gains[index - 1])) / 14.0
        average_loss = (average_loss * 13.0 + float(losses[index - 1])) / 14.0
        rsi[index] = _rsi_value(average_gain, average_loss)
    if not all(isfinite(float(value)) and 0.0 <= value <= 100.0 for value in rsi[14:]):
        raise ValueError("RSI output must remain finite within 0..100")

    output = pd.DataFrame({
        "observation_date": dates.dt.strftime("%Y-%m-%d"),
        "ticker": frame["ticker"],
        "date_semantics": frame["date_semantics"],
        "instrument_id": "KRX:1028",
        "observation_time": dates.dt.strftime("%Y-%m-%d") + "T15:30:00+09:00",
        "available_at": dates.dt.strftime("%Y-%m-%d") + "T15:30:00+09:00",
        "usable_from": dates.shift(-1).dt.strftime("%Y-%m-%d") + "T09:00:00+09:00",
        RSI14_COLUMN: rsi,
        "source_dataset": RSI14_SOURCE_DATASET,
        "source_contract_version": 1,
        "feature_version": RSI14_FEATURE_VERSION,
        "pit_status": RSI14_PIT_STATUS,
    })
    output = output.iloc[14:-1].reset_index(drop=True)
    if output.empty or not (
        pd.to_datetime(output["available_at"], utc=True)
        < pd.to_datetime(output["usable_from"], utc=True)
    ).all():
        raise ValueError("RSI T+1 availability clock is invalid")
    return output


__all__ = [
    "RSI14_COLUMN", "RSI14_FEATURE_VERSION", "RSI14_PIT_STATUS",
    "RSI14_SOURCE_DATASET", "build_wilder_rsi14",
]
