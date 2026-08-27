import json
from pathlib import Path

import pytest

import scripts.manual.pilot.pilot_opendart_free_issue as pilot
from stock_data.providers.opendart_free_issue import (
    OpenDartObservationError, PIFRIC_FIELDS, body_sha256, parse_observations,
    request_matrix,
)


HERE = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
CAPTURED = "2026-08-12T01:02:03+00:00"


def test_request_matrix_is_three_public_keyless_sequential_scopes() -> None:
    matrix = request_matrix("00123456", "20240101", "20240131")
    assert [item.operation for item in matrix] == ["list", "fricDecsn", "pifricDecsn"]
    assert [item.sequence for item in matrix] == [1, 2, 3]
    assert all("crtfc_key" not in item.public_parameters for item in matrix)
    assert matrix[0].public_parameters["last_reprt_at"] == "N"
    assert matrix[0].public_parameters["page_count"] == "100"


def test_scope_rejects_bad_identity_pre_2015_and_unbounded_window() -> None:
    with pytest.raises(OpenDartObservationError):
        request_matrix("123", "20240101", "20240102")
    with pytest.raises(OpenDartObservationError):
        request_matrix("00123456", "20141201", "20141202")
    with pytest.raises(OpenDartObservationError):
        request_matrix("00123456", "20240101", "20240202")


def test_fric_parser_preserves_zero_null_empty_and_source_precision() -> None:
    body = (HERE / "fixture_opendart_fric_success.json").read_bytes()
    classification, rows = parse_observations("fricDecsn", body, captured_at_utc=CAPTURED)
    assert classification == "SUCCESS"
    assert len(rows) == 1
    row = rows[0]
    assert row["nstk_estk_cnt"] == "0"
    assert row["bfic_tisstk_estk"] is None
    assert row["nstk_ascnt_ps_estk"] == ""
    assert row["nstk_ascnt_ps_ostk"] == "0.1"
    assert row["landing_response_body_sha256"] == body_sha256(body)
    assert row["source_item_ordinal"] == 0
    assert row["captured_at_utc"] == CAPTURED


def test_list_parser_preserves_original_correction_and_no_supersession_guess() -> None:
    body = (HERE / "fixture_opendart_list_revision.json").read_bytes()
    classification, rows = parse_observations("list", body, captured_at_utc=CAPTURED)
    assert classification == "SUCCESS"
    assert [row["rcept_no"] for row in rows] == ["20240102000001", "20240103000002"]
    assert [row["rm"] for row in rows] == ["정", ""]
    assert all("supersedes_rcept_no" not in row for row in rows)


def test_status_013_is_valid_empty_but_other_status_is_failure() -> None:
    assert parse_observations("fricDecsn", b'{"status":"013","message":"no data"}', captured_at_utc=CAPTURED) == ("VALID_EMPTY", [])
    with pytest.raises(OpenDartObservationError, match="not successful"):
        parse_observations("fricDecsn", b'{"status":"020","message":"limit"}', captured_at_utc=CAPTURED)


def test_schema_and_pagination_fail_closed() -> None:
    body = json.loads((HERE / "fixture_opendart_list_revision.json").read_text(encoding="utf-8"))
    body["total_page"] = 2
    with pytest.raises(OpenDartObservationError, match="refuses pagination"):
        parse_observations("list", json.dumps(body).encode(), captured_at_utc=CAPTURED)
    del body["list"][0]["report_nm"]
    body["total_page"] = 1
    with pytest.raises(OpenDartObservationError, match="documented fields missing"):
        parse_observations("list", json.dumps(body).encode(), captured_at_utc=CAPTURED)


def test_pifric_parser_accepts_only_documented_fixture_fields() -> None:
    item = {field: "" for field in PIFRIC_FIELDS}
    item.update({"rcept_no": "20240102000001", "corp_code": "00123456",
                 "corp_cls": "Y", "corp_name": "Fixture Corp",
                 "fric_nstk_estk_cnt": "0", "fric_nstk_ascnt_ps_estk": None})
    body = json.dumps({"status": "000", "message": "ok", "list": [item]}).encode()
    classification, rows = parse_observations("pifricDecsn", body, captured_at_utc=CAPTURED)
    assert classification == "SUCCESS"
    assert rows[0]["fric_nstk_estk_cnt"] == "0"
    assert rows[0]["fric_nstk_ascnt_ps_estk"] is None


def test_capture_timestamp_must_be_timezone_aware() -> None:
    with pytest.raises(OpenDartObservationError, match="timezone-aware"):
        parse_observations("fricDecsn", b'{"status":"013"}', captured_at_utc="2026-08-12T01:02:03")


def test_manual_pilot_is_exactly_three_calls_and_artifacts_are_keyless(tmp_path, monkeypatch) -> None:
    pifric_item = {field: "" for field in PIFRIC_FIELDS}
    pifric_item.update({"rcept_no": "20240102000001", "corp_code": "00123456",
                        "corp_cls": "Y", "corp_name": "Fixture Corp"})
    bodies = [
        (HERE / "fixture_opendart_list_revision.json").read_bytes(),
        (HERE / "fixture_opendart_fric_success.json").read_bytes(),
        json.dumps({"status": "000", "message": "ok", "list": [pifric_item]}).encode(),
    ]
    calls = []

    class Response:
        status_code = 200

        def __init__(self, content):
            self.content = content

    class Session:
        def get(self, url, *, params, timeout, allow_redirects):
            calls.append((url, params, timeout, allow_redirects))
            return Response(bodies[len(calls) - 1])

    key = "k" * 40
    monkeypatch.setenv("OPENDART_API_KEY", key)
    monkeypatch.setattr(pilot.requests, "Session", Session)
    result = pilot.run_pilot(corp_code="00123456", begin_date="20240101",
                             end_date="20240131", landing_root=tmp_path)
    assert result["status"] == "COMPLETE"
    assert len(calls) == 3
    assert [url.rsplit("/", 1)[-1] for url, _, _, _ in calls] == [
        "list.json", "fricDecsn.json", "pifricDecsn.json",
    ]
    assert all(not allow_redirects for _, _, _, allow_redirects in calls)
    run_dir = Path(result["run_dir"])
    assert json.loads((run_dir / "checkpoint.json").read_text())["status"] == "COMPLETE"
    assert len(list(run_dir.glob("response_*.json"))) == 3
    assert all(key not in path.read_text(encoding="utf-8") for path in run_dir.iterdir() if path.is_file())
