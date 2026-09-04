"""One-request transport and strict parser for Cboe daily put/call statistics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
import json
import math
import re
from typing import Callable, Mapping


# No stable machine URL was present in the retained 2026-08 evidence. This
# Historical Options Data route is a placeholder that a coordinator must
# verify with one curl before enabling live collection.
CBOE_HISTORICAL_OPTIONS_CSV_PLACEHOLDER = (
    "https://www.cboe.com/us/options/market_statistics/historical_data/"
    "?download=csv&date={date}"
)
PROVIDER = "cboe_daily_market_statistics"
REQUIRED_SCOPES = ("TOTAL", "INDEX", "ETP", "EQUITY", "VIX")
SUPPORTED_SCOPES = (*REQUIRED_SCOPES, "SPX_SPXW")


class CboeDailyPcrError(ValueError):
    pass


@dataclass(frozen=True)
class CboeDailyPcrDownload:
    body: bytes
    status_code: int
    content_type: str
    source_url: str
    retrieved_at: datetime


def download_daily_pcr(
    observation_date: date,
    *,
    transport: Callable[..., object],
    source_url_template: str = CBOE_HISTORICAL_OPTIONS_CSV_PLACEHOLDER,
    retrieved_at: datetime | None = None,
) -> CboeDailyPcrDownload:
    """Make exactly one public request and return bytes without parsing them."""
    if "{date}" not in source_url_template:
        raise CboeDailyPcrError("Cboe source URL must contain a {date} placeholder")
    source_url = source_url_template.format(date=observation_date.isoformat())
    response = transport(
        source_url,
        timeout=20,
        headers={
            "Accept": "text/csv, application/json;q=0.9, */*;q=0.1",
            "User-Agent": "StockInvestmentRev1-personal-daily/1.0",
        },
    )
    status_code = int(getattr(response, "status_code"))
    body = bytes(getattr(response, "content"))
    headers = getattr(response, "headers", {})
    content_type = str(headers.get("content-type", "")) if isinstance(headers, Mapping) else ""
    captured = retrieved_at or datetime.now(timezone.utc)
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise CboeDailyPcrError("retrieved_at must be timezone-aware")
    return CboeDailyPcrDownload(
        body=body,
        status_code=status_code,
        content_type=content_type,
        source_url=source_url,
        retrieved_at=captured.astimezone(timezone.utc),
    )


def _key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").strip().upper()).strip("_")


_SCOPE_ALIASES = {
    "TOTAL": "TOTAL",
    "SUM_OF_ALL_PRODUCTS": "TOTAL",
    "TOTAL_PUT_CALL_RATIO": "TOTAL",
    "INDEX": "INDEX",
    "INDEX_PUT_CALL_RATIO": "INDEX",
    "ETP": "ETP",
    "EXCHANGE_TRADED_PRODUCTS": "ETP",
    "EXCHANGE_TRADED_PRODUCTS_PUT_CALL_RATIO": "ETP",
    "EQUITY": "EQUITY",
    "EQUITY_PUT_CALL_RATIO": "EQUITY",
    "VIX": "VIX",
    "CBOE_VOLATILITY_INDEX_VIX_PUT_CALL_RATIO": "VIX",
    "SPX_SPXW": "SPX_SPXW",
    "SPX_SPXW_PUT_CALL_RATIO": "SPX_SPXW",
}


def _lookup(row: Mapping[str, object], *aliases: str, required: bool = True) -> object:
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        if _key(alias) in normalized:
            value = normalized[_key(alias)]
            if value is not None and str(value).strip() != "":
                return value
    if required:
        raise CboeDailyPcrError(f"missing Cboe field: {aliases[0]}")
    return None


def _count(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None or str(value).strip() in {"", "-", "N/A", "null", "None"}:
        if nullable:
            return None
        raise CboeDailyPcrError(f"missing Cboe count: {field}")
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError as error:
        raise CboeDailyPcrError(f"invalid Cboe count: {field}") from error
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise CboeDailyPcrError(f"invalid Cboe count: {field}")
    return int(number)


def _records(body: bytes, content_type: str) -> list[Mapping[str, object]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CboeDailyPcrError("Cboe response is not UTF-8") from error
    if not text.strip():
        raise CboeDailyPcrError("Cboe response is empty")
    if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise CboeDailyPcrError("Cboe JSON is invalid") from error
        if isinstance(payload, dict):
            for key in ("data", "rows", "items", "results"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise CboeDailyPcrError("Cboe JSON rows are invalid")
        return payload
    rows = list(csv.DictReader(StringIO(text)))
    if not rows:
        raise CboeDailyPcrError("Cboe CSV has no data rows")
    return rows


def parse_daily_pcr(
    body: bytes,
    *,
    observation_date: date,
    retrieved_at: datetime,
    content_type: str = "text/csv",
) -> list[dict[str, object]]:
    """Normalize the five required Cboe product groups plus optional SPX/SPXW."""
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise CboeDailyPcrError("retrieved_at must be timezone-aware")
    parsed: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in _records(body, content_type):
        raw_scope = _lookup(row, "scope", "product_group", "category", "name", "label")
        scope = _SCOPE_ALIASES.get(_key(raw_scope))
        if scope is None:
            continue
        if scope in seen:
            raise CboeDailyPcrError(f"duplicate Cboe scope: {scope}")
        raw_date = _lookup(row, "date", "trade_date", "business_date", required=False)
        if raw_date is not None:
            try:
                row_date = date.fromisoformat(str(raw_date).strip()[:10])
            except ValueError as error:
                raise CboeDailyPcrError("invalid Cboe observation date") from error
            if row_date != observation_date:
                raise CboeDailyPcrError("Cboe observation date differs from request")
        call_volume = _count(_lookup(row, "call_volume", "calls", "call"), "call_volume")
        put_volume = _count(_lookup(row, "put_volume", "puts", "put"), "put_volume")
        call_oi = _count(
            _lookup(row, "call_oi", "call_open_interest", "calls_oi", required=False),
            "call_oi", nullable=True,
        )
        put_oi = _count(
            _lookup(row, "put_oi", "put_open_interest", "puts_oi", required=False),
            "put_oi", nullable=True,
        )
        if (call_oi is None) != (put_oi is None):
            raise CboeDailyPcrError("Cboe open-interest pair is partial")
        parsed.append({
            "date": observation_date,
            "scope": scope,
            "call_volume": call_volume,
            "put_volume": put_volume,
            "volume_pcr": None if call_volume == 0 else put_volume / call_volume,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "oi_pcr": None if call_oi in (None, 0) else put_oi / call_oi,
            "provider": PROVIDER,
            "retrieved_at": retrieved_at.astimezone(timezone.utc),
        })
        seen.add(scope)
    missing = set(REQUIRED_SCOPES) - seen
    if missing:
        raise CboeDailyPcrError(f"Cboe response misses required scopes: {sorted(missing)}")
    return sorted(parsed, key=lambda item: SUPPORTED_SCOPES.index(str(item["scope"])))


__all__ = [
    "CBOE_HISTORICAL_OPTIONS_CSV_PLACEHOLDER", "CboeDailyPcrDownload",
    "CboeDailyPcrError", "PROVIDER", "REQUIRED_SCOPES", "SUPPORTED_SCOPES",
    "download_daily_pcr", "parse_daily_pcr",
]
