from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_data.contracts.legacy_market_investor import (
    A001_START_DATE,
    C004_END_DATE,
    C004_START_DATE,
    KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,
)
from stock_data.validation.legacy_market_investor import (
    NUMERIC_COLUMNS,
    validate_legacy_market_investor_net_purchase,
)


START_DATE = C004_START_DATE
END_DATE = C004_END_DATE
EXPECTED_ROWS = 3_834
EXPECTED_SOURCE_SHA256 = "d7bc0414e7e12e51030e8c625fc62fa5537a8d84f890915667bf108c58ca1c75"
SOURCE_RELATIVE_PATH = Path("data/raw/kr/pykrx/investor_flow/kospi_investor_flow_daily.csv")
SOURCE_COLUMNS = (
    "date", "symbol", "institution_net_buy", "other_corporation_net_buy",
    "individual_net_buy", "foreign_net_buy", "total_net_buy",
)
LANDING_SCHEMA = pa.schema([
    pa.field(name, pa.string(), nullable=False) for name in SOURCE_COLUMNS
] + [pa.field("source_file_row_no", pa.int64(), nullable=False)])
NORMALIZED_SCHEMA = pa.schema([
    pa.field("date", pa.date32(), nullable=False),
    pa.field("market", pa.string(), nullable=False),
    *[pa.field(name, pa.int64(), nullable=False) for name in NUMERIC_COLUMNS],
    pa.field("source", pa.string(), nullable=False),
    pa.field("source_operation", pa.string(), nullable=False),
    pa.field("provider_boundary", pa.string(), nullable=False),
])


class LegacyMarketInvestorImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportValidation:
    dataset: str
    rows: int
    dates: int
    coverage_start: str
    coverage_end: str
    primary_key_duplicates: int
    null_counts: dict[str, int]
    nan_count: int
    infinity_count: int
    category_sum_mismatches: int
    valid_zero_rows: int
    negative_value_rows: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_root(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))


def _commit_directories_atomic(staged: dict[Path, Path]) -> None:
    backups: dict[Path, Path | None] = {}
    committed: list[Path] = []
    try:
        for target in staged:
            if target.exists():
                backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
                target.replace(backup)
                backups[target] = backup
            else:
                backups[target] = None
        for target, temporary in staged.items():
            temporary.replace(target)
            committed.append(target)
    except Exception:
        for target in reversed(committed):
            if target.exists():
                shutil.rmtree(target)
        for target, backup in backups.items():
            if backup is not None and backup.exists():
                backup.replace(target)
        raise
    finally:
        for temporary in staged.values():
            if temporary.exists():
                shutil.rmtree(temporary)
        for backup in backups.values():
            if backup is not None and backup.exists():
                shutil.rmtree(backup)


