from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from stock_data.contracts.data_v1 import KR_EQUITY_RIGHTS_SCHEDULE
from stock_data.providers.data_go_kr.rights_observation import (
    DATASET,
    STATUS,
    RightsObservationError,
    _tree_hash,
    promote_rights_diagnostic,
)
from stock_data.storage.contract_arrow import contract_arrow_schema


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diagnostic(root: Path, *, run: str, issuer: str, day: str) -> Path:
    diagnostic = root / "data/landing/diagnostics/b002_p1_rights" / run
    diagnostic.mkdir(parents=True)
    item = {
        "basDt": day,
        "issuCmpyKsdCustNo": issuer,
        "crno": "1101110215578",
        "stckIssuCmpyNm": "fixture",
        "scrsIssuMnbdCd": issuer.zfill(5),
        "scrsIssuMnbdCdNm": "fixture",
        "stckIssuRcd": "001",
        "stckIssuRcdNm": "regular",
        "rgtExertRcd": "01",
        "rgtExertRcdNm": "record-date",
        "rgtExertSttgDt": day,
        "rgtExertEdDt": day,
        "nmlsLckSttgDt": day,
        "nmlsLckEdDt": day,
        "trsnmDptyDcd": "02",
        "trsnmDptyDcdNm": "agent",
        "stckParPrc": "500",
        "stckStacMd": "1231",
    }
    body = _bytes({
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {"items": {"item": [item]}, "numOfRows": 1,
                     "pageNo": 1, "totalCount": 12},
        }
    })
    body_sha = hashlib.sha256(body).hexdigest()
    envelope = {
        "task_id": "B002-P1", "run_id": run, "http_status": 200,
        "response_body_encoding": "base64",
        "response_body_base64": base64.b64encode(body).decode("ascii"),
        "response_body_bytes": len(body), "response_body_sha256": body_sha,
    }
    envelope_path = diagnostic / "response_envelope.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    ledger = {
        "task_id": "B002-P1", "run_id": run,
        "classification": "SOURCE_USABLE",
        "authorized_operation": "GetStocRighScheService_V2/getRighExerReasSche_V2",
        "endpoint": "https://apis.data.go.kr/1160100/GetStocRighScheService_V2/getRighExerReasSche_V2",
        "http_status": 200, "request_count": 1, "retries": 0,
        "json_parseable": True, "result_code": "00", "transport_error_type": None,
        "response_body_bytes": len(body), "response_body_sha256": body_sha,
        "service_key_or_prepared_query_stored": False,
        "returned_item_count": 1, "total_count": 12,
        "request": {"basDt": day, "issuCmpyKsdCustNo": issuer,
                    "numOfRows": 1, "pageNo": 1, "resultType": "json"},
    }
    ledger_path = diagnostic / "call_ledger.redacted.json"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    handoff = {
        "task_id": "B002-P1", "run_id": run, "classification": "SOURCE_USABLE",
        "request_count": 1, "retries": 0,
        "envelope_path": envelope_path.relative_to(root).as_posix(),
        "ledger_path": ledger_path.relative_to(root).as_posix(),
        "envelope_sha256": _sha(envelope_path), "ledger_sha256": _sha(ledger_path),
        "response_body_sha256": body_sha,
    }
    (diagnostic / "handoff_manifest.json").write_text(
        json.dumps(handoff), encoding="utf-8"
    )
    return diagnostic


def _artifact_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted((root / "data/normalized" / DATASET).rglob("*"))
        if path.is_file()
    }


def test_promotes_exact_partial_observation_and_is_idempotent(tmp_path: Path) -> None:
    diagnostic = _diagnostic(
        tmp_path, run="b002_p1_fixture_1", issuer="1115", day="20191231"
    )
    result = promote_rights_diagnostic(
        project_root=tmp_path, diagnostic_root=diagnostic
    )
    assert result["status"] == STATUS
    assert result["row_count"] == 1
    state_path = tmp_path / "data/state/kr_equity_rights_schedule_observation.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == STATUS
    assert state["historical_completeness"] is False
    assert state["canonical_economic_event_identity"] is False
    assert state["api_calls"] == 0
    assert state["snapshots"][0]["declared_total_count"] == 12
    assert state["snapshots"][0]["returned_item_count"] == 1
    output = tmp_path / f"data/normalized/{DATASET}/year=2019/data.parquet"
    assert pq.ParquetFile(output).schema_arrow.equals(
        contract_arrow_schema(KR_EQUITY_RIGHTS_SCHEDULE), check_metadata=False
    )

    original_state = state_path.read_bytes()
    original_artifact = _artifact_bytes(tmp_path)
    repeated = promote_rights_diagnostic(
        project_root=tmp_path, diagnostic_root=diagnostic
    )
    assert repeated["status"] == "ALREADY_RECORDED"
    assert state_path.read_bytes() == original_state
    assert _artifact_bytes(tmp_path) == original_artifact


def test_fails_closed_on_hash_chain_or_partial_semantics_change(tmp_path: Path) -> None:
    diagnostic = _diagnostic(
        tmp_path, run="b002_p1_fixture_1", issuer="1115", day="20191231"
    )
    ledger_path = diagnostic / "call_ledger.redacted.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["retries"] = 1
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(RightsObservationError, match="handoff manifest authenticity"):
        promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=diagnostic)
    assert not (tmp_path / "data/normalized" / DATASET).exists()
    assert not (tmp_path / "data/state/kr_equity_rights_schedule_observation.json").exists()


