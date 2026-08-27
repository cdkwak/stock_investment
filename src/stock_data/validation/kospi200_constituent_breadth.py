from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.kospi200_constituent_breadth import (
    KR_INDEX_CONSTITUENT_DAILY,
    KR_KOSPI200_BREADTH_DAILY,
    KR_KOSPI200_CONSTITUENT_PRICE_DAILY,
)


class KOSPI200ScopeValidationError(ValueError):
    pass


def _base(frame: pd.DataFrame, contract) -> None:
    if list(frame.columns) != list(contract.column_names) or frame.empty:
        raise KOSPI200ScopeValidationError(f"{contract.name} schema is invalid or empty")
    if frame.duplicated(list(contract.primary_key)).any():
        raise KOSPI200ScopeValidationError(f"{contract.name} has duplicate primary keys")
    expected = frame.sort_values(list(contract.sort_key), kind="stable").index
    if not expected.equals(frame.index):
        raise KOSPI200ScopeValidationError(f"{contract.name} rows are not sorted")


def _exact_dates(frame: pd.DataFrame, *columns: str) -> None:
    values = []
    for column in columns:
        parsed = pd.to_datetime(frame[column], format="%Y-%m-%d", errors="coerce")
        if parsed.isna().any():
            raise KOSPI200ScopeValidationError(f"{column} contains an invalid date")
        values.append(parsed.dt.strftime("%Y-%m-%d"))
    if any(not value.equals(values[0]) for value in values[1:]):
        raise KOSPI200ScopeValidationError("membership and observation dates must match exactly")


def validate_index_constituent_daily(frame: pd.DataFrame) -> None:
    _base(frame, KR_INDEX_CONSTITUENT_DAILY)
    _exact_dates(frame, "date", "observation_date")
    if not frame["index_ticker"].eq("1028").all() or not frame["index_symbol"].eq("KOSPI200").all():
        raise KOSPI200ScopeValidationError("only exact KOSPI200 ticker 1028 scope is accepted")
    if not frame["market"].eq("KOSPI").all():
        raise KOSPI200ScopeValidationError("KOSPI200 members must be KOSPI scoped")
    symbols = frame["symbol"].astype(str)
    if not symbols.str.fullmatch(r"[0-9A-Z]{6}").all():
        raise KOSPI200ScopeValidationError("constituent symbol is invalid")
    for column in ("source", "source_operation", "source_captured_at", "source_sha256", "pit_status"):
        if frame[column].fillna("").astype(str).str.strip().eq("").any():
            raise KOSPI200ScopeValidationError(f"{column} must be populated")
    if not frame["source_sha256"].astype(str).str.fullmatch(r"[0-9a-f]{64}").all():
        raise KOSPI200ScopeValidationError("source_sha256 must be lowercase SHA-256")
    if not frame["pit_status"].eq("EXACT_DATE_ONLY_NO_INTERVAL_INFERENCE").all():
        raise KOSPI200ScopeValidationError("survivorship-safe exact-date boundary is required")


def validate_kospi200_constituent_price_daily(frame: pd.DataFrame) -> None:
    _base(frame, KR_KOSPI200_CONSTITUENT_PRICE_DAILY)
    _exact_dates(frame, "date", "membership_observation_date", "source_date")
    if not frame["market"].eq("KOSPI").all():
        raise KOSPI200ScopeValidationError("price scope must be KOSPI")
    numeric = frame[["open", "high", "low", "close", "volume", "trading_value"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all() or (numeric < 0).any().any():
        raise KOSPI200ScopeValidationError("OHLCV values must be finite provider-native nonnegative values")
    suspended = numeric[["open", "high", "low"]].eq(0).all(axis=1)
    invalid = ~suspended & ((numeric["high"] < numeric["low"]) | ~numeric["open"].between(numeric["low"], numeric["high"]) | ~numeric["close"].between(numeric["low"], numeric["high"]))
    if invalid.any():
        raise KOSPI200ScopeValidationError("OHLC relationship is invalid")


def validate_kospi200_breadth_daily(frame: pd.DataFrame) -> None:
    _base(frame, KR_KOSPI200_BREADTH_DAILY)
    _exact_dates(frame, "date", "membership_observation_date")
    previous = pd.to_datetime(frame["previous_session_date"], format="%Y-%m-%d", errors="coerce")
    current = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    if previous.isna().any() or not (previous < current).all():
        raise KOSPI200ScopeValidationError("previous_session_date must precede date")
    numeric = frame[["advancing", "declining", "unchanged", "total", "missing_price_count"]].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or (numeric < 0).any().any():
        raise KOSPI200ScopeValidationError("breadth counts must be nonnegative")
    if not numeric[["advancing", "declining", "unchanged"]].sum(axis=1).eq(numeric["total"]).all():
        raise KOSPI200ScopeValidationError("breadth components differ from total")
    if not numeric["missing_price_count"].eq(0).all() or not frame["scope_status"].eq("COMPLETE_EXACT_DATE").all():
        raise KOSPI200ScopeValidationError("incomplete breadth must not be published")
    if not frame["index_symbol"].eq("KOSPI200").all() or not frame["index_ticker"].eq("1028").all():
        raise KOSPI200ScopeValidationError("breadth scope identity is invalid")