def _write_json_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise LegacyMarketInvestorImportError("state JSON read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_partitioned(frame: pd.DataFrame, root: Path, schema: pa.Schema) -> None:
    working = frame.copy()
    years = pd.to_datetime(working["date"], errors="raise").dt.year
    for year, partition in working.groupby(years, sort=True):
        path = root / f"year={int(year)}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        partition = partition.copy()
        if schema.field("date").type == pa.date32():
            partition["date"] = pd.to_datetime(partition["date"], errors="raise").dt.date
        pq.write_table(pa.Table.from_pandas(partition, schema=schema, preserve_index=False), path)


def _load_source(source_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = pd.read_csv(source_path, dtype="string", keep_default_na=False)
    if tuple(source.columns) != SOURCE_COLUMNS:
        raise LegacyMarketInvestorImportError("legacy source schema changed")
    source["source_file_row_no"] = pd.Series(range(len(source)), dtype="int64")
    parsed_dates = pd.to_datetime(source["date"], format="%Y-%m-%d", errors="coerce")
    if parsed_dates.isna().any():
        raise LegacyMarketInvestorImportError("legacy source has invalid dates")
    in_scope = parsed_dates.between(START_DATE, END_DATE)
    landing = source.loc[in_scope].copy()
    if not landing["symbol"].eq("KOSPI").all():
        raise LegacyMarketInvestorImportError("scoped legacy source is not KOSPI")
    normalized = pd.DataFrame({
        "date": parsed_dates.loc[in_scope].dt.strftime("%Y-%m-%d"),
        "market": "KOSPI",
        "source": "legacy_stock_investment_pykrx_1.2.8",
        "source_operation": "MDCSTAT02202",
        "provider_boundary": "legacy_pre_a001_only",
    })
    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(landing[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all() or values.mod(1).ne(0).any():
            raise LegacyMarketInvestorImportError(f"invalid integer source value in {column}")
        normalized[column] = values.astype("int64")
    landing = landing.loc[:, [*SOURCE_COLUMNS, "source_file_row_no"]]
    normalized = normalized.loc[:, KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names]
    normalized = normalized.sort_values(["date", "market"], kind="stable").reset_index(drop=True)
    return landing, normalized


def _validate_outputs(landing_root: Path, normalized_root: Path) -> ImportValidation:
    landing_paths = sorted(landing_root.rglob("data.parquet"))
    normalized_paths = sorted(normalized_root.rglob("data.parquet"))
    if not landing_paths or not normalized_paths:
        raise LegacyMarketInvestorImportError("staged Parquet partitions are missing")
    landing = pd.concat([pd.read_parquet(path) for path in landing_paths], ignore_index=True)
    normalized = pd.concat([pd.read_parquet(path) for path in normalized_paths], ignore_index=True)
    if tuple(landing.columns) != tuple(LANDING_SCHEMA.names):
        raise LegacyMarketInvestorImportError("landing schema parity failure")
    if tuple(normalized.columns) != KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names:
        raise LegacyMarketInvestorImportError("normalized schema parity failure")
    normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.strftime("%Y-%m-%d")
    normalized = normalized.sort_values(["date", "market"], kind="stable").reset_index(drop=True)
    validate_legacy_market_investor_net_purchase(normalized)
    null_counts = {column: int(normalized[column].isna().sum()) for column in normalized.columns}
    numeric = normalized[list(NUMERIC_COLUMNS)].apply(pd.to_numeric, errors="raise")
    nan_count = int(numeric.isna().sum().sum())
    infinity_count = int(np.isinf(numeric.to_numpy(dtype="float64")).sum())
    category_sum_mismatches = int(numeric[list(NUMERIC_COLUMNS[:-1])].sum(axis=1).ne(numeric["total_net_buy"]).sum())
    duplicate_count = int(normalized.duplicated(["date", "market"]).sum())
    dates = normalized["date"]
    result = ImportValidation(
        dataset=KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.name,
        rows=len(normalized), dates=dates.nunique(), coverage_start=dates.min(), coverage_end=dates.max(),
        primary_key_duplicates=duplicate_count, null_counts=null_counts, nan_count=nan_count,
        infinity_count=infinity_count, category_sum_mismatches=category_sum_mismatches,
        valid_zero_rows=int(numeric.eq(0).any(axis=1).sum()),
        negative_value_rows=int(numeric.lt(0).any(axis=1).sum()),
    )
    if result.rows != EXPECTED_ROWS or result.coverage_start != START_DATE or result.coverage_end != END_DATE:
        raise LegacyMarketInvestorImportError(f"unexpected C004 coverage: {asdict(result)}")
    if any((result.primary_key_duplicates, result.nan_count, result.infinity_count, result.category_sum_mismatches)):
        raise LegacyMarketInvestorImportError(f"C004 integrity failure: {asdict(result)}")
    return result


def run_legacy_market_investor_import(*, project_root: Path, legacy_root: Path) -> dict:
    """Import only the fixed 1999-01-04..2014-06-30 legacy KOSPI slice offline."""
    source_path = legacy_root / SOURCE_RELATIVE_PATH
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    before = source_path.stat()
    source_sha256 = _sha256(source_path)
    if source_sha256 != EXPECTED_SOURCE_SHA256:
        raise LegacyMarketInvestorImportError("legacy source checksum does not match C004 contract")
    landing, normalized = _load_source(source_path)
    after = source_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise LegacyMarketInvestorImportError("legacy source changed during read")

    landing_target = project_root / "data/landing/legacy_stock_investment/kr_market_investor_net_purchase_daily"
    normalized_target = project_root / "data/normalized/kr_market_investor_net_purchase_daily"
    staged = {landing_target: _stage_root(landing_target), normalized_target: _stage_root(normalized_target)}
    try:
        _write_partitioned(landing, staged[landing_target], LANDING_SCHEMA)
        _write_partitioned(normalized, staged[normalized_target], NORMALIZED_SCHEMA)
        validation = _validate_outputs(staged[landing_target], staged[normalized_target])
        _commit_directories_atomic(staged)
    except Exception:
        for path in staged.values():
            if path.exists():
                shutil.rmtree(path)
        raise

    payload = {
        "task_id": "C004", "status": "complete", "api_calls": 0,
        "imported_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "repository": str(legacy_root.resolve()), "path": str(source_path.resolve()),
            "relative_path": str(SOURCE_RELATIVE_PATH).replace("\\", "/"),
            "sha256": source_sha256, "size_bytes": before.st_size,
            "provider": "KRX via PyKRX 1.2.8", "operation": "MDCSTAT02202",
            "monetary_unit": "unit_unknown",
        },
        "scope": {
            "start": START_DATE, "end": END_DATE, "expected_rows": EXPECTED_ROWS,
            "provider_boundary": "legacy_pre_a001_only", "a001_starts": A001_START_DATE,
            "overlap_dates_permitted": False,
            "rationale": "Legacy values are imported separately; they are not concatenated with or substituted for Toss A001.",
        },
        "landing": {"path": str(landing_target.relative_to(project_root)), "schema": LANDING_SCHEMA.names},
        "normalized": {"path": str(normalized_target.relative_to(project_root)), "schema": NORMALIZED_SCHEMA.names},
        "validation": asdict(validation),
    }
    _write_json_atomic(payload, project_root / "data/state/legacy_market_investor_import.json")
    return payload