def test_append_failure_restores_existing_dataset_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _diagnostic(
        tmp_path, run="b002_p1_fixture_1", issuer="1115", day="20191231"
    )
    promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=first)
    state_path = tmp_path / "data/state/kr_equity_rights_schedule_observation.json"
    original_state = state_path.read_bytes()
    original_artifact = _artifact_bytes(tmp_path)
    second = _diagnostic(
        tmp_path, run="b002_p1_fixture_2", issuer="2222", day="20200102"
    )
    original_replace = Path.replace

    def fail_state_promotion(path: Path, target: Path):
        if path.name.startswith("." + state_path.name + ".stage.") and Path(target) == state_path:
            raise OSError("injected state promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_state_promotion)
    with pytest.raises(OSError, match="injected"):
        promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=second)
    assert state_path.read_bytes() == original_state
    assert _artifact_bytes(tmp_path) == original_artifact
    assert not list((tmp_path / "data").rglob("*.rights-observation.*"))


def test_diagnostic_path_must_remain_inside_project(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-rights-diagnostic"
    outside.mkdir(exist_ok=True)
    with pytest.raises(RightsObservationError, match="escapes project root"):
        promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=outside)


def _interrupted_promoted_layout(root: Path) -> tuple[Path, dict[str, Path]]:
    old_project = root / "old"
    new_project = root / "new"
    first_old = _diagnostic(
        old_project, run="b002_p1_fixture_old", issuer="1115", day="20191231"
    )
    promote_rights_diagnostic(project_root=old_project, diagnostic_root=first_old)
    first_new = _diagnostic(
        new_project, run="b002_p1_fixture_old", issuer="1115", day="20191231"
    )
    promote_rights_diagnostic(project_root=new_project, diagnostic_root=first_new)
    second_new = _diagnostic(
        new_project, run="b002_p1_fixture_new", issuer="2222", day="20200102"
    )
    promote_rights_diagnostic(project_root=new_project, diagnostic_root=second_new)

    project = root / "interrupted"
    logical = Path("data/normalized") / DATASET
    canonical = project / logical
    backup = canonical.parent / f".{DATASET}.rights-observation.backup.{'1' * 32}"
    state = project / "data/state/kr_equity_rights_schedule_observation.json"
    state_stage = state.parent / f".{state.name}.stage.{'1' * 32}"
    marker = canonical.parent / f".{DATASET}.rights-observation.transaction.json"
    shutil.copytree(new_project / logical, canonical)
    shutil.copytree(old_project / logical, backup)
    state.parent.mkdir(parents=True)
    shutil.copy2(old_project / "data/state/kr_equity_rights_schedule_observation.json", state)
    shutil.copy2(new_project / "data/state/kr_equity_rights_schedule_observation.json", state_stage)
    payload = {
        "dataset": DATASET, "transaction_id": "1" * 32,
        "phase": "DATASET_PROMOTED", "had_pair": True,
        "dataset_parent": str(canonical.parent.resolve()),
        "state_parent": str(state.parent.resolve()),
        "old_dataset_sha256": _tree_hash(backup, logical),
        "old_state_sha256": _sha(state),
        "new_dataset_sha256": _tree_hash(canonical, logical),
        "new_state_sha256": _sha(state_stage),
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    diagnostic = _diagnostic(
        project, run="b002_p1_fixture_probe", issuer="3333", day="20210104"
    )
    return diagnostic, {
        "canonical": canonical, "backup": backup, "state": state,
        "state_stage": state_stage, "marker": marker,
    }


def _all_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_startup_recovers_exact_interrupted_promotion(tmp_path: Path) -> None:
    diagnostic, paths = _interrupted_promoted_layout(tmp_path)
    result = promote_rights_diagnostic(
        project_root=paths["canonical"].parents[2], diagnostic_root=diagnostic
    )
    assert result["startup_recovery"] == "ROLLED_BACK"
    assert result["status"] == STATUS


@pytest.mark.parametrize("target", ["canonical", "backup", "state", "state_stage"])
def test_recovery_tamper_fails_before_any_mutation(
    tmp_path: Path, target: str
) -> None:
    diagnostic, paths = _interrupted_promoted_layout(tmp_path)
    path = paths[target]
    if path.is_dir():
        next(path.rglob("data.parquet")).write_bytes(b"corrupt")
    else:
        path.write_bytes(b"corrupt")
    project = paths["canonical"].parents[2]
    before = _all_bytes(project)
    with pytest.raises(RightsObservationError, match="fingerprint|cannot be fingerprinted"):
        promote_rights_diagnostic(project_root=project, diagnostic_root=diagnostic)
    assert _all_bytes(project) == before


def test_existing_state_semantics_are_exact(tmp_path: Path) -> None:
    diagnostic = _diagnostic(
        tmp_path, run="b002_p1_fixture_1", issuer="1115", day="20191231"
    )
    promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=diagnostic)
    state_path = tmp_path / "data/state/kr_equity_rights_schedule_observation.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["historical_completeness"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = _all_bytes(tmp_path)
    with pytest.raises(RightsObservationError, match="state does not describe"):
        promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=diagnostic)
    assert _all_bytes(tmp_path) == before
