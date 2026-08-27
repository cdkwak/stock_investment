"""Offline transaction boundary for one dated KRX ETF full-market Raw response.

This module deliberately contains no provider client or live entry point.  The
current Data Status leaves publication, revision, and delisting semantics open;
callers must therefore supply reviewed policy evidence before a candidate can
be accepted.  One immutable MDCSTAT04301 response is referenced by both logical
Raw datasets instead of being copied or requested twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping
from uuid import uuid4


ETF_UNIVERSE_DATASET = "kr_etf_universe_daily"
ETF_OHLCV_DATASET = "kr_etf_ohlcv_daily"
LOGICAL_DATASETS = (ETF_UNIVERSE_DATASET, ETF_OHLCV_DATASET)
SOURCE_OPERATION = "dbms/MDC/STAT/standard/MDCSTAT04301"

REQUIRED_FIELDS = (
    "ISU_SRT_CD", "ISU_CD", "SECUGRP_ID", "ISU_ABBRV", "TDD_CLSPRC",
    "NAV", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL",
    "ACC_TRDVAL", "MKTCAP", "INVSTASST_NETASST_TOTAMT", "LIST_SHRS",
)
PRICE_FIELDS = ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "NAV")
NONNEGATIVE_FIELDS = PRICE_FIELDS + (
    "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "INVSTASST_NETASST_TOTAMT",
    "LIST_SHRS",
)
_SYMBOL = re.compile(r"^[0-9A-Z]{6}$")


class ETFRawDailyError(RuntimeError):
    """A fail-closed ETF Raw transaction or policy error."""


class ETFRawValidEmpty(ETFRawDailyError):
    """The exact full-market call returned a structurally valid empty output."""


@dataclass(frozen=True)
class ETFRawCapture:
    market_date: date
    body: bytes
    captured_at_utc: str
    request_payload: Mapping[str, str]
    business_calls: int = 1
    retry_count: int = 0


@dataclass(frozen=True)
class ETFRawDailyPlan:
    market_date: date
    latest_finalized_market_date: date
    action: str
    reason: str
    row_count_bounds: tuple[int, int] | None
    estimated_business_calls: int
    retry_count: int = 0


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ETFRawDailyError(f"immutable Landing path already exists: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ETFRawDailyError(f"invalid ETF state: {path.name}") from error
    if not isinstance(payload, dict):
        raise ETFRawDailyError(f"invalid ETF state: {path.name}")
    return payload


def _inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    if path.is_symlink():
        raise ETFRawDailyError("ETF artifact path is a symlink")
    resolved = path.resolve(strict=True)
    if root not in resolved.parents:
        raise ETFRawDailyError("ETF artifact path is outside the project root")
    return resolved


def _decimal(row: Mapping[str, object], field: str) -> Decimal:
    raw = str(row.get(field, "")).strip().replace(",", "")
    if not raw or raw == "-":
        raise ETFRawDailyError(f"missing numeric field: {field}")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ETFRawDailyError(f"invalid numeric field: {field}") from error
    if not value.is_finite() or value < 0:
        raise ETFRawDailyError(f"numeric field outside accepted range: {field}")
    return value


def validate_etf_full_market_capture(
    body: bytes, *, market_date: date, row_count_bounds: tuple[int, int],
) -> dict[str, object]:
    """Validate one exact-date, full-market source response without projection."""
    if body.lstrip().startswith(b"<"):
        raise ETFRawDailyError("HTML_OR_RESTRICTION")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ETFRawDailyError("NON_JSON_SOURCE_RESPONSE") from error
    if (
        not isinstance(payload, dict)
        or payload.get("_error_code")
        or payload.get("error")
        or payload.get("errors")
    ):
        raise ETFRawDailyError("SOURCE_ERROR_RESPONSE")
    rows = payload.get("output")
    if not isinstance(rows, list):
        raise ETFRawDailyError("SOURCE_OUTPUT_NOT_A_LIST")
    if not rows:
        raise ETFRawValidEmpty(f"VALID_EMPTY:{market_date.isoformat()}")
    minimum, maximum = row_count_bounds
    if minimum <= 0 or maximum < minimum:
        raise ETFRawDailyError("INVALID_REVIEWED_ROW_COUNT_BOUNDS")
    if not minimum <= len(rows) <= maximum:
        raise ETFRawDailyError(
            f"FULL_MARKET_COMPLETENESS_OUTSIDE_REVIEWED_BOUNDS:{len(rows)}"
        )

    symbols: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not set(REQUIRED_FIELDS).issubset(row):
            raise ETFRawDailyError("ETF_SOURCE_SCHEMA_CHANGE")
        symbol = str(row["ISU_SRT_CD"]).strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ETFRawDailyError(f"INVALID_ETF_SYMBOL:{symbol}")
        if str(row["ISU_CD"]).strip() == "" or str(row["ISU_ABBRV"]).strip() == "":
            raise ETFRawDailyError(f"MISSING_ETF_IDENTITY:{symbol}")
        if str(row["SECUGRP_ID"]).strip() != "EF":
            raise ETFRawDailyError(f"NON_ETF_ROW_IN_FULL_MARKET_RESPONSE:{symbol}")
        values = {field: _decimal(row, field) for field in NONNEGATIVE_FIELDS}
        high, low = values["TDD_HGPRC"], values["TDD_LWPRC"]
        if high < low or high < max(values[field] for field in ("TDD_OPNPRC", "TDD_CLSPRC")):
            raise ETFRawDailyError(f"INVALID_ETF_OHLC_HIGH:{symbol}")
        if low > min(values[field] for field in ("TDD_OPNPRC", "TDD_CLSPRC")):
            raise ETFRawDailyError(f"INVALID_ETF_OHLC_LOW:{symbol}")
        if values["TDD_CLSPRC"] <= 0 or values["NAV"] <= 0 or values["LIST_SHRS"] <= 0:
            raise ETFRawDailyError(f"INVALID_ETF_POSITIVE_FIELD:{symbol}")
        symbols.append(symbol)
    if len(set(symbols)) != len(symbols):
        raise ETFRawDailyError("DUPLICATE_DATE_SYMBOL")
    ordered = "\n".join(sorted(symbols)).encode("ascii")
    return {
        "market_date": market_date.isoformat(),
        "rows": len(rows),
        "body_sha256": _sha256_bytes(body),
        "membership_sha256": _sha256_bytes(ordered),
        "date_symbol_unique": True,
        "source_scope": "FULL_MARKET_ETF_CROSS_SECTION",
    }


def _state_paths(project_root: Path, market_date: date) -> dict[str, Path]:
    token = market_date.strftime("%Y%m%d")
    return {
        "checkpoint": project_root / "data/state/kr_etf_raw_daily_incremental.json",
        "journal": project_root / "data/state/transactions" / f"kr_etf_raw_daily_{token}.json",
        "baseline_universe": project_root / "data/state/pykrx_high_value_raw/kr_etf_universe_daily.json",
        "baseline_ohlcv": project_root / "data/state/pykrx_high_value_raw/kr_etf_ohlcv_daily.json",
    }


def _baseline_evidence(project_root: Path, market_date: date) -> dict[str, object]:
    paths = _state_paths(project_root, market_date)
    universe = _read_json(paths["baseline_universe"])
    ohlcv = _read_json(paths["baseline_ohlcv"])
    if universe.get("dataset") != ETF_UNIVERSE_DATASET or universe.get("status") != "RAW_BACKFILL_COMPLETE":
        raise ETFRawDailyError("ETF_UNIVERSE_BASELINE_NOT_COMPLETE")
    if (
        ohlcv.get("dataset") != ETF_OHLCV_DATASET
        or ohlcv.get("status") != "RAW_BACKFILL_COMPLETE"
        or ohlcv.get("source_dataset") != ETF_UNIVERSE_DATASET
        or ohlcv.get("raw_bytes_copied") is not False
        or int(ohlcv.get("business_calls", -1)) != 0
    ):
        raise ETFRawDailyError("ETF_OHLCV_SHARED_BASELINE_NOT_COMPLETE")
    u_completed, o_completed = universe.get("completed"), ohlcv.get("completed")
    if not isinstance(u_completed, dict) or not isinstance(o_completed, dict) or not u_completed:
        raise ETFRawDailyError("ETF_BASELINE_COMPLETED_DATES_INVALID")
    if set(u_completed) != set(o_completed):
        raise ETFRawDailyError("ETF_BASELINE_DATE_SET_DIFFERS")
    latest = max(u_completed)
    u_record, o_record = u_completed[latest], o_completed[latest]
    if not isinstance(u_record, dict) or not isinstance(o_record, dict):
        raise ETFRawDailyError("ETF_BASELINE_LATEST_RECORD_INVALID")
    if (
        u_record.get("body_path") != o_record.get("body_path")
        or u_record.get("body_sha256") != o_record.get("body_sha256")
        or u_record.get("rows") != o_record.get("rows")
    ):
        raise ETFRawDailyError("ETF_BASELINE_SHARED_REFERENCE_DIFFERS")
    body_path = _inside(project_root, project_root / str(u_record.get("body_path", "")))
    if _sha256_file(body_path) != u_record.get("body_sha256"):
        raise ETFRawDailyError("ETF_BASELINE_LATEST_BODY_HASH_DIFFERS")
    return {
        "coverage_end": f"{latest[:4]}-{latest[4:6]}-{latest[6:]}",
        "dates": len(u_completed),
        "rows": sum(int(value["rows"]) for value in u_completed.values()),
        "latest_body_path": body_path.relative_to(project_root.resolve()).as_posix(),
        "latest_body_sha256": u_record["body_sha256"],
        "universe_state_sha256": _sha256_file(paths["baseline_universe"]),
        "ohlcv_state_sha256": _sha256_file(paths["baseline_ohlcv"]),
    }


def _checkpoint(project_root: Path, market_date: date) -> dict[str, object]:
    path = _state_paths(project_root, market_date)["checkpoint"]
    current = _read_json(path)
    baseline = _baseline_evidence(project_root, market_date)
    if not current:
        return {
            "schema": "kr_etf_raw_daily_incremental.v1",
            "logical_datasets": list(LOGICAL_DATASETS),
            "source_operation": SOURCE_OPERATION,
            "baseline": baseline,
            "completed_dates": {},
        }
    if (
        current.get("schema") != "kr_etf_raw_daily_incremental.v1"
        or current.get("logical_datasets") != list(LOGICAL_DATASETS)
        or current.get("source_operation") != SOURCE_OPERATION
        or current.get("baseline") != baseline
        or not isinstance(current.get("completed_dates"), dict)
    ):
        raise ETFRawDailyError("ETF_INCREMENTAL_CHECKPOINT_IDENTITY_DIFFERS")
    return current


def _verify_completed(project_root: Path, record: Mapping[str, object], market_date: date) -> None:
    path = _inside(project_root, project_root / str(record.get("body_path", "")))
    digest = _sha256_file(path)
    bounds = record.get("row_count_bounds")
    if not isinstance(bounds, list) or len(bounds) != 2 or not all(isinstance(v, int) for v in bounds):
        raise ETFRawDailyError("ETF_COMPLETED_ROW_COUNT_BOUNDS_INVALID")
    analysis = validate_etf_full_market_capture(
        path.read_bytes(), market_date=market_date,
        row_count_bounds=(bounds[0], bounds[1]),
    )
    references = record.get("logical_references")
    if not isinstance(references, dict) or set(references) != set(LOGICAL_DATASETS):
        raise ETFRawDailyError("ETF_LOGICAL_REFERENCES_INVALID")
    for dataset in LOGICAL_DATASETS:
        ref = references[dataset]
        if not isinstance(ref, dict) or ref.get("body_path") != record.get("body_path") or ref.get("body_sha256") != digest:
            raise ETFRawDailyError("ETF_SHARED_REFERENCE_IDENTITY_DIFFERS")
    if digest != record.get("body_sha256") or analysis["membership_sha256"] != record.get("membership_sha256"):
        raise ETFRawDailyError("ETF_COMPLETED_CAPTURE_HASH_DIFFERS")


def _rollback_checkpoint(paths: Mapping[str, Path], journal: Mapping[str, object]) -> None:
    checkpoint = paths["checkpoint"]
    previous = Path(str(journal.get("previous_checkpoint_path", "")))
    if previous.is_file():
        body = previous.read_bytes()
        temporary = checkpoint.with_name(f".{checkpoint.name}.{uuid4().hex}.rollback")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        os.replace(temporary, checkpoint)
    elif journal.get("checkpoint_existed") is False:
        checkpoint.unlink(missing_ok=True)


def recover_etf_raw_daily(project_root: Path, market_date: date) -> str | None:
    paths = _state_paths(project_root, market_date)
    journal = _read_json(paths["journal"])
    if not journal:
        return None
    if journal.get("status") in {
        "SUCCEEDED", "FAILED", "VALID_EMPTY_RETAINED", "RECOVERED",
    }:
        return str(journal["status"])
    if (
        journal.get("market_date") != market_date.isoformat()
        or journal.get("logical_datasets") != list(LOGICAL_DATASETS)
    ):
        raise ETFRawDailyError("ETF_TRANSACTION_JOURNAL_IDENTITY_DIFFERS")
    # STAGED is also rollback-safe: a process can die after the atomic state
    # replace but before it durably records CHECKPOINT_PROMOTED.
    if journal.get("status") in {"STAGED", "CHECKPOINT_PROMOTED"}:
        _rollback_checkpoint(paths, journal)
    _atomic_json(paths["journal"], {**journal, "status": "RECOVERED"})
    return "RECOVERED"


def plan_etf_raw_daily(
    *, project_root: Path, market_date: date, latest_finalized_market_date: date,
    accepted_market_dates: tuple[date, ...],
    source_scope_reviewed: bool = False,
    publication_finality_reviewed: bool = False,
    revision_policy_reviewed: bool = False,
    delisting_policy_reviewed: bool = False,
    row_count_bounds: tuple[int, int] | None = None,
) -> ETFRawDailyPlan:
    recover_etf_raw_daily(project_root, market_date)
    checkpoint = _checkpoint(project_root, market_date)
    completed = checkpoint["completed_dates"]
    target = market_date.isoformat()
    if target in completed:
        record = completed[target]
        if not isinstance(record, dict):
            raise ETFRawDailyError("ETF_COMPLETED_RECORD_INVALID")
        _verify_completed(project_root, record, market_date)
        return ETFRawDailyPlan(
            market_date, latest_finalized_market_date, "NOOP_ALREADY_SUCCEEDED",
            "SHARED_CAPTURE_AND_BOTH_LOGICAL_REFERENCES_VERIFIED", row_count_bounds, 0,
        )
    gates = (
        (source_scope_reviewed, "FULL_MARKET_SOURCE_SCOPE_REVIEW_REQUIRED"),
        (publication_finality_reviewed, "PUBLICATION_FINALITY_REVIEW_REQUIRED"),
        (revision_policy_reviewed, "REVISION_POLICY_REVIEW_REQUIRED"),
        (delisting_policy_reviewed, "DELISTING_POLICY_REVIEW_REQUIRED"),
        (row_count_bounds is not None, "COMPLETENESS_BOUNDS_REVIEW_REQUIRED"),
        (market_date <= latest_finalized_market_date, "SOURCE_DATE_NOT_FINAL"),
        (market_date in set(accepted_market_dates), "DATE_NOT_EXPLICITLY_ACCEPTED"),
    )
    for passed, reason in gates:
        if not passed:
            return ETFRawDailyPlan(
                market_date, latest_finalized_market_date, "BLOCKED", reason,
                row_count_bounds, 0,
            )
    assert row_count_bounds is not None
    minimum, maximum = row_count_bounds
    if minimum <= 0 or maximum < minimum:
        return ETFRawDailyPlan(
            market_date, latest_finalized_market_date, "BLOCKED",
            "INVALID_REVIEWED_ROW_COUNT_BOUNDS", row_count_bounds, 0,
        )
    return ETFRawDailyPlan(
        market_date, latest_finalized_market_date, "READY",
        "EXACT_DATE_SINGLE_SHARED_RAW_TRANSACTION", row_count_bounds, 1,
    )


def execute_etf_raw_daily(
    plan: ETFRawDailyPlan, *, project_root: Path,
    capture_builder: Callable[[date], ETFRawCapture] | None,
) -> dict[str, object]:
    if plan.action == "BLOCKED":
        raise ETFRawDailyError(plan.reason)
    paths = _state_paths(project_root, plan.market_date)
    target = plan.market_date.isoformat()
    if plan.action == "NOOP_ALREADY_SUCCEEDED":
        record = _checkpoint(project_root, plan.market_date)["completed_dates"][target]
        _verify_completed(project_root, record, plan.market_date)
        return {
            "status": "NOOP_ALREADY_SUCCEEDED", "business_calls": 0,
            "retry_count": 0, "promoted_logical_datasets": 0,
        }
    if plan.action != "READY" or capture_builder is None or plan.row_count_bounds is None:
        raise ETFRawDailyError("READY_PLAN_AND_CAPTURE_BUILDER_REQUIRED")

    run_id = uuid4().hex
    landing = project_root / "data/landing/pykrx/etf_daily_raw" / f"run={run_id}"
    body_path = landing / "response.json"
    provenance_path = landing / "provenance.json"
    stage = project_root / "data/staging/kr_etf_raw_daily" / f"run={run_id}"
    previous_checkpoint = stage / "previous_checkpoint.json"
    checkpoint_existed = paths["checkpoint"].is_file()
    journal: dict[str, object] = {
        "schema": "kr_etf_raw_daily.transaction.v1",
        "run_id": run_id,
        "market_date": target,
        "logical_datasets": list(LOGICAL_DATASETS),
        "checkpoint_existed": checkpoint_existed,
        "previous_checkpoint_path": str(previous_checkpoint.resolve()),
        "landing_response_path": body_path.relative_to(project_root).as_posix(),
        "status": "PREPARED",
    }
    _atomic_json(paths["journal"], journal)
    checkpoint_promoted = False
    try:
        capture = capture_builder(plan.market_date)
        if (
            capture.market_date != plan.market_date
            or capture.business_calls != 1
            or capture.retry_count != 0
            or dict(capture.request_payload) != {
                "trdDd": plan.market_date.strftime("%Y%m%d"), "bld": SOURCE_OPERATION,
            }
        ):
            raise ETFRawDailyError("ETF_CAPTURE_SCOPE_OR_CALL_BUDGET_DIFFERS")
        _write_bytes_new(body_path, capture.body)
        journal.update({
            "status": "CAPTURE_RETAINED", "body_sha256": _sha256_bytes(capture.body),
            "business_calls": 1, "retry_count": 0,
        })
        _atomic_json(paths["journal"], journal)
        try:
            analysis = validate_etf_full_market_capture(
                capture.body, market_date=plan.market_date,
                row_count_bounds=plan.row_count_bounds,
            )
        except ETFRawValidEmpty as error:
            _atomic_json(provenance_path, {
                "schema": "kr_etf_raw_daily.provenance.v1", "market_date": target,
                "source_operation": SOURCE_OPERATION, "captured_at_utc": capture.captured_at_utc,
                "request_payload": dict(capture.request_payload),
                "response_sha256": _sha256_bytes(capture.body),
                "classification": "VALID_EMPTY_REJECTED", "accepted": False,
            })
            journal.update({"status": "VALID_EMPTY_RETAINED", "error_type": type(error).__name__})
            _atomic_json(paths["journal"], journal)
            raise

        _atomic_json(provenance_path, {
            "schema": "kr_etf_raw_daily.provenance.v1", "market_date": target,
            "source_operation": SOURCE_OPERATION, "captured_at_utc": capture.captured_at_utc,
            "request_payload": dict(capture.request_payload), "classification": "ACCEPTED",
            "accepted": True, **analysis,
        })
        checkpoint = _checkpoint(project_root, plan.market_date)
        if target in checkpoint["completed_dates"]:
            raise ETFRawDailyError("ETF_DATE_BECAME_COMPLETED_DURING_TRANSACTION")
        if checkpoint_existed:
            _write_bytes_new(previous_checkpoint, paths["checkpoint"].read_bytes())
        shared = {
            "body_path": body_path.relative_to(project_root).as_posix(),
            "body_sha256": analysis["body_sha256"],
        }
        checkpoint["completed_dates"][target] = {
            **shared,
            "rows": analysis["rows"],
            "membership_sha256": analysis["membership_sha256"],
            "row_count_bounds": list(plan.row_count_bounds),
            "source_scope": analysis["source_scope"],
            "source_business_calls": 1,
            "retry_count": 0,
            "logical_references": {
                ETF_UNIVERSE_DATASET: {**shared, "projection": "DATED_MEMBERSHIP_AND_IDENTITY"},
                ETF_OHLCV_DATASET: {**shared, "projection": "PROVIDER_NATIVE_OHLCV_NAV"},
            },
        }
        checkpoint["latest_date"] = max(checkpoint["completed_dates"])
        journal["status"] = "STAGED"
        _atomic_json(paths["journal"], journal)
        _atomic_json(paths["checkpoint"], checkpoint)
        checkpoint_promoted = True
        journal["status"] = "CHECKPOINT_PROMOTED"
        _atomic_json(paths["journal"], journal)
        _verify_completed(project_root, checkpoint["completed_dates"][target], plan.market_date)
        journal["status"] = "SUCCEEDED"
        _atomic_json(paths["journal"], journal)
        return {
            "status": "SUCCEEDED", "business_calls": 1, "retry_count": 0,
            "rows": analysis["rows"], "promoted_logical_datasets": 2,
            "shared_body_sha256": analysis["body_sha256"],
        }
    except ETFRawValidEmpty:
        raise
    except Exception as error:
        if checkpoint_promoted:
            _rollback_checkpoint(paths, journal)
        journal.update({"status": "FAILED", "error_type": type(error).__name__})
        _atomic_json(paths["journal"], journal)
        raise


__all__ = [
    "ETFRawCapture", "ETFRawDailyError", "ETFRawDailyPlan", "ETFRawValidEmpty",
    "execute_etf_raw_daily", "plan_etf_raw_daily", "recover_etf_raw_daily",
    "validate_etf_full_market_capture",
]
