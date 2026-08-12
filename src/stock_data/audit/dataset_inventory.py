from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Mapping

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from stock_data.contracts.base import DatasetContract
from stock_data.contracts.registry import CONTRACTS


REPORT_SCHEMA = "stock_data.dataset_inventory"
REPORT_VERSION = 2
LAYERS = ("landing", "normalized", "derived", "published")
IGNORED_COMPONENTS = {"quarantine", "_quarantine", "tmp", "temp"}
STATE_SCALAR_FIELDS = ("dataset", "status", "task_id")
STATE_COUNT_FIELDS = (
    "completed_partitions",
    "valid_empty_partitions",
    "failed_partitions",
    "staged_partitions",
    "completed_dates",
    "empty_dates",
    "failed_dates",
    "completed_targets",
    "failed_targets",
    "progress",
)
STATE_REPORT_DIRECTORY = "audits"


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ignored(relative: Path) -> str | None:
    for part in relative.parts:
        lowered = part.lower()
        if part.startswith("."):
            return "hidden_path_component"
        if lowered in IGNORED_COMPONENTS:
            return f"documented_ignored_component:{lowered}"
    return None


def _artifact_root(file_path: Path, layer_root: Path) -> Path:
    parts = file_path.parent.relative_to(layer_root).parts
    first_partition = next(
        (index for index, part in enumerate(parts) if "=" in part and not part.startswith("=")),
        None,
    )
    if first_partition is None:
        return file_path.parent
    if first_partition == 0:
        return layer_root
    return layer_root.joinpath(*parts[:first_partition])


def _arrow_dtype(value: pa.DataType) -> str:
    if pa.types.is_string(value) or pa.types.is_large_string(value):
        return "string"
    if pa.types.is_int64(value):
        return "int64"
    if pa.types.is_float64(value):
        return "float64"
    if pa.types.is_boolean(value):
        return "bool"
    if pa.types.is_date32(value):
        return "date32"
    if pa.types.is_timestamp(value):
        timezone = f", {value.tz}" if value.tz else ""
        return f"timestamp[{value.unit}{timezone}]"
    if pa.types.is_decimal(value):
        return f"decimal({value.precision},{value.scale})"
    return str(value)


def _schema_fields(schema: pa.Schema) -> list[dict[str, object]]:
    return [
        {"name": field.name, "dtype": _arrow_dtype(field.type), "nullable": field.nullable}
        for field in schema
    ]


def _schema_signature(schema: pa.Schema) -> str:
    return json.dumps(_schema_fields(schema), sort_keys=True, separators=(",", ":"))


def _dataset_metadata(schema: pa.Schema) -> str | None:
    value = (schema.metadata or {}).get(b"dataset")
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _partition_name(file_path: Path, artifact_root: Path) -> str:
    relative = file_path.parent.relative_to(artifact_root)
    return "." if not relative.parts else relative.as_posix()


def _column_statistics(
    files: list[Path], column: str
) -> tuple[int | None, object | None, object | None]:
    null_count = 0
    minimum = None
    maximum = None
    for path in files:
        parquet = pq.ParquetFile(path)
        index = parquet.schema_arrow.get_field_index(column)
        if index < 0:
            return None, None, None
        for row_group_index in range(parquet.metadata.num_row_groups):
            statistics = parquet.metadata.row_group(row_group_index).column(index).statistics
            if statistics is None or not statistics.has_null_count:
                return None, None, None
            null_count += int(statistics.null_count)
            if statistics.has_min_max:
                candidate_min = statistics.min
                candidate_max = statistics.max
                minimum = candidate_min if minimum is None else min(minimum, candidate_min)
                maximum = candidate_max if maximum is None else max(maximum, candidate_max)
    return null_count, minimum, maximum


def _scan_null_count(files: list[Path], column: str) -> int:
    count = 0
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(columns=[column], batch_size=65_536):
            count += batch.column(0).null_count
    return count


