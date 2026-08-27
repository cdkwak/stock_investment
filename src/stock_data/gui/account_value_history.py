"""Identifier-free local history of accepted read-only account scale.

The history records account size observations, not investment performance.
External cash flows, deposits, withdrawals, purchases and sales are not
separated because neither accepted account route supplies a complete cash-flow
ledger.  Currencies and sources therefore remain independent.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4


ACCOUNT_VALUE_HISTORY_SCHEMA_VERSION = 1
ACCOUNT_VALUE_HISTORY_SOURCES = ("toss_self", "kb_self")
ACCOUNT_VALUE_HISTORY_METRICS = (
    "TOTAL_ASSETS",
    "OBSERVABLE_COMPONENT_SUM",
    "SECURITIES_VALUE",
)


@dataclass(frozen=True)
class AccountValueHistoryPoint:
    observed_at: str
    value: float
    securities_value: float | None
    cash_buying_power: float | None


@dataclass(frozen=True)
class AccountValueHistorySeries:
    source_id: str
    currency: str
    metric: str
    points: tuple[AccountValueHistoryPoint, ...]


def _aware_iso(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an aware ISO timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    # One instant has one storage identity.  Preserving the caller's offset
    # would let +09:00 and +00:00 bypass duplicate/conflict checks.
    return parsed.astimezone(timezone.utc).isoformat()


def _decimal_text(value: Any, field: str, *, nonnegative: bool) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a decimal string or null")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field} must be finite") from None
    if not number.is_finite() or (nonnegative and number < 0):
        raise ValueError(f"{field} is outside its accepted domain")
    return value


def _sum_decimal_text(left: str | None, right: str | None) -> str | None:
    if left is None or right is None:
        return None
    return format(Decimal(left) + Decimal(right), "f")


def toss_account_value_observation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project a validated Toss snapshot to value-only currency observations."""

    if not isinstance(snapshot, dict):
        raise ValueError("Toss account snapshot must be an object")
    if snapshot.get("provider") != "tossinvest_open_api":
        raise ValueError("Toss account history provider differs")
    observed_at = _aware_iso(snapshot.get("collected_at"), "collected_at")
    summaries = snapshot.get("summaries")
    buying_power = snapshot.get("buying_power")
    if not isinstance(summaries, list) or not (
        isinstance(buying_power, list) or buying_power is None
    ):
        raise ValueError("Toss account history components are incomplete")
    cash_by_currency: dict[str, str] = {}
    for row in buying_power or ():
        if not isinstance(row, dict) or row.get("currency") not in {"KRW", "USD"}:
            raise ValueError("Toss buying-power history identity differs")
        currency = row["currency"]
        if currency in cash_by_currency:
            raise ValueError("Toss buying-power history currency is duplicated")
        cash = _decimal_text(
            row.get("cash_buying_power"), "cash_buying_power", nonnegative=True,
        )
        if cash is None:
            raise ValueError("Toss buying power cannot be null")
        cash_by_currency[currency] = cash

    currencies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in summaries:
        if not isinstance(row, dict) or row.get("currency") not in {"KRW", "USD"}:
            raise ValueError("Toss summary history identity differs")
        currency = row["currency"]
        if currency in seen:
            raise ValueError("Toss summary history currency is duplicated")
        seen.add(currency)
        securities = _decimal_text(
            row.get("market_value"), "market_value", nonnegative=True,
        )
        purchase = _decimal_text(
            row.get("purchase_amount"), "purchase_amount", nonnegative=True,
        )
        pnl = _decimal_text(
            row.get("profit_loss"), "profit_loss", nonnegative=False,
        )
        cash = cash_by_currency.get(currency)
        metric = (
            "OBSERVABLE_COMPONENT_SUM" if cash is not None
            else "SECURITIES_VALUE"
        )
        currencies.append({
            "currency": currency,
            "metric": metric,
            "value": (
                _sum_decimal_text(securities, cash)
                if cash is not None else securities
            ),
            "total_assets": None,
            "securities_value": securities,
            "cash_buying_power": cash,
            "purchase_amount": purchase,
            "unrealized_pnl": pnl,
        })
    return validate_account_value_observation({
        "schema_version": ACCOUNT_VALUE_HISTORY_SCHEMA_VERSION,
        "source_id": "toss_self",
        "observed_at": observed_at,
        "interpretation": "ACCOUNT_SCALE_NOT_PERFORMANCE_EXTERNAL_FLOWS_UNSEPARATED",
        "currencies": currencies,
    })


