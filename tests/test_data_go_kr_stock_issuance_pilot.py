from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.manual.data_go_kr_stock_issuance_pilot import (
    BASE_DATE, ENDPOINT, EXPECTED_TOTAL, NUM_ROWS, PilotError, SOURCE_FIELDS,
    run_pilot, verify_pilot_run,
)


class Response:
    status_code = 200

    def __init__(self, payload=None, body=None):
        self._payload = payload
        self.content = body if body is not None else json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")


def item(index=1):
    row = {field: "" for field in SOURCE_FIELDS}
    row.update({
        "basDt": BASE_DATE, "crno": f"23411100981{40 + index:02d}"[-13:],
        "isinCd": f"KR73423400{index:02d}"[-12:], "isinCdNm": f"issuer-{index}",
        "stckIssuCmpyNm": f"issuer-{index}", "scrsDcd": "21",
        "stckIssuSqno": str(index), "stckIssuDt": "20231201",
        "stckIssuDcnt": str(index), "scrsItmsKcd": "0101",
        "scrsItmsKcdNm": "보통주", "stckIssuRcd": "101",
        "stckIssuRcdNm": "유상증자", "issuStckCnt": str(394 + index),
    })
    return row


def payload(rows=None, *, total=EXPECTED_TOTAL):
    rows = rows if rows is not None else [item(1), item(2)]
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
        "body": {"pageNo": 1, "numOfRows": NUM_ROWS, "totalCount": total,
                 "items": {"item": rows}}}}


class Backend:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        assert url == ENDPOINT
        return self.response


def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "configured-test-key")
    root = tmp_path / "project"
    root.mkdir()
    return root


def test_passes_one_call_and_retains_exact_evidence(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    backend = Backend(Response(payload()))
    result = run_pilot(root, delegate=backend)
    assert result["status"] == "PILOT_PASSED_KNOWN_POSITIVE_SCHEMA"
    assert result["raw_requests"] == backend.calls == 1
    run = Path(result["run_root"])
    assert {p.name for p in run.iterdir()} == {
        "raw_response.body", "raw_call.json", "response.json", "call_ledger.json", "manifest.json"
    }
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assessment"]["rows"] == 2
    assert manifest["assessment"]["predictive_use"].startswith("BLOCKED")
    assert not (root / "data/normalized").exists()
    assert not (root / "data/state/data_go_kr_provider.lock").exists()
    assert verify_pilot_run(root, run)["status"] == "OFFLINE_AUDIT_PASS"


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_date", "negative_count"])
def test_schema_or_domain_anomaly_stops_after_landing(tmp_path, monkeypatch, mutation):
    root = project(tmp_path, monkeypatch)
    rows = [item(1), item(2)]
    if mutation == "missing":
        rows[0].pop("scrsDcd")
    elif mutation == "extra":
        rows[0]["unexpected"] = "x"
    elif mutation == "wrong_date":
        rows[0]["basDt"] = "20231225"
    else:
        rows[0]["issuStckCnt"] = "-1"
    result = run_pilot(root, delegate=Backend(Response(payload(rows))))
    assert result["status"] == "PILOT_STOPPED"
    run = Path(result["run_root"])
    assert (run / "raw_response.body").exists()
    assert (run / "response.json").exists()
    assert json.loads((run / "manifest.json").read_text(encoding="utf-8"))["normalized_writes"] is False


def test_secret_echo_never_persists_body(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    result = run_pilot(root, delegate=Backend(Response(body=b"configured-test-key")))
    assert result["status"] == "PILOT_STOPPED"
    run = Path(result["run_root"])
    assert not (run / "raw_response.body").exists()
    assert b"configured-test-key" not in b"".join(p.read_bytes() for p in run.iterdir())


def test_total_drift_stops_and_lock_blocks(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    result = run_pilot(root, delegate=Backend(Response(payload([item(1)], total=1))))
    assert result["status"] == "PILOT_STOPPED"
    lock = root / "data/state/data_go_kr_provider.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("active", encoding="utf-8")
    landing = root / "data/landing/diagnostics/data_go_kr_stock_issuance_pilot"
    before = {path.name for path in landing.iterdir()}
    with pytest.raises(PilotError, match="already held"):
        run_pilot(root, delegate=Backend(Response(payload())))
    assert {path.name for path in landing.iterdir()} == before


def test_cli_requires_explicit_confirmation():
    from scripts.manual.data_go_kr_stock_issuance_pilot import main

    with pytest.raises(SystemExit, match="requires"):
        main([])


def test_offline_verifier_rejects_tamper(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    result = run_pilot(root, delegate=Backend(Response(payload())))
    run = Path(result["run_root"])
    raw = run / "raw_response.body"
    raw.write_bytes(raw.read_bytes() + b" ")
    with pytest.raises(PilotError, match="raw call/body evidence differs"):
        verify_pilot_run(root, run)
