from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_data.contracts.global_market import (
    FRED_TREASURY_YIELD_DAILY,
    US_TREASURY_SPREAD_DAILY,
)


DATASET = "us_treasury_spread_daily"
INPUT_DATASET = "fred_treasury_yield_daily"
DATASET_VERSION = 1
FORMULAS = {
    "spread_10y_2y": "dgs10 - dgs2",
    "spread_30y_2y": "dgs30 - dgs2",
}
SCHEMA = pa.schema(
    (
        pa.field("date", pa.date32(), nullable=False),
        pa.field("spread_10y_2y", pa.float64(), nullable=True),
        pa.field("spread_30y_2y", pa.float64(), nullable=True),
    ),
    metadata={
        b"dataset": DATASET.encode(),
        b"dataset_version": str(DATASET_VERSION).encode(),
        b"layer": b"derived",
        b"primary_key": b"date",
        b"partition_by": b"year",
    },
)
INPUT_SCHEMA = pa.schema(
    (
        pa.field("date", pa.date32()),
        pa.field("dgs2", pa.float64()),
        pa.field("dgs10", pa.float64()),
        pa.field("dgs30", pa.float64()),
    )
)


class TreasurySpreadBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Validation:
    rows: int
    coverage_start: str
    coverage_end: str
    primary_key_duplicates: int
    source_null_counts: dict[str, int]
    output_null_counts: dict[str, int]
    infinity_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_contracts() -> None:
    expected_input = ("date", "dgs2", "dgs10", "dgs30")
    expected_output = tuple(SCHEMA.names)
    if (
        FRED_TREASURY_YIELD_DAILY.name != INPUT_DATASET
        or FRED_TREASURY_YIELD_DAILY.primary_key != ("date",)
        or tuple(column.name for column in FRED_TREASURY_YIELD_DAILY.columns)
        != expected_input
    ):
        raise TreasurySpreadBuildError("input Dataset Contract changed")
    if (
        US_TREASURY_SPREAD_DAILY.name != DATASET
        or US_TREASURY_SPREAD_DAILY.version != DATASET_VERSION
        or US_TREASURY_SPREAD_DAILY.layer != "derived"
        or US_TREASURY_SPREAD_DAILY.primary_key != ("date",)
        or US_TREASURY_SPREAD_DAILY.partition_by != ("year",)
        or tuple(column.name for column in US_TREASURY_SPREAD_DAILY.columns)
        != expected_output
    ):
        raise TreasurySpreadBuildError("output Dataset Contract changed")


def _partition_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise TreasurySpreadBuildError(f"input dataset directory not found: {root}")
    files = sorted(root.glob("year=*/data.parquet"))
    all_parquet = sorted(root.rglob("*.parquet"))
    if not files or files != all_parquet:
        raise TreasurySpreadBuildError(
            "input must contain only year=<YYYY>/data.parquet partitions"
        )
    return files


