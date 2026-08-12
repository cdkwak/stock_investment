from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.base import DatasetContract
from stock_data.contracts.investor_bridge import (
    KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
)
from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
    KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER,
    KR_EQUITY_PRICE_DAILY,
    KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.published.canonical_equity_universe import validate_canonical_universe
from stock_data.storage.contract_arrow import (
    contract_arrow_schema,
    dataframe_to_contract_table,
    restore_contract_dates,
)
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.investor_bridge import validate_investor_bridge
from stock_data.validation.kr_equity import (
    validate_equity_market_cap,
    validate_equity_master,
    validate_equity_price,
)


Validator = Callable[[pd.DataFrame], None]


class SchemaMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationSpec:
    contract: DatasetContract
    relative_root: str
    validator: Validator


MIGRATION_SPECS = {
    spec.contract.name: spec
    for spec in (
        MigrationSpec(
            KR_EQUITY_MARKET_CAP_DAILY,
            "data/normalized/kr_equity_market_cap_daily",
            validate_equity_market_cap,
        ),
        MigrationSpec(
            KR_EQUITY_MASTER,
            "data/normalized/kr_equity_master",
            validate_equity_master,
        ),
        MigrationSpec(
            KR_EQUITY_PRICE_DAILY,
            "data/normalized/kr_equity_price_daily",
            validate_equity_price,
        ),
        MigrationSpec(
            KR_EQUITY_UNIVERSE_DAILY,
            "data/normalized/kr_equity_universe_daily",
            lambda frame: validate_data_v1(frame, KR_EQUITY_UNIVERSE_DAILY),
        ),
        MigrationSpec(
            KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
            "data/published/kr_equity_canonical_universe_daily",
            validate_canonical_universe,
        ),
        MigrationSpec(
            KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
            "data/published/kr_market_investor_net_purchase_bridge_daily",
            validate_investor_bridge,
        ),
    )
}

_TRANSACTION_PHASES = {
    "PREPARING", "STAGED", "PROMOTION_PENDING", "ROOT_BACKED_UP", "PROMOTED", "VERIFIED",
    "BACKUP_RETIRED", "RECOVERED_ORIGINAL",
}


def _partition_values(path: Path, root: Path, contract: DatasetContract) -> dict[str, str]:
    relative = path.parent.relative_to(root)
    parts = relative.parts
    if len(parts) != len(contract.partition_by):
        raise SchemaMigrationError(f"unexpected partition path: {relative.as_posix()}")
    values: dict[str, str] = {}
    for expected, part in zip(contract.partition_by, parts):
        prefix = expected + "="
        if not part.startswith(prefix) or not part[len(prefix):]:
            raise SchemaMigrationError(f"unexpected partition path: {relative.as_posix()}")
        values[expected] = part[len(prefix):]
    return values


def _verify_partition_values(
    frame: pd.DataFrame, values: dict[str, str], contract: DatasetContract, relative: str
) -> None:
    for name, expected in values.items():
        if name == "year":
            date_column = "date" if "date" in frame else "source_snapshot_date"
            actual = pd.to_datetime(frame[date_column], errors="raise").dt.year.astype(str)
        else:
            actual = frame[name].astype(str)
        if not actual.eq(expected).all():
            raise SchemaMigrationError(f"partition values differ from path: {relative}")


def _verify_disjoint_partition_contract(contract: DatasetContract) -> None:
    primary_key = set(contract.primary_key)
    for name in contract.partition_by:
        key_name = (
            "date" if name == "year" and "date" in contract.column_names
            else "source_snapshot_date" if name == "year" else name
        )
        if key_name not in primary_key:
            raise SchemaMigrationError(
                f"partition {name} is not derived from the primary key for {contract.name}"
            )


def _canonical_value(value: object, dtype: str) -> str:
    if value is None or pd.isna(value):
        return "N:"
    if dtype == "date32":
        return "V:" + pd.Timestamp(value).date().isoformat()
    if dtype.startswith("timestamp["):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise SchemaMigrationError("timezone-naive timestamp in fingerprint")
        return "V:" + timestamp.tz_convert("UTC").isoformat()
    if dtype == "int64":
        numeric = Decimal(str(value))
        if not numeric.is_finite() or numeric != numeric.to_integral_value():
            raise SchemaMigrationError("non-integral int64 value in fingerprint")
        return "V:" + str(int(numeric))
    if dtype == "float64":
        numeric = float(value)
        if not np.isfinite(numeric):
            raise SchemaMigrationError("non-finite float64 value in fingerprint")
        return "V:" + numeric.hex()
    if dtype.startswith("decimal("):
        numeric = Decimal(str(value))
        if not numeric.is_finite():
            raise SchemaMigrationError("non-finite decimal value in fingerprint")
        return "V:" + format(numeric, "f")
    if dtype == "bool":
        if not isinstance(value, (bool, np.bool_)):
            raise SchemaMigrationError("non-boolean value in fingerprint")
        return "V:1" if value else "V:0"
    return "V:" + str(value)


