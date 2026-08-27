"""Fail-closed offline boundary for daily KRX foreign-ownership Raw capture.

The retained history is immutable provider JSON in Landing, despite its legacy
"Raw" label.  This module deliberately creates no Normalized/Canonical output
and contains no transport implementation.  A future active runbook must supply
an exact-date authorization and a one-call capture function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
from uuid import uuid4

from stock_data.contracts.kr_equity_foreign_ownership_raw import (
    SOURCE_POLICY,
    ForeignOwnershipSourcePolicy,
)


DATASET = "kr_equity_foreign_ownership_daily"
SOURCE = "KRX_via_pykrx"
SOURCE_OPERATION = "MDCSTAT03701"
SOURCE_BLD = "dbms/MDC/STAT/standard/MDCSTAT03701"
SOURCE_SCOPE = "ALL"
REQUIRED_FIELDS = (
    "ISU_SRT_CD",
    "LIST_SHRS",
    "FORN_HD_QTY",
    "FORN_SHR_RT",
    "FORN_ORD_LMT_QTY",
    "FORN_LMT_EXHST_RT",
)
BASELINE_STATE = Path("data/state/pykrx_high_value_raw") / f"{DATASET}.json"
CHECKPOINT = (
    Path("data/state/pykrx_high_value_raw")
    / f"{DATASET}_incremental.json"
)
JOURNAL = (
    Path("data/state/pykrx_high_value_raw")
    / f"{DATASET}_incremental_journal.json"
)
LANDING_ROOT = Path("data/landing/pykrx/high_value_raw") / DATASET / "incremental"
CHECKPOINT_SCHEMA = "pykrx_foreign_ownership_daily.incremental.v1"
JOURNAL_TERMINAL = {
    "SUCCEEDED",
    "SUCCEEDED_RECOVERED",
    "FAILED_FETCH",
    "FAILED_VALIDATION",
    "FAILED_BEFORE_CHECKPOINT",
    "RECOVERY_REQUIRES_REVIEW",
}


class ForeignOwnershipGateError(RuntimeError):
    """The request cannot cross the reviewed offline operation boundary."""


@dataclass(frozen=True)
class ExactDateAuthorization:
    intended_date: str
    completed_date: str
    provider_available: bool
    finality_evidence: str
    business_calls_max: int = 1
    retries: int = 0


@dataclass(frozen=True)
class SourceCapture:
    body: bytes
    source: str
    source_operation: str
    source_scope: str
    source_date: str
    request_payload: Mapping[str, str]
    http_status: int = 200


CaptureFn = Callable[[Mapping[str, str]], SourceCapture]


def request_payload(target_date: str) -> dict[str, str]:
    compact = _date_key(target_date)
    return {
        "searchType": "1",
        "mktId": SOURCE_SCOPE,
        "trdDd": compact,
        "isuLmtRto": "0",
        "bld": SOURCE_BLD,
    }


def validate_authorization(
    authorization: ExactDateAuthorization,
    *,
    target_date: str,
    source_policy: ForeignOwnershipSourcePolicy = SOURCE_POLICY,
) -> None:
    expected = _iso_date(target_date)
    if _iso_date(authorization.intended_date) != expected:
        raise ForeignOwnershipGateError("authorization intended date mismatch")
    if _iso_date(authorization.completed_date) != expected:
        raise ForeignOwnershipGateError("authorization completed date mismatch")
    if not authorization.provider_available:
        raise ForeignOwnershipGateError("provider availability is not confirmed")
    if (
        not source_policy.execution_authorized
        or source_policy.finality_evidence_id is None
    ):
        raise ForeignOwnershipGateError(
            "source publication/finality policy is not executable"
        )
    if authorization.finality_evidence != source_policy.finality_evidence_id:
        raise ForeignOwnershipGateError("typed source finality evidence mismatch")
    if (
        authorization.business_calls_max != source_policy.business_calls_max
        or authorization.retries != source_policy.retries
        or source_policy.business_calls_max != 1
        or source_policy.retries != 0
    ):
        raise ForeignOwnershipGateError("call budget must be one call with retry zero")


def analyze_capture(capture: SourceCapture, *, target_date: str) -> dict[str, Any]:
    compact = _date_key(target_date)
    if capture.source != SOURCE:
        raise ValueError("foreign-ownership source identity mismatch")
    if capture.source_operation != SOURCE_OPERATION:
        raise ValueError("foreign-ownership source operation mismatch")
    if capture.source_scope != SOURCE_SCOPE:
        raise ValueError("foreign-ownership source must cover ALL market scope")
    if _date_key(capture.source_date) != compact:
        raise ValueError("foreign-ownership source date mismatch")
    if dict(capture.request_payload) != request_payload(target_date):
        raise ValueError("foreign-ownership exact-date request mismatch")
    if capture.http_status != 200:
        raise ValueError(f"foreign-ownership HTTP status {capture.http_status}")
    return _analyze_body(capture.body, target_date=target_date)


def _analyze_body(body: bytes, *, target_date: str) -> dict[str, Any]:
    if not isinstance(body, bytes) or not body:
        raise ValueError("foreign-ownership capture body is empty")
    if body.lstrip().startswith(b"<"):
        raise ValueError("foreign-ownership capture is HTML/restriction content")
    try:
        payload = json.loads(body)
    except Exception as error:
        raise ValueError("foreign-ownership capture is not JSON") from error
    if not isinstance(payload, dict) or any(
        payload.get(name) for name in ("_error_code", "error", "errors")
    ):
        raise ValueError("foreign-ownership provider error payload")
    rows = payload.get("output")
    if not isinstance(rows, list) or not rows:
        raise ValueError("foreign-ownership valid-empty is not accepted")

    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not set(REQUIRED_FIELDS).issubset(row):
            raise ValueError("foreign-ownership source schema mismatch")
        for field in REQUIRED_FIELDS:
            value = row[field]
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"foreign-ownership null field: {field}")
        symbol = str(row["ISU_SRT_CD"]).strip()
        if symbol in seen:
            raise ValueError(f"foreign-ownership duplicate symbol: {symbol}")
        seen.add(symbol)
    return {
        "market_date": _iso_date(target_date),
        "rows": len(rows),
        "symbols": len(seen),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "source_scope": SOURCE_SCOPE,
        "provider_duplicate_groups": [],
        "null_count": 0,
    }


def pre_network_action(project_root: Path, *, target_date: str) -> str:
    compact = _date_key(target_date)
    checkpoint = _load_checkpoint(project_root)
    if checkpoint is not None and compact in checkpoint["completed"]:
        _verify_incremental_record(project_root, compact, checkpoint["completed"][compact])
        return "NOOP_ALREADY_SUCCEEDED"

    baseline = _baseline_summary(project_root)
    record = baseline["completed"].get(compact)
    if record is not None:
        _verify_baseline_record(project_root, compact, record)
        return "NOOP_BASELINE_ALREADY_RETAINED"

    journal = _read_json(project_root / JOURNAL)
    if journal is not None and journal.get("target_date") == compact:
        status = str(journal.get("status", ""))
        if status not in {"SUCCEEDED", "SUCCEEDED_RECOVERED"}:
            if status in JOURNAL_TERMINAL:
                return "FAILED_ATTEMPT_REQUIRES_NEW_AUTHORIZATION"
            return "RECOVERY_REQUIRED_PRE_NETWORK"
    return "RUN_REQUIRED"


def recover_incomplete(project_root: Path) -> str:
    journal_path = project_root / JOURNAL
    journal = _read_json(journal_path)
    if journal is None or journal.get("status") in JOURNAL_TERMINAL:
        return "NO_RECOVERY_REQUIRED"
    if journal.get("dataset") != DATASET:
        raise ForeignOwnershipGateError("foreign-ownership journal identity mismatch")
    compact = _date_key(str(journal.get("target_date", "")))

    checkpoint = _load_checkpoint(project_root)
    if checkpoint is not None and compact in checkpoint["completed"]:
        _verify_incremental_record(project_root, compact, checkpoint["completed"][compact])
        journal["status"] = "SUCCEEDED_RECOVERED"
        journal["recovered_at_utc"] = _utc_now()
        _atomic_json(journal_path, journal)
        return "SUCCEEDED_RECOVERED"

    response_value = str(journal.get("response_path", ""))
    if not response_value:
        journal["status"] = "RECOVERY_REQUIRES_REVIEW"
        journal["failure"] = "NO_DURABLE_RESPONSE_AFTER_POSSIBLE_CALL"
        _atomic_json(journal_path, journal)
        raise ForeignOwnershipGateError("incomplete call has no durable response")
    response_path = _safe_project_path(project_root, response_value)
    if not response_path.is_file() or response_path.is_symlink():
        raise ForeignOwnershipGateError("recovery response path is unavailable")
    body = response_path.read_bytes()
    capture = _capture_from_journal(journal, body)
    analysis = analyze_capture(capture, target_date=compact)
    if analysis["body_sha256"] != journal.get("response_sha256"):
        raise ForeignOwnershipGateError("recovery response hash mismatch")

    provenance_path = response_path.with_name("provenance.json")
    if not provenance_path.exists():
        _atomic_json_new(
            provenance_path,
            _provenance(project_root, response_path, capture, analysis),
        )
    checkpoint = _checkpoint_with_record(
        project_root,
        compact,
        _record(project_root, response_path, provenance_path, analysis),
    )
    _atomic_json(project_root / CHECKPOINT, checkpoint)
    journal["status"] = "SUCCEEDED_RECOVERED"
    journal["recovered_at_utc"] = _utc_now()
    _atomic_json(journal_path, journal)
    return "SUCCEEDED_RECOVERED"


def run_foreign_ownership_incremental(
    project_root: Path,
    *,
    target_date: str,
    authorization: ExactDateAuthorization | None,
    capture_fn: CaptureFn | None,
    source_policy: ForeignOwnershipSourcePolicy = SOURCE_POLICY,
    transition_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one exact ALL-market capture; contains no provider implementation."""
    compact = _date_key(target_date)
    recovered = recover_incomplete(project_root)
    action = pre_network_action(project_root, target_date=compact)
    if action in {"NOOP_ALREADY_SUCCEEDED", "NOOP_BASELINE_ALREADY_RETAINED"}:
        return {
            "status": action,
            "intended_date": _iso_date(compact),
            "business_calls": 0,
            "recovery": recovered,
            "normalized_writes": False,
        }
    if action != "RUN_REQUIRED":
        raise ForeignOwnershipGateError(action)
    if authorization is None:
        raise ForeignOwnershipGateError("exact-date finality authorization is required")
    validate_authorization(
        authorization,
        target_date=compact,
        source_policy=source_policy,
    )
    if capture_fn is None:
        raise ForeignOwnershipGateError("authorized capture function is required")

    transaction_id = f"foreign_{compact}_{uuid4().hex}"
    run_dir = project_root / LANDING_ROOT / f"date={compact}" / f"run={transaction_id}"
    response_path = run_dir / "response.json"
    journal_path = project_root / JOURNAL
    journal: dict[str, Any] = {
        "schema": CHECKPOINT_SCHEMA,
        "dataset": DATASET,
        "target_date": compact,
        "transaction_id": transaction_id,
        "status": "RUNNING",
        "request_payload": request_payload(compact),
        "business_calls": 0,
        "retry_count": 0,
        "normalized_writes": False,
        "started_at_utc": _utc_now(),
    }
    _atomic_json(journal_path, journal)

    try:
        capture = capture_fn(request_payload(compact))
        journal["business_calls"] = 1
    except Exception as error:
        journal["status"] = "FAILED_FETCH"
        journal["failure_type"] = type(error).__name__
        journal["failed_at_utc"] = _utc_now()
        _atomic_json(journal_path, journal)
        raise

    _atomic_bytes_new(response_path, capture.body)
    journal.update(
        status="CAPTURED",
        response_path=_relative(project_root, response_path),
        response_sha256=hashlib.sha256(capture.body).hexdigest(),
        source=capture.source,
        source_operation=capture.source_operation,
        source_scope=capture.source_scope,
        source_date=_date_key(capture.source_date),
        http_status=capture.http_status,
        captured_request_payload=dict(capture.request_payload),
    )
    _atomic_json(journal_path, journal)
    if transition_hook is not None:
        transition_hook("after_capture")

    try:
        analysis = analyze_capture(capture, target_date=compact)
        if analysis["body_sha256"] != journal["response_sha256"]:
            raise ValueError("foreign-ownership captured bytes changed")
        provenance_path = response_path.with_name("provenance.json")
        _atomic_json_new(
            provenance_path,
            _provenance(project_root, response_path, capture, analysis),
        )
        journal.update(
            status="VALIDATED",
            rows=analysis["rows"],
            provenance_path=_relative(project_root, provenance_path),
        )
        _atomic_json(journal_path, journal)
        if transition_hook is not None:
            transition_hook("before_checkpoint")

        checkpoint = _checkpoint_with_record(
            project_root,
            compact,
            _record(project_root, response_path, provenance_path, analysis),
        )
        journal["status"] = "COMMITTING"
        _atomic_json(journal_path, journal)
        _atomic_json(project_root / CHECKPOINT, checkpoint)
        if transition_hook is not None:
            transition_hook("after_checkpoint")
    except Exception as error:
        checkpoint = _load_checkpoint(project_root)
        committed = checkpoint is not None and compact in checkpoint["completed"]
        if not committed:
            journal["status"] = (
                "FAILED_VALIDATION"
                if isinstance(error, ValueError)
                else "FAILED_BEFORE_CHECKPOINT"
            )
            journal["failure_type"] = type(error).__name__
            journal["failed_at_utc"] = _utc_now()
            _atomic_json(journal_path, journal)
        raise

    journal["status"] = "SUCCEEDED"
    journal["completed_at_utc"] = _utc_now()
    _atomic_json(journal_path, journal)
    return {
        "status": "SUCCEEDED",
        "intended_date": _iso_date(compact),
        "business_calls": 1,
        "rows": analysis["rows"],
        "source_scope": SOURCE_SCOPE,
        "landing_path": _relative(project_root, response_path),
        "normalized_writes": False,
    }


