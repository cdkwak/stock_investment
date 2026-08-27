"""Read-only local Parquet benchmark for the Dashboard/Index GUI MVP.

This script deliberately has no provider, refresh, collector, or write path for
the existing data tree. It writes only timestamped benchmark result files under
the requested output directory.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import datetime as dt
import json
import os
import pathlib
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


ROOT = pathlib.Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    relative_path: str
    required_columns: tuple[str, ...]
    metric_columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    filters: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    index_windows: bool = False


SPECS = (
    ArtifactSpec(
        "kr_index_daily",
        "data/normalized/kr_index_daily",
        ("date", "symbol", "market", "close", "volume", "trading_value"),
        ("close", "volume", "trading_value"),
        ("date", "symbol"),
        {"market": ("KOSPI", "KOSDAQ"), "symbol": ("KOSPI", "KOSDAQ")},
        True,
    ),
    ArtifactSpec(
        "global_index_price_daily",
        "data/normalized/global_index_price_daily",
        ("date", "symbol", "close"),
        ("close",),
        ("date", "symbol"),
        {"symbol": ("SP500", "NASDAQ_COMPOSITE", "NASDAQ100")},
        True,
    ),
    ArtifactSpec(
        "global_etf_price_daily",
        "data/normalized/global_etf_price_daily",
        ("date", "symbol", "close", "volume"),
        ("close", "volume"),
        ("date", "symbol"),
        {"symbol": ("SOXX",)},
        True,
    ),
    ArtifactSpec(
        "fred_treasury_yield_daily",
        "data/normalized/fred_treasury_yield_daily",
        ("date", "dgs2", "dgs10"),
        ("dgs2", "dgs10"),
        ("date",),
    ),
    ArtifactSpec(
        "us_treasury_spread_daily",
        "data/derived/us_treasury_spread_daily",
        ("date", "spread_10y_2y"),
        ("spread_10y_2y",),
        ("date",),
    ),
    ArtifactSpec(
        "kr_market_investor_net_purchase_bridge_daily",
        "data/published/kr_market_investor_net_purchase_bridge_daily",
        (
            "date",
            "market",
            "foreign_net_purchase",
            "institution_net_purchase",
            "individual_net_purchase",
            "provider_segment",
        ),
        ("foreign_net_purchase", "institution_net_purchase", "individual_net_purchase"),
        ("date", "market", "provider_segment"),
        {"market": ("KOSPI", "KOSDAQ")},
    ),
    ArtifactSpec(
        "kr_market_breadth_daily",
        "data/derived/kr_market_breadth_daily",
        ("date", "market", "advancing", "declining", "unchanged"),
        ("advancing", "declining", "unchanged"),
        ("date", "market"),
        {"market": ("KOSPI", "KOSDAQ")},
    ),
    ArtifactSpec(
        "kr_kospi200_option_pcr_daily",
        "data/derived/kr_kospi200_option_pcr_daily",
        ("date", "scope", "market_scope", "volume_pcr", "observation_status"),
        ("volume_pcr",),
        ("date", "scope", "market_scope"),
        {"scope": ("krx_openapi_kospi200_option_total",)},
    ),
    ArtifactSpec(
        "kr_kospi200_futures_nearest_listed_daily",
        "data/derived/kr_kospi200_futures_nearest_listed_daily",
        ("date", "session", "settlement_basis", "basis_status"),
        ("settlement_basis",),
        ("date", "bridge_segment", "session"),
        {
            "session": ("REGULAR_DAY",),
            "basis_status": ("SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE",),
        },
    ),
    ArtifactSpec(
        "kr_kospi200_futures_investor_net_purchase_daily",
        "data/normalized/kr_kospi200_futures_investor_net_purchase_daily",
        (
            "date",
            "product",
            "session",
            "investor_type_source",
            "net_purchase_trading_value",
        ),
        ("net_purchase_trading_value",),
        ("date", "product", "session", "investor_type_source"),
        {
            "product": ("KOSPI200_FUTURES",),
            "session": ("ALL",),
            "investor_type_source": ("외국인 합계",),
        },
    ),
)


NETWORK_CALLS = 0


def install_network_guard() -> None:
    """Fail closed if this benchmark ever attempts a network connection."""

    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def blocked_connect(self: Any, address: Any) -> None:
        global NETWORK_CALLS
        NETWORK_CALLS += 1
        raise RuntimeError(f"network forbidden by benchmark: connect({address!r})")

    def blocked_create_connection(*args: Any, **kwargs: Any) -> None:
        global NETWORK_CALLS
        NETWORK_CALLS += 1
        raise RuntimeError("network forbidden by benchmark: create_connection")

    socket.socket.connect = blocked_connect  # type: ignore[method-assign]
    socket.create_connection = blocked_create_connection  # type: ignore[assignment]
    # Keep references alive for debuggability and to make the guard explicit.
    _ = (original_connect, original_create_connection)


def _rss_bytes() -> int:
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        get_info.restype = ctypes.c_int
        if not get_info(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        ):
            return 0
        return int(counters.WorkingSetSize)
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1024 if sys.platform != "darwin" else 1))
    except Exception:
        return 0


class MemorySampler:
    def __init__(self) -> None:
        self.start = 0
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "MemorySampler":
        self.start = _rss_bytes()
        self.peak = self.start

        def sample() -> None:
            while not self._stop.is_set():
                self.peak = max(self.peak, _rss_bytes())
                self._stop.wait(0.002)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self.peak = max(self.peak, _rss_bytes())


def _as_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, pa.Scalar):
        return _as_date(value.as_py())
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, pa.Scalar):
        return _json_value(value.as_py())
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _expr_for(spec: ArtifactSpec, schema_names: set[str], start: dt.date | None, end: dt.date | None) -> ds.Expression:
    expr: ds.Expression = ds.scalar(True)
    if "year" in schema_names and (start or end):
        years = sorted({d.year for d in (start, end) if d is not None})
        expr = expr & ds.field("year").isin(years)
    if start is not None:
        expr = expr & (ds.field("date") >= start)
    if end is not None:
        expr = expr & (ds.field("date") <= end)
    for column, values in spec.filters.items():
        if column in schema_names:
            expr = expr & ds.field(column).isin(list(values))
    return expr


def _metadata(root: pathlib.Path) -> dict[str, Any]:
    files = sorted(root.rglob("*.parquet"))
    result: dict[str, Any] = {
        "file_count": len(files),
        "total_bytes": 0,
        "rows": 0,
        "date_values": set(),
        "file_infos": [],
        "schema_names": set(),
        "metadata_readable": True,
        "metadata_error": None,
    }
    for path in files:
        try:
            parquet = pq.ParquetFile(path)
            metadata = parquet.metadata
            result["total_bytes"] += path.stat().st_size
            result["rows"] += metadata.num_rows
            result["schema_names"].update(metadata.schema.names)
            date_min: dt.date | None = None
            date_max: dt.date | None = None
            date_index = metadata.schema.names.index("date") if "date" in metadata.schema.names else -1
            if date_index >= 0:
                for row_group in range(metadata.num_row_groups):
                    stats = metadata.row_group(row_group).column(date_index).statistics
                    if stats is not None:
                        low = _as_date(stats.min)
                        high = _as_date(stats.max)
                        date_min = low if date_min is None or (low and low < date_min) else date_min
                        date_max = high if date_max is None or (high and high > date_max) else date_max
            if date_min:
                result["date_values"].add(date_min)
            if date_max:
                result["date_values"].add(date_max)
            result["file_infos"].append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "bytes": path.stat().st_size,
                    "rows": metadata.num_rows,
                    "date_min": date_min,
                    "date_max": date_max,
                }
            )
        except Exception as exc:  # pragma: no cover - exercised by corrupt artifacts
            result["metadata_readable"] = False
            result["metadata_error"] = repr(exc)
    return result


def _build_dataset(root: pathlib.Path) -> ds.Dataset:
    return ds.dataset(root, format="parquet", partitioning="hive")


def _latest_dates(dataset: ds.Dataset, spec: ArtifactSpec, metadata: dict[str, Any]) -> tuple[dt.date | None, dt.date | None]:
    dates = sorted(d for d in metadata["date_values"] if d is not None)
    if dates:
        latest = dates[-1]
    else:
        latest = None
    # A single partition can contain many dates; inspect only the latest year
    # and the selected entities to resolve the previous exact trading date.
    if latest is None:
        return None, None
    schema_names = set(dataset.schema.names)
    expr = _expr_for(spec, schema_names, dt.date(latest.year, 1, 1), latest)
    try:
        table = dataset.scanner(columns=["date"], filter=expr, use_threads=False).to_table()
        values = sorted({_as_date(v) for v in table.column("date").to_pylist() if _as_date(v)})
        if values:
            latest = values[-1]
            previous = values[-2] if len(values) > 1 else None
            return latest, previous
    except Exception:
        pass
    return latest, None


def _query(
    dataset: ds.Dataset,
    spec: ArtifactSpec,
    columns: list[str],
    start: dt.date,
    end: dt.date,
    mode: str,
) -> dict[str, Any]:
    schema_names = set(dataset.schema.names)
    expression = _expr_for(spec, schema_names, start, end)
    selected = list(dataset.get_fragments(filter=expression))
    total_files = len(list(dataset.get_fragments()))
    started = time.perf_counter_ns()
    with MemorySampler() as memory:
        table = dataset.scanner(columns=columns, filter=expression, use_threads=False).to_table()
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    selected_paths = [str(getattr(fragment, "path", "")) for fragment in selected]
    return {
        "query_name": mode,
        "mode": mode.split("_")[-1],
        "latency_ms": round(elapsed, 3),
        "rows": table.num_rows,
        "columns_projected": columns,
        "selected_files": len(selected_paths),
        "total_files": total_files,
        "selected_paths": selected_paths,
        "partition_pruning_used": len(selected_paths) < total_files,
        "full_dataset_scan": len(selected_paths) == total_files,
        "peak_memory_bytes": memory.peak,
        "memory_delta_bytes": max(0, memory.peak - memory.start),
    }


def _latest_nulls(table: pa.Table, metrics: tuple[str, ...]) -> int:
    if table.num_rows == 0:
        return 0
    nulls = 0
    for column in metrics:
        if column in table.column_names:
            nulls += table.column(column).null_count
    return int(nulls)


def _latest_query(
    dataset: ds.Dataset,
    spec: ArtifactSpec,
    latest: dt.date,
    mode: str,
) -> tuple[dict[str, Any], pa.Table]:
    columns = list(dict.fromkeys((*spec.required_columns, "year")))
    columns = [column for column in columns if column in dataset.schema.names]
    schema_names = set(dataset.schema.names)
    expression = _expr_for(spec, schema_names, latest, latest)
    selected = list(dataset.get_fragments(filter=expression))
    total_files = len(list(dataset.get_fragments()))
    started = time.perf_counter_ns()
    with MemorySampler() as memory:
        table = dataset.scanner(columns=columns, filter=expression, use_threads=False).to_table()
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    selected_paths = [str(getattr(fragment, "path", "")) for fragment in selected]
    result = {
        "query_name": "latest_metric",
        "mode": mode,
        "latency_ms": round(elapsed, 3),
        "rows": table.num_rows,
        "columns_projected": columns,
        "selected_files": len(selected_paths),
        "total_files": total_files,
        "selected_paths": selected_paths,
        "partition_pruning_used": len(selected_paths) < total_files,
        "full_dataset_scan": len(selected_paths) == total_files,
        "peak_memory_bytes": memory.peak,
        "memory_delta_bytes": max(0, memory.peak - memory.start),
        "latest_row_null_count": _latest_nulls(table, spec.metric_columns),
    }
    return result, table


def _duplicate_keys(root: pathlib.Path, keys: tuple[str, ...]) -> dict[str, Any]:
    """Check each partition file with projected keys; never materialize data columns."""

    duplicate_count = 0
    rows_checked = 0
    errors: list[str] = []
    for path in sorted(root.rglob("*.parquet")):
        seen: set[tuple[Any, ...]] = set()
        try:
            parquet = pq.ParquetFile(path)
            available = set(parquet.schema.names)
            if not set(keys).issubset(available):
                errors.append(f"{path}: missing key column")
                continue
            for batch in parquet.iter_batches(columns=list(keys), batch_size=65536):
                arrays = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
                for row in zip(*arrays):
                    key = tuple(_json_value(value) for value in row)
                    rows_checked += 1
                    if key in seen:
                        duplicate_count += 1
                    else:
                        seen.add(key)
        except Exception as exc:
            errors.append(f"{path}: {exc!r}")
    return {
        "duplicate_count": duplicate_count,
        "rows_checked": rows_checked,
        "duplicate_key_scan_scope": "all parquet files, projected key columns only",
        "errors": errors,
        "passed": not errors and duplicate_count == 0,
    }


def _artifact_check(spec: ArtifactSpec) -> tuple[dict[str, Any], ds.Dataset | None, dict[str, Any]]:
    root = ROOT / spec.relative_path
    check: dict[str, Any] = {
        "artifact": spec.name,
        "path": str(root.relative_to(ROOT)),
        "exists": root.exists(),
        "parquet_readable": False,
        "required_columns_present": False,
        "required_columns_missing": [],
        "latest_date": None,
        "previous_date": None,
        "latest_row_null_count": None,
        "duplicate_key": None,
        "file_count": 0,
        "total_bytes": 0,
        "latest_partition_bytes": 0,
        "latest_partition_files": 0,
        "metadata_error": None,
        "error": None,
    }
    if not root.exists():
        return check, None, {}
    metadata = _metadata(root)
    check.update(
        {
            "file_count": metadata["file_count"],
            "total_bytes": metadata["total_bytes"],
            "metadata_error": metadata["metadata_error"],
        }
    )
    try:
        dataset = _build_dataset(root)
        missing = sorted(set(spec.required_columns) - set(dataset.schema.names))
        check["required_columns_missing"] = missing
        check["required_columns_present"] = not missing
        latest, previous = _latest_dates(dataset, spec, metadata)
        check["latest_date"] = latest
        check["previous_date"] = previous
        latest_files = [item for item in metadata["file_infos"] if item["date_max"] == latest]
        check["latest_partition_files"] = len(latest_files)
        check["latest_partition_bytes"] = sum(item["bytes"] for item in latest_files)
        if not check["required_columns_present"] or latest is None:
            return check, dataset, metadata
        latest_result, latest_table = _latest_query(dataset, spec, latest, "cold_proxy")
        check["latest_row_null_count"] = latest_result["latest_row_null_count"]
        check["parquet_readable"] = metadata["metadata_readable"] and latest_result["rows"] >= 0
        check["duplicate_key"] = _duplicate_keys(root, spec.key_columns)
        return check, dataset, metadata
    except Exception as exc:
        check["error"] = repr(exc)
        return check, None, metadata


def _run_for_spec(spec: ArtifactSpec, dataset: ds.Dataset, metadata: dict[str, Any], latest: dt.date) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    columns = list(dict.fromkeys((*spec.required_columns, "year")))
    columns = [column for column in columns if column in dataset.schema.names]
    latest_result, _ = _latest_query(dataset, spec, latest, "cold_proxy")
    latest_result["artifact"] = spec.name
    records.append(latest_result)
    warm_result, _ = _latest_query(dataset, spec, latest, "warm")
    warm_result["artifact"] = spec.name
    records.append(warm_result)
    if spec.index_windows:
        for days in (60, 120, 365):
            start = latest - dt.timedelta(days=days - 1)
            cold = _query(dataset, spec, columns, start, latest, f"index_{days}d_cold_proxy")
            cold["artifact"] = spec.name
            records.append(cold)
            warm = _query(dataset, spec, columns, start, latest, f"index_{days}d_warm")
            warm["artifact"] = spec.name
            records.append(warm)
    return records


def _write_csv(path: pathlib.Path, artifact_checks: list[dict[str, Any]], queries: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for check in artifact_checks:
        rows.append(
            {
                "record_type": "artifact_check",
                "artifact": check.get("artifact"),
                "exists": check.get("exists"),
                "parquet_readable": check.get("parquet_readable"),
                "required_columns_present": check.get("required_columns_present"),
                "latest_date": check.get("latest_date"),
                "previous_date": check.get("previous_date"),
                "latest_row_null_count": check.get("latest_row_null_count"),
                "duplicate_key_passed": (check.get("duplicate_key") or {}).get("passed"),
                "file_count": check.get("file_count"),
                "total_bytes": check.get("total_bytes"),
                "latest_partition_bytes": check.get("latest_partition_bytes"),
            }
        )
    for query in queries:
        rows.append({"record_type": "query", **query})
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json_value(value) for key, value in row.items()})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts" / "gui_benchmark"))
    args = parser.parse_args()
    install_network_guard()
    started_at = dt.datetime.now(dt.timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    output_dir = pathlib.Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"gui_local_query_benchmark_{run_id}.json"
    csv_path = output_dir / f"gui_local_query_benchmark_{run_id}.csv"

    artifact_checks: list[dict[str, Any]] = []
    datasets: dict[str, tuple[ds.Dataset, dict[str, Any], dt.date]] = {}
    for spec in SPECS:
        check, dataset, metadata = _artifact_check(spec)
        artifact_checks.append(check)
        if dataset is not None and check.get("required_columns_present") and check.get("latest_date"):
            latest_value = check["latest_date"]
            latest_date = latest_value if isinstance(latest_value, dt.date) else dt.date.fromisoformat(str(latest_value))
            datasets[spec.name] = (dataset, metadata, latest_date)

    queries: list[dict[str, Any]] = []
    for spec in SPECS:
        item = datasets.get(spec.name)
        if item is not None:
            queries.extend(_run_for_spec(spec, *item))

    all_query_records = [record for record in queries]
    peak_memory = max((record.get("peak_memory_bytes", 0) for record in all_query_records), default=0)
    full_scans = [
        {"artifact": record.get("artifact"), "query_name": record.get("query_name"), "mode": record.get("mode")}
        for record in all_query_records
        if record.get("full_dataset_scan")
    ]
    payload = {
        "run_id": run_id,
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pid": os.getpid(),
        "network_calls": NETWORK_CALLS,
        "data_mutations": 0,
        "collector_or_backfill_runs": 0,
        "existing_data_write_paths": 0,
        "artifact_checks": artifact_checks,
        "queries": all_query_records,
        "summary": {
            "artifact_count": len(SPECS),
            "artifacts_present": sum(bool(item.get("exists")) for item in artifact_checks),
            "artifacts_readable": sum(bool(item.get("parquet_readable")) for item in artifact_checks),
            "peak_memory_bytes": peak_memory,
            "unnecessary_full_dataset_scans": full_scans,
            "full_scan_count": len(full_scans),
        },
        "result_json": str(json_path),
        "result_csv": str(csv_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_value), encoding="utf-8")
    _write_csv(csv_path, artifact_checks, all_query_records)
    print(json.dumps({"status": "completed", "pid": os.getpid(), "result_json": str(json_path), "result_csv": str(csv_path), "network_calls": NETWORK_CALLS, "peak_memory_bytes": peak_memory, "full_scan_count": len(full_scans)}, ensure_ascii=False))
    return 0 if NETWORK_CALLS == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
