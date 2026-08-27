from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SCHEMA_VERSION = 1
ACCOUNT_KINDS = frozenset({"PENSION", "ISA", "GENERAL"})
_SOURCE_ID = re.compile(r"manual:[a-z0-9][a-z0-9_-]{0,47}")
_TICKER = re.compile(r"\d{6}")
_ACCOUNT_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){9,13}\d(?!\d)")


@dataclass(frozen=True, slots=True)
class ManualAccountPosition:
    name: str
    ticker: str
    quantity: float
    average_cost: float | None
    purchase_total: float | None


@dataclass(frozen=True, slots=True)
class ManualAccountRecord:
    source_id: str
    label: str
    account_kind: str
    snapshot_date: str
    currency: str
    positions: tuple[ManualAccountPosition, ...]


@dataclass(frozen=True, slots=True)
class ManualAccountRegistry:
    accounts: tuple[ManualAccountRecord, ...] = ()


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise ValueError(f"{field} is outside the accepted range")
    return result


def _optional_number(value: Any, field: str) -> float | None:
    return None if value is None else _number(value, field)


def parse_manual_account_registry(payload: Any) -> ManualAccountRegistry:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "accounts"}:
        raise ValueError("manual account registry keys do not match schema v1")
    if (
        not isinstance(payload["schema_version"], int)
        or isinstance(payload["schema_version"], bool)
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("manual account registry schema version is unsupported")
    rows = payload["accounts"]
    if not isinstance(rows, list):
        raise TypeError("manual account registry accounts must be a list")
    accounts: list[ManualAccountRecord] = []
    seen_sources: set[str] = set()
    for row in rows:
        required = {"source_id", "label", "account_kind", "snapshot_date", "currency", "positions"}
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("manual account keys do not match schema v1")
        source_id = row["source_id"]
        label = row["label"]
        if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
            raise ValueError("manual account source_id is invalid")
        if source_id in seen_sources:
            raise ValueError("manual account source_id is duplicated")
        if (
            not isinstance(label, str) or not label.strip() or label != label.strip()
            or len(label) > 60 or _ACCOUNT_NUMBER.search(label)
        ):
            raise ValueError("manual account label is invalid or private-shaped")
        if row["account_kind"] not in ACCOUNT_KINDS:
            raise ValueError("manual account kind is unsupported")
        snapshot_date = row["snapshot_date"]
        if not isinstance(snapshot_date, str) or date.fromisoformat(snapshot_date).isoformat() != snapshot_date:
            raise ValueError("manual account snapshot_date is invalid")
        if row["currency"] != "KRW":
            raise ValueError("manual account currency must be KRW")
        raw_positions = row["positions"]
        if not isinstance(raw_positions, list) or not raw_positions:
            raise ValueError("manual account positions must be a non-empty list")
        positions: list[ManualAccountPosition] = []
        seen_tickers: set[str] = set()
        for item in raw_positions:
            keys = {"name", "ticker", "quantity", "average_cost", "purchase_total"}
            if not isinstance(item, dict) or set(item) != keys:
                raise ValueError("manual account position keys do not match schema v1")
            name, ticker = item["name"], item["ticker"]
            if (
                not isinstance(name, str) or not name.strip()
                or name != name.strip() or len(name) > 80
                or _ACCOUNT_NUMBER.search(name)
            ):
                raise ValueError("manual account position name is invalid or private-shaped")
            if not isinstance(ticker, str) or _TICKER.fullmatch(ticker) is None or ticker in seen_tickers:
                raise ValueError("manual account position ticker is invalid or duplicated")
            quantity = _number(item["quantity"], "position.quantity", positive=True)
            average_cost = _optional_number(item["average_cost"], "position.average_cost")
            purchase_total = _optional_number(item["purchase_total"], "position.purchase_total")
            if average_cost is not None and purchase_total is not None:
                if not math.isclose(quantity * average_cost, purchase_total, rel_tol=1e-9, abs_tol=1e-6):
                    raise ValueError("manual account purchase total does not reconcile")
            positions.append(ManualAccountPosition(name, ticker, quantity, average_cost, purchase_total))
            seen_tickers.add(ticker)
        seen_sources.add(source_id)
        accounts.append(ManualAccountRecord(
            source_id, label, row["account_kind"], snapshot_date, "KRW", tuple(positions),
        ))
    return ManualAccountRegistry(tuple(accounts))


def manual_account_registry_payload(registry: ManualAccountRegistry) -> dict[str, object]:
    if not isinstance(registry, ManualAccountRegistry):
        raise TypeError("manual account registry is required")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "accounts": [
            {
                "source_id": account.source_id,
                "label": account.label,
                "account_kind": account.account_kind,
                "snapshot_date": account.snapshot_date,
                "currency": account.currency,
                "positions": [
                    {
                        "name": position.name,
                        "ticker": position.ticker,
                        "quantity": position.quantity,
                        "average_cost": position.average_cost,
                        "purchase_total": position.purchase_total,
                    }
                    for position in account.positions
                ],
            }
            for account in registry.accounts
        ],
    }
    parse_manual_account_registry(payload)
    return payload


class LocalManualAccountStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> ManualAccountRegistry:
        try:
            return parse_manual_account_registry(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return ManualAccountRegistry()

    def save(self, registry: ManualAccountRegistry) -> None:
        payload = manual_account_registry_payload(registry)
        body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = [
    "ACCOUNT_KINDS", "LocalManualAccountStore", "ManualAccountPosition",
    "ManualAccountRecord", "ManualAccountRegistry", "manual_account_registry_payload",
    "parse_manual_account_registry",
]
