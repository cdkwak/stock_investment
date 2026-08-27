from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Iterable


SCHEMA_VERSION = 1
SOURCE_SHEET = "아빠"
SNAPSHOT_DATE = "2026-02-03"
SECTIONS = ("ISA", "종합")
ROW_STATUSES = frozenset({"AVAILABLE", "UNAVAILABLE"})


@dataclass(frozen=True, slots=True)
class ManualAccountMarketValueRow:
    section: str
    ticker: str
    status: str
    currency: str
    provider_symbol: str | None
    provider: str | None
    exchange: str | None
    unit: str | None
    price: Decimal | None
    as_of: str | None
    captured_at: str | None
    finality: str | None
    market_value: Decimal | None
    weight_pct: Decimal | None
    unrealized_pnl: Decimal | None
    return_pct: Decimal | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ManualAccountSectionSummary:
    section: str
    currency: str
    total_rows: int
    available_rows: int
    market_value: Decimal
    complete: bool


@dataclass(frozen=True, slots=True)
class ManualAccountMarketValueCache:
    source_sheet: str
    snapshot_date: str
    basis_sha256: str
    generated_at: str
    rows: tuple[ManualAccountMarketValueRow, ...]
    section_summaries: tuple[ManualAccountSectionSummary, ...]


def _aware_iso(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an aware ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _decimal(
    value: Any, field: str, *, positive: bool = False, allow_negative: bool = False,
) -> Decimal:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field} is invalid") from error
    if (
        not result.is_finite()
        or (not allow_negative and result < 0)
        or (positive and result <= 0)
    ):
        raise ValueError(f"{field} is outside the accepted range")
    return result


def _optional_decimal(
    value: Any, field: str, *, allow_negative: bool = False,
) -> Decimal | None:
    return None if value is None else _decimal(
        value, field, allow_negative=allow_negative,
    )


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} is invalid")
    return value


def manual_account_basis_sha256(snapshot: Any) -> str:
    """Bind a cache to normalized acquisition facts without copying them."""

    try:
        holdings: Iterable[Any] = snapshot.holdings
        payload = {
            "source_sheet": snapshot.source_sheet,
            "snapshot_date": snapshot.snapshot_date,
            "currency": snapshot.currency,
            "holdings": [{
                "section": row.section,
                "name": row.name,
                "ticker": row.ticker,
                "quantity": repr(float(row.quantity)),
                "average_cost": (
                    None if row.average_cost is None else repr(float(row.average_cost))
                ),
                "purchase_total": (
                    None if row.purchase_total is None else repr(float(row.purchase_total))
                ),
            } for row in holdings],
        }
    except (AttributeError, TypeError, ValueError) as error:
        raise TypeError("validated manual account snapshot is required") from error
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return sha256(body).hexdigest()


