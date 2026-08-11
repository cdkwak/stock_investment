from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


START_DATE = "20100101"
END_DATE = "20191231"
SOURCE = "legacy_stock_investment"
SOURCE_FILE_ROW_NO = "SOURCE_FILE_ROW_NO"

FUTURES_SOURCE_COLUMNS = (
    "BAS_DD", "PROD_NM", "MKT_NM", "ISU_CD", "ISU_NM", "TDD_CLSPRC",
    "CMPPREVDD_PRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "SPOT_PRC",
    "SETL_PRC", "ACC_TRDVOL", "ACC_TRDVAL", "ACC_OPNINT_QTY",
)
OPTIONS_SOURCE_COLUMNS = (
    "BAS_DD", "PROD_NM", "RGHT_TP_NM", "ISU_CD", "ISU_NM", "TDD_CLSPRC",
    "CMPPREVDD_PRC", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "IMP_VOLT",
    "NXTDD_BAS_PRC", "ACC_TRDVOL", "ACC_TRDVAL", "ACC_OPNINT_QTY",
    "SOURCE_ROW_NO",
)


@dataclass(frozen=True)
class LegacyDerivativeSpec:
    kind: str
    dataset: str
    product_name: str
    source_relative_path: str
    source_operation: str
    source_columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    sort_key: tuple[str, ...]
    normalized_schema: pa.Schema


def _field(name: str, dtype: pa.DataType, *, nullable: bool = False) -> pa.Field:
    return pa.field(name, dtype, nullable=nullable)


COMMON_NORMALIZED_FIELDS = (
    _field("date", pa.date32()),
    _field("product_name", pa.string()),
)
COMMON_PRICES = (
    _field("close", pa.float64(), nullable=True),
    _field("change", pa.float64(), nullable=True),
    _field("open", pa.float64(), nullable=True),
    _field("high", pa.float64(), nullable=True),
    _field("low", pa.float64(), nullable=True),
)
COMMON_ACTIVITY = (
    _field("volume", pa.int64(), nullable=True),
    _field("trading_value", pa.int64(), nullable=True),
    _field("open_interest", pa.int64(), nullable=True),
    _field("source", pa.string()),
    _field("source_operation", pa.string()),
)

FUTURES_NORMALIZED_SCHEMA = pa.schema(
    COMMON_NORMALIZED_FIELDS
    + (
        _field("market_name", pa.string()),
        _field("contract", pa.string()),
        _field("name", pa.string()),
    )
    + COMMON_PRICES
    + (
        _field("spot_price", pa.float64(), nullable=True),
        _field("settlement_price", pa.float64(), nullable=True),
    )
    + COMMON_ACTIVITY
    + (_field("source_file_row_no", pa.int64()),)
)
OPTIONS_NORMALIZED_SCHEMA = pa.schema(
    COMMON_NORMALIZED_FIELDS
    + (
        _field("right_type", pa.string()),
        _field("contract", pa.string()),
        _field("name", pa.string()),
    )
    + COMMON_PRICES
    + (
        _field("implied_volatility", pa.float64(), nullable=True),
        _field("next_day_base_price", pa.float64(), nullable=True),
    )
    + COMMON_ACTIVITY
    + (
        _field("source_file_row_no", pa.int64()),
        _field("source_row_no", pa.int64()),
    )
)

FUTURES_SPEC = LegacyDerivativeSpec(
    kind="futures",
    dataset="krx_legacy_kospi200_futures_daily",
    product_name="코스피200 선물",
    source_relative_path="data/raw/kr/krx/derivatives/futures/fut_bydd_trd_all.csv",
    source_operation="krx_fut_bydd_trd",
    source_columns=FUTURES_SOURCE_COLUMNS,
    primary_key=("date", "market_name", "contract"),
    sort_key=("date", "source_file_row_no"),
    normalized_schema=FUTURES_NORMALIZED_SCHEMA,
)
OPTIONS_SPEC = LegacyDerivativeSpec(
    kind="options",
    dataset="krx_legacy_kospi200_options_daily",
    product_name="코스피200 옵션",
    source_relative_path="data/raw/kr/krx/derivatives/options/opt_bydd_trd_all.csv",
    source_operation="krx_opt_bydd_trd",
    source_columns=OPTIONS_SOURCE_COLUMNS,
    primary_key=("date", "source_row_no"),
    sort_key=("date", "source_file_row_no"),
    normalized_schema=OPTIONS_NORMALIZED_SCHEMA,
)
SPECS = (FUTURES_SPEC, OPTIONS_SPEC)


