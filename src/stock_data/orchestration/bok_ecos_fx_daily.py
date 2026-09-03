"""Bounded daily orchestration for official BOK ECOS USD/KRW observations.

The 17:00 KST availability clock is a project operating assumption requested
for current display, not a verified BOK publication guarantee.  Missing target
rows are therefore ``EXPECTED_PROVIDER_LAG`` and never overwrite prior data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.contracts.bok_ecos_fx import BOK_ECOS_USD_KRW_DAILY
from stock_data.providers.bok_ecos_fx_daily import (
    INFO_NO_DATA,
    capture_range,
    validate_window,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.storage.contract_arrow import dataframe_to_contract_table
from stock_data.validation.data_v1 import validate_data_v1


DATASET_ID = BOK_ECOS_USD_KRW_DAILY.name
NORMALIZED_RELATIVE = Path("data/normalized") / DATASET_ID
STATE_RELATIVE = Path("data/state") / f"{DATASET_ID}.json"
KST = ZoneInfo("Asia/Seoul")
MAX_DAILY_SESSIONS = 30


class BokFxPlanAction(StrEnum):
    NOOP_ALREADY_CURRENT = "NOOP_ALREADY_CURRENT"
    COLLECT = "COLLECT"


@dataclass(frozen=True)
class BokFxDailyPlan:
    target_session: date
    action: BokFxPlanAction
    start: date | None
    end: date | None
    sessions: tuple[date, ...]
    max_api_calls: int


def _previous_business_day(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _next_business_day(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def business_sessions(start: date, end: date) -> tuple[date, ...]:
    if start > end:
        return ()
    values: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return tuple(values)


def target_session(now: datetime) -> date:
    """Return the requested 17:00 KST weekday target.

    ECOS holiday/publication timing remains unverified, so this is deliberately
    a Monday-Friday operational calendar rather than an asserted BOK calendar.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(KST)
    candidate = local.date() if local.hour >= 17 else local.date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def plan_daily_operation(
    *, retained_latest: date | None, target: date,
) -> BokFxDailyPlan:
    if retained_latest is not None and retained_latest >= target:
        return BokFxDailyPlan(
            target, BokFxPlanAction.NOOP_ALREADY_CURRENT, None, None, (), 0,
        )
    start = target if retained_latest is None else _next_business_day(retained_latest)
    sessions = business_sessions(start, target)[:MAX_DAILY_SESSIONS]
    if not sessions:
        return BokFxDailyPlan(
            target, BokFxPlanAction.NOOP_ALREADY_CURRENT, None, None, (), 0,
        )
    return BokFxDailyPlan(
        target, BokFxPlanAction.COLLECT, sessions[0], sessions[-1], sessions, 1,
    )


def validate_bok_fx(frame: pd.DataFrame) -> None:
    validate_data_v1(frame, BOK_ECOS_USD_KRW_DAILY, allow_empty=False)
    if (pd.to_numeric(frame["rate_krw_per_usd"], errors="coerce") <= 0).any():
        raise ValueError("BOK USD/KRW rate must be positive")
    if set(frame["item_code"].astype(str)) != {"0000001"}:
        raise ValueError("BOK USD/KRW item identity differs")
    if set(frame["stat_code"].astype(str)) != {"731Y001"}:
        raise ValueError("BOK USD/KRW table identity differs")
    if set(frame["source"].astype(str)) != {"BOK_ECOS"}:
        raise ValueError("BOK USD/KRW source identity differs")
    if set(frame["source_operation"].astype(str)) != {"StatisticSearch"}:
        raise ValueError("BOK USD/KRW operation identity differs")
    if frame["unit"].astype(str).str.strip().eq("").any():
        raise ValueError("BOK USD/KRW unit is empty")


def _read_existing(project_root: Path) -> pd.DataFrame:
    root = project_root / NORMALIZED_RELATIVE
    try:
        return read_dataset(root, BOK_ECOS_USD_KRW_DAILY, validate_bok_fx)
    except FileNotFoundError:
        return pd.DataFrame(columns=BOK_ECOS_USD_KRW_DAILY.column_names)


def retained_latest(project_root: Path) -> date | None:
    frame = _read_existing(Path(project_root).resolve())
    if frame.empty:
        return None
    return pd.to_datetime(frame["date"], errors="raise").max().date()


