from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import stock_data.audit.dataset_inventory as inventory_module
from stock_data.audit.dataset_inventory import (
    REPORT_SCHEMA,
    build_inventory,
    render_markdown,
    serialize_json,
    write_immutable_snapshot,
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
            "sha256": artifact["file_manifest"][0]["sha256"],
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


def test_landing_summary_is_bound_to_path_and_bytes_without_body_hash(tmp_path):
    landing = tmp_path / "data/landing/provider/call.json"
    landing.parent.mkdir(parents=True)
    landing.write_bytes(b"opaque source body")
    report = build_inventory(tmp_path, contracts={})
    assert report["landing_summary"]["body_read"] is False
    assert report["landing_summary"]["metadata_manifest"] == [
        {"path": "data/landing/provider/call.json", "bytes": 18}
    ]
    assert "sha256" not in report["landing_summary"]["metadata_manifest"][0]


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


def test_state_aliases_and_nested_immutable_audits_avoid_false_missing_state(tmp_path):
    contract = _contract("kr_short_selling_balance_daily")
    _write(tmp_path / "data/normalized/kr_short_selling_balance_daily", _rows())
    state_root = tmp_path / "data/state"
    state_root.mkdir(parents=True)
    (state_root / "kr_short_selling_balance_daily_v2.json").write_text(
        json.dumps({"dataset": "balance", "status": "complete"}), encoding="utf-8"
    )
    nested = state_root / "audits/global/immutable.json"
    nested.parent.mkdir(parents=True)
    nested.write_text(
        json.dumps({"audit_schema": "fixture", "dataset": "kr_short_selling_balance_daily"}),
        encoding="utf-8",
    )
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    artifact = report["artifacts"][0]
    assert artifact["state_presence"] == "PRESENT"
    assert [state["state_kind"] for state in artifact["states"]] == ["IMMUTABLE_AUDIT", "OPERATIONAL"]
    assert report["summary"]["state_files"] == 2


def test_registered_nested_root_alias_is_confirmed(tmp_path):
    contract = _contract("kr_kospi200_futures_provider_bridge_daily")
    root = tmp_path / "data/published/c007_kospi200_derivatives_bridge/kr_kospi200_futures_provider_bridge_daily"
    _write(root, _rows())
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    artifact = report["artifacts"][0]
    assert artifact["dataset"] == contract.name
    assert artifact["dataset_association"] == "REGISTERED_LAYER_ROOT_ALIAS"
    assert artifact["registration"] == "REGISTERED"


def test_immutable_snapshot_is_content_addressed_idempotent_and_rejects_forgery(tmp_path):
    contract = _contract()
    _write(tmp_path / "data/normalized/registered", _rows())
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    first = write_immutable_snapshot(
        tmp_path, report, contracts={contract.name: contract}, max_key_rows=10
    )
    second = write_immutable_snapshot(
        tmp_path, report, contracts={contract.name: contract}, max_key_rows=10
    )
    assert first["status"] == "CREATED"
    assert second["status"] == "ALREADY_RECORDED"
    assert Path(tmp_path, first["path"]).read_text("utf-8") == serialize_json(report)
    forged = dict(report)
    forged["classification"] = "FORGED"
    try:
        write_immutable_snapshot(
            tmp_path, forged, contracts={contract.name: contract}, max_key_rows=10
        )
    except ValueError as error:
        assert "digest differs" in str(error)
    else:
        raise AssertionError("forged report was accepted")


def test_input_tree_mutation_during_scan_is_rejected(tmp_path, monkeypatch):
    contract = _contract()
    path = _write(tmp_path / "data/normalized/registered", _rows())
    original = inventory_module._artifact_record
    changed = False

    def changing(**kwargs):
        nonlocal changed
        result = original(**kwargs)
        if not changed:
            changed = True
            path.write_bytes(path.read_bytes() + b"mutation")
        return result

    monkeypatch.setattr(inventory_module, "_artifact_record", changing)
    with pytest.raises(RuntimeError, match="inputs changed during scan"):
        build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)


