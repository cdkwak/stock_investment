from __future__ import annotations

from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.audit.global_artifact_manifest import (
    CONTRACTS,
    PROVENANCE_STATUS,
    GlobalArtifactAuditError,
    build_dataset_audit,
    build_global_artifact_audits,
    upgrade_global_artifact_audit_states,
    write_audit_state,
)
from stock_data.contracts.global_market import (
    FRED_DEXJPUS_IDENTITY,
    FRED_TREASURY_YIELD_DAILY,
    FRED_USD_FX_DAILY,
    GLOBAL_INDEX_PRICE_DAILY,
)
from stock_data.storage.contract_arrow import contract_arrow_schema
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.global_market import validate_fred, validate_global_index


def _global_frame(close: float = 105.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2025-12-31", "symbol": "SP500", "source_ticker": "^GSPC",
                "open": 100.0, "high": 110.0, "low": 90.0, "close": close,
                "volume": 1000,
            },
            {
                "date": "2026-01-02", "symbol": "SP500", "source_ticker": "^GSPC",
                "open": 101.0, "high": 111.0, "low": 91.0, "close": 106.0,
                "volume": None,
            },
        ]
    )


def _fred_frame(contract) -> pd.DataFrame:
    columns = contract.column_names[1:]
    return pd.DataFrame(
        [
            {"date": "2025-12-31", **{column: float(index + 1) for index, column in enumerate(columns)}},
            {"date": "2026-01-02", **{column: None for column in columns}},
        ]
    )


def _write_all(root: Path) -> None:
    write_dataset_atomic(
        _global_frame(), root / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name,
        GLOBAL_INDEX_PRICE_DAILY, validate_global_index,
    )
    for contract in (FRED_TREASURY_YIELD_DAILY, FRED_USD_FX_DAILY):
        write_dataset_atomic(
            _fred_frame(contract), root / "data/normalized" / contract.name,
            contract, validate_fred,
        )


def test_fred_dexjpus_contract_preserves_official_quote_direction() -> None:
    identity = FRED_DEXJPUS_IDENTITY
    assert identity.series_id == "DEXJPUS"
    assert identity.column == "dexjpus"
    assert identity.display_pair == "USD/JPY"
    assert identity.official_unit == "Japanese Yen to One U.S. Dollar"
    assert identity.frequency == "Daily"
    assert identity.seasonal_adjustment == "Not Seasonally Adjusted"
    assert identity.release == "H.10 Foreign Exchange Rates"
    assert identity.quote_convention == "JPY_PER_USD"
    assert identity.display_scale == 1.0
    column = next(value for value in FRED_USD_FX_DAILY.columns if value.name == "dexjpus")
    assert column.unit == identity.official_unit
    assert "without reciprocal or 100-unit scaling" in column.description


def test_manifest_is_exact_deterministic_and_explicitly_provenance_limited(tmp_path: Path):
    _write_all(tmp_path)
    first = build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)
    second = build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)
    assert first == second
    assert first["classification"] == PROVENANCE_STATUS
    assert first["source_provenance"] == {
        "status": PROVENANCE_STATUS,
        "retained_lossless_landing": False,
        "retained_call_ledger": False,
        "source_provenance_reconstructed": False,
        "statement": (
            "This audit proves only the current local Normalized artifact; "
            "it does not prove or recreate provider-response provenance."
        ),
    }
    assert first["scope"]["network_calls"] == 0
    assert first["scope"]["landing_files_read"] == 0
    assert first["summary"]["validation_status"] == "PASS"
    assert first["summary"]["rows"] == 2
    assert first["summary"]["coverage"] == {
        "column": "date", "minimum": "2025-12-31", "maximum": "2026-01-02",
        "null_count": 0,
    }
    assert len(first["file_manifest"]) == 2
    for entry in first["file_manifest"]:
        path = tmp_path / entry["path"]
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["rows"] == 1 and entry["bytes"] == path.stat().st_size
    assert first["checks"]["primary_key"]["status"] == "PASS"
    assert first["checks"]["nulls"]["counts"]["volume"] == 1
    assert first["checks"]["infinity"]["total"] == 0
    assert first["contract"]["expected_arrow_schema"]["fields"] == [
        {"name": field.name, "dtype": str(field.type), "nullable": field.nullable}
        for field in contract_arrow_schema(GLOBAL_INDEX_PRICE_DAILY)
    ]