def _checkpoint_with_record(
    project_root: Path, compact: str, record: Mapping[str, Any]
) -> dict[str, Any]:
    baseline = _baseline_summary(project_root)
    checkpoint = _load_checkpoint(project_root)
    if checkpoint is None:
        checkpoint = {
            "schema": CHECKPOINT_SCHEMA,
            "dataset": DATASET,
            "baseline_state_path": BASELINE_STATE.as_posix(),
            "baseline_state_sha256": baseline["sha256"],
            "baseline_latest_date": baseline["latest"],
            "completed": {},
            "normalized_writes": False,
        }
    elif checkpoint.get("baseline_state_sha256") != baseline["sha256"]:
        raise ForeignOwnershipGateError("foreign-ownership baseline state changed")
    completed = dict(checkpoint["completed"])
    if compact in completed:
        if dict(completed[compact]) != dict(record):
            raise ForeignOwnershipGateError("foreign-ownership date already has another capture")
        return checkpoint
    completed[compact] = dict(record)
    checkpoint["completed"] = completed
    checkpoint["latest_completed_date"] = max(completed)
    checkpoint["status"] = "INCREMENTAL_RAW_ACCEPTED"
    checkpoint["updated_at_utc"] = _utc_now()
    return checkpoint


def _baseline_summary(project_root: Path) -> dict[str, Any]:
    path = project_root / BASELINE_STATE
    raw = path.read_bytes()
    payload = json.loads(raw)
    completed = payload.get("completed")
    if (
        payload.get("dataset") != DATASET
        or payload.get("status") != "RAW_BACKFILL_COMPLETE"
        or not isinstance(completed, dict)
        or payload.get("expected_dates") != len(completed)
        or not completed
    ):
        raise ForeignOwnershipGateError("foreign-ownership baseline checkpoint is invalid")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "latest": max(completed),
        "completed": completed,
    }