def _merge_append_only(existing: pd.DataFrame, incoming: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if existing.empty:
        result = incoming.copy()
        validate_bok_fx(result)
        return result, len(result)
    by_date = {
        pd.Timestamp(row.date).date(): row
        for row in existing.itertuples(index=False)
    }
    accepted: list[int] = []
    identity_fields = (
        "rate_krw_per_usd", "item_code", "stat_code", "unit", "source",
        "source_operation",
    )
    for index, row in incoming.iterrows():
        observed = pd.Timestamp(row["date"]).date()
        prior = by_date.get(observed)
        if prior is None:
            accepted.append(index)
            continue
        if any(getattr(prior, field) != row[field] for field in identity_fields):
            raise ValueError(
                f"append-only BOK USD/KRW conflict for retained date {observed.isoformat()}"
            )
    additions = incoming.loc[accepted]
    merged = pd.concat([existing, additions], ignore_index=True)
    merged = merged.sort_values("date", kind="stable").reset_index(drop=True)
    validate_bok_fx(merged)
    return merged, len(additions)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_range(
    project_root: Path,
    *,
    start: date,
    end: date,
    api_key: str,
    session: Any | None = None,
    retrieved_at: datetime | None = None,
) -> dict[str, object]:
    """Capture one <=400-day range and atomically append validated new dates."""
    validate_window(start, end)
    root = Path(project_root).resolve()
    existing = _read_existing(root)
    latest_before = (
        pd.to_datetime(existing["date"], errors="raise").max().date()
        if not existing.empty else None
    )
    captured = capture_range(
        root, start=start, end=end, api_key=api_key, session=session,
        retrieved_at=retrieved_at,
    )
    if captured.result_code == INFO_NO_DATA:
        return {
            "schema_version": 1, "dataset": DATASET_ID,
            "status": "EXPECTED_PROVIDER_LAG", "api_calls": captured.api_calls,
            "retry_count": 0, "rows_received": 0, "rows_added": 0,
            "latest_before": latest_before.isoformat() if latest_before else None,
            "latest_after": latest_before.isoformat() if latest_before else None,
            "run_id": captured.run_id,
        }
    merged, added = _merge_append_only(existing, captured.frame)
    if added:
        dataset_root = root / NORMALIZED_RELATIVE
        write_dataset_atomic(merged, dataset_root, BOK_ECOS_USD_KRW_DAILY, validate_bok_fx)
        verified = read_dataset(dataset_root, BOK_ECOS_USD_KRW_DAILY, validate_bok_fx)
        if not dataframe_to_contract_table(
            verified, BOK_ECOS_USD_KRW_DAILY,
        ).equals(dataframe_to_contract_table(merged, BOK_ECOS_USD_KRW_DAILY)):
            raise ValueError("BOK USD/KRW Normalized read-back differs")
    latest_after = pd.to_datetime(merged["date"], errors="raise").max().date()
    status = "PROMOTED" if added else "NOOP_IDEMPOTENT"
    state = {
        "schema_version": 1,
        "dataset": DATASET_ID,
        "contract_version": BOK_ECOS_USD_KRW_DAILY.version,
        "status": status,
        "latest": latest_after.isoformat(),
        "run_id": captured.run_id,
        "landing_response_sha256": captured.response_sha256,
        "rows_total": len(merged),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(root / STATE_RELATIVE, state)
    if json.loads((root / STATE_RELATIVE).read_text(encoding="utf-8")) != state:
        raise ValueError("BOK USD/KRW state read-back differs")
    return {
        "schema_version": 1, "dataset": DATASET_ID, "status": status,
        "api_calls": captured.api_calls, "retry_count": 0,
        "rows_received": len(captured.frame), "rows_added": added,
        "latest_before": latest_before.isoformat() if latest_before else None,
        "latest_after": latest_after.isoformat(), "run_id": captured.run_id,
    }


def run_daily_lane(
    project_root: Path,
    *,
    target: date,
    api_key: str,
    session: Any | None = None,
    retrieved_at: datetime | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    before = retained_latest(root)
    plan = plan_daily_operation(retained_latest=before, target=target)
    if plan.action is BokFxPlanAction.NOOP_ALREADY_CURRENT:
        return {
            "schema_version": 1, "lane": "BOK_FX_DAILY",
            "status": "NOOP_IDEMPOTENT", "target_session": target.isoformat(),
            "window": None, "api_calls": 0, "retry_count": 0,
            "latest_before": before.isoformat() if before else None,
            "latest_after": before.isoformat() if before else None,
            "reason": "ALREADY_CURRENT",
        }
    assert plan.start is not None and plan.end is not None
    result = refresh_range(
        root, start=plan.start, end=plan.end, api_key=api_key,
        session=session, retrieved_at=retrieved_at,
    )
    latest_after = (
        date.fromisoformat(str(result["latest_after"]))
        if result.get("latest_after") else None
    )
    target_in_result = latest_after is not None and latest_after >= target
    status = (
        "EXPECTED_PROVIDER_LAG"
        if not target_in_result else
        "NOOP_IDEMPOTENT" if result["status"] == "NOOP_IDEMPOTENT" else
        "COMPLETE"
    )
    return {
        **result,
        "lane": "BOK_FX_DAILY",
        "status": status,
        "target_session": target.isoformat(),
        "window": {"start": plan.start.isoformat(), "end": plan.end.isoformat()},
        "sessions_planned": [value.isoformat() for value in plan.sessions],
        "reason": (
            "TARGET_ROW_NOT_YET_AVAILABLE"
            if status == "EXPECTED_PROVIDER_LAG" else
            "TARGET_PRESENT_APPEND_ONLY"
        ),
    }


__all__ = [
    "BokFxDailyPlan", "BokFxPlanAction", "DATASET_ID", "MAX_DAILY_SESSIONS",
    "business_sessions", "plan_daily_operation", "refresh_range",
    "retained_latest", "run_daily_lane", "target_session", "validate_bok_fx",
]
