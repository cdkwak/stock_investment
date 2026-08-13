from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest
import requests

from scripts.manual import a007_investor_range_diagnostic_support as support
from scripts.manual import diagnose_a007_investor_range as runner
from scripts.manual.pykrx_short_selling_pilot_support import BudgetExceeded, PilotStopped


def _body(dates=support.EXPECTED_DATES, *, all_zero: bool = False) -> bytes:
    rows = []
    for day in dates:
        value = "0" if all_zero else "10"
        rows.append({
            "TRD_DD": f"{day[:4]}/{day[4:6]}/{day[6:]}",
            "STR_CONST_VAL1": value,
            "STR_CONST_VAL2": "20" if not all_zero else "0",
            "STR_CONST_VAL3": "30" if not all_zero else "0",
            "STR_CONST_VAL4": "0",
            "STR_CONST_VAL5": "60" if not all_zero else "0",
        })
    return json.dumps({"OutBlock_1": rows}).encode()


def _response(body: bytes, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


def test_scope_is_one_fixed_recent_multi_day_probe_not_failed_history():
    assert support.MAX_BUSINESS_REQUESTS == 1
    assert support.MAX_RAW_HTTP_REQUESTS == 6
    assert support.SCOPE == {
        "strtDd": "20260804", "endDd": "20260810", "inqCondTpCd": 1, "mktTpCd": 1,
    }
    assert len(support.EXPECTED_DATES) == 5
    assert "20080102" not in json.dumps(support.manifest_payload(run_id="x", created_at_utc="z"))


def test_classifier_requires_exact_multi_date_coverage_and_positive_anchor():
    result = support.classify_response(_body())
    assert result.classification == "MULTI_DATE_RANGE_CONFIRMED"
    assert result.source_rows == 5
    assert result.observed_dates == support.EXPECTED_DATES
    with pytest.raises(PilotStopped, match=r"DATE_COVERAGE_MISMATCH:1/5"):
        support.classify_response(_body((support.EXPECTED_DATES[-1],)))
    with pytest.raises(PilotStopped, match="NO_POSITIVE_KNOWN_RECENT_OBSERVATION"):
        support.classify_response(_body(all_zero=True))


@pytest.mark.parametrize("body, reason", [
    (b"<html>blocked</html>", "HTML_OR_RESTRICTION"),
    (b'{"_error_code":"blocked"}', "SOURCE_ERROR_PAYLOAD"),
    (b'{"OutBlock_1":[]}', "ANOMALOUS_EMPTY_RANGE"),
])
def test_classifier_fails_closed_on_access_and_empty_payloads(body, reason):
    with pytest.raises(PilotStopped, match=reason):
        support.classify_response(body)


def test_classifier_rejects_duplicate_schema_and_accounting_errors():
    payload = json.loads(_body())
    payload["OutBlock_1"][1]["TRD_DD"] = payload["OutBlock_1"][0]["TRD_DD"]
    with pytest.raises(PilotStopped, match="DUPLICATE_SOURCE_DATE"):
        support.classify_response(json.dumps(payload).encode())
    payload = json.loads(_body())
    del payload["OutBlock_1"][0]["STR_CONST_VAL2"]
    with pytest.raises(PilotStopped, match="SCHEMA_MISMATCH"):
        support.classify_response(json.dumps(payload).encode())
    payload = json.loads(_body())
    payload["OutBlock_1"][0]["STR_CONST_VAL5"] = "61"
    with pytest.raises(PilotStopped, match="INVESTOR_TOTAL_MISMATCH"):
        support.classify_response(json.dumps(payload).encode())


def test_cli_requires_both_manual_safety_acknowledgements(monkeypatch):
    monkeypatch.setattr("sys.argv", ["diagnose_a007_investor_range.py"])
    assert runner.main() == 2
    monkeypatch.setattr("sys.argv", [
        "diagnose_a007_investor_range.py", "--acknowledge-cooldown-ended",
    ])
    assert runner.main() == 2


def test_runner_is_diagnostic_only_retry_zero_and_uses_shared_lock():
    source = inspect.getsource(runner)
    assert "to_parquet" not in source
    assert "data/normalized" not in source
    assert "checkpoint" not in inspect.getsource(runner.run_diagnostic).lower()
    assert "for attempt" not in source and "Retry" not in source
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"


class _Authenticated:
    is_authenticated = True

    def is_valid(self):
        return True


def test_simulated_run_writes_exact_immutable_evidence_only(tmp_path, monkeypatch):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_range"
    lock = project / "data/state/d_owned_krx_short_selling.lock"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=test_user\nKRX_PW=test_password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda unused: "1.2.8")
    calls = []

    def fake_request(session, method, url, **kwargs):
        calls.append((method, url))
        assert kwargs["allow_redirects"] is False
        return _response(_body())

    monkeypatch.setattr(requests.Session, "request", fake_request)

    def execute():
        requests.Session().post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")

    result = runner.run_diagnostic(
        env_file=env, project_root=project, landing_root=landing, lock_path=lock,
        session_getter=_Authenticated, execute_probe=execute,
    )
    assert result["status"] == "PASS"
    assert result["business_requests"] == result["raw_http_requests"] == 1
    assert len(calls) == 1 and not lock.exists()
    run_dir = Path(result["run_dir"])
    assert {p.name for p in run_dir.iterdir()} == {
        runner.BODY_NAME, runner.PROVENANCE_NAME, runner.LEDGER_NAME, runner.MANIFEST_NAME,
    }
    body = (run_dir / runner.BODY_NAME).read_bytes()
    provenance = json.loads((run_dir / runner.PROVENANCE_NAME).read_text())
    assert provenance["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert provenance["ledger_relative_path"].endswith("/call_ledger.jsonl")
    ledger = [json.loads(line) for line in (run_dir / runner.LEDGER_NAME).read_text().splitlines()]
    assert [item["event"] for item in ledger].count("HTTP_RESPONSE") == 1
    assert ledger[-1]["event"] == "DIAGNOSTIC_PASSED"
    combined = b"".join(path.read_bytes() for path in run_dir.iterdir())
    assert b"test_user" not in combined and b"test_password" not in combined
    assert not (project / "data/normalized").exists()


def test_second_business_request_is_blocked_before_network_and_first_evidence_retained(tmp_path, monkeypatch):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_range"
    lock = project / "data/state/d_owned_krx_short_selling.lock"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=fixture_user\nKRX_PW=fixture_password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda unused: "1.2.8")
    calls = []

    def fake_request(session, method, url, **kwargs):
        calls.append(url)
        return _response(_body())

    monkeypatch.setattr(requests.Session, "request", fake_request)

    def execute_twice():
        session = requests.Session()
        session.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")
        session.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")

    with pytest.raises(BudgetExceeded, match="business request budget"):
        runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing, lock_path=lock,
            session_getter=_Authenticated, execute_probe=execute_twice,
        )
    assert len(calls) == 1 and not lock.exists()
    run_dir = next(landing.iterdir())
    assert (run_dir / runner.BODY_NAME).is_file()
    assert (run_dir / runner.PROVENANCE_NAME).is_file()
    events = [json.loads(x)["event"] for x in (run_dir / runner.LEDGER_NAME).read_text().splitlines()]
    assert "BUSINESS_BUDGET_EXHAUSTED" in events
    assert events[-1] == "DIAGNOSTIC_STOPPED"


