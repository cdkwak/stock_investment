from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import math
import re
from typing import Any


MANUAL_ACCOUNT_SNAPSHOT_SCHEMA_VERSION = 1
MANUAL_ACCOUNT_SOURCE_SHEET = "아빠"
MANUAL_ACCOUNT_SNAPSHOT_DATE = "2026-02-03"
MANUAL_ACCOUNT_CURRENCY = "KRW"
MANUAL_ACCOUNT_SECTIONS = ("ISA", "종합")


@dataclass(frozen=True)
class ManualAccountHolding:
    section: str
    name: str
    ticker: str
    quantity: float
    average_cost: float | None
    purchase_total: float | None


@dataclass(frozen=True)
class ManualAccountSnapshot:
    source_sheet: str
    snapshot_date: str
    currency: str
    holdings: tuple[ManualAccountHolding, ...]


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise ValueError(f"{field} is outside the accepted range")
    return result


def _optional_number(value: Any, field: str) -> float | None:
    return None if value is None else _number(value, field)


def parse_manual_account_snapshot(payload: Any) -> ManualAccountSnapshot:
    """Parse one synthetic/local dated holdings-basis snapshot, fail closed."""

    required = {
        "schema_version", "source_sheet", "snapshot_date", "currency", "holdings",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("manual account snapshot keys do not match schema v1")
    if (
        isinstance(payload["schema_version"], bool)
        or not isinstance(payload["schema_version"], int)
        or payload["schema_version"] != MANUAL_ACCOUNT_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError("manual account snapshot schema version is unsupported")
    if payload["source_sheet"] != MANUAL_ACCOUNT_SOURCE_SHEET:
        raise ValueError("manual account snapshot source_sheet is not authorized")
    if payload["currency"] != MANUAL_ACCOUNT_CURRENCY:
        raise ValueError("manual account snapshot currency must be KRW")

    snapshot_date = payload["snapshot_date"]
    if not isinstance(snapshot_date, str):
        raise TypeError("snapshot_date must be a canonical ISO date")
    try:
        parsed_date = date.fromisoformat(snapshot_date)
    except ValueError as error:
        raise ValueError("snapshot_date must be a canonical ISO date") from error
    if parsed_date.isoformat() != snapshot_date:
        raise ValueError("snapshot_date must be a canonical ISO date")
    if snapshot_date != MANUAL_ACCOUNT_SNAPSHOT_DATE:
        raise ValueError("manual account snapshot date is not authorized")

    rows = payload["holdings"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("manual account holdings must be a non-empty list")
    holding_keys = {
        "section", "name", "ticker", "quantity", "average_cost", "purchase_total",
    }
    seen: set[tuple[str, str]] = set()
    holdings: list[ManualAccountHolding] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != holding_keys:
            raise ValueError("manual account holding keys do not match schema v1")
        section = row["section"]
        if section not in MANUAL_ACCOUNT_SECTIONS:
            raise ValueError("manual account holding section is unsupported")
        name = row["name"]
        ticker = row["ticker"]
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("manual account holding name is invalid")
        if not isinstance(ticker, str) or re.fullmatch(r"\d{6}", ticker) is None:
            raise ValueError("manual account holding ticker must be six digits")
        identity = (section, ticker)
        if identity in seen:
            raise ValueError("manual account holding identity is duplicated")
        seen.add(identity)

        quantity = _number(row["quantity"], "holding.quantity", positive=True)
        average_cost = _optional_number(row["average_cost"], "holding.average_cost")
        purchase_total = _optional_number(
            row["purchase_total"], "holding.purchase_total",
        )
        if average_cost is not None and purchase_total is not None:
            expected = Decimal(str(row["quantity"])) * Decimal(str(row["average_cost"]))
            retained = Decimal(str(row["purchase_total"]))
            if expected != retained:
                raise ValueError("manual account purchase total does not reconcile")
        holdings.append(ManualAccountHolding(
            section=section,
            name=name,
            ticker=ticker,
            quantity=quantity,
            average_cost=average_cost,
            purchase_total=purchase_total,
        ))

    return ManualAccountSnapshot(
        source_sheet=MANUAL_ACCOUNT_SOURCE_SHEET,
        snapshot_date=snapshot_date,
        currency=MANUAL_ACCOUNT_CURRENCY,
        holdings=tuple(holdings),
    )


def validate_manual_account_snapshot(
    snapshot: ManualAccountSnapshot,
) -> ManualAccountSnapshot:
    """Revalidate a typed snapshot so direct construction cannot bypass parsing."""

    if not isinstance(snapshot, ManualAccountSnapshot):
        raise TypeError("manual account snapshot is required")
    if not isinstance(snapshot.holdings, tuple):
        raise TypeError("manual account holdings must be a tuple")
    rows: list[dict[str, Any]] = []
    for holding in snapshot.holdings:
        if not isinstance(holding, ManualAccountHolding):
            raise TypeError("manual account holding is required")
        rows.append({
            "section": holding.section,
            "name": holding.name,
            "ticker": holding.ticker,
            "quantity": holding.quantity,
            "average_cost": holding.average_cost,
            "purchase_total": holding.purchase_total,
        })
    return parse_manual_account_snapshot({
        "schema_version": MANUAL_ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
        "source_sheet": snapshot.source_sheet,
        "snapshot_date": snapshot.snapshot_date,
        "currency": snapshot.currency,
        "holdings": rows,
    })


__all__ = [
    "MANUAL_ACCOUNT_CURRENCY",
    "MANUAL_ACCOUNT_SECTIONS",
    "MANUAL_ACCOUNT_SNAPSHOT_SCHEMA_VERSION",
    "MANUAL_ACCOUNT_SNAPSHOT_DATE",
    "MANUAL_ACCOUNT_SOURCE_SHEET",
    "ManualAccountHolding",
    "ManualAccountSnapshot",
    "parse_manual_account_snapshot",
    "validate_manual_account_snapshot",
]
