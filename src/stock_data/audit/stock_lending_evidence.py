"""Offline evidence audit for the retained FSC stock-lending collection.

The audit never calls the provider and never changes collection checkpoints or
data artifacts.  It proves the currently retained responses, checkpoints, and
Normalized datasets while keeping the historical execution overlap unresolved.
"""

from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_data.contracts.data_v1 import (
    KR_STOCK_LENDING_DAILY,
    KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.storage.contract_arrow import (
    contract_arrow_schema,
    dataframe_to_contract_table,
    restore_contract_dates,
)
from stock_data.providers.data_go_kr.data_v1 import (
    normalize_stock_lending,
    normalize_stock_lending_market,
    normalize_stock_lending_participant,
)
from stock_data.pipelines.stock_lending_backfill import (
    StockLendingBackfillLocked,
    stock_lending_run_lock,
)
from stock_data.validation.data_v1 import validate_data_v1


AUDIT_SCHEMA = "stock_data.stock_lending_retained_execution_evidence"
AUDIT_VERSION = 1
EXECUTION_STATUS = "REVIEW_REQUIRED_OVERLAPPING_EXECUTION"
DEFAULT_STATE_RELATIVE = Path("data/state/audits/stock_lending_retained_execution")
RANGE_NAME = "20210401_open"
RANGE_MARKER = "range:20210401:open"
CONTRACTS = {
    contract.name: contract
    for contract in (
        KR_STOCK_LENDING_DAILY,
        KR_STOCK_LENDING_MARKET_DAILY,
        KR_STOCK_LENDING_PARTICIPANT_DAILY,
    )
}
NORMALIZERS = {
    KR_STOCK_LENDING_DAILY.name: normalize_stock_lending,
    KR_STOCK_LENDING_MARKET_DAILY.name: normalize_stock_lending_market,
    KR_STOCK_LENDING_PARTICIPANT_DAILY.name: normalize_stock_lending_participant,
}


class StockLendingEvidenceAuditError(RuntimeError):
    pass


_REPARSE_POINT = 0x400


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_redirect(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError as error:
        raise StockLendingEvidenceAuditError(f"cannot inspect path topology: {path}") from error
    return path.is_symlink() or bool(getattr(status, "st_file_attributes", 0) & _REPARSE_POINT)


def _assert_plain_components(project_root: Path, path: Path, *, require_final: bool = True) -> None:
    root = project_root.absolute()
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise StockLendingEvidenceAuditError(f"path escapes project root: {path}") from error
    if not root.is_dir() or _is_redirect(root):
        raise StockLendingEvidenceAuditError("project root topology is unsafe")
    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        if not current.exists():
            if require_final or index < len(relative.parts) - 1:
                raise StockLendingEvidenceAuditError(f"required logical path is missing: {current}")
            return
        if _is_redirect(current):
            raise StockLendingEvidenceAuditError(f"redirected path component is forbidden: {current}")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as error:
        raise StockLendingEvidenceAuditError(f"path escapes project root: {path}") from error


def _logical_paths(project_root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in sorted(CONTRACTS):
        paths.extend([
            project_root / "data/landing/data_go_kr" / name,
            project_root / "data/normalized" / name,
            project_root / "data/state" / f"{name}.json",
            project_root / "data/state" / f"{name}_historical.json",
        ])
    return paths


def _input_manifest(project_root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for root in _logical_paths(project_root):
        _assert_plain_components(project_root, root)
        candidates = [root] if root.is_file() else sorted(root.rglob("*"), key=lambda p: p.as_posix())
        if root.is_dir():
            entries.append({"path": _relative(root, project_root), "type": "directory"})
        for path in candidates:
            _assert_plain_components(project_root, path)
            if path.is_dir():
                entries.append({"path": _relative(path, project_root), "type": "directory"})
            elif path.is_file():
                entries.append({
                    "path": _relative(path, project_root), "type": "file",
                    "bytes": path.stat().st_size, "sha256": _sha256_file(path),
                })
            else:
                raise StockLendingEvidenceAuditError(f"unsupported input path: {path}")
    return sorted(entries, key=lambda item: str(item["path"]))


def _contract_record(contract) -> dict[str, object]:
    return {
        "name": contract.name, "version": contract.version, "source": contract.source,
        "primary_key": list(contract.primary_key), "sort_key": list(contract.sort_key),
        "partition_by": list(contract.partition_by),
        "columns": [asdict(column) for column in contract.columns],
        "arrow_schema": str(contract_arrow_schema(contract)),
    }


def _canonical_full_row_digest(frame: pd.DataFrame, contract) -> str:
    """Hash lossless contract-typed Arrow IPC in stable contract-key order."""
    ordered = frame[list(contract.column_names)].sort_values(
        list(contract.sort_key), kind="stable"
    ).reset_index(drop=True)
    table = dataframe_to_contract_table(ordered, contract).combine_chunks()
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table, max_chunksize=max(len(table), 1))
    return _sha256_bytes(sink.getvalue().to_pybytes())


@contextmanager
def _audit_state_lock(project_root: Path, state_root: Path):
    """Own the final verify-and-publish critical section with a nonce lock."""
    lock_path = state_root / ".write.lock"
    _assert_plain_components(project_root, state_root)
    token = uuid4().hex.encode("ascii")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise StockLendingEvidenceAuditError("another stock-lending audit state writer holds the lock") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_plain_components(project_root, lock_path)
        if not lock_path.is_file() or lock_path.read_bytes() != token:
            raise StockLendingEvidenceAuditError("audit state lock ownership differs")
        yield
    finally:
        if lock_path.exists():
            _assert_plain_components(project_root, lock_path)
            if lock_path.is_file() and lock_path.read_bytes() == token:
                lock_path.unlink()


@contextmanager
def _collector_input_lock(project_root: Path):
    """Share the canonical collector lock for rebuild through publication."""
    try:
        with stock_lending_run_lock(project_root):
            yield
    except StockLendingBackfillLocked as error:
        raise StockLendingEvidenceAuditError(
            "stock-lending collector owns the input lock; audit state was not published"
        ) from error


def _read_landing(path: Path) -> tuple[dict[str, object], list[Mapping[str, object]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        response = payload[0]["response"]
        header = response["header"]
        body = response["body"]
        page = int(body["pageNo"])
        page_size = int(body["numOfRows"])
        total = int(body["totalCount"])
        container = body.get("items") or {}
        item = container.get("item", []) if isinstance(container, dict) else []
        rows = item if isinstance(item, list) else [item]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as error:
        raise StockLendingEvidenceAuditError(f"invalid Landing response: {path}") from error
    if (
        not isinstance(payload, list) or len(payload) != 1
        or str(header.get("resultCode")) != "00" or page < 1 or page_size < 1 or total < 0
        or not all(isinstance(row, Mapping) for row in rows)
    ):
        raise StockLendingEvidenceAuditError(f"unsuccessful or invalid Landing response: {path}")
    metadata = {"page_no": page, "page_size": page_size, "total_count": total, "source_rows": len(rows)}
    return metadata, rows


def _load_state(path: Path, dataset: str) -> dict[str, object]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StockLendingEvidenceAuditError(f"invalid checkpoint: {path}") from error
    if state.get("dataset") != dataset:
        raise StockLendingEvidenceAuditError(f"checkpoint dataset differs: {path}")
    for field in ("completed_partitions", "valid_empty_partitions", "staged_partitions"):
        if not isinstance(state.get(field), list):
            raise StockLendingEvidenceAuditError(f"checkpoint field differs: {path}:{field}")
        values = state[field]
        if not all(isinstance(value, str) for value in values) or len(values) != len(set(values)):
            raise StockLendingEvidenceAuditError(f"checkpoint entries differ: {path}:{field}")
    if not isinstance(state.get("failed_partitions"), dict):
        raise StockLendingEvidenceAuditError(f"checkpoint failures differ: {path}")
    return state


def _audit_dataset(project_root: Path, name: str) -> tuple[dict[str, object], set[str]]:
    contract = CONTRACTS[name]
    landing_root = project_root / "data/landing/data_go_kr" / name
    normalized_root = project_root / "data/normalized" / name
    history_root = landing_root / "historical" / RANGE_NAME
    if sorted(p.name for p in (landing_root / "historical").iterdir()) != [RANGE_NAME]:
        raise StockLendingEvidenceAuditError(f"{name}: historical range topology differs")
    landing_files = sorted(landing_root.rglob("*.json"), key=lambda p: p.as_posix())
    all_files = sorted(p for p in landing_root.rglob("*") if p.is_file())
    if landing_files != all_files:
        raise StockLendingEvidenceAuditError(f"{name}: unexpected Landing files")
    manifest: list[dict[str, object]] = []
    historical_rows = 0
    historical_dates: set[str] = set()
    history_meta: list[dict[str, object]] = []
    hashes: list[str] = []
    retained_source_rows = 0
    historical_normalized_frames: list[pd.DataFrame] = []
    incremental_frames: list[tuple[Path, str, pd.DataFrame]] = []
    for path in landing_files:
        metadata, rows = _read_landing(path)
        digest = _sha256_file(path)
        hashes.append(digest)
        retained_source_rows += len(rows)
        kind = "historical_page" if path.parent == history_root else "incremental_capture"
        if kind == "incremental_capture" and path.parent != landing_root:
            raise StockLendingEvidenceAuditError(f"{name}: unexpected Landing topology")
        if kind == "historical_page":
            try:
                file_page = int(path.stem.removeprefix("page="))
            except ValueError as error:
                raise StockLendingEvidenceAuditError(f"{name}: invalid page filename") from error
            if path.name != f"page={file_page:05d}.json" or metadata["page_no"] != file_page:
                raise StockLendingEvidenceAuditError(f"{name}: page identity differs")
            metadata["file_page"] = file_page
            history_meta.append(metadata)
            historical_rows += len(rows)
            source_dates = [str(row.get("basDt", "")) for row in rows]
            if any(len(value) != 8 or not value.isdigit() for value in source_dates):
                raise StockLendingEvidenceAuditError(f"{name}: invalid historical source date")
            historical_dates.update(source_dates)
            try:
                historical_normalized_frames.append(NORMALIZERS[name](rows))
            except Exception as error:
                raise StockLendingEvidenceAuditError(
                    f"{name}: historical Landing normalization failed ({type(error).__name__})"
                ) from error
        else:
            capture_date = path.stem
            source_dates = [str(row.get("basDt", "")) for row in rows]
            if (
                len(capture_date) != 8 or not capture_date.isdigit()
                or metadata["page_no"] != 1 or metadata["total_count"] != len(rows)
                or any(value != capture_date for value in source_dates)
            ):
                raise StockLendingEvidenceAuditError(f"{name}: incremental capture scope differs")
            try:
                incremental_frames.append((path, capture_date, NORMALIZERS[name](rows)))
            except Exception as error:
                raise StockLendingEvidenceAuditError(
                    f"{name}: incremental Landing normalization failed ({type(error).__name__})"
                ) from error
        manifest.append({
            "path": _relative(path, project_root), "kind": kind,
            "bytes": path.stat().st_size, "sha256": digest, **metadata,
        })
    if len(hashes) != len(set(hashes)):
        raise StockLendingEvidenceAuditError(f"{name}: duplicate retained response content")
    pages = [int(value["file_page"]) for value in history_meta]
    if pages != list(range(1, len(pages) + 1)):
        raise StockLendingEvidenceAuditError(f"{name}: historical pages are not contiguous")
    totals = {int(value["total_count"]) for value in history_meta}
    sizes = {int(value["page_size"]) for value in history_meta}
    expected_pages = math.ceil(next(iter(totals)) / next(iter(sizes))) if len(totals) == len(sizes) == 1 else -1
    if len(totals) != 1 or len(sizes) != 1 or expected_pages != len(pages) or historical_rows != next(iter(totals)):
        raise StockLendingEvidenceAuditError(f"{name}: historical pagination totals differ")
    page_size = next(iter(sizes))
    for index, metadata in enumerate(history_meta, start=1):
        expected_rows = page_size if index < expected_pages else next(iter(totals)) - page_size * (expected_pages - 1)
        if int(metadata["source_rows"]) != expected_rows:
            raise StockLendingEvidenceAuditError(f"{name}: historical page row count differs")
    historical_normalized = pd.concat(historical_normalized_frames, ignore_index=True)
    historical_normalized = historical_normalized[list(contract.column_names)].sort_values(
        list(contract.sort_key), kind="stable"
    ).reset_index(drop=True)
    try:
        validate_data_v1(historical_normalized, contract, allow_empty=False)
    except Exception as error:
        raise StockLendingEvidenceAuditError(
            f"{name}: full historical Landing normalization failed ({type(error).__name__})"
        ) from error
    historical_full_row_sha256 = _canonical_full_row_digest(historical_normalized, contract)
    del historical_normalized, historical_normalized_frames

    history_state = _load_state(project_root / "data/state" / f"{name}_historical.json", f"{name}_historical")
    expected_completed = [RANGE_MARKER] + [f"{RANGE_MARKER}:page:{page:05d}" for page in pages]
    if (
        sorted(history_state["completed_partitions"]) != sorted(expected_completed)
        or history_state["valid_empty_partitions"] or history_state["failed_partitions"]
        or history_state["staged_partitions"]
    ):
        raise StockLendingEvidenceAuditError(f"{name}: historical checkpoint continuity differs")

    parquet_files = sorted(normalized_root.glob("year=*/data.parquet"))
    all_normalized_files = sorted(p for p in normalized_root.rglob("*") if p.is_file())
    if not parquet_files or parquet_files != all_normalized_files:
        raise StockLendingEvidenceAuditError(f"{name}: Normalized topology differs")
    expected_schema = contract_arrow_schema(contract)
    expected_names_types = [(f.name, f.type) for f in expected_schema]
    frames: list[pd.DataFrame] = []
    parquet_manifest: list[dict[str, object]] = []
    for path in parquet_files:
        try:
            year = int(path.parent.name.removeprefix("year="))
        except ValueError as error:
            raise StockLendingEvidenceAuditError(f"{name}: invalid year partition") from error
        parquet = pq.ParquetFile(path)
        actual_schema = parquet.schema_arrow
        if [(f.name, f.type) for f in actual_schema] != expected_names_types:
            raise StockLendingEvidenceAuditError(f"{name}: Parquet schema/order differs")
        frame = restore_contract_dates(pd.read_parquet(path), contract)
        if list(frame.columns) != list(contract.column_names):
            raise StockLendingEvidenceAuditError(f"{name}: dataframe schema/order differs")
        dates = pd.to_datetime(frame["date"], errors="coerce")
        if dates.isna().any() or not dates.dt.year.eq(year).all():
            raise StockLendingEvidenceAuditError(f"{name}: year partition rows differ")
        frames.append(frame)
        parquet_manifest.append({
            "path": _relative(path, project_root), "bytes": path.stat().st_size,
            "sha256": _sha256_file(path), "rows": parquet.metadata.num_rows,
            "row_groups": parquet.metadata.num_row_groups,
            "physical_schema": [
                {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}
                for field in actual_schema
            ],
            "physical_nullability_matches_contract": actual_schema.equals(expected_schema, check_metadata=False),
        })
    frame = pd.concat(frames, ignore_index=True).sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    try:
        validate_data_v1(frame, contract, allow_empty=False)
    except Exception as error:
        raise StockLendingEvidenceAuditError(f"{name}: domain validation failed ({type(error).__name__})") from error
    key_nulls = int(frame[list(contract.primary_key)].isna().any(axis=1).sum())
    duplicates = int(frame.duplicated(list(contract.primary_key)).sum())
    non_nullable_nulls = {
        column.name: int(frame[column.name].isna().sum())
        for column in contract.columns if not column.nullable and frame[column.name].isna().any()
    }
    infinity = {
        column.name: int(np.isinf(pd.to_numeric(frame[column.name], errors="coerce").to_numpy(dtype="float64")).sum())
        for column in contract.columns if column.dtype.startswith("float")
    }
    if key_nulls or duplicates or non_nullable_nulls or sum(infinity.values()):
        raise StockLendingEvidenceAuditError(f"{name}: PK/null/infinity checks failed")
    normalized_dates = set(pd.to_datetime(frame["date"]).dt.strftime("%Y%m%d"))
    normalized_full_row_sha256 = _canonical_full_row_digest(frame, contract)
    if (
        len(frame) != historical_rows or normalized_dates != historical_dates
        or normalized_full_row_sha256 != historical_full_row_sha256
    ):
        raise StockLendingEvidenceAuditError(f"{name}: historical source/Normalized reconciliation differs")
    incremental_comparisons: list[dict[str, object]] = []
    minimum_date = min(historical_dates)
    maximum_date = max(historical_dates)
    for path, capture_date, source_frame in incremental_frames:
        if capture_date < minimum_date or capture_date > maximum_date:
            raise StockLendingEvidenceAuditError(f"{name}: incremental capture is outside historical coverage")
        normalized_slice = frame[
            pd.to_datetime(frame["date"]).dt.strftime("%Y%m%d").eq(capture_date)
        ].reset_index(drop=True)
        source_digest = _canonical_full_row_digest(source_frame, contract)
        normalized_digest = _canonical_full_row_digest(normalized_slice, contract)
        if len(source_frame) != len(normalized_slice) or source_digest != normalized_digest:
            raise StockLendingEvidenceAuditError(f"{name}: incremental capture differs from Normalized date slice")
        incremental_comparisons.append({
            "path": _relative(path, project_root), "source_date": capture_date,
            "source_rows": len(source_frame), "normalized_slice_rows": len(normalized_slice),
            "source_normalized_full_row_sha256": source_digest,
            "normalized_date_slice_full_row_sha256": normalized_digest,
            "status": "PASS",
        })
    operational = _load_state(project_root / "data/state" / f"{name}.json", name)
    completed_dates = set(str(value) for value in operational["completed_partitions"])
    if (
        normalized_dates != completed_dates or operational["valid_empty_partitions"]
        or operational["failed_partitions"] or operational["staged_partitions"]
    ):
        raise StockLendingEvidenceAuditError(f"{name}: operational checkpoint reconciliation differs")
    coverage = {
        "minimum": min(normalized_dates), "maximum": max(normalized_dates),
        "source_dates": len(normalized_dates),
    }
    return ({
        "dataset": name, "contract": _contract_record(contract),
        "landing": {
            "response_count": len(manifest), "unique_successful_response_count": len(set(hashes)),
            "historical_page_count": len(history_meta), "incremental_capture_count": len(manifest) - len(history_meta),
            "retained_source_rows_including_overlapping_incremental_captures": retained_source_rows,
            "historical_source_rows": historical_rows,
            "historical_normalized_full_row_sha256": historical_full_row_sha256,
            "comparison_scope": (
                "HISTORICAL_PAGES_ONLY; incremental captures overlap historical coverage "
                "and are verified separately against exact Normalized date slices, not appended or double-counted"
            ),
            "incremental_capture_reconciliation": incremental_comparisons,
            "manifest": manifest,
        },
        "checkpoints": {
            "historical": _relative(project_root / "data/state" / f"{name}_historical.json", project_root),
            "operational": _relative(project_root / "data/state" / f"{name}.json", project_root),
            "historical_completed_markers": len(expected_completed), "operational_completed_dates": len(completed_dates),
            "status": "PASS",
        },
        "normalized": {
            "rows": len(frame), "file_count": len(parquet_manifest), "files": parquet_manifest,
            "coverage": coverage, "primary_key": {"status": "PASS", "null_rows": 0, "duplicate_rows_after_first": 0},
            "nulls": {"status": "PASS", "non_nullable_violations": {}},
            "infinity": {"status": "PASS", "counts": infinity, "total": 0},
            "schema": {
                "status": "PASS",
                "physical_nullability": (
                    "MATCH" if all(value["physical_nullability_matches_contract"] for value in parquet_manifest)
                    else "MISMATCH_PHYSICAL_SCHEMA_ONLY"
                ),
                "interpretation": "observed required-column nulls are audited independently",
            },
            "domain": {"status": "PASS"},
            "historical_source_reconciliation": {"status": "PASS", "rows": historical_rows, "source_dates": len(historical_dates)},
            "canonical_full_row_sha256": normalized_full_row_sha256,
        },
    }, normalized_dates)


def build_stock_lending_evidence_audit(project_root: Path) -> dict[str, object]:
    project_root = project_root.absolute()
    _assert_plain_components(project_root, project_root)
    pre_manifest = _input_manifest(project_root)
    datasets: list[dict[str, object]] = []
    date_sets: dict[str, set[str]] = {}
    for name in sorted(CONTRACTS):
        dataset, dates = _audit_dataset(project_root, name)
        datasets.append(dataset)
        date_sets[name] = dates
    response_hashes = [
        response["sha256"]
        for dataset in datasets for response in dataset["landing"]["manifest"]
    ]
    if len(response_hashes) != len(set(response_hashes)):
        raise StockLendingEvidenceAuditError("retained response content is duplicated across datasets")
    post_manifest = _input_manifest(project_root)
    if pre_manifest != post_manifest:
        raise StockLendingEvidenceAuditError("audit inputs changed during scan")
    detail_dates = date_sets[KR_STOCK_LENDING_DAILY.name]
    date_gaps = {}
    for name in (KR_STOCK_LENDING_MARKET_DAILY.name, KR_STOCK_LENDING_PARTICIPANT_DAILY.name):
        extra = sorted(date_sets[name] - detail_dates)
        missing = sorted(detail_dates - date_sets[name])
        date_gaps[name] = {
            "status": "PASS" if not extra else "FAIL", "reference_dataset": KR_STOCK_LENDING_DAILY.name,
            "missing_from_dataset_count": len(missing), "missing_from_dataset": missing,
            "unexpected_extra_count": len(extra), "unexpected_extra": extra,
            "interpretation": "source-absent dates are preserved and are not fabricated",
        }
        if extra:
            raise StockLendingEvidenceAuditError(f"{name}: dates outside detail source coverage")
    unique_count = sum(int(item["landing"]["unique_successful_response_count"]) for item in datasets)
    report: dict[str, object] = {
        "audit_schema": AUDIT_SCHEMA, "audit_version": AUDIT_VERSION,
        "classification": "DATA_COMPLETE_WITH_EXECUTION_REVIEW_REQUIRED",
        "scope": {
            "network_calls": 0, "artifacts_modified": False, "checkpoints_modified": False,
            "retained_unique_successful_responses": unique_count,
        },
        "execution_accounting": {
            "status": EXECUTION_STATUS, "exact_total_calls_known": False,
            "minimum_successful_unique_responses": unique_count,
            "statement": (
                "A wrapper timeout did not terminate its child and an overlapping resume occurred. "
                "Exact task-level call totals cannot be reconstructed; retained unique successful responses are only a lower bound."
            ),
        },
        "input_manifest": pre_manifest,
        "input_manifest_sha256": _sha256_bytes(_canonical_bytes(pre_manifest)),
        "datasets": datasets, "date_gaps_against_detail": date_gaps,
        "global_response_hash_uniqueness": {
            "status": "PASS", "response_count": len(response_hashes),
            "unique_sha256_count": len(set(response_hashes)),
        },
        "summary": {"artifact_validation_status": "PASS", "execution_review_status": EXECUTION_STATUS},
    }
    report["audit_manifest_sha256"] = _sha256_bytes(_canonical_bytes(report))
    return report


def _state_path(project_root: Path, report: Mapping[str, object]) -> Path:
    return project_root / DEFAULT_STATE_RELATIVE / f"{report['audit_manifest_sha256']}.json"


def write_stock_lending_evidence_state(project_root: Path, report: Mapping[str, object]) -> dict[str, object]:
    project_root = project_root.absolute()
    supplied = dict(report)
    digest = supplied.pop("audit_manifest_sha256", None)
    if digest != _sha256_bytes(_canonical_bytes(supplied)):
        raise StockLendingEvidenceAuditError("audit report digest differs")
    with _collector_input_lock(project_root):
        return _write_stock_lending_evidence_state_locked(project_root, report)


def _write_stock_lending_evidence_state_locked(
    project_root: Path, report: Mapping[str, object]
) -> dict[str, object]:
    """Rebuild and publish while the canonical collector lock is owned."""
    # Rebuild independently: callers cannot forge PASS fields, manifests, or contracts.
    current = build_stock_lending_evidence_audit(project_root)
    if _canonical_bytes(current) != _canonical_bytes(report):
        raise StockLendingEvidenceAuditError("audit report differs from current inputs")
    if _input_manifest(project_root) != current["input_manifest"]:
        raise StockLendingEvidenceAuditError("audit inputs changed immediately before state creation")
    state_root = project_root / DEFAULT_STATE_RELATIVE
    state_parent = project_root / "data/state"
    _assert_plain_components(project_root, state_parent)
    current_directory = state_parent
    for component in Path("audits/stock_lending_retained_execution").parts:
        current_directory /= component
        if current_directory.exists():
            _assert_plain_components(project_root, current_directory)
        else:
            current_directory.mkdir()
            _assert_plain_components(project_root, current_directory)
    path = _state_path(project_root, current)
    payload = _canonical_bytes(current)
    with _audit_state_lock(project_root, state_root):
        _assert_plain_components(project_root, path.parent)
        pre_write_manifest = _input_manifest(project_root)
        if pre_write_manifest != current["input_manifest"]:
            raise StockLendingEvidenceAuditError("audit inputs changed at state creation gate")
        if path.exists():
            _assert_plain_components(project_root, path)
            if not path.is_file() or path.read_bytes() != payload:
                raise StockLendingEvidenceAuditError("immutable audit state content differs")
            return {"status": "EXISTS_IDENTICAL", "path": _relative(path, project_root), "report": current}
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _assert_plain_components(project_root, path.parent)
            immediate_manifest = _input_manifest(project_root)
            if immediate_manifest != pre_write_manifest or immediate_manifest != current["input_manifest"]:
                raise StockLendingEvidenceAuditError("audit inputs changed immediately before immutable link")
            try:
                os.link(temporary, path)
            except FileExistsError:
                _assert_plain_components(project_root, path)
                if not path.is_file() or path.read_bytes() != payload:
                    raise StockLendingEvidenceAuditError("immutable audit state collision")
                return {"status": "EXISTS_IDENTICAL", "path": _relative(path, project_root), "report": current}
        finally:
            temporary.unlink(missing_ok=True)
        return {"status": "CREATED", "path": _relative(path, project_root), "report": current}


def upgrade_stock_lending_evidence_state(project_root: Path) -> dict[str, object]:
    return write_stock_lending_evidence_state(project_root, build_stock_lending_evidence_audit(project_root))
