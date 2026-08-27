import pytest
import json
import hashlib

from scripts.manual.pilot.pilot_ls_source_inventory import (
    MAX_DATA_CALLS, _output_rows, _plan_digest, _row_dates, _select_current_future, _verified_resume, frozen_scopes,
)


def test_frozen_plan_is_exact_bounded_read_only_scope():
    plan = frozen_scopes()
    assert len(plan) == MAX_DATA_CALLS == 11
    assert {item["tr_code"] for item in plan} == {"t1633", "t8428", "t8467", "t2214", "t2424", "t1903", "t1716", "t3320"}
    assert all(item["endpoint"].startswith(("/stock/", "/futureoption/")) for item in plan)
    assert all("account" not in item["endpoint"] and "order" not in item["endpoint"] for item in plan)
    assert len(_plan_digest(plan)) == 64
    assert _plan_digest(plan) == _plan_digest(frozen_scopes())


def test_current_future_selection_rejects_missing_master_and_skips_spreads():
    payload = {"t8467OutBlock": [
        {"hname": "F SP 06-2609", "shcode": "D016669S"},
        {"hname": "F 2609", "shcode": "A0169000"},
    ]}
    assert _select_current_future(payload) == "A0169000"
    with pytest.raises(ValueError):
        _select_current_future({"t8467OutBlock": []})


def test_row_dates_preserve_only_source_reported_dates():
    assert _row_dates([{"date": "20260814"}, {"dt": "20260813153000"}, {"value": 1}]) == ["20260813", "20260814"]
    assert _row_dates({"date": "20260814"}) == []


def test_source_success_without_output_block_is_valid_empty_only_for_exact_envelope():
    rows, valid_empty = _output_rows({"rsp_cd": "00000", "rsp_msg": "no data"}, "t1633OutBlock1")
    assert rows == [] and valid_empty
    rows, valid_empty = _output_rows({"rsp_cd": "00000", "unexpected": 1}, "t1633OutBlock1")
    assert rows is None and not valid_empty


def test_resume_revalidates_exact_stopped_evidence(tmp_path):
    plan = frozen_scopes()
    digest = _plan_digest(plan)
    run = tmp_path / "data" / "landing" / "diagnostics" / "ls_openapi_source_inventory" / "run1"
    run.mkdir(parents=True)
    raw = b'{"rsp_cd":"00000"}'
    (run / "01_program_kospi_20260814.response.json").write_bytes(raw)
    (run / "01_program_kospi_20260814.provenance.json").write_text(json.dumps({"scope_id": plan[0]["id"], "plan_sha256": digest, "raw_response_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    checkpoint = {"run_id": "run1", "status": "PILOT_STOPPED", "secret_scan": "PASS", "plan": plan, "plan_sha256": digest, "data_calls": 1, "results": [{"scope_id": plan[0]["id"]}]}
    (run / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    results, source = _verified_resume(tmp_path, run, plan, digest)
    assert source == "run1" and len(results) == 1
    (run / "01_program_kospi_20260814.response.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw hash"):
        _verified_resume(tmp_path, run, plan, digest)