@dataclass(frozen=True)
class DatasetMigrationResult:
    dataset: str
    layer: str
    source: str
    source_operation: str
    primary_key: tuple[str, ...]
    sort_key: tuple[str, ...]
    coverage_start: str
    coverage_end: str
    rows: int
    dates: int
    primary_key_duplicates: int
    secondary_key: tuple[str, ...]
    secondary_key_duplicate_rows: int
    secondary_key_duplicate_groups: int
    null_counts: dict[str, int]
    infinity_count: int
    ohlc_violations: int


class LegacyDerivativeMigrationError(RuntimeError):
    pass


def _landing_schema(spec: LegacyDerivativeSpec) -> pa.Schema:
    return pa.schema(
        [_field(name, pa.string()) for name in spec.source_columns]
        + [_field(SOURCE_FILE_ROW_NO, pa.int64())]
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_numeric(series: pd.Series, *, integer: bool, column: str) -> pd.Series:
    text = series.astype("string").str.replace(",", "", regex=False)
    missing = text.eq("")
    parsed = pd.to_numeric(text.mask(missing), errors="coerce")
    invalid = ~missing & parsed.isna()
    if invalid.any():
        sample = series.loc[invalid].iloc[0]
        raise LegacyDerivativeMigrationError(
            f"{column} contains an invalid numeric source value: {sample!r}"
        )
    finite = parsed.dropna().map(math.isfinite)
    if not finite.all():
        raise LegacyDerivativeMigrationError(f"{column} contains a non-finite value")
    if integer:
        fractional = parsed.dropna().mod(1).ne(0)
        if fractional.any():
            raise LegacyDerivativeMigrationError(
                f"{column} contains a non-integral source value"
            )
        return parsed.astype("Int64")
    return parsed.astype("Float64")


def _normalize_chunk(frame: pd.DataFrame, spec: LegacyDerivativeSpec) -> pd.DataFrame:
    expected_columns = (*spec.source_columns, SOURCE_FILE_ROW_NO)
    if tuple(frame.columns) != expected_columns:
        raise LegacyDerivativeMigrationError(f"{spec.kind} source schema changed")
    if frame.empty:
        return pd.DataFrame(columns=spec.normalized_schema.names)
    if not frame["PROD_NM"].eq(spec.product_name).all():
        raise LegacyDerivativeMigrationError("cross-product row reached normalization")
    if frame[["BAS_DD", "ISU_CD", "ISU_NM"]].eq("").any().any():
        raise LegacyDerivativeMigrationError("required source identity is empty")

    try:
        dates = pd.to_datetime(frame["BAS_DD"], format="%Y%m%d", errors="raise").dt.date
    except (TypeError, ValueError) as error:
        raise LegacyDerivativeMigrationError("BAS_DD is not YYYYMMDD") from error

    result = pd.DataFrame({
        "date": dates,
        "product_name": frame["PROD_NM"].astype("string"),
    })
    if spec.kind == "futures":
        result["market_name"] = frame["MKT_NM"].astype("string")
    else:
        result["right_type"] = frame["RGHT_TP_NM"].astype("string")
        unknown = ~result["right_type"].isin(("CALL", "PUT"))
        if unknown.any():
            raise LegacyDerivativeMigrationError(
                f"unverified option right type: {result.loc[unknown, 'right_type'].iloc[0]!r}"
            )
    result["contract"] = frame["ISU_CD"].astype("string")
    result["name"] = frame["ISU_NM"].astype("string")

    numeric = {
        "close": ("TDD_CLSPRC", False),
        "change": ("CMPPREVDD_PRC", False),
        "open": ("TDD_OPNPRC", False),
        "high": ("TDD_HGPRC", False),
        "low": ("TDD_LWPRC", False),
        "volume": ("ACC_TRDVOL", True),
        "trading_value": ("ACC_TRDVAL", True),
        "open_interest": ("ACC_OPNINT_QTY", True),
    }
    if spec.kind == "futures":
        numeric.update({
            "spot_price": ("SPOT_PRC", False),
            "settlement_price": ("SETL_PRC", False),
        })
    else:
        numeric.update({
            "implied_volatility": ("IMP_VOLT", False),
            "next_day_base_price": ("NXTDD_BAS_PRC", False),
        })
    for target, (source, integer) in numeric.items():
        result[target] = _parse_numeric(frame[source], integer=integer, column=source)

    result["source"] = SOURCE
    result["source_operation"] = spec.source_operation
    file_rows = _parse_numeric(
        frame[SOURCE_FILE_ROW_NO], integer=True, column=SOURCE_FILE_ROW_NO
    )
    if file_rows.isna().any() or (file_rows < 0).any():
        raise LegacyDerivativeMigrationError(
            "SOURCE_FILE_ROW_NO must be a nonnegative integer"
        )
    result["source_file_row_no"] = file_rows
    if spec.kind == "options":
        source_rows = _parse_numeric(
            frame["SOURCE_ROW_NO"], integer=True, column="SOURCE_ROW_NO"
        )
        if source_rows.isna().any() or (source_rows < 0).any():
            raise LegacyDerivativeMigrationError("SOURCE_ROW_NO must be a nonnegative integer")
        result["source_row_no"] = source_rows

    return result[spec.normalized_schema.names]


def _ohlc_violations(frame: pd.DataFrame) -> int:
    values = frame[["open", "high", "low", "close"]]
    # KRX retains source zeroes for inactive contracts.  A non-trading row can
    # therefore have zero OHLC fields beside a non-zero theoretical close.
    # Apply bar inequalities only when the source reports traded volume.
    traded = frame["volume"].fillna(0).gt(0)
    invalid = traded & values["high"].notna() & values["low"].notna() & (
        values["high"] < values["low"]
    )
    for column in ("open", "close"):
        comparable = traded & values[["high", "low", column]].notna().all(axis=1)
        invalid |= comparable & (
            (values[column] > values["high"]) | (values[column] < values["low"])
        )
    return int(invalid.sum())


def _stage_root(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))