def test_exact_pk_infinity_and_unexpected_file_failures_are_recorded(tmp_path: Path):
    contract = FRED_TREASURY_YIELD_DAILY
    root = tmp_path / "data/normalized" / contract.name
    path = root / "year=2025/data.parquet"
    path.parent.mkdir(parents=True)
    table = pa.Table.from_pylist(
        [
            {"date": date(2025, 1, 2), "dgs2": 1.0, "dgs10": None, "dgs30": 3.0},
            {"date": date(2025, 1, 2), "dgs2": float("inf"), "dgs10": 2.0, "dgs30": 3.0},
        ],
        schema=contract_arrow_schema(contract),
    )
    pq.write_table(table, path)
    (root / "unexpected.txt").write_text("retained", encoding="utf-8")
    report = build_dataset_audit(tmp_path, contract.name)
    assert report["summary"]["validation_status"] == "FAIL"
    assert report["checks"]["primary_key"]["duplicate_rows_after_first"] == 1
    assert report["checks"]["infinity"]["counts"]["dgs2"] == 1
    assert report["checks"]["unexpected_files"]["status"] == "FAIL"
    assert report["checks"]["unexpected_files"]["files"][0]["sha256"] == hashlib.sha256(
        b"retained"
    ).hexdigest()


def test_content_addressed_state_is_atomic_immutable_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, FRED_USD_FX_DAILY.name)
    first = write_audit_state(tmp_path, report)
    target = tmp_path / first["path"]
    original = target.read_bytes()
    timestamp = target.stat().st_mtime_ns
    second = write_audit_state(tmp_path, report)
    assert second["status"] == "ALREADY_RECORDED"
    assert target.read_bytes() == original and target.stat().st_mtime_ns == timestamp

    target.write_bytes(b"tampered\n")
    with pytest.raises(GlobalArtifactAuditError, match="immutable audit state differs"):
        write_audit_state(tmp_path, report)
    assert target.read_bytes() == b"tampered\n"

    other = build_dataset_audit(tmp_path, FRED_TREASURY_YIELD_DAILY.name)
    original_link = __import__("os").link
    monkeypatch.setattr("stock_data.audit.global_artifact_manifest.os.link",
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError, match="injected"):
        write_audit_state(tmp_path, other)
    parent = tmp_path / "data/state/audits/global_normalized_artifacts" / other["dataset"]
    assert not list(parent.glob("*.json"))
    assert not list(parent.glob(".*.tmp"))
    monkeypatch.setattr("stock_data.audit.global_artifact_manifest.os.link", original_link)


def test_changed_artifact_creates_new_state_without_replacing_prior(tmp_path: Path):
    _write_all(tmp_path)
    first_report = build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)
    first = write_audit_state(tmp_path, first_report)
    first_path = tmp_path / first["path"]
    first_bytes = first_path.read_bytes()
    write_dataset_atomic(
        _global_frame(close=104.0),
        tmp_path / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name,
        GLOBAL_INDEX_PRICE_DAILY, validate_global_index,
    )
    second_report = build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)
    second = write_audit_state(tmp_path, second_report)
    assert second["status"] == "CREATED"
    assert second["path"] != first["path"]
    assert first_path.read_bytes() == first_bytes
    assert len(list(first_path.parent.glob("*.json"))) == 2


