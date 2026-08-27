from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.manual.pilot.data_go_kr_stock_issuance_pilot import (
    ENDPOINT, PilotError, SOURCE_FIELDS, run_current_scope_pilot,
    verify_current_scope_run,
)


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class Backend:
    def __init__(self, payload):
        self.response = Response(payload)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        assert url == ENDPOINT
        return self.response


def row():
    value = {field: "" for field in SOURCE_FIELDS}
    value.update({
        "basDt": "20260813", "crno": "2341110098149", "isinCd": "KR7342340007",
        "isinCdNm": "issuer", "stckIssuCmpyNm": "issuer", "scrsDcd": "21",
        "stckIssuSqno": "1", "stckIssuDt": "20260814", "stckIssuDcnt": "1",
        "scrsItmsKcd": "0101", "scrsItmsKcdNm": "보통주", "stckIssuRcd": "101",
        "stckIssuRcdNm": "유상증자", "issuStckCnt": "395",
    })
    return value


def payload(*, total=15000, rows=None):
    rows = [row()] if rows is None else rows
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL"},
        "body": {"pageNo": 1, "numOfRows": 1, "totalCount": total,
                 "items": {"item": rows}}}}


def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "configured-test-key")
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_current_scope_count_and_offline_audit(tmp_path, monkeypatch):
    project = root(tmp_path, monkeypatch)
    backend = Backend(payload())
    result = run_current_scope_pilot(project, delegate=backend)
    assert result["status"] == "CURRENT_SCOPE_COUNT_PASSED"
    assert result["declared_total"] == 15000
    assert result["pages_at_9999"] == 2
    assert backend.calls == 1
    audit = verify_current_scope_run(project, Path(result["run_root"]))
    assert audit["status"] == "OFFLINE_AUDIT_PASS"
    assert audit["source_snapshot_date"] == "20260813"


@pytest.mark.parametrize("total,rows", [(0, []), (2, []), (2, [row(), row()])])
def test_current_scope_invalid_page_stops_after_capture(tmp_path, monkeypatch, total, rows):
    project = root(tmp_path, monkeypatch)
    result = run_current_scope_pilot(project, delegate=Backend(payload(total=total, rows=rows)))
    assert result["status"] == "CURRENT_SCOPE_STOPPED"
    run = Path(result["run_root"])
    assert (run / "raw_response.body").exists()
    assert (run / "manifest.json").exists()


def test_current_scope_verifier_rejects_tamper(tmp_path, monkeypatch):
    project = root(tmp_path, monkeypatch)
    result = run_current_scope_pilot(project, delegate=Backend(payload()))
    run = Path(result["run_root"])
    (run / "raw_call.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PilotError, match="raw call/body"):
        verify_current_scope_run(project, run)