def _fingerprint(frame: pd.DataFrame, contract: DatasetContract) -> str:
    ordered = frame.sort_values(list(contract.primary_key), kind="stable").reset_index(drop=True)
    canonical = pd.DataFrame(index=ordered.index)
    for column in contract.columns:
        canonical[column.name] = ordered[column.name].map(
            lambda value, dtype=column.dtype: _canonical_value(value, dtype)
        )
    payload = canonical.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _marker_path(root: Path, dataset: str) -> Path:
    return root.parent / f".{dataset}.schema-migration.transaction.json"


def _write_marker(path: Path, payload: dict[str, str]) -> None:
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _transaction_paths(
    root: Path, dataset: str, payload: dict[str, object]
) -> tuple[Path, Path, Path]:
    transaction_id = str(payload.get("transaction_id", ""))
    expected_stage = f".{dataset}.schema-migration.stage.{transaction_id}"
    expected_backup = f".{dataset}.schema-migration.backup.{transaction_id}"
    expected_retired = f".{dataset}.schema-migration.retired.{transaction_id}"
    if (
        len(transaction_id) != 32
        or any(character not in "0123456789abcdef" for character in transaction_id)
        or payload.get("dataset") != dataset
        or payload.get("root_name") != root.name
        or payload.get("stage_name") != expected_stage
        or payload.get("backup_name") != expected_backup
        or payload.get("retired_name") != expected_retired
        or payload.get("phase") not in _TRANSACTION_PHASES
        or payload.get("mode") not in {"dry-run", "apply"}
    ):
        raise SchemaMigrationError("transaction marker is invalid or unsafe")
    return (
        root.parent / expected_stage,
        root.parent / expected_backup,
        root.parent / expected_retired,
    )


def _transaction_orphans(root: Path, dataset: str) -> set[Path]:
    parent = root.parent
    result = set(parent.glob(f".{dataset}.schema-migration.stage.*"))
    result.update(parent.glob(f".{dataset}.schema-migration.backup.*"))
    result.update(parent.glob(f".{dataset}.schema-migration.retired.*"))
    result.update(parent.glob(f".{dataset}.schema-migration.transaction.json.*.tmp"))
    return result


def recover_interrupted_transaction(*, project_root: Path, dataset: str) -> str:
    if dataset not in MIGRATION_SPECS:
        raise SchemaMigrationError(f"dataset is not allowlisted: {dataset}")
    spec = MIGRATION_SPECS[dataset]
    root = project_root.resolve() / spec.relative_root
    marker = _marker_path(root, dataset)
    orphans = _transaction_orphans(root, dataset)
    if not marker.exists():
        if orphans:
            raise SchemaMigrationError("orphan schema-migration paths exist without a marker")
        if not root.is_dir():
            raise SchemaMigrationError(f"canonical dataset root is missing: {root}")
        return "NONE"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaMigrationError("transaction marker is unreadable") from error
    if not isinstance(payload, dict):
        raise SchemaMigrationError("transaction marker is invalid")
    stage, backup, retired = _transaction_paths(root, dataset, payload)
    unexpected = orphans - {stage, backup, retired}
    if unexpected:
        raise SchemaMigrationError("ambiguous orphan schema-migration paths exist")
    for path in (stage, backup, retired):
        if path.exists() and not path.is_dir():
            raise SchemaMigrationError("transaction path is not a directory")

    root_exists = root.is_dir()
    stage_exists, backup_exists, retired_exists = (
        stage.is_dir(), backup.is_dir(), retired.is_dir()
    )
    try:
        if retired_exists:
            if (
                root_exists and not stage_exists and not backup_exists
                and payload["phase"] in {"VERIFIED", "BACKUP_RETIRED"}
            ):
                shutil.rmtree(retired)
                marker.unlink()
                return "FINALIZED_VERIFIED_ROOT"
            raise SchemaMigrationError("retired backup state is ambiguous")
        if not root_exists and backup_exists:
            backup.replace(root)
            _set_phase(marker, payload, "RECOVERED_ORIGINAL")
            if stage_exists:
                shutil.rmtree(stage)
            marker.unlink()
            return "RESTORED_BACKUP"
        if root_exists and stage_exists and not backup_exists:
            shutil.rmtree(stage)
            marker.unlink()
            return "DISCARDED_UNPROMOTED_STAGE"
        if root_exists and backup_exists and not stage_exists:
            root.replace(stage)
            backup.replace(root)
            _set_phase(marker, payload, "RECOVERED_ORIGINAL")
            shutil.rmtree(stage)
            marker.unlink()
            return "ROLLED_BACK_PROMOTED_ROOT"
        if root_exists and not stage_exists and not backup_exists:
            if payload["phase"] not in {
                "PREPARING", "STAGED", "PROMOTION_PENDING", "VERIFIED",
                "BACKUP_RETIRED", "RECOVERED_ORIGINAL",
            }:
                raise SchemaMigrationError("transaction paths contradict marker phase")
            marker.unlink()
            return "CLEARED_MARKER"
    except SchemaMigrationError:
        raise
    except Exception as error:
        raise SchemaMigrationError(
            "automatic transaction recovery failed; marker and recoverable paths were retained"
        ) from error
    raise SchemaMigrationError(
        "ambiguous interrupted transaction; canonical root was not modified"
    )