def test_state_writer_refuses_reconstructed_provenance_claim(tmp_path: Path):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, FRED_TREASURY_YIELD_DAILY.name)
    report["source_provenance"]["retained_lossless_landing"] = True
    report.pop("audit_manifest_sha256")
    canonical = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    report["audit_manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    with pytest.raises(GlobalArtifactAuditError, match="provenance boundary differs"):
        write_audit_state(tmp_path, report)
    assert not (tmp_path / "data/state/audits").exists()


def test_cli_writes_all_three_separate_states(tmp_path: Path, capsys):
    _write_all(tmp_path)
    script = Path(__file__).resolve().parents[3] / "scripts/manual/audit/audit_global_normalized_artifacts.py"
    spec = importlib.util.spec_from_file_location("audit_global_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--project-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [value["dataset"] for value in result] == sorted(CONTRACTS)
    assert {value["status"] for value in result} == {"CREATED"}
    assert len(list((tmp_path / "data/state/audits/global_normalized_artifacts").rglob("*.json"))) == 3


def test_build_all_has_stable_dataset_order(tmp_path: Path):
    _write_all(tmp_path)
    checkpoint = tmp_path / "data/state/global_index_price_daily.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b'{"dataset":"global_index_price_daily","status":"legacy"}\n')
    checkpoint_before = checkpoint.read_bytes()
    reports = build_global_artifact_audits(tmp_path)
    assert [report["dataset"] for report in reports] == sorted(CONTRACTS)
    writes = upgrade_global_artifact_audit_states(tmp_path)
    assert [result["dataset"] for result in writes] == sorted(CONTRACTS)
    assert checkpoint.read_bytes() == checkpoint_before
    assert all("data/state/audits/global_normalized_artifacts/" in result["path"]
               for result in writes)


def test_mutation_during_scan_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_all(tmp_path)
    dataset_root = tmp_path / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name
    victim = dataset_root / "symbol=SP500/year=2025/data.parquet"
    original_read = pd.read_parquet
    calls = {"count": 0}

    def mutate_after_read(path, *args, **kwargs):
        result = original_read(path, *args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            victim.write_bytes(victim.read_bytes() + b"changed-during-scan")
        return result

    monkeypatch.setattr("stock_data.audit.global_artifact_manifest.pd.read_parquet",
                        mutate_after_read)
    with pytest.raises(GlobalArtifactAuditError, match="changed during audit"):
        build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)


def test_mutation_between_build_and_write_is_rejected_without_state(tmp_path: Path):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)
    write_dataset_atomic(
        _global_frame(close=104.0),
        tmp_path / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name,
        GLOBAL_INDEX_PRICE_DAILY, validate_global_index,
    )
    with pytest.raises(GlobalArtifactAuditError, match="independent rebuild"):
        write_audit_state(tmp_path, report)
    assert not (tmp_path / "data/state/audits").exists()


def test_mutation_after_writer_rebuild_fails_immediate_creation_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, GLOBAL_INDEX_PRICE_DAILY.name)
    module = __import__("stock_data.audit.global_artifact_manifest", fromlist=["build_dataset_audit"])
    original_build = module.build_dataset_audit
    victim = tmp_path / "data/normalized/global_index_price_daily/symbol=SP500/year=2025/data.parquet"

    def rebuild_then_mutate(*args, **kwargs):
        rebuilt = original_build(*args, **kwargs)
        victim.write_bytes(victim.read_bytes() + b"changed-after-rebuild")
        return rebuilt

    monkeypatch.setattr(module, "build_dataset_audit", rebuild_then_mutate)
    with pytest.raises(GlobalArtifactAuditError, match="before state creation|state creation gate"):
        write_audit_state(tmp_path, report)
    assert not list((tmp_path / "data/state/audits").rglob("*.json"))


