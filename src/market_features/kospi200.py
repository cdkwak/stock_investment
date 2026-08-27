from __future__ import annotations

from datetime import datetime
from math import sqrt

import numpy as np
import pandas as pd

from .types import FeatureDefinition


_SOURCE = "kr_kospi200_index_daily"
_FORBIDDEN_LABEL_COLUMNS = frozenset({"mae_20d", "mfe_20d", "label_available_at", "label_version"})
FEATURE_DEFINITIONS = (
    FeatureDefinition("return_5d", 1, 5, "DROP_UNTIL_LOOKBACK_COMPLETE", _SOURCE, 1, "PIT_SAFE_EOD_T_PLUS_1"),
    FeatureDefinition("return_20d", 1, 20, "DROP_UNTIL_LOOKBACK_COMPLETE", _SOURCE, 1, "PIT_SAFE_EOD_T_PLUS_1"),
    FeatureDefinition("return_60d", 1, 60, "DROP_UNTIL_LOOKBACK_COMPLETE", _SOURCE, 1, "PIT_SAFE_EOD_T_PLUS_1"),
    FeatureDefinition("realized_volatility_20d", 1, 20, "DROP_UNTIL_LOOKBACK_COMPLETE", _SOURCE, 1, "PIT_SAFE_EOD_T_PLUS_1"),
    FeatureDefinition("ma_distance_60d", 1, 60, "DROP_UNTIL_LOOKBACK_COMPLETE", _SOURCE, 1, "PIT_SAFE_EOD_T_PLUS_1"),
    FeatureDefinition("rolling_drawdown_60d", 1, 60, "DROP_UNTIL_LOOKBACK_COMPLETE", _SOURCE, 1, "PIT_SAFE_EOD_T_PLUS_1"),
)


def _validated_source_dates(values: pd.Series) -> pd.Series:
    message = "source dates must be canonical YYYY-MM-DD strings"
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


def build_kospi200_features(source: pd.DataFrame) -> pd.DataFrame:
    """Build the frozen Phase-1 slice; never performs I/O or calendar inference."""
    required = {"date", "close", "ticker", "date_semantics"}
    if not required.issubset(source.columns) or source.empty:
        raise ValueError("KOSPI200 feature source schema/content is invalid")
    forbidden = {
        column for column in source.columns
        if column.startswith(("forward_", "label_")) or column in _FORBIDDEN_LABEL_COLUMNS
    }
    if forbidden:
        raise ValueError(f"label namespace is forbidden in feature input: {sorted(forbidden)}")
    frame = source.loc[:, ["date", "close", "ticker", "date_semantics"]].copy()
    dates = _validated_source_dates(frame["date"])
    close = pd.to_numeric(frame["close"], errors="raise").astype("float64")
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("source dates must be unique and sorted")
    if (
        not frame["ticker"].map(lambda value: isinstance(value, str)).all()
        or not frame["ticker"].eq("1028").all()
        or not frame["date_semantics"].map(lambda value: isinstance(value, str)).all()
        or not frame["date_semantics"].eq("KRX_TRADING_DATE_DAILY_FINAL").all()
    ):
        raise ValueError("source identity/date semantics differ")
    if not np.isfinite(close).all() or close.le(0).any():
        raise ValueError("close must be positive and finite")

    daily = close.pct_change(fill_method=None)
    result = pd.DataFrame({
        "observation_date": dates.dt.strftime("%Y-%m-%d"),
        "ticker": frame["ticker"],
        "date_semantics": frame["date_semantics"],
        "observation_time": dates.dt.strftime("%Y-%m-%d") + "T15:30:00+09:00",
        "available_at": dates.dt.strftime("%Y-%m-%d") + "T15:30:00+09:00",
        "usable_from": dates.shift(-1).dt.strftime("%Y-%m-%d") + "T09:00:00+09:00",
        "return_5d": close.pct_change(5, fill_method=None),
        "return_20d": close.pct_change(20, fill_method=None),
        "return_60d": close.pct_change(60, fill_method=None),
        "realized_volatility_20d": daily.rolling(20, min_periods=20).std(ddof=1) * sqrt(252.0),
        "ma_distance_60d": close / close.rolling(60, min_periods=60).mean() - 1.0,
        "rolling_drawdown_60d": close / close.rolling(60, min_periods=60).max() - 1.0,
    })
    result["source_dataset"] = _SOURCE
    result["source_contract_version"] = 1
    result["feature_set_version"] = 1
    result["pit_status"] = "PIT_SAFE_EOD_T_PLUS_1"
    feature_columns = [definition.feature_name for definition in FEATURE_DEFINITIONS]
    result = result.dropna(subset=feature_columns + ["usable_from"]).reset_index(drop=True)
    if not np.isfinite(result[feature_columns].to_numpy(dtype="float64")).all():
        raise ValueError("feature output contains non-finite values")
    if not (
        pd.to_datetime(result["available_at"], utc=True)
        < pd.to_datetime(result["usable_from"], utc=True)
    ).all():
        raise ValueError("feature T+1 availability clock is invalid")
    return result
