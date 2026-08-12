from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from stock_data.audit.dataset_inventory import (
    REPORT_SCHEMA,
    build_inventory,
    render_markdown,
    serialize_json,
    write_outputs,
)
from stock_data.contracts.base import ColumnContract, DatasetContract


def _contract(name: str = "registered") -> DatasetContract:
    return DatasetContract(
        name=name,
        version=1,
        status="active",
        description="fixture",
        source="fixture",
        layer="normalized",
        storage_format="parquet",
        frequency="daily",
        timezone="Asia/Seoul",
        primary_key=("date", "symbol"),
        sort_key=("date", "symbol"),
        partition_by=("year",),
        columns=(
            ColumnContract("date", "date32", False),
            ColumnContract("symbol", "string", False),
            ColumnContract("value", "float64", False),
        ),
    )


def _write(root: Path, rows: list[dict], *, schema: pa.Schema | None = None) -> Path:
    path = root / "year=2025" / "data.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


def _rows() -> list[dict]:
    return [
        {"date": date(2025, 1, 2), "symbol": "A", "value": 1.0},
        {"date": date(2025, 1, 3), "symbol": "B", "value": 2.0},
    ]


def test_reports_missing_registered_without_completion_claim(tmp_path):
    report = build_inventory(tmp_path, contracts={"registered": _contract()}, max_key_rows=10)
    assert report["report_schema"] == REPORT_SCHEMA
    assert report["summary"]["missing_registered_artifacts"] == 1
    assert report["missing_registered"][0]["dataset"] == "registered"
    assert "NOT_DATA_COMPLETE_ASSERTION" in report["missing_registered"][0]["classification"]


def test_reports_unregistered_artifact(tmp_path):
    _write(tmp_path / "data/derived/unregistered", _rows())
    report = build_inventory(tmp_path, contracts={})
    assert report["summary"]["unregistered_artifacts"] == 1
    artifact = report["artifacts"][0]
    assert artifact["dataset"] == "unregistered"
    assert artifact["registration"] == "UNREGISTERED"
    assert artifact["file_manifest"] == [
        {
            "path": "data/derived/unregistered/year=2025/data.parquet",
            "partition": "year=2025",
            "rows": 2,
            "bytes": artifact["bytes"],
            "schema_id": artifact["physical_schemas"][0]["schema_id"],
        }
    ]


def test_physical_nullable_is_separate_from_logical_required_value_validation(tmp_path):
    schema = pa.schema(
        [
            pa.field("date", pa.date32(), nullable=True),
            pa.field("symbol", pa.string(), nullable=True),
            pa.field("value", pa.float64(), nullable=True),
        ]
    )
    _write(tmp_path / "data/normalized/registered", _rows(), schema=schema)
    report = build_inventory(tmp_path, contracts={"registered": _contract()}, max_scan_rows=10)
    artifact = report["artifacts"][0]
    assert artifact["contract_schema"]["status"] == "PASS"
    assert artifact["physical_nullability"]["status"] == "MISMATCH"
    assert artifact["nullability"]["status"] == "PASS"
    assert report["summary"]["issue_counts"] == {
        "missing_registered_artifacts": 0,
        "physical_nullability_mismatches": 1,
    }


def test_reports_real_dtype_mismatch_and_required_value_nullability(tmp_path):
    schema = pa.schema(
        [
            pa.field("date", pa.date32(), nullable=False),
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("value", pa.string(), nullable=True),
        ]
    )
    _write(
        tmp_path / "data/normalized/registered",
        [{"date": date(2025, 1, 2), "symbol": "A", "value": None}],
        schema=schema,
    )
    report = build_inventory(tmp_path, contracts={"registered": _contract()}, max_scan_rows=10)
    artifact = report["artifacts"][0]
    assert artifact["contract_schema"]["status"] == "FAIL"
    assert artifact["physical_nullability"]["status"] == "MISMATCH"
    assert artifact["contract_schema"]["mismatches"][0]["dtype_mismatches"] == [
        {"column": "value", "expected": "float64", "actual": "string"}
    ]
    assert artifact["nullability"]["status"] == "FAIL"
    assert artifact["nullability"]["non_nullable_violations"] == {"value": 1}


