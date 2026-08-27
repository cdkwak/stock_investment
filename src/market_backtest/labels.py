from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd


LABEL_HORIZONS_TRADING_DAYS = (5, 20, 60)
MAX_LABEL_HORIZON_TRADING_DAYS = max(LABEL_HORIZONS_TRADING_DAYS)
SOURCE_IDENTITY_COLUMNS = ("ticker", "date_semantics")


LABEL_NAMESPACE = frozenset({
    *(f"forward_return_{horizon}d" for horizon in LABEL_HORIZONS_TRADING_DAYS),
    *(f"forward_max_drawdown_{horizon}d" for horizon in LABEL_HORIZONS_TRADING_DAYS),
    "mae_20d", "mfe_20d", "label_available_at", "label_version",
})


def _validated_source_dates(values: pd.Series) -> pd.Series:
    message = "label source dates must be canonical YYYY-MM-DD strings"
    if not isinstance(values, pd.Series):
        raise ValueError(message)
    parsed: list[datetime] = []
    for value in values:
        if type(value) is not str:
            raise ValueError(message)
        try:
            date_value = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(message) from None
        if date_value.strftime("%Y-%m-%d") != value:
            raise ValueError(message)
        parsed.append(date_value)
    try:
        return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")
    except (OverflowError, TypeError, ValueError):
        raise ValueError(message) from None


def _validated_source_identity(
    frame: pd.DataFrame, *, artifact: str,
) -> tuple[str, str]:
    if frame.empty or not set(SOURCE_IDENTITY_COLUMNS).issubset(frame.columns):
        raise ValueError(f"{artifact} source identity schema/content is invalid")
    identity = []
    for column in SOURCE_IDENTITY_COLUMNS:
        values = frame[column]
        if not values.map(
            lambda value: isinstance(value, str) and bool(value.strip())
        ).all():
            raise ValueError(f"{artifact} source identity must be exact and non-empty")
        unique = values.drop_duplicates()
        if len(unique) != 1:
            raise ValueError(f"{artifact} source identity must be constant")
        identity.append(unique.iloc[0])
    return identity[0], identity[1]


def build_forward_labels(source: pd.DataFrame) -> pd.DataFrame:
    """Create outcome-only labels kept outside feature/simulation inputs."""
    if not {"date", "close", *SOURCE_IDENTITY_COLUMNS}.issubset(source.columns) or source.empty:
        raise ValueError("label source schema/content is invalid")
    dates = _validated_source_dates(source["date"])
    ticker, date_semantics = _validated_source_identity(source, artifact="label source")
    close = pd.to_numeric(source["close"], errors="raise").astype("float64")
    if (
        dates.duplicated().any() or not dates.is_monotonic_increasing
        or not np.isfinite(close).all() or close.le(0).any()
    ):
        raise ValueError("label source key/value is invalid")
    output = pd.DataFrame({
        "observation_date": dates.dt.strftime("%Y-%m-%d"),
        "ticker": ticker,
        "date_semantics": date_semantics,
    })
    for horizon in LABEL_HORIZONS_TRADING_DAYS:
        output[f"forward_return_{horizon}d"] = close.shift(-horizon) / close - 1.0
        future = pd.concat([close.shift(-step) / close - 1.0 for step in range(1, horizon + 1)], axis=1)
        output[f"forward_max_drawdown_{horizon}d"] = future.min(axis=1, skipna=False)
    forward_20 = pd.concat([close.shift(-step) / close - 1.0 for step in range(1, 21)], axis=1)
    output["mae_20d"] = forward_20.min(axis=1, skipna=False)
    output["mfe_20d"] = forward_20.max(axis=1, skipna=False)
    output["label_available_at"] = dates.shift(-MAX_LABEL_HORIZON_TRADING_DAYS).dt.strftime("%Y-%m-%d") + "T15:30:00+09:00"
    output["label_version"] = 1
    output = output.dropna().reset_index(drop=True)
    numeric = output.select_dtypes(include=["number"]).drop(columns=["label_version"])
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("label output contains non-finite values")
    return output


__all__ = [
    "LABEL_HORIZONS_TRADING_DAYS", "LABEL_NAMESPACE",
    "MAX_LABEL_HORIZON_TRADING_DAYS", "SOURCE_IDENTITY_COLUMNS",
    "build_forward_labels",
]
