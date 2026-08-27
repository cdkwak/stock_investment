import json
from pathlib import Path

import requests

from scripts.manual.pilot.kbsec_token_pilot_support import redact, response_evidence, secret_scan


def make_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    response._content = json.dumps(payload).encode("utf-8")
    return response


def test_response_evidence_redacts_token_and_embedded_bearer():
    response = make_response({
        "access_token": "live-token-value", "expires_in": 3600,
        "message": "Authorization: Bearer live-token-value",
    })
    evidence = response_evidence(response, known_secrets=("app-key", "app-secret"))
    exposed = json.dumps(evidence)
    assert evidence["http_status"] == 200
    assert evidence["raw_response_bytes"] > 0 and len(evidence["raw_response_sha256"]) == 64
    assert "live-token-value" not in exposed
    assert evidence["body_redacted"]["access_token"] == "[REDACTED]"


def test_recursive_redaction_covers_credentials_in_diagnostic_fields():
    value = {"dataHeader": {"processMessage": "bad app-secret and Bearer abc.def"}}
    safe = redact(value, known_secrets=("app-secret",))
    exposed = json.dumps(safe)
    assert "app-secret" not in exposed and "abc.def" not in exposed


def test_secret_scan_checks_all_persisted_files(tmp_path: Path):
    safe = tmp_path / "safe.json"
    safe.write_text('{"access_token":"[REDACTED]"}', encoding="utf-8")
    assert secret_scan([safe], ("live-secret",))
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text("live-secret", encoding="utf-8")
    assert not secret_scan([safe, unsafe], ("live-secret",))
