from __future__ import annotations

from dataclasses import asdict
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

from stock_data.contracts.legacy_kospi200 import KR_KOSPI200_OPTION_PCR_DAILY
from stock_data.derived.kospi200_option_pcr import (
    DATASET,
    DATASET_VERSION,
    LAYER,
    MARKET_SCOPE,
    OBSERVED,
    PARTITION_BY,
    PCR_SCHEMA,
    PRIMARY_KEY,
    SCOPE,
    SORT_KEY,
    VALID_EMPTY,
    KOSPI200OptionPCRError,
    PCRValidation,
    validate_pcr,
)


INPUT_DATASET = "kr_kospi200_options_daily"
SOURCE = "data_go_kr"
SOURCE_OPERATION = "GetDerivativeProductInfoService/getOptionsPriceInfo"
INPUT_PRIMARY_KEY = ("date", "contract")
INPUT_COLUMNS = (
    "date",
    "contract",
    "call_put",
    "volume",
    "open_interest",
    "source",
    "source_operation",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_contract_compatibility() -> None:
    contract = KR_KOSPI200_OPTION_PCR_DAILY
    dtype_names = {
        pa.date32(): "date32",
        pa.string(): "string",
        pa.int64(): "int64",
        pa.float64(): "float64",
    }
    expected_columns = tuple(
        (field.name, dtype_names[field.type], field.nullable) for field in PCR_SCHEMA
    )
    registered_columns = tuple(
        (column.name, column.dtype, column.nullable) for column in contract.columns
    )
    if (
        contract.name != DATASET
        or contract.version != DATASET_VERSION
        or contract.layer != LAYER
        or contract.storage_format != "parquet"
        or contract.primary_key != PRIMARY_KEY
        or contract.sort_key != SORT_KEY
        or contract.partition_by != PARTITION_BY
        or registered_columns != expected_columns
    ):
        raise KOSPI200OptionPCRError(
            "modern PCR output is incompatible with the registered C002 contract"
        )


def _parse_state_dates(values: object, *, field: str) -> set[date]:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise KOSPI200OptionPCRError(f"{field} must be a list of YYYYMMDD strings")
    if len(values) != len(set(values)):
        raise KOSPI200OptionPCRError(f"{field} contains duplicates")
    try:
        return set(pd.to_datetime(values, format="%Y%m%d", errors="raise").date)
    except (TypeError, ValueError) as error:
        raise KOSPI200OptionPCRError(f"{field} contains an invalid date") from error


def _sum_preserving_all_missing(values: pd.Series) -> int | None:
    available = values.dropna()
    return None if available.empty else int(available.sum())


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _read_and_aggregate(
    input_root: Path, *, lower: date, upper: date
) -> tuple[pd.DataFrame, int, list[dict[str, object]], set[date]]:
    paths = sorted(input_root.glob("year=*/data.parquet"))
    if not paths:
        raise FileNotFoundError(f"{INPUT_DATASET} partitions not found: {input_root}")

    rows: list[dict[str, object]] = []
    input_rows = 0
    input_files: list[dict[str, object]] = []
    seen_dates: set[date] = set()
    seen_keys: set[tuple[date, str]] = set()
    for path in paths:
        table = pq.ParquetFile(path).read(columns=list(INPUT_COLUMNS))
        frame = table.to_pandas()
        frame = frame.loc[
            pd.to_datetime(frame["date"]).dt.date.between(lower, upper)
        ].reset_index(drop=True)
        if frame.empty:
            continue
        input_rows += len(frame)
        input_files.append(
            {
                "path": str(path.resolve()),
                "rows": len(frame),
                "sha256": _sha256(path),
            }
        )
        if frame[list(INPUT_PRIMARY_KEY)].isna().any().any():
            raise KOSPI200OptionPCRError("modern option input primary key contains null")
        keys = set(frame[list(INPUT_PRIMARY_KEY)].itertuples(index=False, name=None))
        if len(keys) != len(frame) or seen_keys.intersection(keys):
            raise KOSPI200OptionPCRError("modern option input primary key duplicates")
        seen_keys.update(keys)
        if not frame["source"].eq(SOURCE).all():
            raise KOSPI200OptionPCRError("modern option source provenance differs")
        if not frame["source_operation"].eq(SOURCE_OPERATION).all():
            raise KOSPI200OptionPCRError("modern option source operation differs")
        unknown = ~frame["call_put"].isin(("CALL", "PUT"))
        if unknown.any():
            raise KOSPI200OptionPCRError(
                f"unverified modern option side: {frame.loc[unknown, 'call_put'].iloc[0]!r}"
            )
        for column in ("volume", "open_interest"):
            if frame[column].dropna().lt(0).any():
                raise KOSPI200OptionPCRError(f"negative modern source {column}")

        dates = set(frame["date"])
        if seen_dates.intersection(dates):
            raise KOSPI200OptionPCRError("modern option partitions overlap by date")
        seen_dates.update(dates)
        for observed_date, daily in frame.groupby("date", sort=True):
            calls = daily.loc[daily["call_put"].eq("CALL")]
            puts = daily.loc[daily["call_put"].eq("PUT")]
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
    return (
        pd.DataFrame(rows, columns=PCR_SCHEMA.names),
        input_rows,
        input_files,
        seen_dates,
    )


def _apply_checkpoint(
    observed: pd.DataFrame,
    *,
    physical_dates: set[date],
    input_state_path: Path,
    lower: date,
    upper: date,
) -> tuple[pd.DataFrame, int]:
    state = json.loads(input_state_path.read_text(encoding="utf-8"))
    if state.get("dataset") != INPUT_DATASET:
        raise KOSPI200OptionPCRError("modern checkpoint dataset differs")
    if state.get("failed_partitions"):
        raise KOSPI200OptionPCRError("modern checkpoint contains failures")
    if state.get("staged_partitions"):
        raise KOSPI200OptionPCRError("modern checkpoint contains staged partitions")
    completed = _parse_state_dates(
        state.get("completed_partitions"), field="completed_partitions"
    )
    empty = _parse_state_dates(
        state.get("valid_empty_partitions"), field="valid_empty_partitions"
    )
    completed = {value for value in completed if lower <= value <= upper}
    empty = {value for value in empty if lower <= value <= upper}
    if completed.intersection(empty):
        raise KOSPI200OptionPCRError("modern checkpoint terminal states overlap")
    if physical_dates != completed:
        raise KOSPI200OptionPCRError(
            "modern checkpoint differs from physical observations: "
            f"state_only={len(completed - physical_dates)}, "
            f"data_only={len(physical_dates - completed)}"
        )
    if set(observed["date"]).intersection(empty):
        raise KOSPI200OptionPCRError("modern checkpoint marks observed data valid-empty")

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


def validate_modern_pcr(frame: pd.DataFrame) -> PCRValidation:
    if tuple(frame.columns) != tuple(PCR_SCHEMA.names) or frame.empty:
        raise KOSPI200OptionPCRError("modern PCR schema or content is empty")
    duplicates = int(frame.duplicated(list(PRIMARY_KEY), keep=False).sum())
    if duplicates:
        raise KOSPI200OptionPCRError(f"modern PCR primary key duplicates={duplicates}")
    if frame[list(PRIMARY_KEY)].isna().any().any():
        raise KOSPI200OptionPCRError("modern PCR primary key contains null")
    ordered = list(frame[list(SORT_KEY)].itertuples(index=False, name=None))
    if ordered != sorted(ordered):
        raise KOSPI200OptionPCRError("modern PCR sort key is not monotonic")
    exact_values = {
        "scope": SCOPE,
        "market_scope": MARKET_SCOPE,
        "source": SOURCE,
        "source_operation": SOURCE_OPERATION,
        "input_dataset": INPUT_DATASET,
    }
    for column, value in exact_values.items():
        if not frame[column].eq(value).all():
            raise KOSPI200OptionPCRError(f"modern PCR {column} differs")
    if not frame["observation_status"].isin((OBSERVED, VALID_EMPTY)).all():
        raise KOSPI200OptionPCRError("modern PCR observation status differs")

    counts = ("call_rows", "put_rows", "unclassified_rows")
    aggregates = ("call_volume", "put_volume", "call_open_interest", "put_open_interest")
    for column in (*counts, *aggregates):
        if frame[column].dropna().lt(0).any():
            raise KOSPI200OptionPCRError(f"negative modern PCR field: {column}")
    if not frame["unclassified_rows"].eq(0).all():
        raise KOSPI200OptionPCRError("modern PCR contains unclassified rows")
    empty = frame.loc[frame["observation_status"].eq(VALID_EMPTY)]
    if not empty[[*counts, *aggregates]].eq(0).all().all():
        raise KOSPI200OptionPCRError("modern valid-empty row is not zero-valued")
    if not empty[["volume_pcr", "open_interest_pcr"]].isna().all().all():
        raise KOSPI200OptionPCRError("modern valid-empty ratio is not null")

    for ratio, numerator, denominator in (
        ("volume_pcr", "put_volume", "call_volume"),
        ("open_interest_pcr", "put_open_interest", "call_open_interest"),
    ):
        expected = frame.apply(lambda row: _ratio(row[numerator], row[denominator]), axis=1)
        actual = frame[ratio]
        matches = (actual.isna() & expected.isna()) | (
            actual.notna() & expected.notna() & actual.eq(expected)
        )
        if not matches.all():
            raise KOSPI200OptionPCRError(f"modern {ratio} differs from aggregates")
    numeric = frame[[*counts, *aggregates, "volume_pcr", "open_interest_pcr"]]
    infinity = sum(
        int(series.dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for _, series in numeric.items()
    )
    if infinity:
        raise KOSPI200OptionPCRError(f"modern PCR infinity count={infinity}")
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


def _table(frame: pd.DataFrame) -> pa.Table:
    table = pa.Table.from_pandas(
        frame[PCR_SCHEMA.names], schema=PCR_SCHEMA, preserve_index=False, safe=True
    )
    return table.replace_schema_metadata(PCR_SCHEMA.metadata)


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


def _manifest_entry(path: Path, *, output_path: Path | None = None) -> dict[str, object]:
    entry: dict[str, object] = {
        "path": str(path.resolve()),
        "rows": pq.ParquetFile(path).metadata.num_rows,
        "sha256": _sha256(path),
    }
    if output_path is not None:
        entry["output_path"] = str(output_path.resolve())
    return entry


def _validate_combined(
    prior: pd.DataFrame, modern: pd.DataFrame
) -> dict[str, object]:
    prior_validation = validate_pcr(prior)
    modern_validation = validate_modern_pcr(modern)
    prior_dates = pd.to_datetime(prior["date"])
    modern_dates = pd.to_datetime(modern["date"])
    prior_last = prior_dates.max()
    modern_first = modern_dates.min()
    prior_last_rows = prior.loc[prior_dates.eq(prior_last)]
    if (
        prior_last.strftime("%Y-%m-%d") != "2019-12-31"
        or len(prior_last_rows) != 1
        or prior_last_rows.iloc[0]["observation_status"] != VALID_EMPTY
        or modern_first.strftime("%Y-%m-%d") != "2020-01-02"
    ):
        raise KOSPI200OptionPCRError("exact 2019/2020 PCR boundary differs")
    if prior_last >= modern_first:
        raise KOSPI200OptionPCRError("prior and modern PCR segments overlap")
    combined = pd.concat([prior, modern], ignore_index=True)
    duplicates = int(combined.duplicated(list(PRIMARY_KEY), keep=False).sum())
    if duplicates:
        raise KOSPI200OptionPCRError(f"combined PCR primary key duplicates={duplicates}")
    ordered = list(combined[list(SORT_KEY)].itertuples(index=False, name=None))
    if ordered != sorted(ordered):
        raise KOSPI200OptionPCRError("combined PCR sort key is not monotonic")
    numeric = combined[
        [
            "call_volume",
            "put_volume",
            "volume_pcr",
            "call_open_interest",
            "put_open_interest",
            "open_interest_pcr",
            "call_rows",
            "put_rows",
            "unclassified_rows",
        ]
    ]
    infinity = sum(
        int(series.dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for _, series in numeric.items()
    )
    if infinity:
        raise KOSPI200OptionPCRError(f"combined PCR infinity count={infinity}")
    return {
        "rows": len(combined),
        "prior_rows": len(prior),
        "modern_rows": len(modern),
        "observed_rows": (
            prior_validation.observed_rows + modern_validation.observed_rows
        ),
        "valid_empty_rows": (
            prior_validation.valid_empty_rows + modern_validation.valid_empty_rows
        ),
        "coverage_start": prior_dates.min().strftime("%Y-%m-%d"),
        "coverage_end": modern_dates.max().strftime("%Y-%m-%d"),
        "primary_key_duplicates": duplicates,
        "null_counts": {
            name: int(combined[name].isna().sum()) for name in PCR_SCHEMA.names
        },
        "infinity_count": infinity,
        "boundary": {
            "prior_last_date": prior_last.strftime("%Y-%m-%d"),
            "prior_last_observation_status": VALID_EMPTY,
            "modern_first_date": modern_first.strftime("%Y-%m-%d"),
            "calendar_day_gap": int((modern_first - prior_last).days),
        },
    }


def _write_dataset_atomic(
    frame: pd.DataFrame, target: Path, *, prior_derived_root: Path
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    target.parent.mkdir(parents=True, exist_ok=True)
    prior_paths = sorted(prior_derived_root.glob("year=*/data.parquet"))
    if not prior_paths:
        raise FileNotFoundError(
            f"immutable prior PCR partitions not found: {prior_derived_root}"
        )
    prior_years = {path.parent.name for path in prior_paths}
    modern_years = {
        f"year={year}" for year in pd.to_datetime(frame["date"]).dt.year.unique()
    }
    if prior_years.intersection(modern_years):
        raise KOSPI200OptionPCRError("prior and modern PCR partition years overlap")
    prior_source_manifest = [
        _manifest_entry(path, output_path=target / path.parent.name / path.name)
        for path in prior_paths
    ]
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
    try:
        staged_prior_paths: list[Path] = []
        for source_path, source_entry in zip(prior_paths, prior_source_manifest):
            staged_path = stage / source_path.parent.name / source_path.name
            staged_path.parent.mkdir(parents=True)
            shutil.copyfile(source_path, staged_path)
            if _sha256(staged_path) != source_entry["sha256"]:
                raise KOSPI200OptionPCRError("staged prior PCR byte hash differs")
            staged_prior_paths.append(staged_path)

        years = pd.to_datetime(frame["date"]).dt.year
        staged_modern_paths: list[Path] = []
        for year in sorted(years.unique()):
            partition = frame.loc[years.eq(year)].reset_index(drop=True)
            path = stage / f"year={year}" / "data.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(_table(partition), path, compression="zstd")
            staged_modern_paths.append(path)
        all_paths = sorted([*staged_prior_paths, *staged_modern_paths])
        all_tables = [pq.ParquetFile(path).read() for path in all_paths]
        if any(
            not table.schema.equals(PCR_SCHEMA, check_metadata=True)
            for table in all_tables
        ):
            raise KOSPI200OptionPCRError("staged combined PCR schema differs")
        prior_restored = pa.concat_tables(
            [pq.ParquetFile(path).read() for path in staged_prior_paths]
        ).to_pandas()
        modern_restored = pa.concat_tables(
            [pq.ParquetFile(path).read() for path in staged_modern_paths]
        ).to_pandas()
        combined_validation = _validate_combined(prior_restored, modern_restored)
        if not _table(modern_restored).equals(_table(frame)):
            raise KOSPI200OptionPCRError("staged modern PCR values differ")
        _commit_directory_atomic(stage, target)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    preserved_prior_manifest: list[dict[str, object]] = []
    for source_entry in prior_source_manifest:
        output_path = Path(str(source_entry["output_path"]))
        output_hash = _sha256(output_path)
        if output_hash != source_entry["sha256"]:
            raise KOSPI200OptionPCRError("committed prior PCR byte hash differs")
        preserved_prior_manifest.append(
            {
                "source_path": source_entry["path"],
                "output_path": source_entry["output_path"],
                "rows": source_entry["rows"],
                "sha256": output_hash,
            }
        )
    modern_manifest = [
        _manifest_entry(target / f"year={year}" / "data.parquet")
        for year in sorted(years.unique())
    ]
    return preserved_prior_manifest, modern_manifest, combined_validation


def _write_json_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise KOSPI200OptionPCRError("modern state JSON read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_modern_kospi200_option_pcr(
    *,
    input_root: Path,
    input_state_path: Path,
    output_root: Path,
    output_state_path: Path,
    prior_derived_root: Path,
    start: str = "20200101",
    end: str | None = None,
) -> dict:
    """Build 2020+ C002-compatible PCR solely from existing normalized artifacts."""

    _assert_contract_compatibility()
    if len(start) != 8 or not start.isdigit():
        raise ValueError("start must be YYYYMMDD")
    if end is not None and (len(end) != 8 or not end.isdigit()):
        raise ValueError("end must be YYYYMMDD")
    state_payload = json.loads(input_state_path.read_text(encoding="utf-8"))
    terminal = [
        *state_payload.get("completed_partitions", []),
        *state_payload.get("valid_empty_partitions", []),
    ]
    if not terminal:
        raise KOSPI200OptionPCRError("modern checkpoint has no terminal partitions")
    effective_end = end or max(terminal)
    if start > effective_end:
        raise ValueError("start must not be after end")
    lower = pd.to_datetime(start, format="%Y%m%d", errors="raise").date()
    upper = pd.to_datetime(effective_end, format="%Y%m%d", errors="raise").date()
    observed, input_rows, input_files, physical_dates = _read_and_aggregate(
        input_root, lower=lower, upper=upper
    )
    result, valid_empty_rows = _apply_checkpoint(
        observed,
        physical_dates=physical_dates,
        input_state_path=input_state_path,
        lower=lower,
        upper=upper,
    )
    validation = validate_modern_pcr(result)
    preserved_prior_files, modern_output_files, combined_validation = (
        _write_dataset_atomic(
            result, output_root, prior_derived_root=prior_derived_root
        )
    )
    payload = {
        "task_id": "C006",
        "status": "complete",
        "dataset": DATASET,
        "dataset_version": DATASET_VERSION,
        "layer": LAYER,
        "schema": str(PCR_SCHEMA),
        "primary_key": list(PRIMARY_KEY),
        "sort_key": list(SORT_KEY),
        "partition_by": list(PARTITION_BY),
        "range": {"start": start, "end": effective_end},
        "api_calls": 0,
        "input_dataset": INPUT_DATASET,
        "input_rows": input_rows,
        "input_files": input_files,
        "input_state": {
            "path": str(input_state_path.resolve()),
            "sha256": _sha256(input_state_path),
        },
        "source": SOURCE,
        "source_operation": SOURCE_OPERATION,
        "valid_empty_rows": valid_empty_rows,
        "boundary": combined_validation["boundary"],
        "validation": asdict(validation),
        "preserved_prior_files": preserved_prior_files,
        "modern_output_files": modern_output_files,
        "combined_validation": combined_validation,
        "failed": {},
        "staged": [],
    }
    _write_json_atomic(payload, output_state_path)
    return payload
