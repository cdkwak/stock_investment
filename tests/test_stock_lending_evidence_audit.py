from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import stock_data.audit.stock_lending_evidence as audit_module
from stock_data.audit.stock_lending_evidence import (
    EXECUTION_STATUS,
    StockLendingEvidenceAuditError,
    build_stock_lending_evidence_audit,
    write_stock_lending_evidence_state,
)
from stock_data.contracts.data_v1 import (
    KR_STOCK_LENDING_DAILY,
    KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.storage.contract_arrow import dataframe_to_contract_table


CONTRACTS = (
    KR_STOCK_LENDING_DAILY,
    KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)


def _frame(contract, date="2021-04-01"):
    if contract is KR_STOCK_LENDING_DAILY:
        values = [date, "KOSPI", "000001", "sample", 1, 2, 3, 4]
    elif contract is KR_STOCK_LENDING_MARKET_DAILY:
        values = [date, 1, 2, 3, 4]
    else:
        values = [date, "broker", "domestic", 1, 10.0, 2, 20.0]
    return pd.DataFrame([values], columns=contract.column_names)


def _landing(page=1, *, result="00", tag="historical"):
    return [{
        "response": {
            "header": {"resultCode": result, "resultMsg": "NORMAL SERVICE."},
            "body": {
                "numOfRows": 2, "pageNo": page, "totalCount": 1,
                "items": {"item": [{"basDt": "20210401", "tag": tag}]},
            },
        }
    }]


def _state(dataset, completed):
    return {
        "dataset": dataset, "completed_partitions": completed,
        "valid_empty_partitions": [], "failed_partitions": {}, "staged_partitions": [],
    }


def _write_fixture(root: Path):
    for contract in CONTRACTS:
        landing = root / "data/landing/data_go_kr" / contract.name
        history = landing / "historical/20210401_open"
        history.mkdir(parents=True)
        (history / "page=00001.json").write_text(json.dumps(_landing()), encoding="utf-8")
        (landing / "20210401.json").write_text(json.dumps(_landing(tag="incremental")), encoding="utf-8")
        normalized = root / "data/normalized" / contract.name / "year=2021"
        normalized.mkdir(parents=True)
        pq.write_table(dataframe_to_contract_table(_frame(contract), contract), normalized / "data.parquet")
        state_root = root / "data/state"
        state_root.mkdir(parents=True, exist_ok=True)
        (state_root / f"{contract.name}_historical.json").write_text(
            json.dumps(_state(f"{contract.name}_historical", [
                "range:20210401:open", "range:20210401:open:page:00001",
            ])), encoding="utf-8",
        )
        (state_root / f"{contract.name}.json").write_text(
            json.dumps(_state(contract.name, ["20210401"])), encoding="utf-8",
        )


def test_build_and_content_addressed_write_are_deterministic_and_honest(tmp_path):
    _write_fixture(tmp_path)
    first = build_stock_lending_evidence_audit(tmp_path)
    second = build_stock_lending_evidence_audit(tmp_path)
    assert first == second
    assert first["scope"]["network_calls"] == 0
    assert first["scope"]["retained_unique_successful_responses"] == 6
    assert first["execution_accounting"]["status"] == EXECUTION_STATUS
    assert first["execution_accounting"]["exact_total_calls_known"] is False
    assert all(item["normalized"]["historical_source_reconciliation"]["status"] == "PASS" for item in first["datasets"])
    created = write_stock_lending_evidence_state(tmp_path, first)
    existing = write_stock_lending_evidence_state(tmp_path, first)
    assert created["status"] == "CREATED"
    assert existing["status"] == "EXISTS_IDENTICAL"
    assert Path(tmp_path, created["path"]).read_bytes().endswith(b"\n")


def test_checkpoint_page_gap_is_rejected(tmp_path):
    _write_fixture(tmp_path)
    path = tmp_path / "data/state/kr_stock_lending_daily_historical.json"
    state = json.loads(path.read_text("utf-8"))
    state["completed_partitions"][-1] = "range:20210401:open:page:00002"
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(StockLendingEvidenceAuditError, match="checkpoint continuity"):
        build_stock_lending_evidence_audit(tmp_path)


def test_restriction_or_api_error_response_is_rejected(tmp_path):
    _write_fixture(tmp_path)
    path = tmp_path / "data/landing/data_go_kr/kr_stock_lending_market_daily/20210401.json"
    path.write_text(json.dumps(_landing(result="99", tag="restriction")), encoding="utf-8")
    with pytest.raises(StockLendingEvidenceAuditError, match="unsuccessful"):
        build_stock_lending_evidence_audit(tmp_path)


@pytest.mark.parametrize("fault", ["schema", "year", "null", "infinity", "duplicate"])
def test_normalized_contract_pk_null_infinity_and_year_faults_are_rejected(tmp_path, fault):
    _write_fixture(tmp_path)
    contract = KR_STOCK_LENDING_PARTICIPANT_DAILY
    path = tmp_path / f"data/normalized/{contract.name}/year=2021/data.parquet"
    frame = _frame(contract)
    if fault == "schema":
        table = dataframe_to_contract_table(frame, contract).select(list(reversed(contract.column_names)))
    else:
        if fault == "year":
            frame.loc[0, "date"] = "2022-01-01"
        elif fault == "null":
            table = dataframe_to_contract_table(frame, contract)
            index = contract.column_names.index("participant_group")
            table = table.set_column(index, "participant_group", pa.array([None], type=pa.string()))
        elif fault == "infinity":
            frame.loc[0, "lender_ratio"] = float("inf")
        elif fault == "duplicate":
            frame = pd.concat([frame, frame], ignore_index=True)
        if fault != "null":
            table = dataframe_to_contract_table(frame, contract)
    pq.write_table(table, path)
    with pytest.raises(StockLendingEvidenceAuditError):
        build_stock_lending_evidence_audit(tmp_path)


def test_input_mutation_during_scan_is_rejected(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    original = audit_module._audit_dataset
    mutated = False

    def changing(root, name):
        nonlocal mutated
        result = original(root, name)
        if not mutated:
            mutated = True
            path = root / "data/state/kr_stock_lending_daily.json"
            path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        return result

    monkeypatch.setattr(audit_module, "_audit_dataset", changing)
    with pytest.raises(StockLendingEvidenceAuditError, match="changed during scan"):
        build_stock_lending_evidence_audit(tmp_path)


def test_forged_pass_report_is_rejected(tmp_path):
    _write_fixture(tmp_path)
    report = build_stock_lending_evidence_audit(tmp_path)
    report["summary"]["artifact_validation_status"] = "FORGED_PASS"
    unsigned = dict(report)
    unsigned.pop("audit_manifest_sha256")
    report["audit_manifest_sha256"] = audit_module._sha256_bytes(audit_module._canonical_bytes(unsigned))
    with pytest.raises(StockLendingEvidenceAuditError, match="differs from current inputs"):
        write_stock_lending_evidence_state(tmp_path, report)


def test_mutation_between_rebuild_and_state_creation_is_rejected(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    report = build_stock_lending_evidence_audit(tmp_path)
    original = audit_module._input_manifest
    calls = 0

    def changing(root):
        nonlocal calls
        calls += 1
        if calls == 3:
            path = root / "data/state/kr_stock_lending_daily.json"
            path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        return original(root)

    monkeypatch.setattr(audit_module, "_input_manifest", changing)
    with pytest.raises(StockLendingEvidenceAuditError, match="immediately before"):
        write_stock_lending_evidence_state(tmp_path, report)
    assert not list((tmp_path / audit_module.DEFAULT_STATE_RELATIVE).glob("*.json")) if (tmp_path / audit_module.DEFAULT_STATE_RELATIVE).exists() else True


def test_redirected_state_parent_is_rejected(tmp_path, monkeypatch):
    _write_fixture(tmp_path)
    report = build_stock_lending_evidence_audit(tmp_path)
    state_parent = tmp_path / "data/state/audits"
    state_parent.mkdir(parents=True)
    original = audit_module._is_redirect
    monkeypatch.setattr(audit_module, "_is_redirect", lambda path: path.absolute() == state_parent.absolute() or original(path))
    with pytest.raises(StockLendingEvidenceAuditError, match="redirected path component"):
        write_stock_lending_evidence_state(tmp_path, report)