def test_raw_budget_blocks_business_before_seventh_network_transaction(tmp_path, monkeypatch):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_range"
    lock = project / "data/state/d_owned_krx_short_selling.lock"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=fixture_user\nKRX_PW=fixture_password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda unused: "1.2.8")
    calls = []

    def fake_request(session, method, url, **kwargs):
        calls.append(url)
        return _response(b"authentication")

    monkeypatch.setattr(requests.Session, "request", fake_request)

    def authenticate_with_full_budget():
        session = requests.Session()
        for _ in range(support.MAX_RAW_HTTP_REQUESTS):
            session.get("https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd")
        return _Authenticated()

    def execute():
        requests.Session().post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd")

    with pytest.raises(BudgetExceeded, match="raw HTTP budget"):
        runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing, lock_path=lock,
            session_getter=authenticate_with_full_budget, execute_probe=execute,
        )
    assert len(calls) == support.MAX_RAW_HTTP_REQUESTS
    run_dir = next(landing.iterdir())
    assert not (run_dir / runner.BODY_NAME).exists()
    events = [json.loads(x)["event"] for x in (run_dir / runner.LEDGER_NAME).read_text().splitlines()]
    assert "HTTP_BUDGET_EXHAUSTED" in events
    assert events[-1] == "DIAGNOSTIC_STOPPED"


def test_existing_shared_lock_prevents_any_network_call(tmp_path, monkeypatch):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_range"
    lock = project / "data/state/d_owned_krx_short_selling.lock"
    env = project / ".env"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}", encoding="utf-8")
    env.write_text("KRX_ID=fixture_user\nKRX_PW=fixture_password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(runner.importlib.metadata, "version", lambda unused: "1.2.8")
    called = False

    def execute():
        nonlocal called
        called = True

    with pytest.raises(PilotStopped, match="lock already exists"):
        runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing, lock_path=lock,
            session_getter=_Authenticated, execute_probe=execute,
        )
    assert not called