def _cleanup_roots(paths: Iterable[Path]) -> None:
    for path in paths:
        if path.exists():
            shutil.rmtree(path)


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
        restored = json.loads(temporary.read_text(encoding="utf-8"))
        canonical = json.loads(json.dumps(payload, ensure_ascii=False))
        if restored != canonical:
            raise LegacyDerivativeMigrationError("state JSON read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _partition_path(root: Path, year: int) -> Path:
    path = root / f"year={year}" / "data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_spec(
    *,
    source_path: Path,
    landing_stage: Path,
    normalized_stage: Path,
    spec: LegacyDerivativeSpec,
    start: str,
    end: str,
    chunksize: int,
) -> None:
    landing_writers: dict[int, pq.ParquetWriter] = {}
    normalized_writers: dict[int, pq.ParquetWriter] = {}
    try:
        reader = pd.read_csv(
            source_path,
            dtype={name: "string" for name in spec.source_columns},
            keep_default_na=False,
            na_filter=False,
            encoding="utf-8-sig",
            chunksize=chunksize,
        )
        for source in reader:
            if tuple(source.columns) != spec.source_columns:
                raise LegacyDerivativeMigrationError(f"{spec.kind} source schema changed")
            selected = source.loc[
                source["BAS_DD"].between(start, end)
                & source["PROD_NM"].eq(spec.product_name)
            ].copy()
            if selected.empty:
                continue
            selected[SOURCE_FILE_ROW_NO] = selected.index.astype("int64")
            normalized = _normalize_chunk(selected, spec)
            years = selected["BAS_DD"].str[:4].astype(int)
            for year in sorted(years.unique()):
                positions = years.eq(year)
                raw_year = selected.loc[
                    positions, [*spec.source_columns, SOURCE_FILE_ROW_NO]
                ].reset_index(drop=True)
                normalized_year = normalized.loc[positions].reset_index(drop=True)
                if year not in landing_writers:
                    landing_writers[year] = pq.ParquetWriter(
                        _partition_path(landing_stage, year),
                        _landing_schema(spec), compression="zstd",
                    )
                    normalized_writers[year] = pq.ParquetWriter(
                        _partition_path(normalized_stage, year),
                        spec.normalized_schema, compression="zstd",
                    )
                landing_writers[year].write_table(pa.Table.from_pandas(
                    raw_year, schema=_landing_schema(spec), preserve_index=False, safe=True
                ))
                normalized_writers[year].write_table(pa.Table.from_pandas(
                    normalized_year, schema=spec.normalized_schema,
                    preserve_index=False, safe=True,
                ))
    finally:
        for writer in (*landing_writers.values(), *normalized_writers.values()):
            writer.close()