def _read_source(root: Path) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    frames = []
    manifest = []
    for path in _partition_files(root):
        try:
            year = int(path.parent.name.removeprefix("year="))
        except ValueError as exc:
            raise TreasurySpreadBuildError(f"invalid year partition: {path}") from exc
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if not schema.remove_metadata().equals(INPUT_SCHEMA):
            raise TreasurySpreadBuildError(f"input physical schema changed: {path}")
        frame = parquet.read().to_pandas()
        dates = pd.to_datetime(frame["date"], errors="raise")
        if not dates.dt.year.eq(year).all():
            raise TreasurySpreadBuildError(f"input row outside year partition: {path}")
        frames.append(frame)
        manifest.append(
            {
                "path": path.relative_to(root).as_posix(),
                "rows": parquet.metadata.num_rows,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    source = pd.concat(frames, ignore_index=True)
    source["date"] = pd.to_datetime(source["date"], errors="raise").dt.date
    if source.empty or source["date"].isna().any():
        raise TreasurySpreadBuildError("input is empty or contains null dates")
    if source["date"].duplicated().any():
        raise TreasurySpreadBuildError("input primary key contains duplicates")
    if not source["date"].is_monotonic_increasing:
        raise TreasurySpreadBuildError("input dates are not globally sorted")
    for column in ("dgs2", "dgs10", "dgs30"):
        if source[column].dropna().map(lambda value: not math.isfinite(float(value))).any():
            raise TreasurySpreadBuildError(f"input {column} contains infinity")
    return source, manifest


def calculate_treasury_spreads(yields: pd.DataFrame) -> pd.DataFrame:
    required = ["date", "dgs2", "dgs10", "dgs30"]
    if list(yields.columns) != required or yields.empty:
        raise ValueError("invalid treasury yield source")
    dates = pd.to_datetime(yields["date"], errors="raise")
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("invalid treasury yield source dates")
    for column in required[1:]:
        numeric = pd.to_numeric(yields[column], errors="coerce")
        if not numeric.isna().equals(yields[column].isna()):
            raise ValueError(f"invalid treasury yield source column: {column}")
        if numeric.dropna().map(lambda value: not math.isfinite(float(value))).any():
            raise ValueError(f"non-finite treasury yield source column: {column}")
    result = pd.DataFrame({"date": dates.dt.date})
    result["spread_10y_2y"] = yields["dgs10"].sub(yields["dgs2"])
    result["spread_30y_2y"] = yields["dgs30"].sub(yields["dgs2"])
    return result


def validate_treasury_spreads(
    source: pd.DataFrame, result: pd.DataFrame
) -> Validation:
    if tuple(result.columns) != tuple(SCHEMA.names) or len(result) != len(source):
        raise TreasurySpreadBuildError("output schema or row count differs")
    duplicates = int(result["date"].duplicated(keep=False).sum())
    if duplicates or result["date"].isna().any() or not result["date"].is_monotonic_increasing:
        raise TreasurySpreadBuildError("output primary key is invalid")
    for output, (long_tenor, short_tenor) in {
        "spread_10y_2y": ("dgs10", "dgs2"),
        "spread_30y_2y": ("dgs30", "dgs2"),
    }.items():
        expected_null = source[long_tenor].isna() | source[short_tenor].isna()
        if not result[output].isna().equals(expected_null):
            raise TreasurySpreadBuildError(f"{output} null propagation differs")
        available = ~expected_null
        expected = source.loc[available, long_tenor].sub(
            source.loc[available, short_tenor]
        )
        if not result.loc[available, output].equals(expected):
            raise TreasurySpreadBuildError(f"{output} formula differs")
    infinity = sum(
        int(result[column].dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for column in SCHEMA.names[1:]
    )
    if infinity:
        raise TreasurySpreadBuildError("output contains infinity")
    dates = pd.to_datetime(result["date"], errors="raise")
    return Validation(
        rows=len(result),
        coverage_start=dates.min().strftime("%Y-%m-%d"),
        coverage_end=dates.max().strftime("%Y-%m-%d"),
        primary_key_duplicates=duplicates,
        source_null_counts={
            column: int(source[column].isna().sum())
            for column in ("dgs2", "dgs10", "dgs30")
        },
        output_null_counts={
            column: int(result[column].isna().sum()) for column in SCHEMA.names[1:]
        },
        infinity_count=infinity,
    )


def _table(frame: pd.DataFrame) -> pa.Table:
    table = pa.Table.from_pandas(
        frame[SCHEMA.names], schema=SCHEMA, preserve_index=False, safe=True
    )
    return table.replace_schema_metadata(SCHEMA.metadata)


def _write_stage(result: pd.DataFrame, output_root: Path) -> Path:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.stage-", dir=output_root.parent)
    )
    try:
        years = pd.to_datetime(result["date"]).dt.year
        for year in sorted(years.unique()):
            partition = result.loc[years.eq(year)].reset_index(drop=True)
            path = stage / f"year={year}" / "data.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(_table(partition), path, compression="zstd")
            restored = pq.ParquetFile(path).read()
            if not restored.schema.equals(SCHEMA, check_metadata=True):
                raise TreasurySpreadBuildError("staged output schema differs")
        restored = pd.concat(
            [pq.read_table(path).to_pandas() for path in sorted(stage.glob("year=*/data.parquet"))],
            ignore_index=True,
        )
        if not restored.equals(result):
            raise TreasurySpreadBuildError("staged output read-back differs")
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _stage_json(payload: dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False, newline="\n",
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise TreasurySpreadBuildError("state JSON read-back differs")
        return temporary
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _commit_output_and_state(
    *, stage: Path, output_root: Path, state_stage: Path, output_state_path: Path
) -> None:
    """Commit output and its completion state as one rollback-protected pair."""

    output_backup: Path | None = None
    state_backup: Path | None = None
    output_installed = False
    state_installed = False
    committed = False
    try:
        if output_root.exists():
            output_backup = output_root.parent / f".{output_root.name}.backup-{uuid4().hex}"
            output_root.replace(output_backup)
        if output_state_path.exists():
            state_backup = output_state_path.parent / (
                f".{output_state_path.name}.backup-{uuid4().hex}"
            )
            output_state_path.replace(state_backup)
        stage.replace(output_root)
        output_installed = True
        state_stage.replace(output_state_path)
        state_installed = True
        committed = True
    except Exception:
        if state_installed:
            output_state_path.unlink(missing_ok=True)
        if state_backup is not None and state_backup.exists():
            state_backup.replace(output_state_path)
        if output_installed:
            shutil.rmtree(output_root, ignore_errors=True)
        if output_backup is not None and output_backup.exists():
            output_backup.replace(output_root)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        state_stage.unlink(missing_ok=True)
        if committed and output_backup is not None:
            shutil.rmtree(output_backup, ignore_errors=True)
        if committed and state_backup is not None:
            state_backup.unlink(missing_ok=True)


def _output_manifest(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "rows": pq.ParquetFile(path).metadata.num_rows,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.glob("year=*/data.parquet"))
    ]


def build_treasury_spread_dataset(
    *,
    input_root: Path,
    input_state_path: Path,
    output_root: Path,
    output_state_path: Path,
) -> dict[str, object]:
    """Rebuild retained FRED term spreads offline with exact local lineage."""

    _assert_contracts()
    if not input_state_path.is_file():
        raise TreasurySpreadBuildError("input state is required for local lineage")
    try:
        input_state = json.loads(input_state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TreasurySpreadBuildError("input state is not valid JSON") from exc
    if not isinstance(input_state, dict):
        raise TreasurySpreadBuildError("input state must be a JSON object")
    if input_state.get("dataset") != INPUT_DATASET:
        raise TreasurySpreadBuildError("input state dataset identity differs")

    source, input_files = _read_source(input_root)
    result = calculate_treasury_spreads(source)
    validation = validate_treasury_spreads(source, result)
    stage = _write_stage(result, output_root)
    output_files = _output_manifest(stage)
    if sum(int(item["rows"]) for item in output_files) != validation.rows:
        shutil.rmtree(stage, ignore_errors=True)
        raise TreasurySpreadBuildError("committed output manifest row count differs")

    payload: dict[str, object] = {
        "task_id": "OFFLINE_TREASURY_SPREAD_REBUILD",
        "status": "artifact_complete_provenance_limited",
        "dataset": DATASET,
        "dataset_version": DATASET_VERSION,
        "layer": "derived",
        "api_calls": 0,
        "formulas": FORMULAS,
        "primary_key": ["date"],
        "sort_key": ["date"],
        "partition_by": ["year"],
        "validation": asdict(validation),
        "input": {
            "dataset": INPUT_DATASET,
            "state_path": input_state_path.name,
            "state_sha256": _sha256(input_state_path),
            "files": input_files,
        },
        "output_files": output_files,
        "provenance_limitations": [
            "The retained FRED input has no lossless Landing response or call ledger.",
            "This manifest proves exact local derivation from retained normalized files only.",
            "Source-series nulls are propagated and never filled.",
        ],
        "failed": {},
        "staged": [],
    }
    state_stage = _stage_json(payload, output_state_path)
    _commit_output_and_state(
        stage=stage,
        output_root=output_root,
        state_stage=state_stage,
        output_state_path=output_state_path,
    )
    return payload