def test_forged_pass_and_manifest_are_independently_rejected(tmp_path: Path):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, FRED_TREASURY_YIELD_DAILY.name)
    report["summary"]["rows"] = 999
    report["summary"]["validation_status"] = "PASS"
    report["file_manifest"][0]["sha256"] = "0" * 64
    report.pop("audit_manifest_sha256")
    report["audit_manifest_sha256"] = hashlib.sha256(
        (json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        .encode("utf-8")
    ).hexdigest()
    with pytest.raises(GlobalArtifactAuditError, match="independent rebuild"):
        write_audit_state(tmp_path, report)
    assert not (tmp_path / "data/state/audits").exists()


def test_state_parent_directory_redirect_is_rejected(tmp_path: Path):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, FRED_USD_FX_DAILY.name)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = tmp_path / "data/state/audits"
    redirected.parent.mkdir(parents=True)
    try:
        redirected.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(GlobalArtifactAuditError, match="redirected path component"):
        write_audit_state(tmp_path, report)
    assert not list(outside.rglob("*.json"))


def test_schema_order_year_null_and_domain_failures_are_explicit(tmp_path: Path):
    contract = GLOBAL_INDEX_PRICE_DAILY
    root = tmp_path / "data/normalized" / contract.name
    path = root / "symbol=SP500/year=2025/data.parquet"
    path.parent.mkdir(parents=True)
    wrong_order = pa.schema([
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("date", pa.date32(), nullable=False),
        *list(contract_arrow_schema(contract))[2:],
    ])
    row = {
        "date": date(2025, 1, 2), "symbol": "SP500", "source_ticker": "^GSPC",
        "open": 100.0, "high": 90.0, "low": 95.0, "close": 105.0, "volume": 1,
    }
    pq.write_table(pa.Table.from_pylist([row], schema=wrong_order), path)
    schema_report = build_dataset_audit(tmp_path, contract.name)
    assert schema_report["checks"]["contract_schema_types_and_order"]["status"] == "FAIL"
    assert schema_report["checks"]["primary_key"]["status"] == "NOT_RUN_SCHEMA_MISMATCH"

    shutil_root = root.parent / "saved"
    root.replace(shutil_root)
    path = root / "symbol=SP500/year=2024/data.parquet"
    path.parent.mkdir(parents=True)
    nullable_schema = pa.schema([
        pa.field(field.name, field.type, nullable=True)
        for field in contract_arrow_schema(contract)
    ])
    null_bad = dict(row, date=None, high=110.0, low=90.0)
    pq.write_table(pa.Table.from_pylist([null_bad], schema=nullable_schema), path)
    value_report = build_dataset_audit(tmp_path, contract.name)
    assert value_report["checks"]["partition_rows"]["status"] == "FAIL"
    assert value_report["checks"]["nulls"]["non_nullable_violations"]["date"] == 1
    assert value_report["checks"]["domain_validation"]["status"] == "PASS"

    path.unlink()
    wrong_year = dict(row, date=date(2025, 1, 2), high=90.0, low=95.0)
    pq.write_table(pa.Table.from_pylist([wrong_year], schema=contract_arrow_schema(contract)), path)
    year_domain = build_dataset_audit(tmp_path, contract.name)
    assert year_domain["checks"]["partition_rows"]["status"] == "FAIL"
    assert year_domain["checks"]["domain_validation"]["status"] == "FAIL"


def test_reparse_state_component_is_rejected_even_without_symlink_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _write_all(tmp_path)
    report = build_dataset_audit(tmp_path, FRED_USD_FX_DAILY.name)
    redirected = tmp_path / "data/state/audits"
    redirected.mkdir(parents=True)
    original = __import__("stock_data.audit.global_artifact_manifest", fromlist=["_is_redirect"])
    original_check = original._is_redirect
    monkeypatch.setattr(
        "stock_data.audit.global_artifact_manifest._is_redirect",
        lambda path: path.absolute() == redirected.absolute() or original_check(path),
    )
    with pytest.raises(GlobalArtifactAuditError, match="redirected path component"):
        write_audit_state(tmp_path, report)
    assert not list(redirected.rglob("*.json"))