def _validate_spec(
    *, landing_root: Path, normalized_root: Path, spec: LegacyDerivativeSpec,
) -> DatasetMigrationResult:
    landing_paths = sorted(landing_root.glob("year=*/data.parquet"))
    normalized_paths = sorted(normalized_root.glob("year=*/data.parquet"))
    if not landing_paths or [p.parent.name for p in landing_paths] != [
        p.parent.name for p in normalized_paths
    ]:
        raise LegacyDerivativeMigrationError(f"{spec.dataset} partitions are incomplete")

    rows = duplicates = semantic_rows = semantic_groups = infinity = ohlc = 0
    dates: set[str] = set()
    minimum: str | None = None
    maximum: str | None = None
    null_counts = {
        field.name: 0 for field in spec.normalized_schema if field.nullable
    }
    previous_key: tuple | None = None
    for landing_path, normalized_path in zip(landing_paths, normalized_paths):
        landing_table = pq.read_table(landing_path)
        normalized_table = pq.read_table(normalized_path)
        if not landing_table.schema.equals(_landing_schema(spec), check_metadata=False):
            raise LegacyDerivativeMigrationError(f"{spec.dataset} landing schema differs")
        if not normalized_table.schema.equals(spec.normalized_schema, check_metadata=False):
            raise LegacyDerivativeMigrationError(f"{spec.dataset} normalized schema differs")
        landing = landing_table.to_pandas()
        normalized = normalized_table.to_pandas()
        expected = pa.Table.from_pandas(
            _normalize_chunk(landing, spec), schema=spec.normalized_schema,
            preserve_index=False, safe=True,
        )
        if not expected.equals(normalized_table):
            raise LegacyDerivativeMigrationError(
                f"{spec.dataset} normalized values differ from landing"
            )
        if len(landing) != len(normalized):
            raise LegacyDerivativeMigrationError(f"{spec.dataset} layer row counts differ")

        keys = normalized[list(spec.primary_key)]
        duplicates += int(keys.duplicated(keep=False).sum())
        ordered = list(
            normalized[list(spec.sort_key)].itertuples(index=False, name=None)
        )
        if ordered != sorted(ordered):
            raise LegacyDerivativeMigrationError(f"{spec.dataset} sort key is not monotonic")
        if previous_key is not None and ordered and ordered[0] <= previous_key:
            raise LegacyDerivativeMigrationError(f"{spec.dataset} partitions overlap")
        if ordered:
            previous_key = ordered[-1]

        semantic = normalized[["date", "contract"]]
        grouped = semantic.value_counts()
        semantic_groups += int(grouped.gt(1).sum())
        semantic_rows += int(grouped.loc[grouped.gt(1)].sum())
        for column in null_counts:
            null_counts[column] += int(normalized[column].isna().sum())
        numeric = normalized.select_dtypes(include=["number"])
        infinity += int(np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan)).sum())
        ohlc += _ohlc_violations(normalized)
        date_values = pd.to_datetime(normalized["date"]).dt.strftime("%Y-%m-%d")
        dates.update(date_values)
        part_min, part_max = date_values.min(), date_values.max()
        minimum = part_min if minimum is None else min(minimum, part_min)
        maximum = part_max if maximum is None else max(maximum, part_max)
        rows += len(normalized)

    if duplicates or infinity or ohlc:
        raise LegacyDerivativeMigrationError(
            f"{spec.dataset} failed integrity validation: "
            f"pk_duplicates={duplicates}, infinity={infinity}, ohlc={ohlc}"
        )
    assert minimum is not None and maximum is not None
    return DatasetMigrationResult(
        dataset=spec.dataset,
        layer="normalized",
        source=SOURCE,
        source_operation=spec.source_operation,
        primary_key=spec.primary_key,
        sort_key=spec.sort_key,
        coverage_start=minimum,
        coverage_end=maximum,
        rows=rows,
        dates=len(dates),
        primary_key_duplicates=duplicates,
        secondary_key=("date", "contract"),
        secondary_key_duplicate_rows=semantic_rows,
        secondary_key_duplicate_groups=semantic_groups,
        null_counts=null_counts,
        infinity_count=infinity,
        ohlc_violations=ohlc,
    )


