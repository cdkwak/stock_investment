"""FINRA short-data Landing-only parsing and validation helpers.

This module intentionally keeps Daily Short Sale Volume and Equity Short Interest
as separate source families.  It does not create a normalized dataset.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class FinraSchemaError(ValueError):
    """Raised when a FINRA response cannot satisfy the bounded-pilot contract."""


DAILY_REQUIRED_COLUMNS = (
    "Date",
    "Symbol",
    "ShortVolume",
    "ShortExemptVolume",
    "TotalVolume",
    "Market",
)
SHORT_INTEREST_REQUIRED_FIELDS = (
    "issueSymbolIdentifier",
    "settlementDate",
    "currentShortShareNumber",
    "averageShortShareNumber",
)


@dataclass(frozen=True)
class ParsedDailyShortSaleVolume:
    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    schema_sha256: str


@dataclass(frozen=True)
class ParsedShortInterest:
    fields: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    schema_sha256: str


def _schema_sha256(fields: Sequence[str]) -> str:
    canonical = json.dumps(list(fields), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def parse_daily_short_sale_volume(raw: bytes) -> ParsedDailyShortSaleVolume:
    """Parse one official pipe-delimited FINRA daily volume file fail-closed."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FinraSchemaError("daily short-sale response is not UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    if not reader.fieldnames:
        raise FinraSchemaError("daily short-sale response has no header")
    columns = tuple(reader.fieldnames)
    if columns != DAILY_REQUIRED_COLUMNS:
        raise FinraSchemaError(
            f"unexpected daily short-sale schema: {columns!r}; expected {DAILY_REQUIRED_COLUMNS!r}"
        )
    rows: list[dict[str, str]] = []
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(row.get(column) is None for column in DAILY_REQUIRED_COLUMNS):
            raise FinraSchemaError(f"daily short-sale row {line_number} has missing field")
        if not row["Date"] or not row["Symbol"] or not row["Market"]:
            raise FinraSchemaError(f"daily short-sale row {line_number} has null entity/date key")
        for numeric in ("ShortVolume", "ShortExemptVolume", "TotalVolume"):
            try:
                value = int(row[numeric])
            except ValueError as exc:
                raise FinraSchemaError(f"daily short-sale row {line_number} has non-integer {numeric}") from exc
            if value < 0:
                raise FinraSchemaError(f"daily short-sale row {line_number} has negative {numeric}")
        rows.append({column: row[column] for column in DAILY_REQUIRED_COLUMNS})
    if not rows:
        raise FinraSchemaError("daily short-sale response has no data rows")
    return ParsedDailyShortSaleVolume(columns=columns, rows=tuple(rows), schema_sha256=_schema_sha256(columns))


def parse_short_interest(raw: bytes) -> ParsedShortInterest:
    """Parse one official FINRA Equity Short Interest JSON response fail-closed."""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinraSchemaError("short-interest response is not UTF-8 JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise FinraSchemaError("short-interest response must be a non-empty JSON list")
    if not all(isinstance(row, Mapping) for row in payload):
        raise FinraSchemaError("short-interest response contains a non-object row")
    field_sets = {tuple(sorted(row.keys())) for row in payload}
    if len(field_sets) != 1:
        raise FinraSchemaError("short-interest response has conflicting row schemas")
    fields = next(iter(field_sets))
    missing = [field for field in SHORT_INTEREST_REQUIRED_FIELDS if field not in fields]
    if missing:
        raise FinraSchemaError(f"short-interest response missing required fields: {missing!r}")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(payload):
        symbol = row["issueSymbolIdentifier"]
        settlement_date = row["settlementDate"]
        market = row.get("marketCategoryCode", "")
        if not isinstance(symbol, str) or not symbol or not isinstance(settlement_date, str) or not settlement_date:
            raise FinraSchemaError(f"short-interest row {index} has null entity/date key")
        if row["currentShortShareNumber"] is None:
            raise FinraSchemaError(f"short-interest row {index} has null current short interest")
        key = (symbol, settlement_date, str(market))
        if key in seen:
            raise FinraSchemaError(f"short-interest response has conflicting duplicate key: {key!r}")
        seen.add(key)
        rows.append(dict(row))
    return ParsedShortInterest(fields=fields, rows=tuple(rows), schema_sha256=_schema_sha256(fields))


def target_daily_rows(parsed: ParsedDailyShortSaleVolume, symbols: set[str]) -> tuple[dict[str, str], ...]:
    return tuple(row for row in parsed.rows if row["Symbol"] in symbols)


def target_short_interest_rows(parsed: ParsedShortInterest, symbols: set[str]) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in parsed.rows if row["issueSymbolIdentifier"] in symbols)
