from __future__ import annotations

from datetime import date, datetime
import re

import numpy as np
import pandas as pd

from stock_data.contracts.us_option_pcr import (
    ORATS_OPTION_CORE_OBSERVATION,
    US_UNDERLYING_OPTION_PCR_DAILY,
)


class USOptionPCRError(ValueError):
    """The retained ORATS three-underlying scope is not trustworthy."""


NORMALIZED_COLUMNS = ORATS_OPTION_CORE_OBSERVATION.column_names
DERIVED_COLUMNS = US_UNDERLYING_OPTION_PCR_DAILY.column_names
_TICKERS = ("SPX", "QQQ", "NDX")
_ORDER = {ticker: position for position, ticker in enumerate(_TICKERS)}
_TYPES = {"SPX": "INDEX", "QQQ": "ETF", "NDX": "INDEX"}
_COUNTS = ("call_volume", "put_volume", "call_open_interest", "put_open_interest")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_frame(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise USOptionPCRError(f"{name} must be a pandas DataFrame")
    if frame.empty:
        raise USOptionPCRError(f"{name} must not be empty")
    if tuple(frame.columns) != columns:
        raise USOptionPCRError(f"{name} columns do not match the registered contract")


def _date(value: object, field: str) -> str:
    if isinstance(value, pd.Timestamp):
        value = value.date()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or value != value.strip():
        raise USOptionPCRError(f"{field} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise USOptionPCRError(f"{field} must be a canonical ISO date") from None
    if parsed.isoformat() != value:
        raise USOptionPCRError(f"{field} must be a canonical ISO date")
    return value


def _timestamp(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        if nullable:
            return None
        raise USOptionPCRError(f"{field} must not be null")
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        raise USOptionPCRError(f"{field} must be a timezone-aware timestamp") from None
    if parsed.tzinfo is None:
        raise USOptionPCRError(f"{field} must be a timezone-aware timestamp")
    return parsed.tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _count(value: object, field: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise USOptionPCRError(f"{field} must be a non-negative int64")
    result = int(value)
    if result < 0 or result > np.iinfo(np.int64).max:
        raise USOptionPCRError(f"{field} must be a non-negative int64")
    return result


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.astype("float64").div(denominator).mask(denominator.eq(0), np.nan)


def _parse(frame: pd.DataFrame) -> pd.DataFrame:
    _require_frame(frame, NORMALIZED_COLUMNS, "normalized input")
    if len(frame) != 3:
        raise USOptionPCRError("normalized input must contain exactly three rows")
    source = frame.copy(deep=True)
    source["trade_date"] = source["trade_date"].map(lambda value: _date(value, "trade_date"))
    tickers = source["provider_ticker"]
    if tickers.map(type).ne(str).any() or set(tickers) != set(_TICKERS) or tickers.duplicated().any():
        raise USOptionPCRError("provider_ticker scope must be exactly SPX, QQQ, NDX")
    if not source["asset_type"].eq(tickers.map(_TYPES)).all():
        raise USOptionPCRError("asset_type does not match provider_ticker")
    if not source["observation_status"].eq("OBSERVED").all():
        raise USOptionPCRError("every normalized row must be OBSERVED")
    if not source["source"].eq("ORATS_DELAYED_CORES").all():
        raise USOptionPCRError("source must be ORATS_DELAYED_CORES without fallback")
    for column in _COUNTS:
        source[column] = source[column].map(lambda value, name=column: _count(value, name)).astype("int64")
    for column in ("provider_updated_at_utc", "provider_snapshot_at_utc"):
        source[column] = source[column].map(
            lambda value, name=column: _timestamp(value, name, nullable=True)
        )
    source["captured_at_utc"] = source["captured_at_utc"].map(
        lambda value: _timestamp(value, "captured_at_utc")
    )
    if source["trade_date"].nunique(dropna=False) != 1:
        raise USOptionPCRError("all three rows must have the same trade_date")
    if source["captured_at_utc"].nunique(dropna=False) != 1:
        raise USOptionPCRError("all three rows must have the same captured_at_utc")
    if source["landing_sha256"].nunique(dropna=False) != 1:
        raise USOptionPCRError("all three rows must have the same landing_sha256")
    landing_hash = source["landing_sha256"].iloc[0]
    if not isinstance(landing_hash, str) or _SHA256.fullmatch(landing_hash) is None:
        raise USOptionPCRError("landing_sha256 must be 64 lowercase hex characters")
    return source.assign(_order=tickers.map(_ORDER)).sort_values(
        "_order", kind="mergesort"
    ).drop(columns="_order").reset_index(drop=True)


def derive_us_option_pcr(normalized: pd.DataFrame) -> pd.DataFrame:
    """Derive one atomic SPX/QQQ/NDX daily P/C scope without I/O or fallback."""
    source = _parse(normalized)
    output = pd.DataFrame({
        "trade_date": source["trade_date"],
        "underlying": source["provider_ticker"],
        "underlying_type": source["asset_type"],
        "provider": "ORATS",
        "provider_ticker": source["provider_ticker"],
        "scope": "PROVIDER_ALL_LISTED_CHAIN",
        "root_scope_status": "UNCONFIRMED",
        "session": "US_OPTIONS_REGULAR",
        "call_volume": source["call_volume"],
        "put_volume": source["put_volume"],
        "volume_pcr": _ratio(source["put_volume"], source["call_volume"]),
        "volume_finality_status": "UNCONFIRMED",
        "call_open_interest": source["call_open_interest"],
        "put_open_interest": source["put_open_interest"],
        "open_interest_pcr": _ratio(source["put_open_interest"], source["call_open_interest"]),
        "open_interest_timing_status": "PROVIDER_DAILY_TAG_AT_CAPTURE",
        "selected_capture_at_utc": source["captured_at_utc"],
        "available_at_utc": source["captured_at_utc"],
        "input_dataset": ORATS_OPTION_CORE_OBSERVATION.name,
        "landing_sha256": source["landing_sha256"],
        "revision_status": "UNKNOWN_REVISION",
        "observation_status": "OBSERVED",
        "pit_status": "PIT_BLOCKED_HISTORICAL_AVAILABILITY",
    }, columns=DERIVED_COLUMNS)
    validate_us_option_pcr_derived(output)
    return output


def validate_us_option_pcr_derived(frame: pd.DataFrame) -> None:
    """Fail closed if an exact derived contract frame was altered."""
    _require_frame(frame, DERIVED_COLUMNS, "derived input")
    if len(frame) != 3 or frame["underlying"].tolist() != list(_TICKERS):
        raise USOptionPCRError("derived underlying order/scope is invalid")
    if not frame["provider_ticker"].eq(frame["underlying"]).all():
        raise USOptionPCRError("derived provider_ticker differs from underlying")
    if frame["trade_date"].map(lambda value: _date(value, "trade_date")).nunique() != 1:
        raise USOptionPCRError("derived trade_date scope is invalid")
    selected = frame["selected_capture_at_utc"].map(
        lambda value: _timestamp(value, "selected_capture_at_utc")
    )
    if selected.nunique() != 1 or not frame["available_at_utc"].eq(frame["selected_capture_at_utc"]).all():
        raise USOptionPCRError("derived selected/available capture scope is invalid")
    if frame["landing_sha256"].nunique(dropna=False) != 1:
        raise USOptionPCRError("derived landing hash scope is invalid")
    landing_hash = frame["landing_sha256"].iloc[0]
    if not isinstance(landing_hash, str) or _SHA256.fullmatch(landing_hash) is None:
        raise USOptionPCRError("derived landing_sha256 is invalid")
    fixed: dict[str, object] = {
        "underlying_type": frame["underlying"].map(_TYPES),
        "provider": "ORATS", "scope": "PROVIDER_ALL_LISTED_CHAIN",
        "root_scope_status": "UNCONFIRMED",
        "session": "US_OPTIONS_REGULAR", "volume_finality_status": "UNCONFIRMED",
        "open_interest_timing_status": "PROVIDER_DAILY_TAG_AT_CAPTURE",
        "input_dataset": ORATS_OPTION_CORE_OBSERVATION.name,
        "revision_status": "UNKNOWN_REVISION", "observation_status": "OBSERVED",
        "pit_status": "PIT_BLOCKED_HISTORICAL_AVAILABILITY",
    }
    for column, expected in fixed.items():
        if not frame[column].eq(expected).all():
            raise USOptionPCRError(f"derived {column} is invalid")
    for column in _COUNTS:
        for value in frame[column]:
            _count(value, column)
    for ratio, numerator, denominator in (
        ("volume_pcr", "put_volume", "call_volume"),
        ("open_interest_pcr", "put_open_interest", "call_open_interest"),
    ):
        expected = _ratio(frame[numerator], frame[denominator])
        actual = pd.to_numeric(frame[ratio], errors="coerce")
        if not np.allclose(actual, expected, rtol=0.0, atol=1e-12, equal_nan=True):
            raise USOptionPCRError(f"derived {ratio} differs from provider totals")


__all__ = ["DERIVED_COLUMNS", "NORMALIZED_COLUMNS", "USOptionPCRError", "derive_us_option_pcr", "validate_us_option_pcr_derived"]