def _load_checkpoint(project_root: Path) -> dict[str, Any] | None:
    checkpoint = _read_json(project_root / CHECKPOINT)
    if checkpoint is None:
        return None
    if (
        checkpoint.get("schema") != CHECKPOINT_SCHEMA
        or checkpoint.get("dataset") != DATASET
        or checkpoint.get("normalized_writes") is not False
        or not isinstance(checkpoint.get("completed"), dict)
    ):
        raise ForeignOwnershipGateError("foreign-ownership incremental checkpoint mismatch")
    return checkpoint


def _verify_baseline_record(
    project_root: Path, compact: str, record: Mapping[str, Any]
) -> None:
    response_path = _safe_project_path(project_root, str(record.get("body_path", "")))
    body = response_path.read_bytes()
    analysis = _analyze_body(body, target_date=compact)
    if (
        analysis["body_sha256"] != record.get("body_sha256")
        or analysis["rows"] != record.get("rows")
    ):
        raise ForeignOwnershipGateError("foreign-ownership baseline Landing mismatch")


def _verify_incremental_record(
    project_root: Path, compact: str, record: Mapping[str, Any]
) -> None:
    response_path = _safe_project_path(project_root, str(record.get("body_path", "")))
    provenance_path = _safe_project_path(
        project_root, str(record.get("provenance_path", ""))
    )
    body = response_path.read_bytes()
    provenance = _read_json(provenance_path)
    if provenance is None:
        raise ForeignOwnershipGateError("foreign-ownership provenance is missing")
    capture = SourceCapture(
        body=body,
        source=str(provenance.get("source", "")),
        source_operation=str(provenance.get("source_operation", "")),
        source_scope=str(provenance.get("source_scope", "")),
        source_date=str(provenance.get("market_date", "")),
        request_payload=provenance.get("request_payload", {}),
        http_status=int(provenance.get("http_status", 0)),
    )
    analysis = analyze_capture(capture, target_date=compact)
    if (
        analysis["body_sha256"] != record.get("body_sha256")
        or analysis["rows"] != record.get("rows")
        or provenance.get("response_sha256") != analysis["body_sha256"]
    ):
        raise ForeignOwnershipGateError("foreign-ownership incremental Landing mismatch")


