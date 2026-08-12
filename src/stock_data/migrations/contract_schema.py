from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from pathlib import Path
import shutil
import tempfile
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


def inspect_dataset(root: Path, spec: MigrationSpec, *, require_schema: bool) -> dict[str, object]:
    contract = spec.contract
    _verify_disjoint_partition_contract(contract)
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
    partitions = []
    total_rows = 0
    null_counts = {column.name: 0 for column in contract.columns}
    minimum = maximum = None
    dataset_hash = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        partition_values = _partition_values(path, root, contract)
        parquet = pq.ParquetFile(path)
        if require_schema and not parquet.schema_arrow.equals(expected_schema, check_metadata=False):
            raise SchemaMigrationError(f"physical schema differs from contract: {relative}")
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


def _promote_root(root: Path, stage: Path) -> Path:
    backup = root.with_name(f".{root.name}.schema-migration.backup.{uuid4().hex}")
    root.replace(backup)
    try:
        stage.replace(root)
    except Exception:
        if not root.exists() and backup.exists():
            backup.replace(root)
        raise
    return backup


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
    if mode == "verify":
        manifest = inspect_dataset(root, spec, require_schema=True)
        return {"mode": mode, "status": "VERIFIED", "manifest": manifest}

    pre = inspect_dataset(root, spec, require_schema=False)
    stage = Path(tempfile.mkdtemp(prefix=f".{dataset}.schema-migration.stage.", dir=root.parent))
    try:
        _rewrite_to_stage(root, stage, spec)
        post = inspect_dataset(stage, spec, require_schema=True)
        if _comparable(pre) != _comparable(post):
            raise SchemaMigrationError("staged data differs from source manifest")
        if mode == "dry-run":
            return {"mode": mode, "status": "DRY_RUN_PASS", "pre": pre, "post": post}
        backup = _promote_root(root, stage)
        try:
            final = inspect_dataset(root, spec, require_schema=True)
            if _comparable(post) != _comparable(final):
                raise SchemaMigrationError("promoted data differs from staged manifest")
        except Exception:
            if root.exists():
                root.replace(stage)
            if backup.exists():
                backup.replace(root)
            raise
        shutil.rmtree(backup)
        return {"mode": mode, "status": "MIGRATED", "pre": pre, "post": final}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