def kb_account_value_observation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project a validated KB snapshot to the provider's exact KRW total."""

    if not isinstance(snapshot, dict) or snapshot.get("provider") != "kbsec_open_api":
        raise ValueError("KB account history provider differs")
    observed_at = _aware_iso(snapshot.get("collected_at"), "collected_at")
    total = _decimal_text(snapshot.get("total_assets"), "total_assets", nonnegative=True)
    securities = _decimal_text(
        snapshot.get("securities_value"), "securities_value", nonnegative=True,
    )
    purchase = _decimal_text(
        snapshot.get("purchase_amount"), "purchase_amount", nonnegative=True,
    )
    pnl = _decimal_text(
        snapshot.get("unrealized_pnl"), "unrealized_pnl", nonnegative=False,
    )
    return validate_account_value_observation({
        "schema_version": ACCOUNT_VALUE_HISTORY_SCHEMA_VERSION,
        "source_id": "kb_self",
        "observed_at": observed_at,
        "interpretation": "ACCOUNT_SCALE_NOT_PERFORMANCE_EXTERNAL_FLOWS_UNSEPARATED",
        "currencies": [{
            "currency": "KRW",
            "metric": "TOTAL_ASSETS",
            "value": total,
            "total_assets": total,
            "securities_value": securities,
            "cash_buying_power": None,
            "purchase_amount": purchase,
            "unrealized_pnl": pnl,
        }],
    })