def _coverage(files: list[Path], total_rows: int) -> dict[str, object]:
    schemas = [pq.ParquetFile(path).schema_arrow for path in files]
    if not all(schema.get_field_index("date") >= 0 for schema in schemas):
        return {"column": None, "status": "NOT_APPLICABLE", "first": None, "last": None}
    _, minimum, maximum = _column_statistics(files, "date")
    method = "PARQUET_STATISTICS"
    if minimum is None or maximum is None:
        method = "STREAMED_DATE_COLUMN"
        minimum = None
        maximum = None
        for path in files:
            for batch in pq.ParquetFile(path).iter_batches(columns=["date"], batch_size=65_536):
                values = batch.column(0)
                if values.null_count == len(values):
                    continue
                batch_min = pc.min(values).as_py()
                batch_max = pc.max(values).as_py()
                minimum = batch_min if minimum is None else min(minimum, batch_min)
                maximum = batch_max if maximum is None else max(maximum, batch_max)
    return {
        "column": "date",
        "status": "EXACT" if total_rows == 0 or minimum is not None else "NO_NON_NULL_VALUES",
        "method": method,
        "first": minimum.isoformat() if hasattr(minimum, "isoformat") else minimum,
        "last": maximum.isoformat() if hasattr(maximum, "isoformat") else maximum,
    }


def _contract_schema_check(
    contract: DatasetContract | None,
    schema_groups: list[dict[str, object]],
) -> dict[str, object]:
    if contract is None:
        return {"status": "NOT_APPLICABLE_UNREGISTERED"}
    expected = {
        column.name: {"dtype": column.dtype, "nullable": column.nullable}
        for column in contract.columns
    }
    mismatches = []
    for group in schema_groups:
        actual = {field["name"]: field for field in group["fields"]}
        mismatch = {
            "schema_id": group["schema_id"],
            "missing_columns": sorted(set(expected) - set(actual)),
            "unexpected_columns": sorted(set(actual) - set(expected)),
            "dtype_mismatches": [],
        }
        for name in sorted(set(expected).intersection(actual)):
            if expected[name]["dtype"] != actual[name]["dtype"]:
                mismatch["dtype_mismatches"].append(
                    {"column": name, "expected": expected[name]["dtype"], "actual": actual[name]["dtype"]}
                )
        if any(mismatch[key] for key in mismatch if key != "schema_id"):
            mismatches.append(mismatch)
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "distinct_physical_schemas": len(schema_groups),
        "mismatches": mismatches,
    }


def _physical_nullability_check(
    contract: DatasetContract | None,
    schema_groups: list[dict[str, object]],
) -> dict[str, object]:
    """Report Arrow footer nullability separately from observed required values.

    Arrow writers commonly retain nullable physical fields even when every
    stored value satisfies a contract's logical non-null requirement.  That is
    a storage-enforcement limitation, not a schema/type drift or proof of a
    null-value violation; the latter is checked independently by
    ``_nullability_check``.
    """
    if contract is None:
        return {"status": "NOT_APPLICABLE_UNREGISTERED", "mismatches": []}
    expected = {column.name: column.nullable for column in contract.columns}
    mismatches = []
    for group in schema_groups:
        actual = {field["name"]: field for field in group["fields"]}
        differences = [
            {"column": name, "expected": expected[name], "actual": actual[name]["nullable"]}
            for name in sorted(set(expected).intersection(actual))
            if expected[name] != actual[name]["nullable"]
        ]
        if differences:
            mismatches.append({"schema_id": group["schema_id"], "columns": differences})
    return {
        "status": "MATCH" if not mismatches else "MISMATCH",
        "mismatches": mismatches,
        "interpretation": (
            "PHYSICAL_NULLABILITY_ONLY; consult nullability for actual required-value validation"
            if mismatches
            else "PHYSICAL_NULLABILITY_MATCHES_CONTRACT"
        ),
    }


