"""Deterministic, read-only terminal audit for A007 short-selling artifacts.

No function in this module authenticates, imports pykrx, or performs network
I/O. Landing bodies are reparsed with the production parsers. Normalized data
is compared exactly in sorted market/year streams, bounding memory to one
Parquet batch plus one source response per merge input.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Iterable, Iterator, Mapping
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from stock_data.contracts.kr_short_selling import SHORT_SELLING_CONTRACTS
from stock_data.pipelines.short_selling_backfill import (
    AUTH_PATHS,
    MINIMUM_SOURCE_DATES,
    _scope_sha256,
    load_canonical_trading_dates,
    plan_scopes,
)
from stock_data.providers.pykrx.short_selling import (
    BUSINESS_URL,
    PARSERS,
    RequestScope,
    balance_scope,
    investor_scope,
    trading_scope,
)


REPORT_SCHEMA = "stock_data.a007_short_selling_audit"
REPORT_VERSION = 2
DATASETS = ("trading", "balance", "investor")
_BATCH_SIZE = 65_536
_MISSING = object()


@dataclass(frozen=True)
class DatasetAuditPlan:
    dataset: str
    start: date
    end: date
    trading_dates: tuple[date, ...]
    expected_scope_ids: tuple[str, ...]
    acceptable_terminal_statuses: tuple[str, ...] = ("BATCH_COMPLETE",)


@dataclass(frozen=True)
class _Artifact:
    scope: RequestScope
    body: Path
    source_rows: int
    normalized_rows: int
    classification: str
    partition_keys: tuple[tuple[str, int], ...]


@dataclass
class _LedgerIndex:
    files: int
    records: int
    invalid_records: int
    responses: dict[tuple[str, int], list[dict[str, object]]]
    correlations: dict[tuple[str, int], list[dict[str, object]]]
    completions: dict[tuple[str, str], list[dict[str, object]]]
    status_counts: Counter[str]
    auth_responses: int
    errors: int
    retry_events: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _finding(findings: list[dict[str, str]], code: str, detail: object) -> None:
    findings.append({"code": code, "detail": str(detail)})


def _scope_from_id(dataset: str, scope_id: str) -> RequestScope:
    if dataset in {"trading", "balance"}:
        match = re.fullmatch(r"(\d{8})_(KOSPI|KOSDAQ)", scope_id)
        if not match:
            raise ValueError("invalid daily scope id")
        day, market = match.groups()
        return trading_scope(day, market) if dataset == "trading" else balance_scope(day, market)
    match = re.fullmatch(
        r"(\d{8})_(\d{8})_(KOSPI|KOSDAQ)_(volume|trading_value)", scope_id
    )
    if not match:
        raise ValueError("invalid investor scope id")
    return investor_scope(*match.groups())


def canonical_plan(
    project_root: Path,
    dataset: str,
    *,
    start: date | None = None,
    end: date | None = None,
    acceptable_terminal_statuses: tuple[str, ...] = ("BATCH_COMPLETE",),
) -> DatasetAuditPlan:
    """Build the default terminal plan from the canonical PIT calendar."""
    if dataset not in DATASETS:
        raise ValueError(f"unsupported dataset: {dataset}")
    canonical_root = project_root.resolve() / "data/normalized/kr_equity_universe_daily"
    start = start or MINIMUM_SOURCE_DATES[dataset]
    if end is None:
        candidates = sorted(canonical_root.glob("market=*/year=*/data.parquet"))
        if not candidates:
            raise FileNotFoundError("canonical universe has no Parquet partitions")
        maximum = None
        for path in candidates:
            parquet = pq.ParquetFile(path)
            index = parquet.schema_arrow.get_field_index("date")
            if index < 0:
                raise ValueError(f"canonical partition has no date: {path}")
            for group in range(parquet.metadata.num_row_groups):
                statistics = parquet.metadata.row_group(group).column(index).statistics
                value = statistics.max if statistics and statistics.has_min_max else None
                if value is not None:
                    maximum = value if maximum is None else max(maximum, value)
        if maximum is None:
            values = []
            for path in candidates:
                for batch in pq.ParquetFile(path).iter_batches(columns=["date"], batch_size=_BATCH_SIZE):
                    if len(batch) - batch.column(0).null_count:
                        values.append(pc.max(pc.drop_null(batch.column(0))).as_py())
            if not values:
                raise ValueError("canonical universe has no non-null date")
            maximum = max(values)
        end = maximum if isinstance(maximum, date) else date.fromisoformat(str(maximum))
    dates = load_canonical_trading_dates(canonical_root, start=start, end=end)
    scopes = plan_scopes(dataset, dates)
    if not acceptable_terminal_statuses:
        raise ValueError("at least one acceptable terminal status is required")
    return DatasetAuditPlan(
        dataset=dataset, start=start, end=end, trading_dates=dates,
        expected_scope_ids=tuple(scope.scope_id for scope in scopes),
        acceptable_terminal_statuses=acceptable_terminal_statuses,
    )


def _read_ledger_records(path: Path) -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    invalid = 0
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError
                    records.append(value)
                except (json.JSONDecodeError, ValueError):
                    invalid += 1
    except (OSError, UnicodeError):
        invalid += 1
    return records, invalid


def _load_ledgers(runs_root: Path) -> _LedgerIndex:
    paths = sorted(runs_root.glob("*/call_ledger.jsonl")) if runs_root.is_dir() else []
    responses: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    correlations: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    completions: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    status_counts: Counter[str] = Counter()
    all_records: list[tuple[str, dict[str, object]]] = []
    invalid = auth = errors = retries = 0
    for path in paths:
        expected_run = path.parent.name
        records, bad = _read_ledger_records(path)
        invalid += bad
        for record in records:
            all_records.append((expected_run, record))
            if record.get("run_id") != expected_run:
                invalid += 1
            event = str(record.get("event", ""))
            if "RETRY" in event.upper():
                retries += 1
            if event == "HTTP_ERROR":
                errors += 1
            if event == "HTTP_RESPONSE":
                status_counts[str(record.get("status_code"))] += 1
                authentication = record.get("authentication")
                url = record.get("url")
                if type(authentication) is not bool or not isinstance(url, str):
                    invalid += 1
                elif authentication is True:
                    if urlsplit(url).path not in AUTH_PATHS:
                        invalid += 1
                    else:
                        auth += 1
                elif url == BUSINESS_URL:
                    if type(record.get("raw_sequence")) is int:
                        responses[(expected_run, record["raw_sequence"])].append(record)
                    else:
                        invalid += 1
                else:
                    invalid += 1
            elif event == "SCOPE_HTTP_CORRELATED":
                if type(record.get("raw_sequence")) is int:
                    correlations[(expected_run, record["raw_sequence"])].append(record)
                else:
                    invalid += 1

    run_datasets: dict[str, set[str]] = defaultdict(set)
    for run_id, record in all_records:
        dataset = record.get("dataset")
        if record.get("event") in {"SCOPE_STARTED", "RUN_COMPLETED"} and dataset in DATASETS:
            run_datasets[run_id].add(str(dataset))
    for run_id, record in all_records:
        if record.get("event") != "SCOPE_COMPLETED" or not isinstance(record.get("scope"), str):
            continue
        datasets = run_datasets.get(run_id, set())
        if len(datasets) == 1:
            completions[(next(iter(datasets)), record["scope"])].append(record)
        else:
            invalid += 1
    return _LedgerIndex(
        files=len(paths), records=len(all_records), invalid_records=invalid,
        responses=dict(responses), correlations=dict(correlations),
        completions=dict(completions), status_counts=status_counts,
        auth_responses=auth, errors=errors, retry_events=retries,
    )


def _classify_correlation(record: Mapping[str, object]) -> str | None:
    scope_id = record.get("scope")
    scope_hash = record.get("scope_sha256")
    if not isinstance(scope_id, str) or not isinstance(scope_hash, str):
        return None
    for dataset in DATASETS:
        try:
            if _scope_sha256(_scope_from_id(dataset, scope_id)) == scope_hash:
                return dataset
        except ValueError:
            continue
    return None


def _ledger_integrity(ledger: _LedgerIndex) -> tuple[dict[str, object], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    response_keys = set(ledger.responses)
    correlation_keys = set(ledger.correlations)
    duplicate_responses = sum(max(0, len(values) - 1) for values in ledger.responses.values())
    duplicate_correlations = sum(max(0, len(values) - 1) for values in ledger.correlations.values())
    orphan_responses = response_keys - correlation_keys
    orphan_correlations = correlation_keys - response_keys
    unknown = sum(
        _classify_correlation(record) is None
        for records in ledger.correlations.values() for record in records
    )
    scope_counts: Counter[tuple[str, str, str]] = Counter()
    for records in ledger.correlations.values():
        for record in records:
            dataset = _classify_correlation(record)
            if dataset:
                scope_counts[(dataset, str(record.get("scope")), str(record.get("scope_sha256")))] += 1
    duplicate_scopes = sum(max(0, count - 1) for count in scope_counts.values())
    invalid_business_metadata = sum(
        record.get("authentication") is not False or record.get("url") != BUSINESS_URL
        for records in ledger.responses.values() for record in records
    )
    for code, count in (
        ("LEDGER_INVALID_RECORD", ledger.invalid_records),
        ("DUPLICATE_BUSINESS_HTTP_RESPONSE", duplicate_responses),
        ("DUPLICATE_SCOPE_HTTP_CORRELATION", duplicate_correlations),
        ("ORPHAN_BUSINESS_HTTP_RESPONSE", len(orphan_responses)),
        ("ORPHAN_SCOPE_HTTP_CORRELATION", len(orphan_correlations)),
        ("UNKNOWN_SCOPE_HTTP_CORRELATION", unknown),
        ("DUPLICATE_SCOPE_BUSINESS_CALL", duplicate_scopes),
        ("BUSINESS_HTTP_METADATA_INVALID", invalid_business_metadata),
    ):
        if count:
            _finding(findings, code, count)
    summary = {
        "status": "PASS" if not findings else "FAIL",
        "files": ledger.files, "records": ledger.records,
        "raw_http_responses": sum(ledger.status_counts.values()),
        "authentication_responses": ledger.auth_responses,
        "unique_business_responses": len(response_keys),
        "http_status_counts": dict(sorted(ledger.status_counts.items())),
        "http_errors": ledger.errors, "retry_events": ledger.retry_events,
        "duplicate_business_responses": duplicate_responses,
        "duplicate_correlations": duplicate_correlations,
        "orphan_business_responses": len(orphan_responses),
        "orphan_correlations": len(orphan_correlations),
        "duplicate_scope_calls": duplicate_scopes,
        "unknown_correlations": unknown,
        "invalid_business_metadata": invalid_business_metadata,
    }
    return summary, findings


def _arrow_dtype(value: pa.DataType) -> str:
    if pa.types.is_string(value) or pa.types.is_large_string(value):
        return "string"
    if pa.types.is_int64(value):
        return "int64"
    if pa.types.is_float64(value):
        return "float64"
    if pa.types.is_date32(value):
        return "date32"
    return str(value)


def _parquet_audit(
    root: Path, dataset: str, checkpoint_rows: int, findings: list[dict[str, str]],
) -> tuple[dict[str, object], dict[tuple[str, int], Path], set[str]]:
    contract = SHORT_SELLING_CONTRACTS[dataset]
    files = sorted(root.glob("market=*/year=*/data.parquet")) if root.is_dir() else []
    total_rows = duplicate_rows = null_key_rows = infinity_count = nan_count = 0
    null_counts: Counter[str] = Counter()
    dates_seen: set[str] = set()
    first = last = None
    schemas: set[tuple[tuple[str, str], ...]] = set()
    partitions: dict[tuple[str, int], Path] = {}
    expected_schema = tuple((column.name, column.dtype) for column in contract.columns)
    for path in files:
        match = re.fullmatch(
            r"market=(KOSPI|KOSDAQ)[\\/]year=(\d{4})[\\/]data\.parquet",
            str(path.relative_to(root)),
        )
        if not match:
            _finding(findings, "PARTITION_PATH_INVALID", path)
            continue
        expected_market, year_text = match.groups()
        expected_year = int(year_text)
        key = (expected_market, expected_year)
        if key in partitions:
            _finding(findings, "PARTITION_DUPLICATED", key)
        partitions[key] = path
        parquet = pq.ParquetFile(path)
        total_rows += parquet.metadata.num_rows
        schema = parquet.schema_arrow
        signature = tuple((field.name, _arrow_dtype(field.type)) for field in schema)
        schemas.add(signature)
        if signature != expected_schema:
            _finding(findings, "PARQUET_SCHEMA_MISMATCH", _relative(path, root))
            continue
        columns = list(contract.column_names)
        pk_indexes = [columns.index(name) for name in contract.primary_key]
        date_index = columns.index("date")
        market_index = columns.index("market")
        float_indexes = [i for i, field in enumerate(schema) if pa.types.is_floating(field.type)]
        seen: set[tuple[object, ...]] = set()
        for batch in parquet.iter_batches(columns=columns, batch_size=_BATCH_SIZE):
            for index, column in enumerate(columns):
                null_counts[column] += batch.column(index).null_count
            date_values = batch.column(date_index)
            market_values = batch.column(market_index)
            for value in date_values.to_pylist():
                if value is not None:
                    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
                    dates_seen.add(text)
                    first = text if first is None else min(first, text)
                    last = text if last is None else max(last, text)
                    if int(text[:4]) != expected_year:
                        _finding(findings, "PARTITION_YEAR_MISMATCH", _relative(path, root))
            if pc.any(pc.not_equal(market_values, pa.scalar(expected_market))).as_py():
                _finding(findings, "PARTITION_MARKET_MISMATCH", _relative(path, root))
            pk_values = [batch.column(index).to_pylist() for index in pk_indexes]
            for pk in zip(*pk_values):
                if any(value is None for value in pk):
                    null_key_rows += 1
                if pk in seen:
                    duplicate_rows += 1
                else:
                    seen.add(pk)
            for index in float_indexes:
                values = batch.column(index)
                infinity_count += int(pc.sum(pc.fill_null(pc.is_inf(values), False)).as_py())
                nan_count += int(pc.sum(pc.fill_null(pc.is_nan(values), False)).as_py())
    required_nulls = {
        column.name: null_counts[column.name]
        for column in contract.columns if not column.nullable and null_counts[column.name]
    }
    for code, detail in (
        ("PRIMARY_KEY_DUPLICATE", duplicate_rows),
        ("PRIMARY_KEY_NULL", null_key_rows),
        ("REQUIRED_NULL", required_nulls),
        ("INFINITY", infinity_count),
        ("NAN", nan_count),
    ):
        if detail:
            _finding(findings, code, detail)
    if total_rows != checkpoint_rows:
        _finding(findings, "CHECKPOINT_PARQUET_ROW_MISMATCH", f"{checkpoint_rows}!={total_rows}")
    schema_pass = bool(files) and len(schemas) == 1 and expected_schema in schemas
    return ({
        "root": root.as_posix(), "files": len(files), "rows": total_rows,
        "checkpoint_declared_rows": checkpoint_rows,
        "row_count_matches_checkpoint": total_rows == checkpoint_rows,
        "coverage": {"first": first, "last": last, "unique_dates": len(dates_seen)},
        "schema_status": "PASS" if schema_pass else "FAIL",
        "primary_key": {
            "status": "PASS" if duplicate_rows == 0 and null_key_rows == 0 else "FAIL",
            "duplicates_after_first": duplicate_rows, "null_rows": null_key_rows,
            "method": "STREAMED_PER_PROVEN_DISJOINT_MARKET_YEAR_PARTITION",
        },
        "nulls": {"status": "PASS" if not required_nulls else "FAIL", "required_column_counts": required_nulls},
        "non_finite": {
            "status": "PASS" if infinity_count == 0 and nan_count == 0 else "FAIL",
            "infinity_count": infinity_count, "nan_count": nan_count,
        },
    }, partitions, dates_seen)


def _canonical_value(value: object) -> object:
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return "__NAN__"
    return value


def _frame_rows(frame, columns: tuple[str, ...], sort_key: tuple[str, ...]) -> Iterator[tuple[tuple[object, ...], tuple[object, ...]]]:
    positions = [columns.index(column) for column in sort_key]
    for raw in frame.loc[:, list(columns)].itertuples(index=False, name=None):
        values = tuple(_canonical_value(value) for value in raw)
        yield tuple(values[index] for index in positions), values


def _actual_rows(path: Path, columns: tuple[str, ...], sort_key: tuple[str, ...]):
    positions = [columns.index(column) for column in sort_key]
    for batch in pq.ParquetFile(path).iter_batches(columns=list(columns), batch_size=_BATCH_SIZE):
        arrays = [column.to_pylist() for column in batch.columns]
        for raw in zip(*arrays):
            values = tuple(_canonical_value(value) for value in raw)
            yield tuple(values[index] for index in positions), values


def _daily_partition_rows(
    artifacts: list[_Artifact], key: tuple[str, int], dataset: str,
):
    """Yield one daily scope at a time; no partition-sized source materialization."""
    contract = SHORT_SELLING_CONTRACTS[dataset]
    market, year = key
    selected = sorted(
        (artifact for artifact in artifacts if key in artifact.partition_keys),
        key=lambda artifact: (artifact.scope.start_date, artifact.scope.scope_id),
    )
    for artifact in selected:
        parsed = PARSERS[dataset](
            artifact.body.read_bytes(), date=artifact.scope.start_date,
            market=artifact.scope.market,
        )
        frame = parsed.dataframe
        dates = frame["date"].astype(str)
        frame = frame.loc[(frame["market"] == market) & (dates.str[:4].astype(int) == year)]
        yield from _frame_rows(frame, contract.column_names, contract.sort_key)


def _investor_metric_rows(
    artifacts: list[_Artifact], key: tuple[str, int], metric: str,
):
    """Yield sequential source chunks for one metric, retaining one chunk only."""
    contract = SHORT_SELLING_CONTRACTS["investor"]
    market, year = key
    selected = sorted(
        (
            artifact for artifact in artifacts
            if key in artifact.partition_keys and artifact.scope.metric == metric
        ),
        key=lambda artifact: (artifact.scope.start_date, artifact.scope.end_date),
    )
    for artifact in selected:
        scope = artifact.scope
        parsed = PARSERS["investor"](
            artifact.body.read_bytes(), market=scope.market, metric=scope.metric
        )
        frame = parsed.dataframe
        dates = frame["date"].astype(str)
        frame = frame.loc[(frame["market"] == market) & (dates.str[:4].astype(int) == year)]
        yield from _frame_rows(frame, contract.column_names, contract.sort_key)


def _expected_partition_rows(
    artifacts: list[_Artifact], key: tuple[str, int], dataset: str,
):
    if dataset in {"trading", "balance"}:
        return _daily_partition_rows(artifacts, key, dataset)
    # Contract order is date/market/metric/investor. At most the two current
    # metric chunk streams are live, independent of historical range length.
    streams = [
        _investor_metric_rows(artifacts, key, metric)
        for metric in ("trading_value", "volume")
    ]
    return heapq.merge(*streams, key=lambda item: item[0])


def _exact_normalized_values(
    artifacts: list[_Artifact], partitions: dict[tuple[str, int], Path], dataset: str,
    findings: list[dict[str, str]],
) -> dict[str, object]:
    expected_keys = {key for artifact in artifacts for key in artifact.partition_keys}
    actual_keys = set(partitions)
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    if missing:
        _finding(findings, "NORMALIZED_PARTITION_MISSING", missing)
    if extra:
        _finding(findings, "NORMALIZED_PARTITION_ORPHAN", extra)
    contract = SHORT_SELLING_CONTRACTS[dataset]
    compared = 0
    mismatches = 0
    for key in sorted(expected_keys & actual_keys):
        try:
            expected = _expected_partition_rows(artifacts, key, dataset)
            actual = _actual_rows(partitions[key], contract.column_names, contract.sort_key)
            previous_expected = previous_actual = None
            from itertools import zip_longest
            for expected_item, actual_item in zip_longest(expected, actual, fillvalue=_MISSING):
                if expected_item is not _MISSING:
                    if previous_expected is not None and expected_item[0] < previous_expected:
                        _finding(findings, "LANDING_NORMALIZED_SORT_INVALID", key)
                        mismatches += 1
                        break
                    previous_expected = expected_item[0]
                if actual_item is not _MISSING:
                    if previous_actual is not None and actual_item[0] < previous_actual:
                        _finding(findings, "PARQUET_SORT_INVALID", key)
                        mismatches += 1
                        break
                    previous_actual = actual_item[0]
                if expected_item is _MISSING or actual_item is _MISSING or expected_item[1] != actual_item[1]:
                    _finding(findings, "NORMALIZED_VALUE_MISMATCH", f"partition={key},row={compared}")
                    mismatches += 1
                    break
                compared += 1
        except Exception as error:
            _finding(findings, "NORMALIZED_COMPARISON_ERROR", f"{key}:{type(error).__name__}:{error}")
            mismatches += 1
    return {
        "status": "PASS" if not missing and not extra and mismatches == 0 else "FAIL",
        "compared_rows": compared, "mismatched_partitions": mismatches,
        "missing_partitions": [list(key) for key in missing],
        "orphan_partitions": [list(key) for key in extra],
        "method": "EXACT_SORTED_STREAM_COMPARISON",
    }


def _parse_artifact(scope: RequestScope, body: Path):
    if scope.dataset == "investor":
        return PARSERS[scope.dataset](body.read_bytes(), market=scope.market, metric=scope.metric)
    return PARSERS[scope.dataset](body.read_bytes(), date=scope.start_date, market=scope.market)


def _partition_keys(scope: RequestScope, frame) -> tuple[tuple[str, int], ...]:
    years = sorted({int(str(value)[:4]) for value in frame["date"]})
    return tuple((scope.market, year) for year in years)


def _pid_status(pid: object) -> str:
    if type(pid) is not int or pid < 1:
        return "INVALID"
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return "RUNNING"
            return "NOT_RUNNING_OR_INACCESSIBLE"
        except Exception:
            return "UNKNOWN"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "NOT_RUNNING"
    except PermissionError:
        return "RUNNING_OR_INACCESSIBLE"
    return "RUNNING"


def _collector_processes() -> tuple[tuple[int, ...] | None, str]:
    """Find only A007 collector processes from command lines, or return UNKNOWN."""
    if os.name != "nt":
        proc = Path("/proc")
        if not proc.is_dir():
            return None, "COMMAND_LINE_ENUMERATION_UNAVAILABLE"
        found = []
        try:
            for entry in proc.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8")
                except (OSError, UnicodeError):
                    continue
                if "backfill_pykrx_short_selling.py" in command:
                    found.append(int(entry.name))
            return tuple(sorted(found)), "PROC_COMMAND_LINE"
        except OSError:
            return None, "COMMAND_LINE_ENUMERATION_FAILED"
    try:
        command = (
            "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
            "Get-CimInstance Win32_Process | Where-Object { "
            "($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
            "$_.CommandLine -match 'backfill_pykrx_short_selling\\.py(?:\\s|[\"'']|$)' "
            "} | ForEach-Object { $_.ProcessId }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
        )
        if result.returncode:
            return None, "CIM_COMMAND_LINE_FAILED"
        pids = tuple(sorted(int(line.strip()) for line in result.stdout.splitlines() if line.strip()))
        return pids, "CIM_COMMAND_LINE"
    except (OSError, subprocess.SubprocessError):
        return None, "CIM_COMMAND_LINE_UNAVAILABLE"


def _runtime_readiness(
    path: Path, root: Path,
    process_probe: Callable[[], tuple[tuple[int, ...] | None, str]] = _collector_processes,
) -> dict[str, object]:
    collector_pids, method = process_probe()
    result: dict[str, object] = {
        "path": _relative(path, root), "lock_exists": path.exists(),
        "collector_process_pids": list(collector_pids) if collector_pids is not None else None,
        "collector_process_count": len(collector_pids) if collector_pids is not None else None,
        "process_probe": method,
    }
    if not path.exists():
        if collector_pids is None:
            result.update({"status": "UNKNOWN", "lock_status": "RELEASED", "reason": "collector command lines could not be verified"})
        elif collector_pids:
            result.update({"status": "FAIL", "lock_status": "RELEASED", "reason": "collector process exists without lock"})
        else:
            result.update({"status": "PASS", "lock_status": "RELEASED"})
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result.update({"status": "FAIL", "lock_status": "PRESENT_INVALID", "owner_pid_matches_collector": None})
        return result
    pid_status = _pid_status(payload.get("pid"))
    owner_pid = payload.get("pid")
    result.update({
        "status": "FAIL", "lock_status": "HELD", "owner": payload.get("owner"),
        "run_id": payload.get("run_id"), "pid": owner_pid,
        "pid_status": pid_status,
        "owner_pid_matches_collector": (
            owner_pid in collector_pids if collector_pids is not None and type(owner_pid) is int
            else None
        ),
    })
    return result


def audit_dataset(
    project_root: Path, dataset: str, *, plan: DatasetAuditPlan,
    ledger: _LedgerIndex,
) -> dict[str, object]:
    root = project_root.resolve()
    contract = SHORT_SELLING_CONTRACTS[dataset]
    checkpoint_path = root / "data/state" / f"{contract.name}_v2.json"
    landing_root = root / "data/landing/pykrx/short_selling"
    dataset_landing = landing_root / dataset
    normalized_root = root / "data/normalized" / contract.name
    integrity: list[dict[str, str]] = []
    completeness: list[dict[str, str]] = []
    if plan.dataset != dataset:
        raise ValueError("audit plan dataset differs")
    if not checkpoint_path.is_file():
        _finding(completeness, "CHECKPOINT_MISSING", checkpoint_path)
        return {
            "dataset": dataset, "contract": contract.name, "status": "INCOMPLETE",
            "artifact_integrity": {"status": "NOT_RUN", "findings": []},
            "completeness": {"status": "FAIL", "findings": completeness},
        }
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _finding(integrity, "CHECKPOINT_INVALID", "not valid JSON")
        return {
            "dataset": dataset, "contract": contract.name, "status": "FAIL",
            "artifact_integrity": {"status": "FAIL", "findings": integrity},
            "completeness": {"status": "NOT_RUN", "findings": []},
        }
    completed = checkpoint.get("completed")
    if checkpoint.get("dataset") != dataset or checkpoint.get("contract_version") != 2 or not isinstance(completed, dict):
        _finding(integrity, "CHECKPOINT_IDENTITY_INVALID", "dataset/version/completed")
        return {
            "dataset": dataset, "contract": contract.name, "status": "FAIL",
            "artifact_integrity": {"status": "FAIL", "findings": integrity},
            "completeness": {"status": "NOT_RUN", "findings": []},
        }

    expected_scopes = set(plan.expected_scope_ids)
    actual_scopes = set(completed)
    missing_scopes = sorted(expected_scopes - actual_scopes)
    extra_scopes = sorted(actual_scopes - expected_scopes)
    if missing_scopes:
        _finding(completeness, "PLANNED_SCOPES_MISSING", len(missing_scopes))
    if extra_scopes:
        _finding(completeness, "UNPLANNED_SCOPES_PRESENT", len(extra_scopes))
    if checkpoint.get("status") not in plan.acceptable_terminal_statuses:
        _finding(completeness, "CHECKPOINT_NOT_TERMINAL", checkpoint.get("status"))
    completed_ledger_scopes = {
        scope_id for ledger_dataset, scope_id in ledger.completions if ledger_dataset == dataset
    }
    correlated_ledger_scopes = {
        str(record.get("scope"))
        for records in ledger.correlations.values() for record in records
        if _classify_correlation(record) == dataset
    }
    orphan_completions = sorted(completed_ledger_scopes - actual_scopes)
    uncheckpointed_calls = sorted(correlated_ledger_scopes - actual_scopes)
    duplicate_completions = sum(
        max(0, len(records) - 1)
        for (ledger_dataset, _), records in ledger.completions.items()
        if ledger_dataset == dataset
    )
    if orphan_completions:
        _finding(integrity, "ORPHAN_SCOPE_COMPLETED", len(orphan_completions))
    if uncheckpointed_calls:
        _finding(integrity, "UNCHECKPOINTED_BUSINESS_SCOPE", len(uncheckpointed_calls))
    if duplicate_completions:
        _finding(integrity, "DUPLICATE_SCOPE_COMPLETED", duplicate_completions)

    declared_rows = 0
    artifacts: list[_Artifact] = []
    expected_bodies: set[Path] = set()
    expected_sidecars: set[Path] = set()
    parsed_classifications: Counter[str] = Counter()
    for scope_id, record in sorted(completed.items()):
        if not isinstance(record, Mapping):
            _finding(integrity, "CHECKPOINT_SCOPE_INVALID", scope_id)
            continue
        try:
            scope = _scope_from_id(dataset, scope_id)
        except ValueError as error:
            _finding(integrity, "SCOPE_ID_INVALID", f"{scope_id}:{error}")
            continue
        body = dataset_landing / f"{scope_id}.json"
        sidecar = body.with_name(f"{body.name}.provenance.json")
        expected_bodies.add(body.resolve())
        expected_sidecars.add(sidecar.resolve())
        try:
            recorded_body = (root / Path(str(record.get("body_file", "")))).resolve()
            recorded_body.relative_to(root)
            if recorded_body != body.resolve():
                raise ValueError("checkpoint body path differs")
            content_hash = _sha256(body)
            provenance = json.loads(sidecar.read_text(encoding="utf-8"))
            run_id = provenance.get("run_id")
            sequence = provenance.get("raw_sequence")
            expected_provenance = {
                "version": 2, "dataset": dataset, "scope_id": scope_id,
                "scope_sha256": _scope_sha256(scope), "http_status_code": 200,
                "response_bytes": body.stat().st_size, "body_sha256": content_hash,
            }
            if any(provenance.get(key) != value for key, value in expected_provenance.items()):
                raise ValueError("provenance values differ")
            relative_ledger = provenance.get("ledger_relative_path")
            ledger_path = (root / Path(str(relative_ledger))).resolve()
            ledger_path.relative_to(root)
            if ledger_path != (landing_root / "runs" / str(run_id) / "call_ledger.jsonl").resolve():
                raise ValueError("ledger path/run mismatch")
            if record.get("body_sha256") != content_hash:
                raise ValueError("checkpoint hash differs")
            key = (str(run_id), sequence) if type(sequence) is int else ("", -1)
            responses = ledger.responses.get(key, [])
            correlations = ledger.correlations.get(key, [])
            if len(responses) != 1 or len(correlations) != 1:
                raise ValueError("HTTP response/correlation is not exact unique")
            response = responses[0]
            expected_http = {
                "method": "POST", "url": BUSINESS_URL, "status_code": 200,
                "response_bytes": body.stat().st_size, "response_sha256": content_hash,
                "authentication": False,
            }
            if any(response.get(field) != value for field, value in expected_http.items()):
                raise ValueError("HTTP response differs")
            correlation = correlations[0]
            if correlation.get("scope") != scope_id or correlation.get("scope_sha256") != _scope_sha256(scope):
                raise ValueError("scope correlation differs")
            parsed = _parse_artifact(scope, body)
            parsed_classifications[parsed.classification] += 1
            if record.get("classification") != parsed.classification:
                raise ValueError("checkpoint classification differs from parser")
            if record.get("source_rows") != parsed.source_rows:
                raise ValueError("checkpoint source_rows differs from parser")
            if record.get("normalized_rows") != len(parsed.dataframe):
                raise ValueError("checkpoint normalized_rows differs from parser")
            if type(record.get("normalized_rows")) is not int or record["normalized_rows"] < 0:
                raise ValueError("checkpoint normalized_rows invalid")
            declared_rows += record["normalized_rows"]
            completions = ledger.completions.get((dataset, scope_id), [])
            if len(completions) != 1:
                raise ValueError("SCOPE_COMPLETED is not exact unique")
            completion = completions[0]
            if completion.get("classification") != parsed.classification:
                raise ValueError("SCOPE_COMPLETED classification differs")
            if completion.get("normalized_rows") != len(parsed.dataframe):
                raise ValueError("SCOPE_COMPLETED normalized_rows differs")
            artifacts.append(_Artifact(
                scope=scope, body=body, source_rows=parsed.source_rows,
                normalized_rows=len(parsed.dataframe), classification=parsed.classification,
                partition_keys=_partition_keys(scope, parsed.dataframe),
            ))
        except Exception as error:
            _finding(integrity, "ARTIFACT_CHAIN_INVALID", f"{scope_id}:{type(error).__name__}:{error}")

    actual_bodies = {
        path.resolve() for path in dataset_landing.glob("*.json")
        if not path.name.endswith(".provenance.json")
    } if dataset_landing.is_dir() else set()
    actual_sidecars = {
        path.resolve() for path in dataset_landing.glob("*.json.provenance.json")
    } if dataset_landing.is_dir() else set()
    orphan_bodies = sorted(path.name for path in actual_bodies - expected_bodies)
    missing_bodies = sorted(path.name for path in expected_bodies - actual_bodies)
    orphan_sidecars = sorted(path.name for path in actual_sidecars - expected_sidecars)
    missing_sidecars = sorted(path.name for path in expected_sidecars - actual_sidecars)
    for code, values in (
        ("ORPHAN_LANDING_BODY", orphan_bodies),
        ("MISSING_LANDING_BODY", missing_bodies),
        ("ORPHAN_LANDING_SIDECAR", orphan_sidecars),
        ("MISSING_LANDING_SIDECAR", missing_sidecars),
    ):
        if values:
            _finding(integrity, code, len(values))

    try:
        parquet, partitions, observed_dates = _parquet_audit(
            normalized_root, dataset, declared_rows, integrity
        )
    except Exception as error:
        _finding(integrity, "PARQUET_AUDIT_ERROR", f"{type(error).__name__}:{error}")
        parquet = {
            "root": normalized_root.as_posix(), "files": 0, "rows": 0,
            "coverage": {"first": None, "last": None, "unique_dates": 0},
            "schema_status": "FAIL",
            "primary_key": {"status": "NOT_RUN"},
            "nulls": {"status": "NOT_RUN"},
            "non_finite": {"status": "NOT_RUN", "infinity_count": None, "nan_count": None},
        }
        partitions, observed_dates = {}, set()
    exact_values = _exact_normalized_values(artifacts, partitions, dataset, integrity)
    expected_dates = {value.isoformat() for value in plan.trading_dates}
    missing_dates = sorted(expected_dates - observed_dates)
    extra_dates = sorted(observed_dates - expected_dates)
    if missing_dates:
        _finding(completeness, "PLANNED_DATES_MISSING", len(missing_dates))
    if extra_dates:
        _finding(completeness, "UNPLANNED_DATES_PRESENT", len(extra_dates))
    expected_coverage = {
        "first": min(expected_dates) if expected_dates else None,
        "last": max(expected_dates) if expected_dates else None,
    }
    if parquet["coverage"]["first"] != expected_coverage["first"] or parquet["coverage"]["last"] != expected_coverage["last"]:
        _finding(completeness, "COVERAGE_MISMATCH", f"expected={expected_coverage},actual={parquet['coverage']}")

    integrity_status = "PASS" if not integrity else "FAIL"
    completeness_status = "PASS" if not completeness else "FAIL"
    status = "FAIL" if integrity else ("PASS" if not completeness else "INCOMPLETE")
    return {
        "dataset": dataset, "contract": contract.name, "status": status,
        "artifact_integrity": {"status": integrity_status, "findings": integrity},
        "completeness": {
            "status": completeness_status, "findings": completeness,
            "plan": {
                "start": plan.start.isoformat(), "end": plan.end.isoformat(),
                "trading_dates": len(plan.trading_dates),
                "expected_scopes": len(plan.expected_scope_ids),
                "acceptable_terminal_statuses": list(plan.acceptable_terminal_statuses),
                "expected_coverage": expected_coverage,
            },
            "completed_scopes": len(completed),
            "missing_scopes": {"count": len(missing_scopes), "sample": missing_scopes[:20]},
            "extra_scopes": {"count": len(extra_scopes), "sample": extra_scopes[:20]},
            "missing_dates": {"count": len(missing_dates), "sample": missing_dates[:20]},
            "extra_dates": {"count": len(extra_dates), "sample": extra_dates[:20]},
        },
        "checkpoint": {
            "path": _relative(checkpoint_path, root), "status": checkpoint.get("status"),
            "completed_scopes": len(completed), "declared_normalized_rows": declared_rows,
            "parsed_classifications": dict(sorted(parsed_classifications.items())),
        },
        "landing": {
            "bodies": len(actual_bodies), "sidecars": len(actual_sidecars),
            "validated_artifact_chains": len(artifacts), "orphan_bodies": orphan_bodies,
            "missing_bodies": missing_bodies, "orphan_sidecars": orphan_sidecars,
            "missing_sidecars": missing_sidecars,
        },
        "ledger": {
            "business_responses": sum(
                1 for key, records in ledger.correlations.items()
                if len(records) == 1 and _classify_correlation(records[0]) == dataset
                and len(ledger.responses.get(key, [])) == 1
            ),
            "http_status_counts": dict(sorted(Counter(
                str(ledger.responses[key][0].get("status_code"))
                for key, records in ledger.correlations.items()
                if len(records) == 1 and _classify_correlation(records[0]) == dataset
                and len(ledger.responses.get(key, [])) == 1
            ).items())),
            "scope_completed": len(completed_ledger_scopes),
            "correlated_scopes": len(correlated_ledger_scopes),
            "uncheckpointed_scopes": {
                "count": len(uncheckpointed_calls), "sample": uncheckpointed_calls[:20]
            },
            "valid_empty_completed": sum(
                1 for (ledger_dataset, _), records in ledger.completions.items()
                for record in records
                if ledger_dataset == dataset and str(record.get("classification", "")).startswith("VALID_EMPTY")
            ),
        },
        "normalized": {**parquet, "exact_values": exact_values},
    }


def audit_a007(
    project_root: Path,
    datasets: Iterable[str] = DATASETS,
    *,
    plans: Mapping[str, DatasetAuditPlan] | None = None,
    collector_process_probe: Callable[
        [], tuple[tuple[int, ...] | None, str]
    ] = _collector_processes,
) -> dict[str, object]:
    root = project_root.resolve()
    selected = tuple(datasets)
    if not selected or any(dataset not in DATASETS for dataset in selected):
        raise ValueError("datasets must be one or more of trading, balance, investor")
    effective_plans = {
        dataset: plans[dataset] if plans and dataset in plans else canonical_plan(root, dataset)
        for dataset in selected
    }
    ledger = _load_ledgers(root / "data/landing/pykrx/short_selling/runs")
    ledger_summary, ledger_findings = _ledger_integrity(ledger)
    results = [
        audit_dataset(root, dataset, plan=effective_plans[dataset], ledger=ledger)
        for dataset in selected
    ]
    runtime = _runtime_readiness(
        root / "data/state/d_owned_krx_short_selling.lock", root,
        collector_process_probe,
    )
    artifact_status = "FAIL" if ledger_findings or any(
        result["artifact_integrity"]["status"] == "FAIL" for result in results
    ) else "PASS"
    completeness_status = "PASS" if all(
        result["completeness"]["status"] == "PASS" for result in results
    ) else "FAIL"
    overall = (
        "FAIL" if artifact_status == "FAIL" else
        "INCOMPLETE" if completeness_status == "FAIL" else
        "UNKNOWN" if runtime["status"] == "UNKNOWN" else
        "NOT_READY" if runtime["status"] != "PASS" else "PASS"
    )
    return {
        "schema": REPORT_SCHEMA, "version": REPORT_VERSION,
        "project_root": root.as_posix(), "status": overall,
        "artifact_integrity": {
            "status": artifact_status, "ledger": ledger_summary,
            "findings": ledger_findings,
        },
        "completeness": {"status": completeness_status},
        "runtime_readiness": {**runtime, "network_calls": 0},
        "datasets": results,
    }


def render_markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# A007 Short-Selling Audit", "", f"Overall: **{report['status']}**", "",
        f"- Artifact integrity: {report['artifact_integrity']['status']}",
        f"- Completeness: {report['completeness']['status']}",
        f"- Runtime readiness: {report['runtime_readiness']['status']}", "",
        "| Dataset | Status | Integrity | Completeness | Scopes | Rows | Coverage | Exact values |",
        "|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for item in report["datasets"]:
        rows = item.get("normalized", {}).get("rows", "-")
        coverage = item.get("normalized", {}).get("coverage", {})
        exact = item.get("normalized", {}).get("exact_values", {}).get("status", "-")
        scopes = item.get("checkpoint", {}).get("completed_scopes", 0)
        lines.append(
            f"| {item['dataset']} | {item['status']} | {item['artifact_integrity']['status']} | "
            f"{item['completeness']['status']} | {scopes} | {rows} | "
            f"{coverage.get('first')}..{coverage.get('last')} | {exact} |"
        )
    findings = list(report["artifact_integrity"].get("findings", []))
    for item in report["datasets"]:
        findings.extend(
            {"code": f"{item['dataset']}:{finding['code']}", "detail": finding["detail"]}
            for gate in ("artifact_integrity", "completeness")
            for finding in item[gate].get("findings", [])
        )
    if findings:
        lines.extend(["", "## Findings", ""])
        lines.extend(f"- `{finding['code']}` — {finding['detail']}" for finding in findings)
    return "\n".join(lines) + "\n"
