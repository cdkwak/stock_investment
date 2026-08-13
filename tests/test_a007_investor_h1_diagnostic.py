from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

from scripts.manual import a007_investor_h1_diagnostic_support as support
from scripts.manual import diagnose_a007_investor_h1 as runner
from scripts.manual import diagnose_a007_investor_range as base_runner
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


FIXTURE_DATES = ("20100104", "20100105", "20120104")


def _digest(dates):
    return hashlib.sha256(("\n".join(dates) + "\n").encode()).hexdigest()


def _row(day: str, total: int = 10):
    return {
        "TRD_DD": f"{day[:4]}/{day[4:6]}/{day[6:]}",
        "STR_CONST_VAL1": str(total), "STR_CONST_VAL2": "0",
        "STR_CONST_VAL3": "0", "STR_CONST_VAL4": "0", "STR_CONST_VAL5": str(total),
    }


def _body(dates=FIXTURE_DATES, total=10):
    return json.dumps({"OutBlock_1": [_row(day, total) for day in dates]}).encode()


def _response(body):
    response = requests.Response()
    response.status_code = 200
    response._content = body
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


class _Session(requests.Session):
    is_authenticated = True

    def is_valid(self):
        return True


class _Wrapper:
    is_authenticated = True

    def __init__(self, session):
        self.session = session

    def is_valid(self):
        return True


def _fixture_plan(monkeypatch):
    monkeypatch.setattr(support, "EXPECTED_DATE_COUNT", len(FIXTURE_DATES))
    monkeypatch.setattr(support, "EXPECTED_DATE_SHA256", _digest(FIXTURE_DATES))
    monkeypatch.setattr(support, "expected_dates", lambda unused: FIXTURE_DATES)


def test_h1_plan_is_frozen_to_one_kospi_volume_request():
    assert support.SCOPE == {
        "strtDd": "20100104", "endDd": "20120104", "inqCondTpCd": 1, "mktTpCd": 1,
    }
    assert support.EXPECTED_DATE_COUNT == 502
    assert support.EXPECTED_DATE_SHA256 == "4614186ad1bdaa70a8796ad96efa2b99e47990b0d7c521cb2a2c9fc5df758628"
    assert support.MAX_BUSINESS_REQUESTS == 1
    assert support.MAX_RAW_HTTP_REQUESTS == support.EXPECTED_RAW_HTTP_REQUESTS == 6
    assert support.REQUIRE_ZERO_RETRY_AUTH_SESSION is True
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"
    assert runner.LANDING_ROOT.name == "a007_investor_h1"
    assert support.EXPECTED_BUSINESS_DATA == {
        "bld": support.BUSINESS_BLD, "strtDd": "20100104", "endDd": "20120104",
        "inqCondTpCd": "1", "mktTpCd": "1",
    }


def test_expected_502_dates_are_bound_to_exact_retained_sources():
    dates = support.expected_dates(Path("."))
    assert len(dates) == 502
    assert dates[0] == "20100104" and dates[-1] == "20120104"
    assert _digest(dates) == support.EXPECTED_DATE_SHA256


def test_h1_classifies_only_exact_full_set_or_exact_end_date_zero_collapse():
    full = support.classify_response(_body(), FIXTURE_DATES)
    assert full.classification == "H1_FULL_RANGE_AVAILABLE"
    assert full.source_rows == 3 and full.positive_total_dates == 3
    collapsed = support.classify_response(_body(("20120104",), total=0), FIXTURE_DATES)
    assert collapsed.classification == "PRE_AVAILABILITY_COLLAPSE"
    assert collapsed.source_rows == 1 and collapsed.positive_total_dates == 0
    with_metadata = json.loads(_body())
    with_metadata["CURRENT_DATETIME"] = "2026.08.13 PM 07:21:10"
    classified = support.classify_response(json.dumps(with_metadata).encode(), FIXTURE_DATES)
    assert classified.source_current_datetime == "2026.08.13 PM 07:21:10"
    with pytest.raises(PilotStopped, match="AMBIGUOUS_STOP:2/3"):
        support.classify_response(_body(FIXTURE_DATES[-2:]), FIXTURE_DATES)
    with pytest.raises(PilotStopped, match="AMBIGUOUS_STOP:1/3"):
        support.classify_response(_body(("20120104",), total=1), FIXTURE_DATES)