def test_landing_mutation_during_summary_is_rejected(tmp_path, monkeypatch):
    landing = tmp_path / "data/landing/provider/source.json"
    landing.parent.mkdir(parents=True)
    landing.write_bytes(b"one")
    original = inventory_module._landing_summary

    def changing(root):
        result = original(root)
        landing.write_bytes(b"changed-size")
        return result

    monkeypatch.setattr(inventory_module, "_landing_summary", changing)
    with pytest.raises(RuntimeError, match="inputs changed during scan"):
        build_inventory(tmp_path, contracts={})


def test_landing_mutation_before_publication_rejects_stale_snapshot(tmp_path, monkeypatch):
    contract = _contract()
    _write(tmp_path / "data/normalized/registered", _rows())
    landing = tmp_path / "data/landing/provider/source.json"
    landing.parent.mkdir(parents=True)
    landing.write_bytes(b"one")
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    original = inventory_module.build_inventory
    calls = 0

    def changing(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            landing.write_bytes(b"changed-before-publication")
        return original(*args, **kwargs)

    monkeypatch.setattr(inventory_module, "build_inventory", changing)
    with pytest.raises(RuntimeError, match="inputs changed before snapshot publication"):
        write_immutable_snapshot(
            tmp_path, report, contracts={contract.name: contract}, max_key_rows=10
        )
    snapshot_root = tmp_path / inventory_module.IMMUTABLE_SNAPSHOT_RELATIVE
    assert not list(snapshot_root.glob("*.json"))


def test_existing_reparse_snapshot_target_is_rejected(tmp_path, monkeypatch):
    contract = _contract()
    _write(tmp_path / "data/normalized/registered", _rows())
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    root = tmp_path / inventory_module.IMMUTABLE_SNAPSHOT_RELATIVE
    root.mkdir(parents=True)
    target = root / f"{report['inventory_sha256']}.json"
    target.write_text(serialize_json(report), encoding="utf-8")
    original = inventory_module._is_redirect
    monkeypatch.setattr(
        inventory_module, "_is_redirect",
        lambda path: path.absolute() == target.absolute() or original(path),
    )
    with pytest.raises(RuntimeError, match="redirected"):
        write_immutable_snapshot(
            tmp_path, report, contracts={contract.name: contract}, max_key_rows=10
        )


def test_reparse_snapshot_parent_is_rejected(tmp_path, monkeypatch):
    contract = _contract()
    _write(tmp_path / "data/normalized/registered", _rows())
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    audits = tmp_path / "data/state/audits"
    audits.mkdir(parents=True)
    original = inventory_module._is_redirect
    monkeypatch.setattr(
        inventory_module, "_is_redirect",
        lambda path: path.absolute() == audits.absolute() or original(path),
    )
    with pytest.raises(RuntimeError, match="redirected"):
        write_immutable_snapshot(
            tmp_path, report, contracts={contract.name: contract}, max_key_rows=10
        )


def test_existing_snapshot_file_symlink_is_rejected_when_supported(tmp_path):
    contract = _contract()
    _write(tmp_path / "data/normalized/registered", _rows())
    report = build_inventory(tmp_path, contracts={contract.name: contract}, max_key_rows=10)
    root = tmp_path / inventory_module.IMMUTABLE_SNAPSHOT_RELATIVE
    root.mkdir(parents=True)
    backing = tmp_path / "outside.json"
    backing.write_text(serialize_json(report), encoding="utf-8")
    target = root / f"{report['inventory_sha256']}.json"
    try:
        target.symlink_to(backing)
    except OSError:
        pytest.skip("file symlink creation is unavailable")
    with pytest.raises(RuntimeError, match="redirected"):
        write_immutable_snapshot(
            tmp_path, report, contracts={contract.name: contract}, max_key_rows=10
        )
