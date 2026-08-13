"""Deterministic local-artifact audits for retained Yahoo/FRED datasets.

This module reads Normalized Parquet only.  It deliberately does not infer or
reconstruct HTTP provenance that was not retained by the original collectors.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_data.contracts.base import DatasetContract
from stock_data.contracts.global_market import (
    FRED_TREASURY_YIELD_DAILY,
    FRED_USD_FX_DAILY,
    GLOBAL_INDEX_PRICE_DAILY,
)
from stock_data.storage.contract_arrow import contract_arrow_schema, restore_contract_dates
from stock_data.validation.global_market import validate_fred, validate_global_index


AUDIT_SCHEMA = "stock_data.global_normalized_artifact_manifest"
AUDIT_VERSION = 1
PROVENANCE_STATUS = "PROVENANCE_LIMITED_NO_RETAINED_LANDING"
DEFAULT_STATE_RELATIVE = Path("data/state/audits/global_normalized_artifacts")
CONTRACTS: Mapping[str, DatasetContract] = {
    contract.name: contract
    for contract in (
        GLOBAL_INDEX_PRICE_DAILY,
        FRED_TREASURY_YIELD_DAILY,
        FRED_USD_FX_DAILY,
    )
}
VALIDATORS = {
    GLOBAL_INDEX_PRICE_DAILY.name: validate_global_index,
    FRED_TREASURY_YIELD_DAILY.name: validate_fred,
    FRED_USD_FX_DAILY.name: validate_fred,
}


class GlobalArtifactAuditError(RuntimeError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise GlobalArtifactAuditError(f"artifact path escapes project root: {path}") from error


def _field(field: pa.Field) -> dict[str, object]:
    return {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}


def _schema_record(schema: pa.Schema) -> dict[str, object]:
    fields = [_field(field) for field in schema]
    signature = _sha256_bytes(_canonical_bytes(fields))
    return {"schema_sha256": signature, "fields": fields}


def _contract_record(contract: DatasetContract) -> dict[str, object]:
    return {
        "name": contract.name,
        "version": contract.version,
        "status": contract.status,
        "source": contract.source,
        "layer": contract.layer,
        "storage_format": contract.storage_format,
        "frequency": contract.frequency,
        "timezone": contract.timezone,
        "primary_key": list(contract.primary_key),
        "sort_key": list(contract.sort_key),
        "partition_by": list(contract.partition_by),
        "columns": [asdict(column) for column in contract.columns],
        "expected_arrow_schema": _schema_record(contract_arrow_schema(contract)),
    }


def _partition_values(path: Path, dataset_root: Path, contract: DatasetContract) -> dict[str, str]:
    parts = path.parent.relative_to(dataset_root).parts
    if len(parts) != len(contract.partition_by):
        raise GlobalArtifactAuditError(f"{contract.name}: partition depth differs")
    values: dict[str, str] = {}
    for expected, component in zip(contract.partition_by, parts):
        prefix = expected + "="
        if not component.startswith(prefix) or not component.removeprefix(prefix):
            raise GlobalArtifactAuditError(f"{contract.name}: partition path differs")
        values[expected] = component.removeprefix(prefix)
    return values


def _validate_partition_rows(
    frame: pd.DataFrame,
    partition_values: Mapping[str, str],
    contract: DatasetContract,
) -> bool:
    for name, expected in partition_values.items():
        if name == "year":
            dates = pd.to_datetime(frame["date"], errors="coerce")
            if dates.isna().any() or not dates.dt.year.eq(int(expected)).all():
                return False
        elif name not in frame or not frame[name].astype("string").eq(expected).all():
            return False
    return True


def _dataset_files(dataset_root: Path) -> tuple[list[Path], list[Path]]:
    if not dataset_root.is_dir():
        raise GlobalArtifactAuditError(f"Normalized dataset is missing: {dataset_root.name}")
    all_files = sorted(path for path in dataset_root.rglob("*") if path.is_file())
    for path in all_files:
        if path.is_symlink():
            raise GlobalArtifactAuditError("symlinked artifact files are not auditable")
    parquet = [path for path in all_files if path.name == "data.parquet"]
    unexpected = [path for path in all_files if path not in parquet]
    if not parquet:
        raise GlobalArtifactAuditError(f"Normalized dataset has no Parquet: {dataset_root.name}")
    return parquet, unexpected


def build_dataset_audit(project_root: Path, dataset: str) -> dict[str, object]:
    """Build one deterministic exact audit without writing state or reading Landing."""
    project_root = project_root.resolve()
    try:
        contract = CONTRACTS[dataset]
    except KeyError as error:
        raise ValueError(f"unsupported global dataset: {dataset}") from error
    dataset_root = project_root / "data" / "normalized" / dataset
    parquet_files, unexpected_files = _dataset_files(dataset_root)
    expected = contract_arrow_schema(contract)
    expected_names_types = [(field.name, field.type) for field in expected]
    schema_groups: dict[str, dict[str, object]] = {}
    file_manifest: list[dict[str, object]] = []
    frames: list[pd.DataFrame] = []
    partition_failures: list[str] = []
    type_or_order_mismatches: list[str] = []
    physical_nullability_mismatches: list[str] = []

    for path in parquet_files:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        schema_info = _schema_record(schema)
        signature = str(schema_info["schema_sha256"])
        schema_groups.setdefault(signature, {**schema_info, "file_count": 0})
        schema_groups[signature]["file_count"] = int(schema_groups[signature]["file_count"]) + 1
        relative = _relative(path, project_root)
        actual_names_types = [(field.name, field.type) for field in schema]
        if actual_names_types != expected_names_types:
            type_or_order_mismatches.append(relative)
        if not schema.equals(expected, check_metadata=False):
            physical_nullability_mismatches.append(relative)
        partition_values = _partition_values(path, dataset_root, contract)
        frame = restore_contract_dates(pd.read_parquet(path), contract)
        if list(frame.columns) == list(contract.column_names):
            frame = frame[list(contract.column_names)]
            if not _validate_partition_rows(frame, partition_values, contract):
                partition_failures.append(relative)
            frames.append(frame)
        else:
            partition_failures.append(relative)
        file_manifest.append(
            {
                "path": relative,
                "partition_values": dict(partition_values),
                "bytes": path.stat().st_size,
                "rows": parquet.metadata.num_rows,
                "row_groups": parquet.metadata.num_row_groups,
                "sha256": _sha256_file(path),
                "schema_sha256": signature,
            }
        )

    unexpected_manifest = [
        {
            "path": _relative(path, project_root),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in unexpected_files
    ]
    total_rows = sum(int(value["rows"]) for value in file_manifest)
    schema_status = "PASS" if not type_or_order_mismatches else "FAIL"
    if schema_status == "PASS":
        frame = pd.concat(frames, ignore_index=True)
        frame = frame[list(contract.column_names)].sort_values(
            list(contract.sort_key), kind="stable"
        ).reset_index(drop=True)
    else:
        frame = pd.DataFrame(columns=contract.column_names)

    null_counts = {column: int(frame[column].isna().sum()) for column in contract.column_names}
    non_nullable_violations = {
        column.name: null_counts[column.name]
        for column in contract.columns
        if not column.nullable and null_counts[column.name]
    }
    key_null_rows = int(frame[list(contract.primary_key)].isna().any(axis=1).sum())
    duplicate_rows = int(frame.duplicated(list(contract.primary_key)).sum())
    infinity_counts: dict[str, int] = {}
    for column in contract.columns:
        if column.dtype.startswith("float"):
            values = pd.to_numeric(frame[column.name], errors="coerce").to_numpy(dtype="float64")
            infinity_counts[column.name] = int(np.isinf(values).sum())
    infinity_total = sum(infinity_counts.values())
    dates = pd.to_datetime(frame["date"], errors="coerce")
    coverage = {
        "column": "date",
        "minimum": None if dates.empty or dates.isna().all() else dates.min().date().isoformat(),
        "maximum": None if dates.empty or dates.isna().all() else dates.max().date().isoformat(),
        "null_count": int(dates.isna().sum()),
    }
    try:
        VALIDATORS[dataset](frame)
        domain_validation = {"status": "PASS", "error_type": None}
    except Exception as error:
        domain_validation = {"status": "FAIL", "error_type": type(error).__name__}

    checks = {
        "row_count": {
            "status": "PASS" if len(frame) == total_rows else "FAIL",
            "manifest_rows": total_rows,
            "scanned_rows": len(frame),
        },
        "contract_schema_types_and_order": {
            "status": schema_status,
            "mismatch_files": type_or_order_mismatches,
        },
        "physical_nullability": {
            "status": "MATCH" if not physical_nullability_mismatches else "MISMATCH",
            "mismatch_files": physical_nullability_mismatches,
            "interpretation": "PHYSICAL_SCHEMA_ONLY; observed nulls are audited separately",
        },
        "partition_rows": {
            "status": "PASS" if not partition_failures else "FAIL",
            "mismatch_files": partition_failures,
        },
        "unexpected_files": {
            "status": "PASS" if not unexpected_manifest else "FAIL",
            "files": unexpected_manifest,
        },
        "primary_key": {
            "status": (
                "NOT_RUN_SCHEMA_MISMATCH" if schema_status != "PASS"
                else ("PASS" if not key_null_rows and not duplicate_rows else "FAIL")
            ),
            "columns": list(contract.primary_key),
            "null_key_rows": key_null_rows,
            "duplicate_rows_after_first": duplicate_rows,
            "audited_rows": len(frame),
        },
        "nulls": {
            "status": (
                "NOT_RUN_SCHEMA_MISMATCH" if schema_status != "PASS"
                else ("PASS" if not non_nullable_violations else "FAIL")
            ),
            "counts": null_counts,
            "non_nullable_violations": non_nullable_violations,
            "audited_rows": len(frame),
        },
        "infinity": {
            "status": (
                "NOT_RUN_SCHEMA_MISMATCH" if schema_status != "PASS"
                else ("PASS" if infinity_total == 0 else "FAIL")
            ),
            "counts": infinity_counts,
            "total": infinity_total,
            "audited_rows": len(frame),
        },
        "domain_validation": domain_validation,
    }
    failing = sorted(
        name for name, check in checks.items()
        if check["status"] == "FAIL"
    )
    artifact_manifest_sha256 = _sha256_bytes(
        _canonical_bytes({"files": file_manifest, "unexpected_files": unexpected_manifest})
    )
    report: dict[str, object] = {
        "audit_schema": AUDIT_SCHEMA,
        "audit_version": AUDIT_VERSION,
        "dataset": dataset,
        "classification": PROVENANCE_STATUS,
        "source_provenance": {
            "status": PROVENANCE_STATUS,
            "retained_lossless_landing": False,
            "retained_call_ledger": False,
            "source_provenance_reconstructed": False,
            "statement": (
                "This audit proves only the current local Normalized artifact; "
                "it does not prove or recreate provider-response provenance."
            ),
        },
        "scope": {
            "network_calls": 0,
            "landing_files_read": 0,
            "collector_checkpoint_modified": False,
            "artifact_root": _relative(dataset_root, project_root),
        },
        "contract": _contract_record(contract),
        "physical_schemas": sorted(schema_groups.values(), key=lambda value: value["schema_sha256"]),
        "file_manifest": file_manifest,
        "artifact_manifest_sha256": artifact_manifest_sha256,
        "summary": {
            "validation_status": "PASS" if not failing else "FAIL",
            "failing_checks": failing,
            "file_count": len(file_manifest),
            "bytes": sum(int(value["bytes"]) for value in file_manifest),
            "rows": total_rows,
            "coverage": coverage,
        },
        "checks": checks,
    }
    report["audit_manifest_sha256"] = _sha256_bytes(_canonical_bytes(report))
    return report


def build_global_artifact_audits(
    project_root: Path, datasets: Iterable[str] | None = None
) -> list[dict[str, object]]:
    selected = sorted(CONTRACTS if datasets is None else set(datasets))
    return [build_dataset_audit(project_root, dataset) for dataset in selected]


def _state_path(project_root: Path, report: Mapping[str, object]) -> Path:
    digest = str(report["audit_manifest_sha256"])
    return project_root / DEFAULT_STATE_RELATIVE / str(report["dataset"]) / f"{digest}.json"


def write_audit_state(project_root: Path, report: Mapping[str, object]) -> dict[str, object]:
    """Atomically create one content-addressed immutable audit state."""
    project_root = project_root.resolve()
    expected = dict(report)
    supplied_digest = expected.pop("audit_manifest_sha256", None)
    digest = _sha256_bytes(_canonical_bytes(expected))
    if supplied_digest != digest:
        raise GlobalArtifactAuditError("audit manifest digest differs")
    dataset = report.get("dataset")
    if (
        dataset not in CONTRACTS
        or report.get("audit_schema") != AUDIT_SCHEMA
        or report.get("audit_version") != AUDIT_VERSION
        or report.get("classification") != PROVENANCE_STATUS
        or not isinstance(report.get("source_provenance"), Mapping)
        or report["source_provenance"].get("status") != PROVENANCE_STATUS
        or report["source_provenance"].get("retained_lossless_landing") is not False
        or report["source_provenance"].get("retained_call_ledger") is not False
        or report["source_provenance"].get("source_provenance_reconstructed") is not False
        or not isinstance(report.get("scope"), Mapping)
        or report["scope"].get("network_calls") != 0
        or report["scope"].get("landing_files_read") != 0
        or report["scope"].get("collector_checkpoint_modified") is not False
    ):
        raise GlobalArtifactAuditError("audit state identity or provenance boundary differs")
    target = _state_path(project_root, report)
    _relative(target, project_root)
    body = json.dumps(
        report, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_symlink() or not target.is_file() or target.read_bytes() != body:
            raise GlobalArtifactAuditError("immutable audit state differs")
        return {"dataset": report["dataset"], "status": "ALREADY_RECORDED",
                "path": _relative(target, project_root), "audit_manifest_sha256": digest}
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != body:
                raise GlobalArtifactAuditError("immutable audit state collision")
            status = "ALREADY_RECORDED"
        else:
            status = "CREATED"
        return {"dataset": report["dataset"], "status": status,
                "path": _relative(target, project_root), "audit_manifest_sha256": digest}
    finally:
        temporary.unlink(missing_ok=True)


def upgrade_global_artifact_audit_states(
    project_root: Path, datasets: Iterable[str] | None = None
) -> list[dict[str, object]]:
    reports = build_global_artifact_audits(project_root, datasets)
    return [write_audit_state(project_root, report) for report in reports]
