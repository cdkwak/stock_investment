from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
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


DATASET = "kr_kospi200_option_pcr_daily"
DATASET_VERSION = 1
LAYER = "derived"
PRIMARY_KEY = ("date", "scope", "market_scope")
SORT_KEY = PRIMARY_KEY
PARTITION_BY = ("year",)
SCOPE = "krx_openapi_kospi200_option_total"
MARKET_SCOPE = "unspecified_by_source"
OBSERVED = "observed"
VALID_EMPTY = "valid_empty"
SOURCE = "legacy_stock_investment"
SOURCE_OPERATION = "krx_opt_bydd_trd"
INPUT_DATASET = "krx_legacy_kospi200_options_daily"


def _field(name: str, dtype: pa.DataType, *, nullable: bool = False) -> pa.Field:
    return pa.field(name, dtype, nullable=nullable)


# Version 1 intentionally excludes trading-value PCR. Aggregate activity is
# nullable so a source day containing only missing values is not rewritten as
# a valid zero. Audited source-empty dates are represented by explicit zeroes.
PCR_SCHEMA = pa.schema(
    (
        _field("date", pa.date32()),
        _field("scope", pa.string()),
        _field("market_scope", pa.string()),
        _field("observation_status", pa.string()),
        _field("call_volume", pa.int64(), nullable=True),
        _field("put_volume", pa.int64(), nullable=True),
        _field("volume_pcr", pa.float64(), nullable=True),
        _field("call_open_interest", pa.int64(), nullable=True),
        _field("put_open_interest", pa.int64(), nullable=True),
        _field("open_interest_pcr", pa.float64(), nullable=True),
        _field("call_rows", pa.int64()),
        _field("put_rows", pa.int64()),
        _field("unclassified_rows", pa.int64()),
        _field("source", pa.string()),
        _field("source_operation", pa.string()),
        _field("input_dataset", pa.string()),
    ),
    metadata={
        b"dataset": DATASET.encode(),
        b"dataset_version": str(DATASET_VERSION).encode(),
        b"layer": LAYER.encode(),
        b"primary_key": ",".join(PRIMARY_KEY).encode(),
        b"partition_by": ",".join(PARTITION_BY).encode(),
    },
)


INPUT_COLUMNS = (
    "date",
    "right_type",
    "volume",
    "open_interest",
    "source",
    "source_operation",
)
PARITY_INTEGER_COLUMNS = (
    "call_volume",
    "put_volume",
    "call_open_interest",
    "put_open_interest",
    "call_rows",
    "put_rows",
    "unclassified_rows",
)
PARITY_RATIO_COLUMNS = ("volume_pcr", "open_interest_pcr")
LEGACY_RATIO_ABSOLUTE_TOLERANCE = 1e-15


class KOSPI200OptionPCRError(RuntimeError):
    pass