def _provenance(
    project_root: Path,
    response_path: Path,
    capture: SourceCapture,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "pykrx_high_value_raw.provenance.v2.incremental",
        "dataset": DATASET,
        "source": capture.source,
        "source_operation": capture.source_operation,
        "source_bld": SOURCE_BLD,
        "source_scope": capture.source_scope,
        "market_date": _date_key(capture.source_date),
        "request_payload": dict(capture.request_payload),
        "http_status": capture.http_status,
        "response_path": _relative(project_root, response_path),
        "response_sha256": analysis["body_sha256"],
        "rows": analysis["rows"],
        "retry_count": 0,
        "normalized_writes": False,
        "captured_at_utc": _utc_now(),
        "provider_duplicate_groups": [],
        "null_count": 0,
    }


def _record(
    project_root: Path,
    response_path: Path,
    provenance_path: Path,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "classification": "SUCCESS_INCREMENTAL_RAW",
        "rows": analysis["rows"],
        "body_path": _relative(project_root, response_path),
        "body_sha256": analysis["body_sha256"],
        "provenance_path": _relative(project_root, provenance_path),
        "source_scope": SOURCE_SCOPE,
        "provider_duplicate_groups": [],
        "null_count": 0,
    }


def _capture_from_journal(journal: Mapping[str, Any], body: bytes) -> SourceCapture:
    return SourceCapture(
        body=body,
        source=str(journal.get("source", "")),
        source_operation=str(journal.get("source_operation", "")),
        source_scope=str(journal.get("source_scope", "")),
        source_date=str(journal.get("source_date", "")),
        request_payload=journal.get("captured_request_payload", {}),
        http_status=int(journal.get("http_status", 0)),
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temp_path = Path(stream.name)
    os.replace(temp_path, path)


def _atomic_json_new(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(path)
    _atomic_json(path, payload)


def _atomic_bytes_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    if path.exists():
        raise FileExistsError(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(body)
        temp_path = Path(stream.name)
    if path.exists():
        temp_path.unlink(missing_ok=True)
        raise FileExistsError(path)
    os.replace(temp_path, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ForeignOwnershipGateError(f"invalid JSON object: {path.name}")
    return payload


def _safe_project_path(project_root: Path, relative: str) -> Path:
    if not relative:
        raise ForeignOwnershipGateError("empty retained path")
    path = (project_root / relative).resolve(strict=True)
    root = project_root.resolve(strict=True)
    if path == root or root not in path.parents or path.is_symlink():
        raise ForeignOwnershipGateError("retained path is outside the project")
    return path


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _date_key(value: str) -> str:
    return date.fromisoformat(_iso_date(value)).strftime("%Y%m%d")


def _iso_date(value: str) -> str:
    token = str(value).strip()
    if len(token) == 8 and token.isdigit():
        token = f"{token[:4]}-{token[4:6]}-{token[6:]}"
    try:
        return date.fromisoformat(token).isoformat()
    except ValueError:
        raise ValueError("target date must be ISO or YYYYMMDD") from None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
