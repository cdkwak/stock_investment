import json
from pathlib import Path

from scripts.manual.ls_derivatives_investor_followup import call_specs, response_shape, validate_reused_k2i_f


def test_call_specs_are_bounded_unique_and_cover_products_history_range_holiday():
    specs = call_specs()
    assert len(specs) == 13
    assert len({label for label, _, _ in specs}) == 13
    assert [(request["bsc_asts_id"], request["fot_clsf_cd"]) for _, request, phase in specs if phase == "product"] == [
        ("K2I", "C"), ("K2I", "P"), ("MKI", "F"), ("MKI", "C"), ("MKI", "P")
    ]
    assert sum(phase == "history" for _, _, phase in specs) == 5
    assert sum(phase == "range" for _, _, phase in specs) == 1
    assert sum(phase == "holiday" for _, _, phase in specs) == 1


def test_response_shape_requires_exact_echo_and_rows():
    request = {
        "tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I",
        "gubun2": "1", "gubun3": "1", "from_date": "20260814", "to_date": "20260814",
    }
    payload = {
        "rsp_cd": "00000",
        "t8462OutBlock": {"tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I"},
        "t8462OutBlock1": [{"date": "20260814", "sv_08": 1}],
    }
    assert response_shape(payload, request) == (True, 1, None)
    payload["t8462OutBlock"]["tm_rng"] = "N"
    assert response_shape(payload, request)[2] == "request_echo_anomaly"


def test_validate_reused_evidence_checks_request_response_and_date(tmp_path: Path):
    run_id = "retained"
    run = tmp_path / "data/landing/diagnostics/ls_derivatives_investor_pilot" / run_id
    run.mkdir(parents=True)
    response = {
        "rsp_cd": "00000",
        "t8462OutBlock": {"tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I"},
        "t8462OutBlock1": [{"date": "20260814"}],
    }
    (run / "20260814_k2i_f_d.json").write_text(json.dumps(response), encoding="utf-8")
    request = {
        "tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I",
        "gubun2": "1", "gubun3": "1", "from_date": "20260814", "to_date": "20260814",
    }
    ledger = {"tr_code": "t8462", "request": {"t8462InBlock": request}, "response_body_sha256": "abc"}
    (run / "call_ledger.jsonl").write_text(json.dumps(ledger) + "\n", encoding="utf-8")
    assert validate_reused_k2i_f(tmp_path, run_id)["row_count"] == 1