@dataclass(frozen=True)
class PCRValidation:
    rows: int
    observed_rows: int
    valid_empty_rows: int
    coverage_start: str
    coverage_end: str
    primary_key_duplicates: int
    null_counts: dict[str, int]
    infinity_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sum_preserving_all_missing(values: pd.Series) -> int | None:
    available = values.dropna()
    return None if available.empty else int(available.sum())


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _aggregate_observed(options_root: Path) -> tuple[pd.DataFrame, int, list[str]]:
    paths = sorted(options_root.glob("year=*/data.parquet"))
    if not paths:
        raise FileNotFoundError(
            f"{INPUT_DATASET} Parquet partitions not found: {options_root}"
        )

    rows: list[dict] = []
    input_rows = 0
    input_partitions: list[str] = []
    seen_dates: set[date] = set()
    for path in paths:
        table = pq.ParquetFile(path).read(columns=list(INPUT_COLUMNS))
        missing = set(INPUT_COLUMNS).difference(table.column_names)
        if missing:
            raise KOSPI200OptionPCRError(
                f"{INPUT_DATASET} is missing columns: {sorted(missing)}"
            )
        frame = table.to_pandas()
        input_rows += len(frame)
        input_partitions.append(path.parent.name)
        if frame.empty:
            continue
        if not frame["source"].eq(SOURCE).all():
            raise KOSPI200OptionPCRError("C001 source provenance differs")
        if not frame["source_operation"].eq(SOURCE_OPERATION).all():
            raise KOSPI200OptionPCRError("C001 source operation differs")
        unknown = ~frame["right_type"].isin(("CALL", "PUT"))
        if unknown.any():
            value = frame.loc[unknown, "right_type"].iloc[0]
            raise KOSPI200OptionPCRError(f"unverified option side: {value!r}")
        for column in ("volume", "open_interest"):
            if frame[column].dropna().lt(0).any():
                raise KOSPI200OptionPCRError(f"negative source {column}")

        dates = set(frame["date"])
        overlap = seen_dates.intersection(dates)
        if overlap:
            raise KOSPI200OptionPCRError(
                f"option partitions overlap on date {min(overlap)}"
            )
        seen_dates.update(dates)
        for observed_date, daily in frame.groupby("date", sort=True):
            calls = daily.loc[daily["right_type"].eq("CALL")]
            puts = daily.loc[daily["right_type"].eq("PUT")]
            call_volume = _sum_preserving_all_missing(calls["volume"])
            put_volume = _sum_preserving_all_missing(puts["volume"])
            call_oi = _sum_preserving_all_missing(calls["open_interest"])
            put_oi = _sum_preserving_all_missing(puts["open_interest"])
            rows.append(
                {
                    "date": observed_date,
                    "scope": SCOPE,
                    "market_scope": MARKET_SCOPE,
                    "observation_status": OBSERVED,
                    "call_volume": call_volume,
                    "put_volume": put_volume,
                    "volume_pcr": _ratio(put_volume, call_volume),
                    "call_open_interest": call_oi,
                    "put_open_interest": put_oi,
                    "open_interest_pcr": _ratio(put_oi, call_oi),
                    "call_rows": len(calls),
                    "put_rows": len(puts),
                    "unclassified_rows": 0,
                    "source": SOURCE,
                    "source_operation": SOURCE_OPERATION,
                    "input_dataset": INPUT_DATASET,
                }
            )
    return pd.DataFrame(rows, columns=PCR_SCHEMA.names), input_rows, input_partitions


def _parse_state_dates(values: object, *, field: str) -> set[date]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise KOSPI200OptionPCRError(f"{field} must be a list of YYYYMMDD strings")
    if len(values) != len(set(values)):
        raise KOSPI200OptionPCRError(f"{field} contains duplicates")
    try:
        return set(pd.to_datetime(values, format="%Y%m%d", errors="raise").date)
    except (TypeError, ValueError) as error:
        raise KOSPI200OptionPCRError(f"{field} contains an invalid date") from error


def _apply_audited_calendar(
    observed: pd.DataFrame,
    *,
    calendar_state_path: Path,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, int]:
    state = json.loads(calendar_state_path.read_text(encoding="utf-8"))
    if state.get("failed_dates"):
        raise KOSPI200OptionPCRError("legacy calendar state contains failed dates")
    completed = _parse_state_dates(
        state.get("completed_options_dates"), field="completed_options_dates"
    )
    empty = _parse_state_dates(
        state.get("empty_options_dates"), field="empty_options_dates"
    )
    lower = pd.to_datetime(start, format="%Y%m%d", errors="raise").date()
    upper = pd.to_datetime(end, format="%Y%m%d", errors="raise").date()
    completed = {value for value in completed if lower <= value <= upper}
    empty = {value for value in empty if lower <= value <= upper}
    observed_dates = set(observed["date"])
    if observed_dates.intersection(empty):
        raise KOSPI200OptionPCRError("calendar marks an observed date as empty")
    if not empty.issubset(completed):
        raise KOSPI200OptionPCRError("empty option dates are not completed")
    if observed_dates.union(empty) != completed:
        missing = completed.difference(observed_dates, empty)
        extra = observed_dates.difference(completed)
        raise KOSPI200OptionPCRError(
            "audited calendar differs from C001 observations: "
            f"unclassified_completed={len(missing)}, uncompleted_observed={len(extra)}"
        )

    empty_rows = pd.DataFrame(
        [
            {
                "date": value,
                "scope": SCOPE,
                "market_scope": MARKET_SCOPE,
                "observation_status": VALID_EMPTY,
                "call_volume": 0,
                "put_volume": 0,
                "volume_pcr": None,
                "call_open_interest": 0,
                "put_open_interest": 0,
                "open_interest_pcr": None,
                "call_rows": 0,
                "put_rows": 0,
                "unclassified_rows": 0,
                "source": SOURCE,
                "source_operation": SOURCE_OPERATION,
                "input_dataset": INPUT_DATASET,
            }
            for value in sorted(empty)
        ],
        columns=PCR_SCHEMA.names,
    )
    result = pd.DataFrame(
        [*observed.to_dict("records"), *empty_rows.to_dict("records")],
        columns=PCR_SCHEMA.names,
    )
    result = result.sort_values(list(SORT_KEY), kind="stable").reset_index(drop=True)
    return result, len(empty)