@pytest.mark.parametrize("body,reason", [
    (b"<html>restriction</html>", "HTML_OR_RESTRICTION_RESPONSE"),
    (b'{"OutBlock_1":[]}', "ANOMALOUS_EMPTY_RANGE"),
    (b'{"OutBlock_1":{},"extra":1}', "TOP_LEVEL_SCHEMA_MISMATCH"),
    (json.dumps({"OutBlock_1": [{**_row("20120104", 0), "extra": "x"}]}).encode(), "SCHEMA_MISMATCH"),
    (json.dumps({"OutBlock_1": [{**_row("20120104", 0), "TRD_DD": "bad"}]}).encode(), "INVALID_DATE"),
    (json.dumps({"OutBlock_1": [{**_row("20120104", 0), "STR_CONST_VAL1": "-1"}]}).encode(), "NEGATIVE_VALUE"),
    (json.dumps({"OutBlock_1": [{**_row("20120104", 0), "STR_CONST_VAL1": "1"}]}).encode(), "INVESTOR_TOTAL_MISMATCH"),
])
def test_h1_strict_html_schema_date_and_domain_gates(body, reason):
    with pytest.raises(PilotStopped, match=reason):
        support.classify_response(body, FIXTURE_DATES)


def test_h1_cli_refuses_without_all_confirmations_before_network(monkeypatch):
    called = False

    def forbidden(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "run_diagnostic", forbidden)
    monkeypatch.setattr("sys.argv", ["diagnose_a007_investor_h1.py"])
    assert runner.main() == 2
    assert called is False


def test_simulated_h1_collapse_is_one_call_landing_only_with_provenance(tmp_path, monkeypatch):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_h1"
    lock = project / "data/state/d_owned_krx_short_selling.lock"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=user\nKRX_PW=password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(base_runner.importlib.metadata, "version", lambda unused: "1.2.8")
    _fixture_plan(monkeypatch)
    session = _Session()
    calls = []
    response_body = _body(("20120104",), total=0)

    def fake_request(current, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _response(response_body)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    def authenticate():
        for _ in range(5):
            session.get("https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd")
        return _Wrapper(session)

    def execute():
        session.post(
            "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            data=dict(support.EXPECTED_BUSINESS_DATA),
        )

    result = base_runner.run_diagnostic(
        env_file=env, project_root=project, landing_root=landing, lock_path=lock,
        session_getter=authenticate, execute_probe=execute, diagnostic_support=support,
    )
    assert result["classification"] == "PRE_AVAILABILITY_COLLAPSE"
    assert result["business_requests"] == 1 and result["raw_http_requests"] == 6
    assert not lock.exists() and not (project / "data/normalized").exists()
    run_dir = Path(result["run_dir"])
    assert (run_dir / "response.json").read_bytes() == response_body
    provenance = json.loads((run_dir / "response.json.provenance.json").read_text("utf-8"))
    assert provenance["body_sha256"] == hashlib.sha256(response_body).hexdigest()
    assert provenance["scope_id"] == support.SCOPE_ID
    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    assert manifest["retry_count"] == 0
    assert manifest["normalized_writes"] is manifest["checkpoint_writes"] is False
    assert sum(url.endswith("getJsonData.cmd") for _, url, _ in calls) == 1


def test_wrong_h1_request_scope_stops_before_business_network(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    env = project / ".env"
    env.write_text("KRX_ID=user\nKRX_PW=password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(base_runner.importlib.metadata, "version", lambda unused: "1.2.8")
    _fixture_plan(monkeypatch)
    session = _Session()
    network = 0

    def fake_request(*args, **kwargs):
        nonlocal network
        network += 1
        return _response(_body())

    monkeypatch.setattr(requests.Session, "request", fake_request)

    def execute():
        session.post(
            "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            data={**support.EXPECTED_BUSINESS_DATA, "strtDd": "20100105"},
        )

    with pytest.raises(PilotStopped, match="BUSINESS_DATA_MISMATCH"):
        base_runner.run_diagnostic(
            env_file=env, project_root=project,
            landing_root=project / "data/landing/diagnostics/h1",
            lock_path=project / "data/state/d_owned_krx_short_selling.lock",
            session_getter=lambda: _Wrapper(session), execute_probe=execute,
            diagnostic_support=support,
        )
    assert network == 0