def run_legacy_derivatives_migration(
    *, project_root: Path, legacy_root: Path,
    start: str = START_DATE, end: str = END_DATE, chunksize: int = 100_000,
) -> dict:
    """Migrate verified 2010-2019 KOSPI200 rows without calling an API.

    The legacy repository is treated strictly as a read-only source snapshot.
    Scoped landing Parquet retains every source cell as a string. Normalized
    Parquet is derived only from that landing representation, and option source
    duplicates are preserved via the source-provided SOURCE_ROW_NO key.
    """

    if len(start) != 8 or len(end) != 8 or not start.isdigit() or not end.isdigit():
        raise ValueError("start and end must be YYYYMMDD")
    if start > end or start < START_DATE or end > END_DATE:
        raise ValueError("migration range must be within 20100101..20191231")
    if chunksize < 1:
        raise ValueError("chunksize must be positive")

    targets: dict[Path, Path] = {}
    source_details: dict[str, dict] = {}
    staged_by_spec: dict[str, tuple[Path, Path]] = {}
    try:
        for spec in SPECS:
            source_path = legacy_root / spec.source_relative_path
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            before = source_path.stat()
            digest = _sha256(source_path)
            landing_target = (
                project_root / "data/landing/legacy_stock_investment"
                / "krx_derivatives_2010_2019" / spec.kind
            )
            normalized_target = project_root / "data/normalized" / spec.dataset
            landing_stage = _stage_root(landing_target)
            normalized_stage = _stage_root(normalized_target)
            targets[landing_target] = landing_stage
            targets[normalized_target] = normalized_stage
            staged_by_spec[spec.kind] = (landing_stage, normalized_stage)
            _write_spec(
                source_path=source_path, landing_stage=landing_stage,
                normalized_stage=normalized_stage, spec=spec, start=start,
                end=end, chunksize=chunksize,
            )
            after = source_path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise LegacyDerivativeMigrationError(
                    f"legacy source changed during read: {source_path}"
                )
            source_details[spec.kind] = {
                "path": str(source_path.resolve()),
                "size_bytes": before.st_size,
                "mtime_ns": before.st_mtime_ns,
                "sha256": digest,
                "source_schema": list(spec.source_columns),
                "landing_primary_key": (
                    ["BAS_DD", "MKT_NM", "ISU_CD"] if spec.kind == "futures"
                    else ["BAS_DD", "SOURCE_ROW_NO"]
                ),
                "derived_provenance_column": {
                    "name": SOURCE_FILE_ROW_NO,
                    "meaning": "zero-based data-row offset in the immutable source CSV",
                },
            }

        results = []
        for spec in SPECS:
            landing_stage, normalized_stage = staged_by_spec[spec.kind]
            results.append(_validate_spec(
                landing_root=landing_stage, normalized_root=normalized_stage, spec=spec
            ))
        _commit_directories_atomic(targets)
    except Exception:
        _cleanup_roots(targets.values())
        raise

    payload = {
        "task_id": "C001",
        "status": "complete",
        "range": {"start": start, "end": end},
        "api_calls": 0,
        "source_repository": str(legacy_root.resolve()),
        "sources": source_details,
        "datasets": [asdict(result) for result in results],
        "failed": {},
        "staged": [],
    }
    _write_json_atomic(
        payload,
        project_root / "data/state/legacy_kospi200_derivatives_2010_2019.json",
    )
    return payload