def _table(frame: pd.DataFrame) -> pa.Table:
    table = pa.Table.from_pandas(
        frame[PCR_SCHEMA.names], schema=PCR_SCHEMA, preserve_index=False, safe=True
    )
    return table.replace_schema_metadata(PCR_SCHEMA.metadata)


def validate_pcr(frame: pd.DataFrame) -> PCRValidation:
    if tuple(frame.columns) != tuple(PCR_SCHEMA.names) or frame.empty:
        raise KOSPI200OptionPCRError("PCR schema or content is empty")
    duplicates = int(frame.duplicated(list(PRIMARY_KEY), keep=False).sum())
    if duplicates:
        raise KOSPI200OptionPCRError(f"PCR primary key duplicates={duplicates}")
    ordered = list(frame[list(SORT_KEY)].itertuples(index=False, name=None))
    if ordered != sorted(ordered):
        raise KOSPI200OptionPCRError("PCR sort key is not monotonic")
    if not frame["scope"].eq(SCOPE).all() or not frame["market_scope"].eq(
        MARKET_SCOPE
    ).all():
        raise KOSPI200OptionPCRError("PCR scope differs")
    if not frame["source"].eq(SOURCE).all() or not frame["source_operation"].eq(
        SOURCE_OPERATION
    ).all():
        raise KOSPI200OptionPCRError("PCR source provenance differs")
    if not frame["input_dataset"].eq(INPUT_DATASET).all():
        raise KOSPI200OptionPCRError("PCR input dataset differs")
    if not frame["observation_status"].isin((OBSERVED, VALID_EMPTY)).all():
        raise KOSPI200OptionPCRError("PCR observation status differs")

    count_columns = ("call_rows", "put_rows", "unclassified_rows")
    aggregate_columns = (
        "call_volume",
        "put_volume",
        "call_open_interest",
        "put_open_interest",
    )
    for column in (*count_columns, *aggregate_columns):
        if frame[column].dropna().lt(0).any():
            raise KOSPI200OptionPCRError(f"negative PCR field: {column}")
    empty = frame.loc[frame["observation_status"].eq(VALID_EMPTY)]
    if not empty[[*count_columns, *aggregate_columns]].eq(0).all().all():
        raise KOSPI200OptionPCRError("valid-empty PCR row is not zero-valued")
    if not empty[[*PARITY_RATIO_COLUMNS]].isna().all().all():
        raise KOSPI200OptionPCRError("valid-empty PCR ratio is not null")

    for prefix, numerator, denominator in (
        ("volume_pcr", "put_volume", "call_volume"),
        ("open_interest_pcr", "put_open_interest", "call_open_interest"),
    ):
        expected = frame.apply(
            lambda row: _ratio(row[numerator], row[denominator]), axis=1
        )
        actual = frame[prefix]
        mismatch = ~(
            (actual.isna() & expected.isna())
            | (actual.notna() & expected.notna() & actual.eq(expected))
        )
        if mismatch.any():
            raise KOSPI200OptionPCRError(f"{prefix} differs from source aggregates")

    numeric = frame[
        [*aggregate_columns, *count_columns, *PARITY_RATIO_COLUMNS]
    ].apply(pd.to_numeric)
    infinity = sum(
        int(series.dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for _, series in numeric.items()
    )
    if infinity:
        raise KOSPI200OptionPCRError(f"PCR infinity count={infinity}")
    dates = pd.to_datetime(frame["date"], errors="raise")
    return PCRValidation(
        rows=len(frame),
        observed_rows=int(frame["observation_status"].eq(OBSERVED).sum()),
        valid_empty_rows=int(frame["observation_status"].eq(VALID_EMPTY).sum()),
        coverage_start=dates.min().strftime("%Y-%m-%d"),
        coverage_end=dates.max().strftime("%Y-%m-%d"),
        primary_key_duplicates=duplicates,
        null_counts={name: int(frame[name].isna().sum()) for name in PCR_SCHEMA.names},
        infinity_count=infinity,
    )


def compare_legacy_pcr(frame: pd.DataFrame, legacy_pcr_path: Path) -> dict:
    legacy = pd.read_csv(legacy_pcr_path, dtype={"date": "string"})
    required = {
        "date",
        "scope",
        "market_scope",
        "source",
        *PARITY_INTEGER_COLUMNS,
        *PARITY_RATIO_COLUMNS,
    }
    missing = required.difference(legacy.columns)
    if missing:
        raise KOSPI200OptionPCRError(
            f"legacy PCR is missing columns: {sorted(missing)}"
        )
    observed = frame.loc[frame["observation_status"].eq(OBSERVED)].copy()
    observed["date"] = pd.to_datetime(observed["date"]).dt.strftime("%Y%m%d")
    legacy = legacy.loc[legacy["date"].isin(observed["date"])].copy()
    if len(legacy) != len(observed) or legacy["date"].duplicated().any():
        raise KOSPI200OptionPCRError("legacy PCR observed-date coverage differs")
    joined = observed.merge(
        legacy,
        on=["date", "scope", "market_scope"],
        how="outer",
        validate="one_to_one",
        suffixes=("_actual", "_legacy"),
        indicator=True,
    )
    if not joined["_merge"].eq("both").all():
        raise KOSPI200OptionPCRError("legacy PCR scope/key differs")
    if not joined["source_operation"].eq(joined["source_legacy"]).all():
        raise KOSPI200OptionPCRError("legacy PCR source operation differs")

    integer_mismatches = 0
    for column in PARITY_INTEGER_COLUMNS:
        actual = pd.to_numeric(joined[f"{column}_actual"], errors="raise")
        expected = pd.to_numeric(joined[f"{column}_legacy"], errors="raise")
        equal = actual.eq(expected) | (actual.isna() & expected.isna())
        integer_mismatches += int((~equal).sum())
    ratio_mismatches = 0
    representation_differences = 0
    maximum_difference = {column: 0.0 for column in PARITY_RATIO_COLUMNS}
    for column in PARITY_RATIO_COLUMNS:
        actual = pd.to_numeric(joined[f"{column}_actual"], errors="coerce")
        expected = pd.to_numeric(joined[f"{column}_legacy"], errors="coerce")
        both = actual.notna() & expected.notna()
        difference = (actual.loc[both] - expected.loc[both]).abs()
        maximum_difference[column] = float(difference.max()) if not difference.empty else 0.0
        ratio_mismatches += int((actual.isna() ^ expected.isna()).sum())
        ratio_mismatches += int(
            difference.gt(LEGACY_RATIO_ABSOLUTE_TOLERANCE).sum()
        )
        representation_differences += int(difference.ne(0).sum())
    if integer_mismatches or ratio_mismatches:
        raise KOSPI200OptionPCRError(
            "legacy PCR parity differs: "
            f"integer={integer_mismatches}, ratio={ratio_mismatches}, "
            f"max_abs={maximum_difference}"
        )
    return {
        "observed_rows_compared": len(observed),
        "integer_mismatches": integer_mismatches,
        "ratio_mismatches": ratio_mismatches,
        "ratio_absolute_tolerance": LEGACY_RATIO_ABSOLUTE_TOLERANCE,
        "last_bit_representation_differences": representation_differences,
        "maximum_absolute_ratio_difference": maximum_difference,
    }


def _commit_directory_atomic(stage: Path, target: Path) -> None:
    backup: Path | None = None
    try:
        if target.exists():
            backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
            target.replace(backup)
        stage.replace(target)
    except Exception:
        if target.exists() and backup is not None:
            shutil.rmtree(target)
        if backup is not None and backup.exists():
            backup.replace(target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)


def _write_dataset_atomic(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
    try:
        years = pd.to_datetime(frame["date"]).dt.year
        for year in sorted(years.unique()):
            partition = frame.loc[years.eq(year)].reset_index(drop=True)
            path = stage / f"year={year}" / "data.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(_table(partition), path, compression="zstd")
        restored_tables = [
            pq.ParquetFile(path).read()
            for path in sorted(stage.glob("year=*/data.parquet"))
        ]
        if any(
            not table.schema.equals(PCR_SCHEMA, check_metadata=True)
            for table in restored_tables
        ):
            raise KOSPI200OptionPCRError(
                "staged PCR schema differs: "
                f"expected={PCR_SCHEMA}, actual={restored_tables[0].schema}"
            )
        restored = pa.concat_tables(restored_tables).to_pandas()
        validate_pcr(restored)
        if not _table(restored).equals(_table(frame)):
            raise KOSPI200OptionPCRError("staged PCR values differ")
        _commit_directory_atomic(stage, target)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _write_json_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".json.tmp",
            prefix=path.stem + "_",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise KOSPI200OptionPCRError("state JSON read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_legacy_kospi200_option_pcr(
    *,
    project_root: Path,
    legacy_calendar_state_path: Path,
    legacy_pcr_path: Path,
    start: str = "20100101",
    end: str = "20191231",
) -> dict:
    """Build C002 from C001 normalized options without API calls or deduplication."""

    if len(start) != 8 or len(end) != 8 or not start.isdigit() or not end.isdigit():
        raise ValueError("start and end must be YYYYMMDD")
    if start > end:
        raise ValueError("start must not be after end")
    options_root = project_root / "data/normalized" / INPUT_DATASET
    observed, input_rows, input_partitions = _aggregate_observed(options_root)
    observed_dates = pd.to_datetime(observed["date"])
    in_range = observed_dates.between(
        pd.to_datetime(start, format="%Y%m%d"),
        pd.to_datetime(end, format="%Y%m%d"),
    )
    observed = observed.loc[in_range].reset_index(drop=True)
    result, valid_empty_rows = _apply_audited_calendar(
        observed,
        calendar_state_path=legacy_calendar_state_path,
        start=start,
        end=end,
    )
    validation = validate_pcr(result)
    parity = compare_legacy_pcr(result, legacy_pcr_path)

    target = project_root / "data/derived" / DATASET
    _write_dataset_atomic(result, target)
    c001_state_path = (
        project_root / "data/state/legacy_kospi200_derivatives_2010_2019.json"
    )
    payload = {
        "task_id": "C002",
        "status": "complete",
        "dataset": DATASET,
        "dataset_version": DATASET_VERSION,
        "layer": LAYER,
        "schema": str(PCR_SCHEMA),
        "primary_key": list(PRIMARY_KEY),
        "sort_key": list(SORT_KEY),
        "partition_by": list(PARTITION_BY),
        "range": {"start": start, "end": end},
        "api_calls": 0,
        "input_dataset": INPUT_DATASET,
        "input_rows": input_rows,
        "input_partitions": input_partitions,
        "source": SOURCE,
        "source_operation": SOURCE_OPERATION,
        "source_provenance": "KRX OpenAPI legacy snapshot migrated by C001",
        "calendar_state": {
            "path": str(legacy_calendar_state_path.resolve()),
            "sha256": _sha256(legacy_calendar_state_path),
            "valid_empty_rows": valid_empty_rows,
        },
        "c001_state": {
            "path": str(c001_state_path.resolve()),
            "sha256": _sha256(c001_state_path),
        },
        "legacy_parity_source": {
            "path": str(legacy_pcr_path.resolve()),
            "sha256": _sha256(legacy_pcr_path),
        },
        "validation": asdict(validation),
        "parity": parity,
        "failed": {},
        "staged": [],
    }
    _write_json_atomic(
        payload,
        project_root / "data/state/legacy_kospi200_option_pcr_2010_2019.json",
    )
    return payload