def _begin_transaction(
    root: Path, dataset: str, mode: str
) -> tuple[Path, Path, Path, Path, dict[str, str]]:
    transaction_id = uuid4().hex
    stage = root.parent / f".{dataset}.schema-migration.stage.{transaction_id}"
    backup = root.parent / f".{dataset}.schema-migration.backup.{transaction_id}"
    retired = root.parent / f".{dataset}.schema-migration.retired.{transaction_id}"
    marker = _marker_path(root, dataset)
    payload = {
        "transaction_id": transaction_id,
        "dataset": dataset,
        "root_name": root.name,
        "stage_name": stage.name,
        "backup_name": backup.name,
        "retired_name": retired.name,
        "mode": mode,
        "phase": "PREPARING",
    }
    _write_marker(marker, payload)
    try:
        stage.mkdir()
    except Exception:
        marker.unlink(missing_ok=True)
        raise
    return stage, backup, retired, marker, payload


def _set_phase(marker: Path, payload: dict[str, str], phase: str) -> None:
    updated = {**payload, "phase": phase}
    _write_marker(marker, updated)
    payload.clear()
    payload.update(updated)


def inspect_dataset(root: Path, spec: MigrationSpec, *, require_schema: bool) -> dict[str, object]:
    contract = spec.contract
    _verify_disjoint_partition_contract(contract)
    if not root.is_dir():
        raise SchemaMigrationError(f"canonical dataset root is missing: {root}")
    unexpected_files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "data.parquet"
    )
    if unexpected_files:
        raise SchemaMigrationError(
            f"unexpected files would not be migrated: {unexpected_files[:3]}"
        )
    paths = sorted(root.rglob("data.parquet"))
    if not paths:
        raise SchemaMigrationError(f"dataset has no Parquet files: {root}")
    expected_schema = contract_arrow_schema(contract)
    for path in paths:
        relative = path.relative_to(root).as_posix()
        schema = pq.ParquetFile(path).schema_arrow
        if tuple(schema.names) != contract.column_names:
            raise SchemaMigrationError(
                f"physical columns/order differ from contract before projection: {relative}"
            )
        if require_schema and not schema.equals(expected_schema, check_metadata=False):
            raise SchemaMigrationError(f"physical schema differs from contract: {relative}")
    partitions = []
    total_rows = 0
    null_counts = {column.name: 0 for column in contract.columns}
    minimum = maximum = None
    dataset_hash = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        partition_values = _partition_values(path, root, contract)
        frame = restore_contract_dates(pd.read_parquet(path), contract)
        frame = frame[list(contract.column_names)].reset_index(drop=True)
        duplicate_rows = int(frame.duplicated(list(contract.primary_key)).sum())
        if duplicate_rows:
            raise SchemaMigrationError(f"duplicate primary key in partition: {relative}")
        _verify_partition_values(frame, partition_values, contract, relative)
        spec.validator(frame)
        fingerprint = _fingerprint(frame, contract)
        dataset_hash.update(relative.encode("utf-8") + b"\0" + fingerprint.encode("ascii") + b"\n")
        rows = len(frame)
        total_rows += rows
        partition_nulls = {
            column.name: int(frame[column.name].isna().sum()) for column in contract.columns
        }
        for column in contract.columns:
            null_counts[column.name] += partition_nulls[column.name]
        coverage_first = coverage_last = None
        if "date" in frame:
            dates = pd.to_datetime(frame["date"], errors="raise")
            coverage_first = dates.min().date().isoformat()
            coverage_last = dates.max().date().isoformat()
            minimum = coverage_first if minimum is None else min(minimum, coverage_first)
            maximum = coverage_last if maximum is None else max(maximum, coverage_last)
        partitions.append({
            "path": relative,
            "rows": rows,
            "coverage_first": coverage_first,
            "coverage_last": coverage_last,
            "null_counts": partition_nulls,
            "pk_duplicate_rows": duplicate_rows,
            "row_fingerprint_sha256": fingerprint,
        })
    return {
        "dataset": contract.name,
        "files": len(paths),
        "partitions": len(partitions),
        "rows": total_rows,
        "coverage_first": minimum,
        "coverage_last": maximum,
        "null_counts": null_counts,
        "pk_check": "PASS_EXACT_PER_DISJOINT_PARTITION",
        "dataset_row_fingerprint_sha256": dataset_hash.hexdigest(),
        "partition_manifest": partitions,
    }