def test_reports_exact_duplicate_and_infinity(tmp_path):
    rows = _rows() + [dict(_rows()[0], value=float("inf"))]
    _write(tmp_path / "data/normalized/registered", rows)
    report = build_inventory(
        tmp_path,
        contracts={"registered": _contract()},
        max_key_rows=10,
        max_scan_rows=10,
    )
    artifact = report["artifacts"][0]
    assert artifact["primary_key"]["status"] == "FAIL"
    assert artifact["primary_key"]["duplicate_rows_after_first"] == 1
    assert artifact["infinity"]["status"] == "FAIL"
    assert artifact["infinity"]["infinity_count"] == 1


def test_safe_state_link_and_atomic_explicit_outputs(tmp_path):
    _write(tmp_path / "data/normalized/registered", _rows())
    state = tmp_path / "data/state/custom.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "dataset": "registered",
                "status": "complete",
                "completed_partitions": ["a", "b"],
                "token": "must-not-appear",
                "failed_partitions": {"x": "secret error"},
            }
        ),
        encoding="utf-8",
    )
    report = build_inventory(tmp_path, contracts={"registered": _contract()}, max_key_rows=10)
    artifact = report["artifacts"][0]
    assert artifact["state_presence"] == "PRESENT"
    assert artifact["states"][0]["operational_counts"] == {
        "completed_partitions": 2,
        "failed_partitions": 1,
    }
    serialized = serialize_json(report)
    assert "must-not-appear" not in serialized
    assert "secret error" not in serialized
    assert "assign completion status" in render_markdown(report)

    json_path = tmp_path / "reports/inventory.json"
    markdown_path = tmp_path / "reports/inventory.md"
    write_outputs(report, json_output=json_path, markdown_output=markdown_path)
    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    assert markdown_path.read_text(encoding="utf-8").startswith("# Data-layer Inventory")


def test_report_is_deterministic_and_ignores_documented_quarantine(tmp_path):
    _write(tmp_path / "data/normalized/registered", _rows())
    _write(tmp_path / "data/normalized/quarantine/rejected", _rows())
    first = build_inventory(tmp_path, contracts={"registered": _contract()}, max_key_rows=10)
    second = build_inventory(tmp_path, contracts={"registered": _contract()}, max_key_rows=10)
    assert first == second
    assert serialize_json(first) == serialize_json(second)
    assert first["summary"]["artifact_roots"] == 1
    assert len(first["ignored_artifacts"]) == 1
    assert first["ignored_artifacts"][0]["reason"].startswith(
        "documented_ignored_component"
    )


def test_saved_audit_output_is_not_reingested_as_operational_state(tmp_path):
    _write(tmp_path / "data/normalized/registered", _rows())
    operational_state = tmp_path / "data/state/registered.json"
    operational_state.parent.mkdir(parents=True)
    operational_state.write_text(
        json.dumps({"dataset": "registered", "status": "complete"}),
        encoding="utf-8",
    )
    first = build_inventory(tmp_path, contracts={"registered": _contract()}, max_key_rows=10)

    json_path = tmp_path / "data/state/audits/dataset_inventory.json"
    markdown_path = tmp_path / "data/state/audits/dataset_inventory.md"
    write_outputs(first, json_output=json_path, markdown_output=markdown_path)

    second = build_inventory(tmp_path, contracts={"registered": _contract()}, max_key_rows=10)
    assert second == first
    assert [state["path"] for state in second["states"]] == [
        "data/state/registered.json"
    ]
    assert all(not state["path"].startswith("data/state/audits/") for state in second["states"])