def _nullability_check(
    files: list[Path],
    total_rows: int,
    contract: DatasetContract | None,
    *,
    max_scan_rows: int,
) -> dict[str, object]:
    if contract is None:
        return {"status": "NOT_APPLICABLE_UNREGISTERED", "null_counts": None}
    counts: dict[str, int | None] = {}
    methods: dict[str, str] = {}
    for column in contract.columns:
        count, _, _ = _column_statistics(files, column.name)
        if count is not None:
            counts[column.name] = count
            methods[column.name] = "PARQUET_STATISTICS"
        elif total_rows <= max_scan_rows and all(
            pq.ParquetFile(path).schema_arrow.get_field_index(column.name) >= 0 for path in files
        ):
            counts[column.name] = _scan_null_count(files, column.name)
            methods[column.name] = "STREAMED_COLUMN"
        else:
            counts[column.name] = None
            methods[column.name] = "SKIPPED_MISSING_COLUMN_OR_ROW_LIMIT"
    violations = {
        column.name: counts[column.name]
        for column in contract.columns
        if not column.nullable and counts[column.name] not in (0, None)
    }
    unknown_required = sorted(
        column.name
        for column in contract.columns
        if not column.nullable and counts[column.name] is None
    )
    return {
        "status": "FAIL" if violations else ("INCOMPLETE" if unknown_required else "PASS"),
        "null_counts": counts,
        "methods": methods,
        "non_nullable_violations": violations,
        "unknown_non_nullable_columns": unknown_required,
    }


def _pk_check(
    files: list[Path], total_rows: int, contract: DatasetContract | None, *, max_key_rows: int
) -> dict[str, object]:
    if contract is None:
        return {"status": "NOT_APPLICABLE_UNREGISTERED", "duplicate_rows_after_first": None}
    if total_rows > max_key_rows:
        return {
            "status": "SKIPPED_ROW_LIMIT",
            "row_limit": max_key_rows,
            "duplicate_rows_after_first": None,
            "null_key_rows": None,
        }
    columns = list(contract.primary_key)
    if any(
        pq.ParquetFile(path).schema_arrow.get_field_index(column) < 0
        for path in files
        for column in columns
    ):
        return {
            "status": "NOT_RUN_SCHEMA_MISMATCH",
            "duplicate_rows_after_first": None,
            "null_key_rows": None,
        }
    seen: set[tuple[object, ...]] = set()
    duplicates = 0
    null_rows = 0
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=65_536):
            for key in zip(*(batch.column(index).to_pylist() for index in range(len(columns)))):
                if any(value is None for value in key):
                    null_rows += 1
                if key in seen:
                    duplicates += 1
                else:
                    seen.add(key)
    return {
        "status": "FAIL" if duplicates or null_rows else "PASS",
        "duplicate_rows_after_first": duplicates,
        "null_key_rows": null_rows,
        "audited_rows": total_rows,
    }


def _infinity_check(files: list[Path], total_rows: int, *, max_scan_rows: int) -> dict[str, object]:
    numeric = sorted(
        {
            field.name
            for path in files
            for field in pq.ParquetFile(path).schema_arrow
            if pa.types.is_floating(field.type)
        }
    )
    if not numeric:
        return {"status": "NOT_APPLICABLE", "columns": [], "infinity_count": 0}
    if total_rows > max_scan_rows:
        return {
            "status": "SKIPPED_ROW_LIMIT",
            "columns": numeric,
            "row_limit": max_scan_rows,
            "infinity_count": None,
        }
    counts = Counter()
    for path in files:
        parquet = pq.ParquetFile(path)
        available = [column for column in numeric if parquet.schema_arrow.get_field_index(column) >= 0]
        for batch in parquet.iter_batches(columns=available, batch_size=65_536):
            for index, column in enumerate(available):
                counts[column] += int(pc.sum(pc.fill_null(pc.is_inf(batch.column(index)), False)).as_py())
    total = sum(counts.values())
    return {
        "status": "FAIL" if total else "PASS",
        "columns": numeric,
        "counts": {column: counts[column] for column in numeric},
        "infinity_count": total,
        "audited_rows": total_rows,
    }