def validate_account_value_observation(payload: Any) -> dict[str, Any]:
    required = {
        "schema_version", "source_id", "observed_at", "interpretation", "currencies",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("account value history keys differ")
    if payload["schema_version"] != ACCOUNT_VALUE_HISTORY_SCHEMA_VERSION:
        raise ValueError("account value history schema differs")
    if payload["source_id"] not in ACCOUNT_VALUE_HISTORY_SOURCES:
        raise ValueError("account value history source differs")
    payload = dict(payload)
    payload["observed_at"] = _aware_iso(payload["observed_at"], "observed_at")
    if payload["interpretation"] != "ACCOUNT_SCALE_NOT_PERFORMANCE_EXTERNAL_FLOWS_UNSEPARATED":
        raise ValueError("account value history interpretation differs")
    rows = payload["currencies"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("account value history currencies are empty")
    row_keys = {
        "currency", "metric", "value", "total_assets", "securities_value",
        "cash_buying_power", "purchase_amount", "unrealized_pnl",
    }
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_keys:
            raise ValueError("account value history currency keys differ")
        currency = row["currency"]
        if currency not in {"KRW", "USD"} or currency in seen:
            raise ValueError("account value history currency differs")
        seen.add(currency)
        metric = row["metric"]
        if metric not in ACCOUNT_VALUE_HISTORY_METRICS:
            raise ValueError("account value history metric differs")
        values = {
            "value": _decimal_text(row["value"], "value", nonnegative=True),
            "total_assets": _decimal_text(
                row["total_assets"], "total_assets", nonnegative=True,
            ),
            "securities_value": _decimal_text(
                row["securities_value"], "securities_value", nonnegative=True,
            ),
            "cash_buying_power": _decimal_text(
                row["cash_buying_power"], "cash_buying_power", nonnegative=True,
            ),
            "purchase_amount": _decimal_text(
                row["purchase_amount"], "purchase_amount", nonnegative=True,
            ),
            "unrealized_pnl": _decimal_text(
                row["unrealized_pnl"], "unrealized_pnl", nonnegative=False,
            ),
        }
        if values["value"] is None:
            raise ValueError("account value history primary value is unavailable")
        if metric == "TOTAL_ASSETS" and values["total_assets"] != values["value"]:
            raise ValueError("account total-assets history does not reconcile")
        if metric == "OBSERVABLE_COMPONENT_SUM":
            expected = _sum_decimal_text(
                values["securities_value"], values["cash_buying_power"],
            )
            if expected is None or Decimal(expected) != Decimal(values["value"]):
                raise ValueError("account observable history does not reconcile")
        if metric == "SECURITIES_VALUE" and values["securities_value"] != values["value"]:
            raise ValueError("account securities-value history does not reconcile")
        normalized.append({"currency": currency, "metric": metric, **values})
    source_id = payload["source_id"]
    if source_id == "kb_self":
        if len(normalized) != 1:
            raise ValueError("KB account history cardinality differs")
        row = normalized[0]
        if row["currency"] != "KRW" or row["metric"] != "TOTAL_ASSETS":
            raise ValueError("KB account history identity differs")
        if row["cash_buying_power"] is not None:
            raise ValueError("KB account history components differ")
    else:
        toss_metrics = {row["metric"] for row in normalized}
        if len(toss_metrics) != 1:
            raise ValueError("Toss account history schema generation differs")
        for row in normalized:
            if row["metric"] not in {
                "OBSERVABLE_COMPONENT_SUM", "SECURITIES_VALUE",
            } or row["total_assets"] is not None:
                raise ValueError("Toss account history identity differs")
            if (
                row["metric"] == "OBSERVABLE_COMPONENT_SUM"
                and row["cash_buying_power"] is None
            ) or (
                row["metric"] == "SECURITIES_VALUE"
                and row["cash_buying_power"] is not None
            ):
                raise ValueError("Toss account history components differ")
    payload["currencies"] = normalized
    return payload


def load_account_value_history(history_root: Path) -> tuple[AccountValueHistorySeries, ...]:
    """Load all complete immutable observations; any corrupt file fails closed."""

    root = Path(history_root)
    grouped: dict[tuple[str, str, str], list[AccountValueHistoryPoint]] = {}
    identities: set[tuple[str, str, str]] = set()
    for source_id in ACCOUNT_VALUE_HISTORY_SOURCES:
        source_root = root / source_id
        if source_root.exists() and (source_root.is_symlink() or not source_root.is_dir()):
            raise ValueError("account value history source root is unsafe")
        for path in sorted(source_root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise ValueError("account value history observation is unsafe")
            try:
                payload = validate_account_value_observation(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                raise ValueError("account value history is invalid") from None
            if payload["source_id"] != source_id:
                raise ValueError("account value history path/source differs")
            for row in payload["currencies"]:
                identity = (source_id, row["currency"], payload["observed_at"])
                if identity in identities:
                    raise ValueError("account value history observation is duplicated")
                identities.add(identity)
                key = (source_id, row["currency"], row["metric"])
                grouped.setdefault(key, []).append(AccountValueHistoryPoint(
                    observed_at=payload["observed_at"],
                    value=float(row["value"]),
                    securities_value=(
                        None if row["securities_value"] is None
                        else float(row["securities_value"])
                    ),
                    cash_buying_power=(
                        None if row["cash_buying_power"] is None
                        else float(row["cash_buying_power"])
                    ),
                ))
    series: list[AccountValueHistorySeries] = []
    for (source_id, currency, metric), points in sorted(grouped.items()):
        points.sort(key=lambda point: datetime.fromisoformat(point.observed_at))
        series.append(AccountValueHistorySeries(
            source_id=source_id,
            currency=currency,
            metric=metric,
            points=tuple(points),
        ))
    return tuple(series)


def _persist_account_value_observation_unlocked(
    project_root: Path,
    payload: dict[str, Any],
) -> str:
    """Atomically add one evidence-backed observation or return `NOOP`.

    This is used only to bootstrap an already accepted retained snapshot. New
    provider refreshes promote history inside their snapshot transaction.
    """

    root = Path(project_root).resolve()
    observation = validate_account_value_observation(payload)
    body = json.dumps(
        observation, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    observed = datetime.fromisoformat(observation["observed_at"])
    stamp = observed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    source_root = (
        root / "data/local/account_value_history" / observation["source_id"]
    )
    source_root.mkdir(parents=True, exist_ok=True)
    for existing in source_root.glob("*.json"):
        try:
            current = validate_account_value_observation(
                json.loads(existing.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            raise ValueError("account value history is invalid") from None
        if current["observed_at"] != observation["observed_at"]:
            continue
        current_body = json.dumps(
            current, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if current_body == body:
            return "NOOP"
        raise ValueError("account value history identity conflicts")
    target = source_root / f"{stamp}-{digest[:12]}-bootstrap.json"
    stage = root / "data/staging/account_value_history" / uuid4().hex
    candidate = stage / "observation.json"
    try:
        stage.mkdir(parents=True, exist_ok=False)
        with candidate.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise FileExistsError("account value history target exists")
        os.replace(candidate, target)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return "CREATED"


def persist_account_value_observation(
    project_root: Path,
    payload: dict[str, Any],
) -> str:
    """Bootstrap one accepted snapshot while excluding privacy removal."""

    # Local import keeps the value contract independent of orchestration at
    # module load time while sharing the same cross-process privacy boundary.
    from stock_data.orchestration.account_privacy import (
        account_snapshot_lifecycle_lock,
    )

    with account_snapshot_lifecycle_lock(project_root):
        return _persist_account_value_observation_unlocked(project_root, payload)


__all__ = [
    "ACCOUNT_VALUE_HISTORY_SCHEMA_VERSION",
    "AccountValueHistoryPoint",
    "AccountValueHistorySeries",
    "kb_account_value_observation",
    "load_account_value_history",
    "persist_account_value_observation",
    "toss_account_value_observation",
    "validate_account_value_observation",
]
