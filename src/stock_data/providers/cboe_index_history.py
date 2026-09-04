"""Cboe public daily index-history CSV provider.

Each request downloads one complete, non-parameterized history file. When a
Landing root is supplied, the exact bytes and a hash-bound receipt are written
before HTTP or CSV validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Mapping

import numpy as np
import pandas as pd
import requests

from stock_data.contracts.global_market import (
    GLOBAL_INDEX_PRICE_DAILY,
    GLOBAL_INDEX_REGISTRY,
    GLOBAL_INDEX_SYMBOLS_BY_PROVIDER,
)


CBOE_INDEX_HISTORY_SYMBOLS = GLOBAL_INDEX_SYMBOLS_BY_PROVIDER[
    "cboe_index_history_csv"
]
USER_AGENT = "stock-investment-rev1/0.1"


class CboeIndexHistoryError(RuntimeError):
    pass


def _normalized_header(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value).lstrip("\ufeff").strip().upper()).strip("_")


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_bytes(path, encoded)


def _capture_response(
    *, root: Path, symbol: str, url: str, response,
    now: Callable[[], datetime],
) -> dict[str, object]:
    body = response.content
    if not isinstance(body, bytes):
        raise CboeIndexHistoryError("Cboe response content is not exact bytes")
    observed = now()
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise CboeIndexHistoryError("capture timestamp must be timezone-aware")
    csv_path = root / f"{symbol}.csv"
    receipt_path = root / f"{symbol}.json"
    if csv_path.exists() or receipt_path.exists():
        raise CboeIndexHistoryError("Cboe Landing capture already exists")
    digest = hashlib.sha256(body).hexdigest()
    headers = getattr(response, "headers", {})
    content_type = str(headers.get("Content-Type", "")) if isinstance(headers, Mapping) else ""
    receipt = {
        "capture_version": 1,
        "provider": "cboe_index_history_csv",
        "operation": "daily_history_csv",
        "captured_at_utc": observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "request_url": url,
        "request_parameters": {"symbol": symbol},
        "http_status": int(response.status_code),
        "response_content_type": content_type,
        "response_body_sha256": digest,
        "response_bytes": len(body),
        "landing_body_file": csv_path.name,
    }
    _atomic_bytes(csv_path, body)
    _atomic_json(receipt_path, receipt)
    if hashlib.sha256(csv_path.read_bytes()).hexdigest() != digest:
        raise CboeIndexHistoryError("Cboe Landing read-back hash differs")
    return receipt


def parse_cboe_index_history_csv(content: bytes, symbol: str) -> pd.DataFrame:
    """Parse one exact Cboe history file into ``global_index_price_daily``."""
    if symbol not in CBOE_INDEX_HISTORY_SYMBOLS:
        raise ValueError(f"unregistered Cboe index history symbol: {symbol}")
    if not isinstance(content, bytes):
        raise TypeError("Cboe CSV content must be bytes")
    try:
        source = pd.read_csv(BytesIO(content), encoding="utf-8-sig")
    except (UnicodeDecodeError, pd.errors.ParserError) as error:
        raise CboeIndexHistoryError("Cboe index history CSV is unreadable") from error
    if source.empty:
        raise CboeIndexHistoryError("Cboe index history CSV is empty")
    normalized = [_normalized_header(column) for column in source.columns]
    if len(normalized) != len(set(normalized)):
        raise CboeIndexHistoryError("Cboe index history headers are ambiguous")
    source.columns = normalized
    date_columns = [name for name in source.columns if name in {"DATE", "TRADE_DATE", "TRADEDATE"}]
    if len(date_columns) != 1:
        raise CboeIndexHistoryError("Cboe index history date header differs")
    date_column = date_columns[0]
    value_columns = [name for name in source.columns if name != date_column]
    ohlc_columns = {name: name for name in ("OPEN", "HIGH", "LOW", "CLOSE") if name in source}
    if len(ohlc_columns) == 4:
        selected = {name.lower(): source[name] for name in ("OPEN", "HIGH", "LOW", "CLOSE")}
    elif len(value_columns) == 1:
        values = source[value_columns[0]]
        selected = {name: values for name in ("open", "high", "low", "close")}
    else:
        close_aliases = [
            name for name in value_columns
            if name in {"CLOSE", "CLOSE_VALUE", "VALUE", "INDEX_VALUE", symbol}
        ]
        if len(close_aliases) != 1 or any(name in source for name in ("OPEN", "HIGH", "LOW")):
            raise CboeIndexHistoryError("Cboe index history value headers differ")
        values = source[close_aliases[0]]
        selected = {name: values for name in ("open", "high", "low", "close")}
    try:
        dates = pd.to_datetime(source[date_column].astype(str).str.strip(), format="%m/%d/%Y", errors="raise")
    except (ValueError, TypeError) as error:
        raise CboeIndexHistoryError("Cboe index history date values differ") from error
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise CboeIndexHistoryError("Cboe index history dates are not strictly monotonic")
    numeric = pd.DataFrame(selected).apply(pd.to_numeric, errors="coerce")
    if numeric["close"].isna().any():
        raise CboeIndexHistoryError("Cboe index history close contains missing values")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise CboeIndexHistoryError("Cboe index history OHLC contains invalid values")
    # Cboe's published files carry a handful of rows whose HIGH and LOW columns are
    # swapped (observed: VIX6M 2019-07-05 OPEN 16.18 HIGH 15.87 LOW 16.50 CLOSE 15.99).
    # Swap those two columns back and record the dates; every other relationship
    # violation is still rejected.
    swapped = numeric["high"] < numeric["low"]
    if swapped.any():
        high = numeric.loc[swapped, "high"].copy()
        numeric.loc[swapped, "high"] = numeric.loc[swapped, "low"]
        numeric.loc[swapped, "low"] = high
    repaired_dates = tuple(dates[swapped].dt.strftime("%Y-%m-%d"))
    if (
        ~numeric["open"].between(numeric["low"], numeric["high"])
        | ~numeric["close"].between(numeric["low"], numeric["high"])
    ).any():
        raise CboeIndexHistoryError("Cboe index history OHLC relationship is invalid")
    frame = pd.DataFrame({
        "date": dates.dt.strftime("%Y-%m-%d"),
        "symbol": symbol,
        "source_ticker": str(GLOBAL_INDEX_REGISTRY[symbol]["source_ticker"]),
        "open": numeric["open"],
        "high": numeric["high"],
        "low": numeric["low"],
        "close": numeric["close"],
        "volume": pd.Series(pd.NA, index=source.index, dtype="Int64"),
    })[list(GLOBAL_INDEX_PRICE_DAILY.column_names)]
    frame.attrs["provider_gap_dates"] = ()
    frame.attrs["repaired_high_low_dates"] = repaired_dates
    return frame


def fetch_cboe_index_history(
    symbol: str, *, session=requests, capture_root: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> pd.DataFrame:
    """Download one complete Cboe history file with retry count fixed at zero."""
    if symbol not in CBOE_INDEX_HISTORY_SYMBOLS:
        raise ValueError(f"unregistered Cboe index history symbol: {symbol}")
    url = str(GLOBAL_INDEX_REGISTRY[symbol]["source_url"])
    response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    receipt = None
    if capture_root is not None:
        receipt = _capture_response(
            root=capture_root, symbol=symbol, url=url, response=response, now=now,
        )
    response.raise_for_status()
    frame = parse_cboe_index_history_csv(response.content, symbol)
    frame.attrs.update({
        "provider": "cboe_index_history_csv",
        "response_bytes": len(response.content),
        "response_body_sha256": hashlib.sha256(response.content).hexdigest(),
        "landing_receipt": receipt,
    })
    return frame