def parse_manual_account_market_value_cache(
    payload: Any,
) -> ManualAccountMarketValueCache:
    required = {
        "schema_version", "source_sheet", "snapshot_date", "basis_sha256",
        "generated_at", "rows", "section_summaries",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("manual market-value cache keys do not match schema v1")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("manual market-value cache schema is unsupported")
    if payload["source_sheet"] != SOURCE_SHEET or payload["snapshot_date"] != SNAPSHOT_DATE:
        raise ValueError("manual market-value cache basis identity is unsupported")
    basis_digest = payload["basis_sha256"]
    if (
        not isinstance(basis_digest, str) or len(basis_digest) != 64
        or any(character not in "0123456789abcdef" for character in basis_digest)
    ):
        raise ValueError("manual market-value basis digest is invalid")
    generated_at = _aware_iso(payload["generated_at"], "generated_at")
    generated_clock = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    raw_rows = payload["rows"]
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("manual market-value rows must be non-empty")
    row_keys = {
        "section", "ticker", "status", "currency", "provider_symbol",
        "provider", "exchange", "unit", "price", "as_of", "captured_at",
        "finality", "market_value", "weight_pct", "unrealized_pnl",
        "return_pct", "reason",
    }
    rows: list[ManualAccountMarketValueRow] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_rows:
        if not isinstance(raw, dict) or set(raw) != row_keys:
            raise ValueError("manual market-value row keys differ")
        section = raw["section"]
        if section not in SECTIONS:
            raise ValueError("manual market-value section is invalid")
        ticker = _text(raw["ticker"], "ticker")
        identity = (section, ticker)
        if identity in seen:
            raise ValueError("manual market-value row is duplicated")
        seen.add(identity)
        status = raw["status"]
        if status not in ROW_STATUSES:
            raise ValueError("manual market-value status is invalid")
        currency = _text(raw["currency"], "currency")
        if status == "AVAILABLE":
            provider_symbol = _text(raw["provider_symbol"], "provider_symbol")
            provider = _text(raw["provider"], "provider")
            exchange = _text(raw["exchange"], "exchange")
            unit = _text(raw["unit"], "unit")
            price = _decimal(raw["price"], "price", positive=True)
            as_of = _aware_iso(raw["as_of"], "as_of")
            captured_at = _aware_iso(raw["captured_at"], "captured_at")
            finality = _text(raw["finality"], "finality")
            if (
                provider != "YAHOO_CHART_API"
                or exchange != "XKRX"
                or unit != "KRW_PER_SHARE"
                or finality not in {"AS_RETRIEVED", "COMPLETED_SESSION"}
                or re.fullmatch(r"\d{6}\.(KS|KQ)", provider_symbol) is None
                or provider_symbol[:6] != ticker
            ):
                raise ValueError("available row provider contract differs")
            as_of_clock = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            captured_clock = datetime.fromisoformat(
                captured_at.replace("Z", "+00:00")
            )
            if not as_of_clock <= captured_clock <= generated_clock:
                raise ValueError("available row timestamps are out of order")
            market_value = _decimal(raw["market_value"], "market_value")
            weight_pct = _decimal(raw["weight_pct"], "weight_pct")
            if weight_pct > Decimal("100"):
                raise ValueError("weight_pct exceeds 100")
            unrealized_pnl = _optional_decimal(
                raw["unrealized_pnl"], "unrealized_pnl", allow_negative=True,
            )
            return_pct = _optional_decimal(
                raw["return_pct"], "return_pct", allow_negative=True,
            )
            if raw["reason"] is not None:
                raise ValueError("available row cannot have an unavailable reason")
            reason = None
        else:
            unavailable_fields = (
                "provider_symbol", "provider", "exchange", "unit", "price",
                "as_of", "captured_at", "finality", "market_value",
                "weight_pct", "unrealized_pnl", "return_pct",
            )
            if any(raw[field] is not None for field in unavailable_fields):
                raise ValueError("unavailable row contains numeric/provider data")
            provider_symbol = provider = exchange = unit = None
            price = market_value = weight_pct = unrealized_pnl = return_pct = None
            as_of = captured_at = finality = None
            reason = _text(raw["reason"], "reason")
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", reason) is None:
                raise ValueError("unavailable reason must be a sanitized code")
        rows.append(ManualAccountMarketValueRow(
            section, ticker, status, currency, provider_symbol, provider,
            exchange, unit, price, as_of, captured_at, finality, market_value,
            weight_pct, unrealized_pnl, return_pct, reason,
        ))

    raw_summaries = payload["section_summaries"]
    if not isinstance(raw_summaries, list) or not raw_summaries:
        raise ValueError("manual market-value section summaries are required")
    summary_keys = {
        "section", "currency", "total_rows", "available_rows",
        "market_value", "complete",
    }
    summaries: list[ManualAccountSectionSummary] = []
    summary_seen: set[tuple[str, str]] = set()
    for raw in raw_summaries:
        if not isinstance(raw, dict) or set(raw) != summary_keys:
            raise ValueError("manual market-value summary keys differ")
        section = raw["section"]
        if section not in SECTIONS:
            raise ValueError("manual market-value summary section is invalid")
        currency = _text(raw["currency"], "summary.currency")
        identity = (section, currency)
        if identity in summary_seen:
            raise ValueError("manual market-value summary is duplicated")
        summary_seen.add(identity)
        total_rows = raw["total_rows"]
        available_rows = raw["available_rows"]
        complete = raw["complete"]
        if (
            type(total_rows) is not int or total_rows <= 0
            or type(available_rows) is not int or not 0 <= available_rows <= total_rows
            or type(complete) is not bool or complete != (available_rows == total_rows)
        ):
            raise ValueError("manual market-value summary counts differ")
        summaries.append(ManualAccountSectionSummary(
            section, currency, total_rows, available_rows,
            _decimal(raw["market_value"], "summary.market_value"), complete,
        ))
    row_groups = {(row.section, row.currency) for row in rows}
    if summary_seen != row_groups:
        raise ValueError("manual market-value summary identities differ from rows")
    for summary in summaries:
        matching = [
            row for row in rows
            if (row.section, row.currency) == (summary.section, summary.currency)
        ]
        available = [row for row in matching if row.status == "AVAILABLE"]
        if (
            len(matching) != summary.total_rows
            or len(available) != summary.available_rows
            or sum(
                (row.market_value or Decimal(0) for row in available), Decimal(0),
            ) != summary.market_value
        ):
            raise ValueError("manual market-value summary does not reconcile")
        weight_total = sum(
            (row.weight_pct or Decimal(0) for row in available), Decimal(0),
        )
        if available and abs(weight_total - Decimal(100)) > Decimal("1e-24"):
            raise ValueError("manual market-value weights do not reconcile")
    return ManualAccountMarketValueCache(
        SOURCE_SHEET, SNAPSHOT_DATE, basis_digest, generated_at,
        tuple(rows), tuple(summaries),
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def manual_account_market_value_cache_payload(
    cache: ManualAccountMarketValueCache,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_sheet": cache.source_sheet,
        "snapshot_date": cache.snapshot_date,
        "basis_sha256": cache.basis_sha256,
        "generated_at": cache.generated_at,
        "rows": [{
            "section": row.section, "ticker": row.ticker,
            "status": row.status, "currency": row.currency,
            "provider_symbol": row.provider_symbol, "provider": row.provider,
            "exchange": row.exchange, "unit": row.unit,
            "price": _decimal_text(row.price), "as_of": row.as_of,
            "captured_at": row.captured_at, "finality": row.finality,
            "market_value": _decimal_text(row.market_value),
            "weight_pct": _decimal_text(row.weight_pct),
            "unrealized_pnl": _decimal_text(row.unrealized_pnl),
            "return_pct": _decimal_text(row.return_pct), "reason": row.reason,
        } for row in cache.rows],
        "section_summaries": [{
            "section": summary.section, "currency": summary.currency,
            "total_rows": summary.total_rows,
            "available_rows": summary.available_rows,
            "market_value": _decimal_text(summary.market_value),
            "complete": summary.complete,
        } for summary in cache.section_summaries],
    }


__all__ = [
    "ManualAccountMarketValueCache", "ManualAccountMarketValueRow",
    "ManualAccountSectionSummary", "SCHEMA_VERSION", "SECTIONS",
    "SNAPSHOT_DATE", "SOURCE_SHEET", "manual_account_basis_sha256",
    "manual_account_market_value_cache_payload",
    "parse_manual_account_market_value_cache",
]
