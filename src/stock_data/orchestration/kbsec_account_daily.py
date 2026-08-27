"""Occurrence-idempotent daily runner for the read-only KB account snapshot."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from stock_data.orchestration.kbsec_account_runtime import (
    KBSecAccountRuntimeWiring,
    build_kbsec_account_runtime,
)
from stock_data.orchestration.toss_account_snapshot import AccountRefreshTrigger
from stock_data.providers.kbsec.client import KBSecClient


KBSEC_ACCOUNT_DAILY_OPERATION = "KBSEC_ACCOUNT_READONLY_DAILY"
KBSEC_ACCOUNT_DAILY_SCHEMA_VERSION = 1
KBSEC_ACCOUNT_DAILY_TIMEZONE = ZoneInfo("Asia/Seoul")
KBSEC_ACCOUNT_DAILY_TIME = time(7, 10)
KBSEC_ACCOUNT_SNAPSHOT_PATH = "data/local/account_snapshots/kb_self.json"

_CLAIM_KEYS = {
    "schema_version", "operation", "occurrence_date", "scheduled_for",
    "claimed_at_utc", "status",
}
_TERMINAL_KEYS = _CLAIM_KEYS | {
    "finished_at_utc", "outcome", "reason", "supplier_calls", "snapshot",
    "snapshot_sha256",
}
_RUNTIME_REASONS = {
    "RUNTIME_CONFIG_REQUIRED", "CLIENT_INITIALIZATION_FAILED",
    "LIVE_EXECUTION_AUTHORITY_REQUIRED",
}
_REFRESH_FAILURE_REASONS = {
    "KB_ACCOUNT_LOCK_TIMEOUT", "KB_ACCOUNT_SUPPLIER_TIMEOUT",
    "KB_ACCOUNT_SUPPLIER_AUTH_FAILED", "KB_ACCOUNT_RESPONSE_REJECTED",
    "KB_ACCOUNT_SUPPLIER_FAILED", "KB_ACCOUNT_PERSISTENCE_FAILED",
}


class KBSecAccountScheduleError(RuntimeError):
    pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise KBSecAccountScheduleError("scheduled KB receipt has duplicate keys")
        result[key] = value
    return result


def _strict_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, KBSecAccountScheduleError) as error:
        raise KBSecAccountScheduleError("scheduled KB receipt is invalid") from error
    if not isinstance(value, dict):
        raise KBSecAccountScheduleError("scheduled KB receipt is not an object")
    return value


def _aware_clock(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise KBSecAccountScheduleError(f"scheduled KB {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise KBSecAccountScheduleError(f"scheduled KB {field} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KBSecAccountScheduleError(f"scheduled KB {field} is naive")
    return parsed


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _occurrence(now: datetime) -> tuple[date, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduled KB refresh clock must be timezone-aware")
    local = now.astimezone(KBSEC_ACCOUNT_DAILY_TIMEZONE)
    day = local.date()
    scheduled = datetime.combine(
        day, KBSEC_ACCOUNT_DAILY_TIME, KBSEC_ACCOUNT_DAILY_TIMEZONE,
    )
    if local < scheduled:
        day -= timedelta(days=1)
        scheduled = datetime.combine(
            day, KBSEC_ACCOUNT_DAILY_TIME, KBSEC_ACCOUNT_DAILY_TIMEZONE,
        )
    return day, scheduled


def _receipt_path(root: Path, occurrence_date: date) -> Path:
    return (
        root.resolve() / "data/state/kbsec_account_snapshot_occurrences"
        / f"{occurrence_date.isoformat()}.json"
    )


def _claim(
    root: Path, *, occurrence_date: date, scheduled_for: datetime,
    claimed_at: datetime,
) -> tuple[Path, bool]:
    path = _receipt_path(root, occurrence_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    claim = {
        "schema_version": KBSEC_ACCOUNT_DAILY_SCHEMA_VERSION,
        "operation": KBSEC_ACCOUNT_DAILY_OPERATION,
        "occurrence_date": occurrence_date.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "claimed_at_utc": claimed_at.astimezone(timezone.utc).isoformat(),
        "status": "CLAIMED_BEFORE_PROVIDER",
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return path, False
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_json_bytes(claim))
        stream.flush()
        os.fsync(stream.fileno())
    return path, True


def strict_kbsec_account_daily_receipt(
    path: Path, *, occurrence_date: date,
) -> dict[str, Any]:
    try:
        payload = _strict_json_object(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, KBSecAccountScheduleError) as error:
        raise KBSecAccountScheduleError("scheduled KB receipt is invalid") from error
    status = payload.get("status")
    keys = _CLAIM_KEYS if status == "CLAIMED_BEFORE_PROVIDER" else _TERMINAL_KEYS
    if (
        set(payload) != keys
        or payload.get("schema_version") != KBSEC_ACCOUNT_DAILY_SCHEMA_VERSION
        or payload.get("operation") != KBSEC_ACCOUNT_DAILY_OPERATION
        or payload.get("occurrence_date") != occurrence_date.isoformat()
        or status not in {
            "CLAIMED_BEFORE_PROVIDER", "TERMINAL_SUCCESS", "TERMINAL_FAILURE",
            "TERMINAL_INELIGIBLE",
        }
    ):
        raise KBSecAccountScheduleError("scheduled KB receipt identity differs")
    scheduled = _aware_clock(payload.get("scheduled_for"), field="scheduled_for")
    claimed = _aware_clock(payload.get("claimed_at_utc"), field="claimed_at_utc")
    expected = datetime.combine(
        occurrence_date, KBSEC_ACCOUNT_DAILY_TIME, KBSEC_ACCOUNT_DAILY_TIMEZONE,
    )
    if scheduled.astimezone(KBSEC_ACCOUNT_DAILY_TIMEZONE) != expected:
        raise KBSecAccountScheduleError("scheduled KB occurrence clock differs")
    if status == "CLAIMED_BEFORE_PROVIDER":
        return payload
    finished = _aware_clock(payload.get("finished_at_utc"), field="finished_at_utc")
    calls = payload.get("supplier_calls")
    if finished < claimed or type(calls) is not int or not 0 <= calls <= 1:
        raise KBSecAccountScheduleError("scheduled KB terminal evidence differs")
    if status == "TERMINAL_SUCCESS":
        digest = payload.get("snapshot_sha256")
        if (
            payload.get("outcome") != "SUCCEEDED"
            or payload.get("reason") is not None
            or calls != 1
            or payload.get("snapshot") != KBSEC_ACCOUNT_SNAPSHOT_PATH
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise KBSecAccountScheduleError("scheduled KB success differs")
    else:
        if payload.get("snapshot") is not None or payload.get("snapshot_sha256") is not None:
            raise KBSecAccountScheduleError("scheduled KB failure retained a target")
        outcome = payload.get("outcome")
        reason = payload.get("reason")
        if status == "TERMINAL_INELIGIBLE":
            if outcome != "NOT_AVAILABLE" or calls != 0 or reason not in _RUNTIME_REASONS:
                raise KBSecAccountScheduleError("scheduled KB ineligible result differs")
        elif not (
            (outcome == "FAILED_PRESERVED_PRIOR" and reason in _REFRESH_FAILURE_REASONS)
            or (outcome == "SCHEDULE_INTERNAL_FAILURE"
                and reason == "SCHEDULE_INTERNAL_FAILURE")
        ):
            raise KBSecAccountScheduleError("scheduled KB failure differs")
    return payload


def _terminalize(
    path: Path, claim: dict[str, Any], terminal: dict[str, Any],
    *, finished_at: datetime,
) -> None:
    if set(claim) != _CLAIM_KEYS or claim.get("status") != "CLAIMED_BEFORE_PROVIDER":
        raise KBSecAccountScheduleError("scheduled KB claim is not terminalizable")
    payload = {
        **claim, **terminal,
        "finished_at_utc": finished_at.astimezone(timezone.utc).isoformat(),
    }
    _atomic_json(path, payload)
    if _strict_json_object(path.read_text(encoding="utf-8")) != payload:
        raise KBSecAccountScheduleError("scheduled KB receipt readback differs")


def _summary(
    *, occurrence_date: date, scheduled_for: datetime, status: str,
    supplier_calls: int = 0, reason: str | None = None,
    outcome: str | None = None, receipt: str | None = None,
    retained_status: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": KBSEC_ACCOUNT_DAILY_SCHEMA_VERSION,
        "operation": KBSEC_ACCOUNT_DAILY_OPERATION,
        "occurrence_date": occurrence_date.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "status": status, "outcome": outcome, "reason": reason,
        "supplier_calls": supplier_calls, "receipt": receipt,
        "retained_status": retained_status,
    }


def run_kbsec_account_daily(
    project_root: Path,
    environment: Mapping[str, str],
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    client_factory: Callable[..., KBSecClient] = KBSecClient,
    runtime_builder: Callable[..., KBSecAccountRuntimeWiring] = build_kbsec_account_runtime,
) -> dict[str, Any]:
    """Run one identifier-free, occurrence-claimed KB read-only refresh."""

    root = project_root.resolve()
    started = now or datetime.now(timezone.utc)
    finished_clock = lambda: max(
        datetime.now(timezone.utc), started.astimezone(timezone.utc),
    )
    occurrence_date, scheduled_for = _occurrence(started)
    if dry_run:
        wiring = runtime_builder(root, environment, client_factory=client_factory)
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status="DRY_RUN_READY" if wiring.enabled else "DRY_RUN_INELIGIBLE",
            reason=wiring.reason,
        )

    path, claimed = _claim(
        root, occurrence_date=occurrence_date, scheduled_for=scheduled_for,
        claimed_at=started,
    )
    if not claimed:
        retained = strict_kbsec_account_daily_receipt(
            path, occurrence_date=occurrence_date,
        )
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status="NOOP_OCCURRENCE_ALREADY_CLAIMED",
            retained_status=str(retained["status"]),
            receipt=path.relative_to(root).as_posix(),
        )

    claim = strict_kbsec_account_daily_receipt(path, occurrence_date=occurrence_date)
    supplier_calls = 0
    try:
        wiring = runtime_builder(root, environment, client_factory=client_factory)
        if not wiring.enabled or wiring.refresher is None:
            terminal = {
                "status": "TERMINAL_INELIGIBLE", "outcome": "NOT_AVAILABLE",
                "reason": wiring.reason, "supplier_calls": 0,
                "snapshot": None, "snapshot_sha256": None,
            }
            _terminalize(path, claim, terminal, finished_at=finished_clock())
            return _summary(
                occurrence_date=occurrence_date, scheduled_for=scheduled_for,
                status=terminal["status"], outcome=terminal["outcome"],
                reason=terminal["reason"], receipt=path.relative_to(root).as_posix(),
            )

        result = wiring.refresher(AccountRefreshTrigger.PERIODIC)
        supplier_calls = result.supplier_calls
        if type(supplier_calls) is not int or not 0 <= supplier_calls <= 1:
            raise KBSecAccountScheduleError("scheduled KB call budget differs")
        if result.status not in {"SUCCEEDED", "FAILED_PRESERVED_PRIOR"}:
            raise KBSecAccountScheduleError("scheduled KB outcome differs")
        if result.status == "SUCCEEDED" and (
            supplier_calls != 1 or result.snapshot_path != KBSEC_ACCOUNT_SNAPSHOT_PATH
        ):
            raise KBSecAccountScheduleError("scheduled KB success evidence differs")
        terminal_status = (
            "TERMINAL_SUCCESS" if result.status == "SUCCEEDED" else "TERMINAL_FAILURE"
        )
        digest = None
        if terminal_status == "TERMINAL_SUCCESS":
            snapshot = (root / KBSEC_ACCOUNT_SNAPSHOT_PATH).resolve()
            snapshot.relative_to(root)
            digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        terminal = {
            "status": terminal_status, "outcome": result.status,
            "reason": result.reason, "supplier_calls": supplier_calls,
            "snapshot": result.snapshot_path, "snapshot_sha256": digest,
        }
        _terminalize(path, claim, terminal, finished_at=finished_clock())
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status=terminal_status, outcome=result.status, reason=result.reason,
            supplier_calls=supplier_calls, receipt=path.relative_to(root).as_posix(),
        )
    except Exception:
        terminal = {
            "status": "TERMINAL_FAILURE", "outcome": "SCHEDULE_INTERNAL_FAILURE",
            "reason": "SCHEDULE_INTERNAL_FAILURE", "supplier_calls": supplier_calls,
            "snapshot": None, "snapshot_sha256": None,
        }
        _terminalize(path, claim, terminal, finished_at=finished_clock())
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status=terminal["status"], outcome=terminal["outcome"],
            reason=terminal["reason"], supplier_calls=supplier_calls,
            receipt=path.relative_to(root).as_posix(),
        )


__all__ = [
    "KBSEC_ACCOUNT_DAILY_OPERATION", "KBSEC_ACCOUNT_DAILY_SCHEMA_VERSION",
    "KBSEC_ACCOUNT_DAILY_TIME", "KBSEC_ACCOUNT_DAILY_TIMEZONE",
    "KBSEC_ACCOUNT_SNAPSHOT_PATH", "KBSecAccountScheduleError",
    "run_kbsec_account_daily", "strict_kbsec_account_daily_receipt",
]