def _comparable(manifest: dict[str, object]) -> dict[str, object]:
    return {
        key: manifest[key]
        for key in (
            "dataset", "files", "partitions", "rows", "coverage_first", "coverage_last",
            "null_counts", "pk_check", "dataset_row_fingerprint_sha256", "partition_manifest",
        )
    }


def _rewrite_to_stage(source: Path, stage: Path, spec: MigrationSpec) -> None:
    contract = spec.contract
    for path in sorted(source.rglob("data.parquet")):
        relative = path.relative_to(source)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        frame = restore_contract_dates(pd.read_parquet(path), contract)
        frame = frame[list(contract.column_names)].reset_index(drop=True)
        spec.validator(frame)
        pq.write_table(dataframe_to_contract_table(frame, contract), target)


def run_schema_migration(
    *, project_root: Path, dataset: str, mode: str, confirmation: str | None = None
) -> dict[str, object]:
    if dataset not in MIGRATION_SPECS:
        raise SchemaMigrationError(f"dataset is not allowlisted: {dataset}")
    if mode not in {"verify", "dry-run", "apply"}:
        raise SchemaMigrationError(f"unsupported mode: {mode}")
    if mode == "apply" and confirmation != dataset:
        raise SchemaMigrationError("apply requires exact dataset confirmation")
    spec = MIGRATION_SPECS[dataset]
    root = project_root.resolve() / spec.relative_root
    startup_recovery = recover_interrupted_transaction(
        project_root=project_root, dataset=dataset
    )
    if mode == "verify":
        manifest = inspect_dataset(root, spec, require_schema=True)
        return {
            "mode": mode, "status": "VERIFIED", "startup_recovery": startup_recovery,
            "manifest": manifest,
        }

    pre = inspect_dataset(root, spec, require_schema=False)
    stage, backup, retired, marker, transaction = _begin_transaction(root, dataset, mode)
    try:
        _rewrite_to_stage(root, stage, spec)
        post = inspect_dataset(stage, spec, require_schema=True)
        if _comparable(pre) != _comparable(post):
            raise SchemaMigrationError("staged data differs from source manifest")
        _set_phase(marker, transaction, "STAGED")
        if mode == "dry-run":
            shutil.rmtree(stage)
            marker.unlink()
            return {
                "mode": mode, "status": "DRY_RUN_PASS", "startup_recovery": startup_recovery,
                "pre": pre, "post": post,
            }
        _set_phase(marker, transaction, "PROMOTION_PENDING")
        root.replace(backup)
        _set_phase(marker, transaction, "ROOT_BACKED_UP")
        stage.replace(root)
        _set_phase(marker, transaction, "PROMOTED")
        final = inspect_dataset(root, spec, require_schema=True)
        if _comparable(post) != _comparable(final):
            raise SchemaMigrationError("promoted data differs from staged manifest")
        _set_phase(marker, transaction, "VERIFIED")
        backup.replace(retired)
        _set_phase(marker, transaction, "BACKUP_RETIRED")
        shutil.rmtree(retired)
        marker.unlink()
        return {
            "mode": mode, "status": "MIGRATED", "startup_recovery": startup_recovery,
            "pre": pre, "post": final,
        }
    except Exception:
        try:
            recover_interrupted_transaction(project_root=project_root, dataset=dataset)
        except Exception as recovery_error:
            raise SchemaMigrationError(
                "migration failed and automatic recovery did not complete"
            ) from recovery_error
        raise