def _safe_state_summary(path: Path, project_root: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "path": _relative(path, project_root),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["parse_status"] = "INVALID_JSON"
        result["linked_datasets"] = []
        return result
    if not isinstance(payload, dict):
        result["parse_status"] = "NON_OBJECT_JSON"
        result["linked_datasets"] = []
        return result
    result["parse_status"] = "OK"
    for field in STATE_SCALAR_FIELDS:
        value = payload.get(field)
        if field in payload and (isinstance(value, (str, bool)) or value is None):
            result[field] = value
    counts = {}
    for field in STATE_COUNT_FIELDS:
        value = payload.get(field)
        if isinstance(value, (list, dict)):
            counts[field] = len(value)
    result["operational_counts"] = counts
    linked = set()
    if isinstance(payload.get("dataset"), str):
        linked.add(payload["dataset"])
    if isinstance(payload.get("datasets"), dict):
        linked.update(str(value) for value in payload["datasets"])
    result["linked_datasets"] = sorted(linked)
    return result


def _states(project_root: Path) -> list[dict[str, object]]:
    state_root = project_root / "data" / "state"
    if not state_root.exists():
        return []
    return [
        _safe_state_summary(path, project_root)
        for path in sorted(state_root.rglob("*.json"))
        if path.relative_to(state_root).parts[0] != STATE_REPORT_DIRECTORY
    ]


def _landing_summary(project_root: Path) -> dict[str, object]:
    root = project_root / "data" / "landing"
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    ignored = 0
    if root.exists():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if _ignored(path.relative_to(root)):
                ignored += 1
                continue
            extension = path.suffix.lower() or "<none>"
            counts[extension] += 1
            sizes[extension] += path.stat().st_size
    return {
        "body_read": False,
        "files_by_extension": dict(sorted(counts.items())),
        "bytes_by_extension": dict(sorted(sizes.items())),
        "ignored_files": ignored,
    }


def _associate_states(dataset: str, states: list[dict[str, object]]) -> list[dict[str, object]]:
    direct_name = f"{dataset}.json"
    matched = []
    for state in states:
        if dataset in state.get("linked_datasets", []) or Path(str(state["path"])).name == direct_name:
            matched.append(
                {
                    key: state[key]
                    for key in (
                        "path", "parse_status", "dataset", "status", "task_id",
                        "operational_counts", "sha256",
                    )
                    if key in state
                }
            )
    return matched


def _artifact_record(
    *,
    project_root: Path,
    layer: str,
    artifact_root: Path,
    files: list[Path],
    contracts: Mapping[str, DatasetContract],
    states: list[dict[str, object]],
    max_key_rows: int,
    max_scan_rows: int,
) -> dict[str, object]:
    schema_buckets: dict[str, dict[str, object]] = {}
    file_manifest = []
    metadata_names = set()
    rows = 0
    total_bytes = 0
    for path in files:
        parquet = pq.ParquetFile(path)
        rows += parquet.metadata.num_rows
        total_bytes += path.stat().st_size
        signature = _schema_signature(parquet.schema_arrow)
        if signature not in schema_buckets:
            schema_buckets[signature] = {
                "schema_id": hashlib.sha256(signature.encode()).hexdigest()[:16],
                "fields": _schema_fields(parquet.schema_arrow),
                "file_count": 0,
            }
        schema_buckets[signature]["file_count"] += 1
        file_manifest.append(
            {
                "path": _relative(path, project_root),
                "partition": _partition_name(path, artifact_root),
                "rows": parquet.metadata.num_rows,
                "bytes": path.stat().st_size,
                "schema_id": schema_buckets[signature]["schema_id"],
            }
        )
        metadata_name = _dataset_metadata(parquet.schema_arrow)
        if metadata_name:
            metadata_names.add(metadata_name)
    schema_groups = sorted(schema_buckets.values(), key=lambda item: item["schema_id"])
    inferred_name = artifact_root.name
    association_status = "ROOT_NAME"
    if len(metadata_names) == 1:
        inferred_name = next(iter(metadata_names))
        association_status = "PARQUET_METADATA"
    elif len(metadata_names) > 1:
        association_status = "CONFLICTING_PARQUET_METADATA"
    contract = None if layer == "landing" else contracts.get(inferred_name)
    linked_states = _associate_states(inferred_name, states)
    return {
        "key": {"layer": layer, "relative_root": _relative(artifact_root, project_root)},
        "dataset": inferred_name,
        "dataset_association": association_status,
        "metadata_dataset_names": sorted(metadata_names),
        "registration": (
            "NOT_APPLICABLE_LANDING"
            if layer == "landing"
            else ("REGISTERED" if contract else "UNREGISTERED")
        ),
        "contract_layer": contract.layer if contract else None,
        "layer_matches_contract": contract is None or contract.layer == layer,
        "files": len(files),
        "file_manifest": file_manifest,
        "partitions": len({_partition_name(path, artifact_root) for path in files}),
        "partition_names": sorted({_partition_name(path, artifact_root) for path in files}),
        "bytes": total_bytes,
        "rows": rows,
        "coverage": _coverage(files, rows),
        "physical_schemas": schema_groups,
        "contract_schema": _contract_schema_check(contract, schema_groups),
        "physical_nullability": _physical_nullability_check(contract, schema_groups),
        "primary_key": _pk_check(files, rows, contract, max_key_rows=max_key_rows),
        "nullability": _nullability_check(files, rows, contract, max_scan_rows=max_scan_rows),
        "infinity": _infinity_check(files, rows, max_scan_rows=max_scan_rows),
        "state_presence": "PRESENT" if linked_states else "NOT_FOUND",
        "states": linked_states,
        "classification": "OBSERVED_NOT_DATA_COMPLETE_ASSERTION",
    }


def build_inventory(
    project_root: Path,
    *,
    contracts: Mapping[str, DatasetContract] | None = None,
    max_key_rows: int = 1_000_000,
    max_scan_rows: int = 1_000_000,
) -> dict[str, object]:
    if max_key_rows < 0 or max_scan_rows < 0:
        raise ValueError("scan row limits must be non-negative")
    project_root = project_root.resolve()
    contracts = dict(CONTRACTS if contracts is None else contracts)
    states = _states(project_root)
    grouped: dict[tuple[str, Path], list[Path]] = defaultdict(list)
    ignored = []
    for layer in LAYERS:
        layer_root = project_root / "data" / layer
        if not layer_root.exists():
            continue
        for path in sorted(layer_root.rglob("*.parquet")):
            reason = _ignored(path.relative_to(layer_root))
            if reason:
                ignored.append({"path": _relative(path, project_root), "reason": reason})
                continue
            grouped[(layer, _artifact_root(path, layer_root))].append(path)
    artifacts = [
        _artifact_record(
            project_root=project_root,
            layer=layer,
            artifact_root=root,
            files=sorted(files),
            contracts=contracts,
            states=states,
            max_key_rows=max_key_rows,
            max_scan_rows=max_scan_rows,
        )
        for (layer, root), files in sorted(
            grouped.items(), key=lambda item: (item[0][0], _relative(item[0][1], project_root))
        )
    ]
    observed_registered = {
        artifact["dataset"]
        for artifact in artifacts
        if artifact["registration"] == "REGISTERED"
    }
    missing = [
        {
            "dataset": name,
            "contract_status": contract.status,
            "contract_layer": contract.layer,
            "classification": "MISSING_REGISTERED_ARTIFACT_NOT_DATA_COMPLETE_ASSERTION",
        }
        for name, contract in sorted(contracts.items())
        if name not in observed_registered
    ]
    unregistered = [
        {"dataset": artifact["dataset"], "key": artifact["key"]}
        for artifact in artifacts
        if artifact["registration"] == "UNREGISTERED"
    ]
    issue_counts = Counter()
    for artifact in artifacts:
        if artifact["registration"] == "UNREGISTERED":
            issue_counts["unregistered_artifacts"] += 1
        if artifact["dataset_association"] == "CONFLICTING_PARQUET_METADATA":
            issue_counts["metadata_dataset_conflicts"] += 1
        if artifact["contract_schema"]["status"] == "FAIL":
            issue_counts["schema_failures"] += 1
        if artifact["physical_nullability"]["status"] == "MISMATCH":
            issue_counts["physical_nullability_mismatches"] += 1
        if artifact["primary_key"]["status"] == "FAIL":
            issue_counts["primary_key_failures"] += 1
        if artifact["nullability"]["status"] == "FAIL":
            issue_counts["nullability_failures"] += 1
        if artifact["infinity"]["status"] == "FAIL":
            issue_counts["infinity_failures"] += 1
        if not artifact["layer_matches_contract"]:
            issue_counts["layer_mismatches"] += 1
    issue_counts["missing_registered_artifacts"] = len(missing)
    return {
        "report_schema": REPORT_SCHEMA,
        "report_version": REPORT_VERSION,
        "scope": {
            "layers": list(LAYERS),
            "max_key_rows": max_key_rows,
            "max_scan_rows": max_scan_rows,
            "network_calls": 0,
            "landing_raw_bodies_read": False,
        },
        "summary": {
            "registered_contracts": len(contracts),
            "artifact_roots": len(artifacts),
            "observed_registered_artifacts": len(observed_registered),
            "missing_registered_artifacts": len(missing),
            "unregistered_artifacts": len(unregistered),
            "state_files": len(states),
            "issue_counts": dict(sorted(issue_counts.items())),
        },
        "artifacts": artifacts,
        "missing_registered": missing,
        "unregistered": unregistered,
        "states": states,
        "landing_summary": _landing_summary(project_root),
        "ignored_artifacts": ignored,
        "classification": "READ_ONLY_INVENTORY_NOT_DATA_COMPLETE_ASSERTION",
    }


def render_markdown(report: Mapping[str, object]) -> str:
    summary = report["summary"]
    lines = [
        "# Data-layer Inventory",
        "",
        "> Read-only filesystem observation; this report does not assign completion status.",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Registered contracts | {summary['registered_contracts']} |",
        f"| Artifact roots | {summary['artifact_roots']} |",
        f"| Observed registered artifacts | {summary['observed_registered_artifacts']} |",
        f"| Missing registered artifacts | {summary['missing_registered_artifacts']} |",
        f"| Unregistered artifacts | {summary['unregistered_artifacts']} |",
        f"| State files | {summary['state_files']} |",
        "",
        "| Layer / dataset | Registration | Files | Rows | Coverage | Schema | Physical nullable | Required values | PK | State |",
        "|---|---|---:|---:|---|---|---|---|---|---|",
    ]
    for artifact in report["artifacts"]:
        coverage = artifact["coverage"]
        coverage_text = (
            f"{coverage.get('first')}..{coverage.get('last')}"
            if coverage.get("status") == "EXACT"
            else coverage.get("status")
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{artifact['key']['layer']} / `{artifact['dataset']}`",
                    artifact["registration"],
                    str(artifact["files"]),
                    str(artifact["rows"]),
                    str(coverage_text),
                    artifact["contract_schema"]["status"],
                    artifact["physical_nullability"]["status"],
                    artifact["nullability"]["status"],
                    artifact["primary_key"]["status"],
                    artifact["state_presence"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Missing registered artifacts", ""])
    lines.extend(
        f"- `{item['dataset']}` ({item['contract_layer']}, contract {item['contract_status']})"
        for item in report["missing_registered"]
    )
    lines.extend(["", "## Unregistered artifacts", ""])
    lines.extend(
        f"- `{item['dataset']}` at `{item['key']['relative_root']}`"
        for item in report["unregistered"]
    )
    lines.append("")
    return "\n".join(lines)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".tmp", prefix=path.name + ".",
            dir=path.parent, delete=False, newline="\n"
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def serialize_json(report: Mapping[str, object]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_outputs(
    report: Mapping[str, object], *, json_output: Path | None, markdown_output: Path | None
) -> None:
    if json_output is not None:
        _write_text_atomic(json_output, serialize_json(report))
    if markdown_output is not None:
        _write_text_atomic(markdown_output, render_markdown(report) + "\n")
