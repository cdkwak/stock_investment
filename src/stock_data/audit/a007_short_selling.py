"""Read-only, deterministic audit for the A007 short-selling datasets.

The auditor never imports pykrx, opens a network connection, or changes an
artifact.  Large normalized datasets are inspected a Parquet batch at a time;
primary-key state is discarded at each proven-disjoint market/year partition.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Iterable, Mapping

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from stock_data.contracts.kr_short_selling import SHORT_SELLING_CONTRACTS
from stock_data.pipelines.short_selling_backfill import _scope_sha256
from stock_data.providers.pykrx.short_selling import (
    BUSINESS_URL,
    RequestScope,
    balance_scope,
    investor_scope,
    trading_scope,
)


REPORT_SCHEMA = "stock_data.a007_short_selling_audit"
REPORT_VERSION = 1
DATASETS = ("trading", "balance", "investor")
_BATCH_SIZE = 65_536


@dataclass
class _LedgerIndex:
    paths: int
    records: int
    invalid_lines: int
    event_counts: Counter[str]
    status_counts: Counter[str]
    auth_responses: int
    business_responses: dict[tuple[str, int], dict[str, object]]
    correlations: dict[tuple[str, int], list[dict[str, object]]]
    scope_correlations: Counter[tuple[str, str]]
    completed_classifications: Counter[tuple[str, str, str]]
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


def _load_ledgers(runs_root: Path) -> _LedgerIndex:
    event_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    business: dict[tuple[str, int], dict[str, object]] = {}
    correlations: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    scope_correlations: Counter[tuple[str, str]] = Counter()
    classifications: Counter[tuple[str, str, str]] = Counter()
    records = invalid = auth = errors = retries = 0
    paths = sorted(runs_root.glob("*/call_ledger.jsonl")) if runs_root.is_dir() else []
    for path in paths:
        expected_run = path.parent.name
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            invalid += 1
            continue
        with stream:
            for line in stream:
                records += 1
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError
                except (json.JSONDecodeError, ValueError):
                    invalid += 1
                    continue
                event = str(record.get("event", ""))
                event_counts[event] += 1
                run_id = record.get("run_id")
                sequence = record.get("raw_sequence")
                if run_id != expected_run:
                    invalid += 1
                if "RETRY" in event.upper():
                    retries += 1
                if event == "HTTP_ERROR":
                    errors += 1
                if event == "HTTP_RESPONSE":
                    status_counts[str(record.get("status_code"))] += 1
                    if record.get("authentication") is True:
                        auth += 1
                    elif record.get("authentication") is False and isinstance(run_id, str) and type(sequence) is int:
                        key = (run_id, sequence)
                        if key in business:
                            invalid += 1
                        else:
                            business[key] = record
                elif event == "SCOPE_HTTP_CORRELATED" and isinstance(run_id, str) and type(sequence) is int:
                    correlations[(run_id, sequence)].append(record)
                    scope = record.get("scope")
                    scope_hash = record.get("scope_sha256")
                    if isinstance(scope, str) and isinstance(scope_hash, str):
                        scope_correlations[(scope, scope_hash)] += 1
                elif event == "SCOPE_COMPLETED":
                    scope = record.get("scope")
                    if isinstance(run_id, str) and isinstance(scope, str):
                        classifications[(run_id, scope, str(record.get("classification")))] += 1
    return _LedgerIndex(
        paths=len(paths), records=records, invalid_lines=invalid,
        event_counts=event_counts, status_counts=status_counts,
        auth_responses=auth, business_responses=business,
        correlations=dict(correlations), scope_correlations=scope_correlations,
        completed_classifications=classifications, errors=errors,
        retry_events=retries,
    )


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
) -> dict[str, object]:
    contract = SHORT_SELLING_CONTRACTS[dataset]
    files = sorted(root.glob("market=*/year=*/data.parquet")) if root.is_dir() else []
    total_rows = duplicate_rows = null_key_rows = infinity_count = 0
    null_counts: Counter[str] = Counter()
    first = last = None
    schemas: set[tuple[tuple[str, str], ...]] = set()
    partition_keys: set[tuple[str, int]] = set()

    if not files and checkpoint_rows:
        _finding(findings, "NORMALIZED_MISSING", root)
    for path in files:
        match = re.fullmatch(r"market=(KOSPI|KOSDAQ)[\\/]year=(\d{4})[\\/]data\.parquet", str(path.relative_to(root)))
        if not match:
            _finding(findings, "PARTITION_PATH_INVALID", path)
            continue
        expected_market, expected_year_text = match.groups()
        expected_year = int(expected_year_text)
        partition_key = (expected_market, expected_year)
        if partition_key in partition_keys:
            _finding(findings, "PARTITION_DUPLICATED", partition_key)
        partition_keys.add(partition_key)
        parquet = pq.ParquetFile(path)
        total_rows += parquet.metadata.num_rows
        schema = parquet.schema_arrow
        signature = tuple((field.name, _arrow_dtype(field.type)) for field in schema)
        schemas.add(signature)
        expected_schema = tuple((column.name, column.dtype) for column in contract.columns)
        if signature != expected_schema:
            _finding(findings, "PARQUET_SCHEMA_MISMATCH", _relative(path, root))
            continue

        seen: set[tuple[object, ...]] = set()
        columns = list(contract.column_names)
        pk_indexes = [columns.index(name) for name in contract.primary_key]
        date_index = columns.index("date")
        market_index = columns.index("market")
        float_indexes = [index for index, field in enumerate(schema) if pa.types.is_floating(field.type)]
        for batch in parquet.iter_batches(columns=columns, batch_size=_BATCH_SIZE):
            for index, column in enumerate(columns):
                null_counts[column] += batch.column(index).null_count
            dates = batch.column(date_index)
            markets = batch.column(market_index)
            if len(dates):
                non_null_dates = pc.drop_null(dates)
                if len(non_null_dates):
                    low = pc.min(non_null_dates).as_py()
                    high = pc.max(non_null_dates).as_py()
                    first = low if first is None else min(first, low)
                    last = high if last is None else max(last, high)
                    years = pc.year(non_null_dates)
                    if pc.any(pc.not_equal(years, pa.scalar(expected_year))).as_py():
                        _finding(findings, "PARTITION_YEAR_MISMATCH", _relative(path, root))
            if pc.any(pc.not_equal(markets, pa.scalar(expected_market))).as_py():
                _finding(findings, "PARTITION_MARKET_MISMATCH", _relative(path, root))
            pk_values = [batch.column(index).to_pylist() for index in pk_indexes]
            for key in zip(*pk_values):
                if any(value is None for value in key):
                    null_key_rows += 1
                if key in seen:
                    duplicate_rows += 1
                else:
                    seen.add(key)
            for index in float_indexes:
                infinity_count += int(
                    pc.sum(pc.fill_null(pc.is_inf(batch.column(index)), False)).as_py()
                )

    required_nulls = {
        column.name: null_counts[column.name]
        for column in contract.columns
        if not column.nullable and null_counts[column.name]
    }
    if duplicate_rows:
        _finding(findings, "PRIMARY_KEY_DUPLICATE", duplicate_rows)
    if null_key_rows:
        _finding(findings, "PRIMARY_KEY_NULL", null_key_rows)
    if required_nulls:
        _finding(findings, "REQUIRED_NULL", required_nulls)
    if infinity_count:
        _finding(findings, "INFINITY", infinity_count)
    if total_rows != checkpoint_rows:
        _finding(findings, "CHECKPOINT_PARQUET_ROW_MISMATCH", f"{checkpoint_rows}!={total_rows}")
    return {
        "root": root.as_posix(), "files": len(files), "rows": total_rows,
        "checkpoint_declared_rows": checkpoint_rows,
        "row_count_matches_checkpoint": total_rows == checkpoint_rows,
        "coverage": {
            "first": first.isoformat() if hasattr(first, "isoformat") else first,
            "last": last.isoformat() if hasattr(last, "isoformat") else last,
        },
        "schema_status": "PASS" if len(schemas) == 1 and files and not any(f["code"] == "PARQUET_SCHEMA_MISMATCH" for f in findings) else "FAIL",
        "primary_key": {
            "status": "PASS" if duplicate_rows == 0 and null_key_rows == 0 else "FAIL",
            "duplicates_after_first": duplicate_rows, "null_rows": null_key_rows,
            "method": "STREAMED_PER_PROVEN_DISJOINT_MARKET_YEAR_PARTITION",
        },
        "nulls": {"status": "PASS" if not required_nulls else "FAIL", "required_column_counts": required_nulls},
        "infinity": {"status": "PASS" if infinity_count == 0 else "FAIL", "count": infinity_count},
    }


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


def _lock_audit(path: Path, root: Path) -> dict[str, object]:
    result: dict[str, object] = {"path": _relative(path, root), "exists": path.exists()}
    if not path.exists():
        result["status"] = "RELEASED"
        return result
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["status"] = "PRESENT_INVALID"
        return result
    result.update({
        "status": "HELD", "owner": payload.get("owner"),
        "run_id": payload.get("run_id"), "pid": payload.get("pid"),
        "pid_status": _pid_status(payload.get("pid")),
    })
    return result


def audit_dataset(project_root: Path, dataset: str, ledger: _LedgerIndex | None = None) -> dict[str, object]:
    if dataset not in DATASETS:
        raise ValueError(f"unsupported dataset: {dataset}")
    root = project_root.resolve()
    contract = SHORT_SELLING_CONTRACTS[dataset]
    checkpoint_path = root / "data/state" / f"{contract.name}_v2.json"
    landing_root = root / "data/landing/pykrx/short_selling"
    normalized_root = root / "data/normalized" / contract.name
    ledger = ledger or _load_ledgers(landing_root / "runs")
    findings: list[dict[str, str]] = []
    if not checkpoint_path.is_file():
        return {
            "dataset": dataset, "contract": contract.name, "status": "NOT_AVAILABLE",
            "checkpoint": {"exists": False, "path": _relative(checkpoint_path, root)},
            "findings": [],
        }
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "dataset": dataset, "contract": contract.name, "status": "FAIL",
            "checkpoint": {"exists": True, "path": _relative(checkpoint_path, root), "parse_status": "INVALID"},
            "findings": [{"code": "CHECKPOINT_INVALID", "detail": "not valid JSON"}],
        }
    completed = checkpoint.get("completed")
    if checkpoint.get("dataset") != dataset or checkpoint.get("contract_version") != 2 or not isinstance(completed, dict):
        return {
            "dataset": dataset, "contract": contract.name, "status": "FAIL",
            "checkpoint": {"exists": True, "path": _relative(checkpoint_path, root), "parse_status": "IDENTITY_INVALID"},
            "findings": [{"code": "CHECKPOINT_IDENTITY_INVALID", "detail": "dataset/version/completed"}],
        }

    declared_rows = 0
    checkpoint_classifications: Counter[str] = Counter()
    expected_bodies: set[Path] = set()
    valid_artifacts = 0
    for scope_id, record in sorted(completed.items()):
        if not isinstance(record, Mapping):
            _finding(findings, "CHECKPOINT_SCOPE_INVALID", scope_id)
            continue
        try:
            scope = _scope_from_id(dataset, scope_id)
        except ValueError:
            _finding(findings, "SCOPE_ID_INVALID", scope_id)
            continue
        declared = record.get("normalized_rows")
        if type(declared) is not int or declared < 0:
            _finding(findings, "CHECKPOINT_ROW_COUNT_INVALID", scope_id)
        else:
            declared_rows += declared
        checkpoint_classifications[str(record.get("classification"))] += 1
        body = landing_root / dataset / f"{scope_id}.json"
        expected_bodies.add(body.resolve())
        provenance_path = body.with_name(f"{body.name}.provenance.json")
        try:
            relative_record = Path(str(record.get("body_file", "")))
            recorded_body = (root / relative_record).resolve()
            recorded_body.relative_to(root)
            if recorded_body != body.resolve():
                raise ValueError
            content_hash = _sha256(body)
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            run_id = provenance.get("run_id")
            sequence = provenance.get("raw_sequence")
            relative_ledger = provenance.get("ledger_relative_path")
            ledger_path = (root / Path(str(relative_ledger))).resolve()
            ledger_path.relative_to(root)
            expected_ledger = landing_root / "runs" / str(run_id) / "call_ledger.jsonl"
            if ledger_path != expected_ledger.resolve():
                raise ValueError("ledger path/run mismatch")
            expected_provenance = {
                "version": 2, "dataset": dataset, "scope_id": scope_id,
                "scope_sha256": _scope_sha256(scope), "http_status_code": 200,
                "response_bytes": body.stat().st_size, "body_sha256": content_hash,
            }
            if any(provenance.get(key) != value for key, value in expected_provenance.items()):
                raise ValueError("provenance values differ")
            if record.get("body_sha256") != content_hash:
                raise ValueError("checkpoint hash differs")
            key = (str(run_id), sequence) if type(sequence) is int else ("", -1)
            response = ledger.business_responses.get(key)
            correlations = ledger.correlations.get(key, [])
            if response is None or len(correlations) != 1:
                raise ValueError("ledger response/correlation is not unique")
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
            valid_artifacts += 1
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            _finding(findings, "LANDING_PROVENANCE_INVALID", f"{scope_id}:{error}")

    actual_bodies = {
        path.resolve() for path in (landing_root / dataset).glob("*.json")
        if not path.name.endswith(".provenance.json")
    } if (landing_root / dataset).is_dir() else set()
    orphan_bodies = sorted(path.name for path in actual_bodies - expected_bodies)
    missing_bodies = sorted(path.name for path in expected_bodies - actual_bodies)
    if orphan_bodies:
        _finding(findings, "ORPHAN_LANDING", len(orphan_bodies))
    if missing_bodies:
        _finding(findings, "MISSING_LANDING", len(missing_bodies))

    parquet = _parquet_audit(normalized_root, dataset, declared_rows, findings)
    dataset_keys: set[tuple[str, int]] = set()
    relevant_scope_counts: Counter[tuple[str, str]] = Counter()
    relevant_runs_and_scopes: set[tuple[str, str]] = set()
    for key, correlations in ledger.correlations.items():
        for correlation in correlations:
            scope_id = correlation.get("scope")
            if not isinstance(scope_id, str):
                continue
            try:
                expected_hash = _scope_sha256(_scope_from_id(dataset, scope_id))
            except ValueError:
                continue
            if correlation.get("scope_sha256") == expected_hash:
                dataset_keys.add(key)
                relevant_scope_counts[(scope_id, expected_hash)] += 1
                relevant_runs_and_scopes.add((key[0], scope_id))
    repeated_scope_calls = sum(max(0, count - 1) for count in relevant_scope_counts.values())
    relevant_responses = {
        key: ledger.business_responses[key]
        for key in dataset_keys if key in ledger.business_responses
    }
    relevant_statuses = Counter(str(record.get("status_code")) for record in relevant_responses.values())
    completed_classifications = Counter()
    for (run_id, scope_id, classification), count in ledger.completed_classifications.items():
        if (run_id, scope_id) in relevant_runs_and_scopes:
            completed_classifications[classification] += count
    unmatched_correlations = len(dataset_keys - set(relevant_responses))
    ledger_summary = {
        "unique_business_responses": len(relevant_responses),
        "http_status_counts": dict(sorted(relevant_statuses.items())),
        "valid_empty_completed": sum(
            count for classification, count in completed_classifications.items()
            if classification.startswith("VALID_EMPTY")
        ),
        "duplicate_scope_correlations": repeated_scope_calls,
        "unmatched_scope_correlations": unmatched_correlations,
        "all_runs_context": {
            "files": ledger.paths, "records": ledger.records,
            "invalid_records": ledger.invalid_lines,
            "raw_http_responses": sum(ledger.status_counts.values()),
            "authentication_responses": ledger.auth_responses,
            "http_status_counts": dict(sorted(ledger.status_counts.items())),
            "http_errors": ledger.errors, "retry_events": ledger.retry_events,
        },
    }
    if ledger.invalid_lines:
        _finding(findings, "LEDGER_INVALID_RECORD", ledger.invalid_lines)
    if repeated_scope_calls:
        _finding(findings, "DUPLICATE_SCOPE_HTTP", repeated_scope_calls)
    if unmatched_correlations:
        _finding(findings, "UNMATCHED_SCOPE_CORRELATION", unmatched_correlations)
    status = "PASS" if not findings else "FAIL"
    return {
        "dataset": dataset, "contract": contract.name, "status": status,
        "checkpoint": {
            "exists": True, "path": _relative(checkpoint_path, root),
            "status": checkpoint.get("status"), "completed_scopes": len(completed),
            "declared_normalized_rows": declared_rows,
            "classifications": dict(sorted(checkpoint_classifications.items())),
        },
        "landing": {
            "bodies": len(actual_bodies), "expected_bodies": len(expected_bodies),
            "valid_checkpoint_artifacts": valid_artifacts,
            "orphan_bodies": orphan_bodies, "missing_bodies": missing_bodies,
        },
        "ledger": ledger_summary, "normalized": parquet, "findings": findings,
    }


def audit_a007(project_root: Path, datasets: Iterable[str] = DATASETS) -> dict[str, object]:
    root = project_root.resolve()
    selected = tuple(datasets)
    if not selected or any(dataset not in DATASETS for dataset in selected):
        raise ValueError("datasets must be one or more of trading, balance, investor")
    ledger = _load_ledgers(root / "data/landing/pykrx/short_selling/runs")
    results = [audit_dataset(root, dataset, ledger) for dataset in selected]
    available = [result for result in results if result["status"] != "NOT_AVAILABLE"]
    return {
        "schema": REPORT_SCHEMA, "version": REPORT_VERSION,
        "project_root": root.as_posix(),
        "status": "FAIL" if any(result["status"] == "FAIL" for result in results) else (
            "PASS" if available else "NOT_AVAILABLE"
        ),
        "datasets": results,
        "runtime": {
            "network_calls": 0,
            "lock": _lock_audit(root / "data/state/d_owned_krx_short_selling.lock", root),
        },
    }


def render_markdown(report: Mapping[str, object]) -> str:
    lines = [
        "# A007 Short-Selling Audit", "",
        f"Status: **{report['status']}**", "",
        "| Dataset | Status | Scopes | Rows | Coverage | PK | Null | Infinity | Findings |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for item in report["datasets"]:
        if item["status"] == "NOT_AVAILABLE":
            lines.append(f"| {item['dataset']} | NOT_AVAILABLE | - | - | - | - | - | - | 0 |")
            continue
        checkpoint = item["checkpoint"]
        normalized = item["normalized"]
        coverage = normalized["coverage"]
        lines.append(
            f"| {item['dataset']} | {item['status']} | {checkpoint['completed_scopes']} | "
            f"{normalized['rows']} | {coverage['first']}..{coverage['last']} | "
            f"{normalized['primary_key']['status']} | {normalized['nulls']['status']} | "
            f"{normalized['infinity']['status']} | {len(item['findings'])} |"
        )
    lines.extend(["", "## Runtime", "", f"- Network calls: {report['runtime']['network_calls']}",
                  f"- Lock: {report['runtime']['lock']['status']}"])
    findings = [
        (item["dataset"], finding)
        for item in report["datasets"] for finding in item.get("findings", [])
    ]
    if findings:
        lines.extend(["", "## Findings", ""])
        lines.extend(f"- `{dataset}:{finding['code']}` — {finding['detail']}" for dataset, finding in findings)
    return "\n".join(lines) + "\n"
