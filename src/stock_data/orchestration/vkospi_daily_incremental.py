"""Offline, exact-date promotion for an already captured VKOSPI Landing response.

This module intentionally contains no HTTP/provider client.  Network capture is a
separate manual operation; this boundary only validates an immutable official KRX
response and atomically appends its single explicitly finalized market date to
Raw, Normalized, and checkpoint state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Callable

import pandas as pd

from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY, KR_VKOSPI_RAW_DAILY
from stock_data.providers.krx_mdc.vkospi import frames_from_history, parse_history_body
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.vkospi_daily import validate_vkospi_daily, validate_vkospi_raw_daily


class VKOSPIDailyOperationError(RuntimeError):
    """Fail-closed error for an unsafe VKOSPI daily promotion."""


@dataclass(frozen=True)
class VKOSPIDailyOperationResult:
    run_id: str
    status: str
    finalized_market_date: str
    raw_root: Path
    normalized_root: Path
    checkpoint_path: Path
    journal_path: Path
    landing_sha256: str
    inserted_rows: int
    total_rows: int


_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_RAW_VALUE_COLUMNS = tuple(KR_VKOSPI_RAW_DAILY.column_names[:9])
_NORMALIZED_VALUE_COLUMNS = tuple(KR_VKOSPI_DAILY.column_names[:8]) + ("pit_status",)


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise VKOSPIDailyOperationError("finalized_market_date must use YYYY-MM-DD") from error


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_existing(root: Path, contract, validator) -> pd.DataFrame | None:
    if not root.exists():
        return None
    try:
        return read_dataset(root, contract, validator)
    except FileNotFoundError as error:
        raise VKOSPIDailyOperationError(f"existing dataset root is empty: {root}") from error


def _same_values(left: pd.DataFrame, right: pd.DataFrame, columns: tuple[str, ...]) -> bool:
    left_values = left.loc[:, columns].reset_index(drop=True)
    right_values = right.loc[:, columns].reset_index(drop=True)
    return left_values.equals(right_values)


def _merge_one_date(
    existing: pd.DataFrame | None,
    incoming: pd.DataFrame,
    *,
    market_date: str,
    value_columns: tuple[str, ...],
    validator: Callable[[pd.DataFrame], None],
) -> tuple[pd.DataFrame, bool]:
    if existing is None:
        validator(incoming)
        return incoming.copy(), True

    validator(existing)
    overlap = existing.loc[existing["market_date"].eq(market_date)]
    if not overlap.empty:
        if not _same_values(overlap, incoming, value_columns):
            raise VKOSPIDailyOperationError("Landing values conflict with accepted VKOSPI row")
        return existing.copy(), False

    retained_latest = str(existing["market_date"].max())
    if market_date < retained_latest:
        raise VKOSPIDailyOperationError("historical target is outside exact-date daily append boundary")
    merged = pd.concat([existing, incoming], ignore_index=True).sort_values(
        "market_date", kind="stable"
    ).reset_index(drop=True)
    validator(merged)
    return merged, True


def run_offline_daily_append(
    landing_response: Path,
    *,
    finalized_market_date: str | date | datetime,
    finality_confirmed: bool,
    run_id: str,
    raw_root: Path,
    normalized_root: Path,
    state_root: Path,
    checkpoint_writer: Callable[[Path, object], None] = _write_json_atomic,
) -> VKOSPIDailyOperationResult:
    """Promote one immutable Landing response without making a provider call."""

    if not finality_confirmed:
        raise VKOSPIDailyOperationError("explicit operator finality confirmation is required")
    if not _RUN_ID.fullmatch(run_id):
        raise VKOSPIDailyOperationError("run_id contains unsafe characters")
    if not isinstance(landing_response, Path) or landing_response.suffix.lower() != ".json":
        raise VKOSPIDailyOperationError("run boundary requires one retained Landing JSON file")
    if not landing_response.is_file():
        raise VKOSPIDailyOperationError(f"Landing response does not exist: {landing_response}")

    selected_date = _parse_date(finalized_market_date).isoformat()
    landing_bytes = landing_response.read_bytes()
    landing_sha256 = hashlib.sha256(landing_bytes).hexdigest()
    try:
        rows, _, _ = parse_history_body(landing_bytes)
    except Exception as error:
        raise VKOSPIDailyOperationError(f"invalid Landing response: {type(error).__name__}") from error
    observed_dates = {
        datetime.strptime(str(row["TRD_DD"]), "%Y/%m/%d").date().isoformat() for row in rows
    }
    if observed_dates != {selected_date}:
        raise VKOSPIDailyOperationError(
            "Landing response must contain exactly the explicitly finalized market date"
        )

    collected_at = datetime.fromtimestamp(
        landing_response.stat().st_mtime, timezone.utc
    ).isoformat()
    raw_incoming, normalized_incoming = frames_from_history(
        rows,
        collected_at=collected_at,
        landing_reference=landing_response.as_posix(),
        response_sha256=landing_sha256,
    )
    validate_vkospi_raw_daily(raw_incoming)
    validate_vkospi_daily(normalized_incoming)

    raw_existing = _read_existing(raw_root, KR_VKOSPI_RAW_DAILY, validate_vkospi_raw_daily)
    normalized_existing = _read_existing(
        normalized_root, KR_VKOSPI_DAILY, validate_vkospi_daily
    )
    if (raw_existing is None) != (normalized_existing is None):
        raise VKOSPIDailyOperationError("Raw and Normalized retained state is inconsistent")
    if raw_existing is not None and normalized_existing is not None:
        if raw_existing["market_date"].tolist() != normalized_existing["market_date"].tolist():
            raise VKOSPIDailyOperationError("Raw and Normalized date keys are inconsistent")

    raw_merged, raw_inserted = _merge_one_date(
        raw_existing,
        raw_incoming,
        market_date=selected_date,
        value_columns=_RAW_VALUE_COLUMNS,
        validator=validate_vkospi_raw_daily,
    )
    normalized_merged, normalized_inserted = _merge_one_date(
        normalized_existing,
        normalized_incoming,
        market_date=selected_date,
        value_columns=_NORMALIZED_VALUE_COLUMNS,
        validator=validate_vkospi_daily,
    )
    if raw_inserted != normalized_inserted:
        raise VKOSPIDailyOperationError("Raw and Normalized overlap decisions differ")

    checkpoint_path = state_root / "kr_vkospi_daily.json"
    journal_path = state_root / "journal" / f"kr_vkospi_daily--{run_id}.json"
    if journal_path.exists():
        prior = json.loads(journal_path.read_text(encoding="utf-8"))
        if prior.get("status") == "SUCCEEDED" and prior.get("landing_sha256") == landing_sha256:
            return VKOSPIDailyOperationResult(
                run_id, "NOOP_IDEMPOTENT", selected_date, raw_root, normalized_root,
                checkpoint_path, journal_path, landing_sha256, 0, len(normalized_merged),
            )
        raise VKOSPIDailyOperationError("run_id already has a non-matching journal")

    status = "SUCCEEDED" if raw_inserted else "NOOP_IDEMPOTENT"
    checkpoint = {
        "status": "DAILY_INCREMENTAL_ACCEPTED_PIT_LIMITED",
        "last_accepted_market_date": str(normalized_merged["market_date"].max()),
        "rows": len(normalized_merged),
        "publication_revision_status": "UNRESOLVED",
        "landing_reference": landing_response.as_posix(),
        "response_sha256": landing_sha256,
        "last_run_id": run_id,
    }
    journal = {
        "version": 1,
        "dataset": "kr_vkospi_daily",
        "run_id": run_id,
        "status": "PREPARED",
        "finalized_market_date": selected_date,
        "landing_sha256": landing_sha256,
        "inserted_rows": int(raw_inserted),
    }
    _write_json_atomic(journal_path, journal)

    if not raw_inserted:
        checkpoint_writer(checkpoint_path, checkpoint)
        journal["status"] = status
        _write_json_atomic(journal_path, journal)
        return VKOSPIDailyOperationResult(
            run_id, status, selected_date, raw_root, normalized_root, checkpoint_path,
            journal_path, landing_sha256, 0, len(normalized_merged),
        )

    transaction = state_root / "transactions" / f"kr_vkospi_daily--{run_id}"
    if transaction.exists():
        raise VKOSPIDailyOperationError("transaction directory already exists")
    stage_raw, stage_normalized = transaction / "stage_raw", transaction / "stage_normalized"
    previous_raw, previous_normalized = transaction / "previous_raw", transaction / "previous_normalized"
    prior_checkpoint = checkpoint_path.read_bytes() if checkpoint_path.exists() else None
    transaction.mkdir(parents=True)
    try:
        write_dataset_atomic(raw_merged, stage_raw, KR_VKOSPI_RAW_DAILY, validate_vkospi_raw_daily)
        write_dataset_atomic(
            normalized_merged, stage_normalized, KR_VKOSPI_DAILY, validate_vkospi_daily
        )
        if raw_root.exists():
            raw_root.replace(previous_raw)
        if normalized_root.exists():
            normalized_root.replace(previous_normalized)
        raw_root.parent.mkdir(parents=True, exist_ok=True)
        normalized_root.parent.mkdir(parents=True, exist_ok=True)
        stage_raw.replace(raw_root)
        stage_normalized.replace(normalized_root)
        checkpoint_writer(checkpoint_path, checkpoint)
    except Exception:
        if raw_root.exists():
            shutil.rmtree(raw_root)
        if normalized_root.exists():
            shutil.rmtree(normalized_root)
        if previous_raw.exists():
            previous_raw.replace(raw_root)
        if previous_normalized.exists():
            previous_normalized.replace(normalized_root)
        if prior_checkpoint is None:
            checkpoint_path.unlink(missing_ok=True)
        else:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint_path.with_suffix(".rollback.tmp")
            temporary.write_bytes(prior_checkpoint)
            temporary.replace(checkpoint_path)
        journal["status"] = "FAILED"
        _write_json_atomic(journal_path, journal)
        raise
    else:
        journal["status"] = "SUCCEEDED"
        _write_json_atomic(journal_path, journal)
        shutil.rmtree(transaction)

    return VKOSPIDailyOperationResult(
        run_id, "SUCCEEDED", selected_date, raw_root, normalized_root, checkpoint_path,
        journal_path, landing_sha256, 1, len(normalized_merged),
    )


__all__ = ["VKOSPIDailyOperationError", "VKOSPIDailyOperationResult", "run_offline_daily_append"]
