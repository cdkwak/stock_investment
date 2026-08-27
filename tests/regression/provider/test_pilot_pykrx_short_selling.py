from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
import requests

from scripts.manual.pilot import pilot_pykrx_short_selling as runner
from scripts.manual.pilot import pykrx_short_selling_pilot_support as support


class FakeResponse:
    def __init__(self, *, status_code=200, content=b"{}", content_type="application/json"):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}


def make_capture(tmp_path: Path, *, initial_count=0, secrets=("id", "pw")):
    ledger = support.AppendOnlyLedger(tmp_path / "ledger.jsonl", credential_values=secrets)
    return runner.HttpCapture(
        ledger=ledger, initial_count=initial_count, credential_values=secrets
    ), ledger


def test_runner_is_landing_only_and_does_not_use_public_fallback_api():
    source = inspect.getsource(runner._execute_core_probe)
    forbidden = (
        "get_shorting_value_by_ticker", "get_shorting_volume_by_ticker",
        "get_shorting_balance_by_ticker", "from pykrx import stock", "stock.",
    )
    assert all(name not in source for name in forbidden)
    assert "core.개별종목_공매도_거래_전종목" in source
    assert "core.전종목_공매도_잔고" in source
    assert "core.투자자별_공매도_거래" in source
    run_source = inspect.getsource(runner.run_pilot)
    assert "data/normalized" not in run_source.lower()
    assert "write_partitioned" not in run_source and "to_parquet" not in run_source
    assert '"normalized_writes": False' in run_source
    assert "for probe in PROBE_MATRIX" in run_source
    assert "Thread" not in run_source and "asyncio" not in run_source


def test_http_capture_enforces_raw_cap_without_retry_or_network(tmp_path):
    capture, ledger = make_capture(tmp_path, initial_count=support.MAX_RAW_HTTP_REQUESTS - 1)
    capture._original = lambda session, method, url, **kwargs: FakeResponse(content=b"auth")
    response = capture._request(
        object(), "GET", "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
    )
    assert response.status_code == 200
    assert capture.count == support.MAX_RAW_HTTP_REQUESTS
    with pytest.raises(support.BudgetExceeded):
        capture._request(
            object(), "GET", "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
        )
    events = [record["event"] for record in ledger.records()]
    assert events == ["HTTP_RESPONSE", "HTTP_BUDGET_EXHAUSTED"]


def test_capture_context_patches_requests_session_with_one_sequential_stream(tmp_path, monkeypatch):
    capture, _ = make_capture(tmp_path)
    calls = []

    def fake_request(session, method, url, **kwargs):
        calls.append((method, url, kwargs["timeout"]))
        return FakeResponse(content=b"auth")

    monkeypatch.setattr(requests.Session, "request", fake_request)
    with capture:
        response = requests.Session().get(
            "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
        )
    assert response.status_code == 200
    assert calls == [
        (
            "GET",
            "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd",
            support.HTTP_TIMEOUT_SECONDS,
        )
    ]
    assert capture.count == 1


def test_capture_excludes_auth_body_but_retains_business_response_for_exact_landing(tmp_path):
    capture, ledger = make_capture(tmp_path)
    responses = iter(
        [
            FakeResponse(content=b"secret-login-body"),
            FakeResponse(content=b'{"OutBlock_1":[]}'),
        ]
    )
    capture._original = lambda session, method, url, **kwargs: next(responses)
    capture._request(
        object(), "POST", "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
    )
    assert capture._business_responses == []
    capture.current_probe = support.PROBE_MATRIX[21].name
    capture._request(
        object(), "POST", "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    )
    response, artifact = capture.take_single_business_response(support.PROBE_MATRIX[21])
    assert response.content == b'{"OutBlock_1":[]}'
    assert artifact is None
    serialized = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert "secret-login-body" not in serialized
    assert "response_sha256" not in ledger.records()[0]
    assert "response_sha256" in ledger.records()[1]


@pytest.mark.parametrize("status", [403, 429])
def test_restriction_status_stops_immediately(status, tmp_path):
    ledger = support.AppendOnlyLedger(tmp_path / "ledger.jsonl", credential_values=("id", "pw"))
    capture = runner.HttpCapture(
        ledger=ledger, initial_count=0, credential_values=("id", "pw"),
        landing_run_dir=tmp_path,
    )
    capture._original = lambda session, method, url, **kwargs: FakeResponse(
        status_code=status, content=b'{"OutBlock_1":[]}'
    )
    capture.current_probe = support.PROBE_MATRIX[0].name
    with pytest.raises(support.PilotStopped, match="HTTP_RESTRICTION"):
        capture._request(
            object(), "POST", "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        )
    assert capture.count == 1
    assert len(ledger.records()) == 1
    assert (tmp_path / support.landing_body_name(support.PROBE_MATRIX[0])).read_bytes() == b'{"OutBlock_1":[]}'


def test_unapproved_endpoint_stops_before_request(tmp_path):
    capture, ledger = make_capture(tmp_path)
    calls = []
    capture._original = lambda *args, **kwargs: calls.append(1)
    with pytest.raises(support.PilotStopped, match="UNAPPROVED_ENDPOINT"):
        capture._request(object(), "GET", "https://example.test/not-approved")
    assert calls == []
    assert ledger.records() == []


def test_business_probe_must_make_exactly_one_raw_request(tmp_path):
    capture, _ = make_capture(tmp_path)
    with pytest.raises(support.PilotStopped, match="BUSINESS_REQUEST_COUNT_MISMATCH"):
        capture.take_single_business_response(support.PROBE_MATRIX[0])
    capture._business_responses = [FakeResponse(), FakeResponse()]
    with pytest.raises(support.PilotStopped, match="BUSINESS_REQUEST_COUNT_MISMATCH"):
        capture.take_single_business_response(support.PROBE_MATRIX[0])


def test_http_errors_are_single_attempt_and_redacted(tmp_path):
    capture, ledger = make_capture(tmp_path, secrets=("actual-id", "actual-pw"))
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("failed for actual-id KRX_PW=actual-pw")

    capture._original = fail
    with pytest.raises(requests.ConnectionError):
        capture._request(
            object(), "GET", "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
        )
    assert calls == [1]
    content = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert "actual-id" not in content and "actual-pw" not in content
    assert ledger.records()[0]["event"] == "HTTP_ERROR"


def test_run_directory_is_unique_and_resume_is_root_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_new_run_id", lambda: "unique-run")
    run_id, run_dir, resumed = runner._prepare_run_dir(tmp_path, None)
    assert (run_id, resumed) == ("unique-run", False)
    assert run_dir.is_dir()
    with pytest.raises(FileExistsError):
        runner._prepare_run_dir(tmp_path, None)
    assert runner._prepare_run_dir(tmp_path, run_dir) == ("unique-run", run_dir.resolve(), True)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    with pytest.raises(support.ResumeSafetyError, match="immediate child"):
        runner._prepare_run_dir(tmp_path, outside)


def test_manifest_policy_has_no_normalized_write_and_fixed_limits():
    assert support.MAX_RAW_HTTP_REQUESTS == 33
    assert support.MAX_RECOVERED_RESUME_RAW_HTTP_REQUESTS == 38
    assert support.MAX_SECOND_RECOVERED_RESUME_RAW_HTTP_REQUESTS == 40
    assert support.MAX_BUSINESS_REQUESTS == 25
    assert support.MIN_BUSINESS_INTERVAL_SECONDS >= 5
    assert support.MAX_JITTER_SECONDS > 0
    assert runner.D_OWNED_LOCK_PATH.name.startswith("d_owned_")
