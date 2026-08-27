from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.manual.diagnostic import a007_investor_s1_diagnostic_support as support
from scripts.manual.diagnostic import diagnose_a007_investor_range as base_runner
from scripts.manual.diagnostic import diagnose_a007_investor_s1 as runner
from scripts.manual.audit import verify_a007_investor_s1 as verifier
from scripts.manual.pilot.pykrx_short_selling_pilot_support import PilotStopped


FIXTURE_DATES = ("20240807", "20240808", "20260807")


def _digest(dates: tuple[str, ...]) -> str:
    return hashlib.sha256(("\n".join(dates) + "\n").encode()).hexdigest()


def _body(dates: tuple[str, ...] = FIXTURE_DATES) -> bytes:
    return json.dumps({"OutBlock_1": [
        {
            "TRD_DD": f"{day[:4]}/{day[4:6]}/{day[6:]}",
            "STR_CONST_VAL1": "10", "STR_CONST_VAL2": "20",
            "STR_CONST_VAL3": "30", "STR_CONST_VAL4": "0",
            "STR_CONST_VAL5": "60",
        }
        for day in dates
    ]}).encode()


def _body_with_current(dates: tuple[str, ...] = FIXTURE_DATES) -> bytes:
    payload = json.loads(_body(dates))
    payload["CURRENT_DATETIME"] = "2026.08.13 PM 07:21:10"
    return json.dumps(payload).encode()


def _response(body: bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response._content = body
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


def _patch_fixture_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(support, "EXPECTED_DATE_COUNT", len(FIXTURE_DATES))
    monkeypatch.setattr(support, "EXPECTED_DATE_SHA256", _digest(FIXTURE_DATES))
    monkeypatch.setattr(support, "expected_dates", lambda unused: FIXTURE_DATES)


class _Authenticated:
    is_authenticated = True

    def is_valid(self):
        return True


class _AuthenticatedSession(requests.Session):
    is_authenticated = True

    def is_valid(self):
        return True


class _AuthenticatedWrapper:
    is_authenticated = True

    def __init__(self, session: requests.Session):
        self.session = session

    def is_valid(self):
        return True


def test_s1_plan_is_exact_single_kospi_volume_request():
    assert support.SCOPE == {
        "strtDd": "20240807", "endDd": "20260807",
        "inqCondTpCd": 1, "mktTpCd": 1,
    }
    assert support.EXPECTED_DATE_COUNT == 485
    assert support.EXPECTED_DATE_SHA256 == (
        "18d0a12c19a17b7cff44f6834006385197c9d72e22b9519fd223b2e0541188a7"
    )
    assert support.MAX_BUSINESS_REQUESTS == 1
    assert support.MAX_RAW_HTTP_REQUESTS == 6
    assert support.EXPECTED_RAW_HTTP_REQUESTS == 6
    assert runner.LANDING_ROOT.name == "a007_investor_s1"
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"
    assert support.EXPECTED_BUSINESS_DATA == {
        "bld": support.BUSINESS_BLD, "strtDd": "20240807",
        "endDd": "20260807", "inqCondTpCd": "1", "mktTpCd": "1",
    }


def test_expected_dates_are_bound_to_exact_retained_files(tmp_path, monkeypatch):
    root = tmp_path / "project"
    sources = []
    date_groups = (("2024-08-07", "2024-08-08"), (), ("2026-08-07",))
    for year, dates in zip((2024, 2025, 2026), date_groups):
        relative = Path(
            "data/published/kr_equity_canonical_universe_daily"
        ) / f"market=KOSPI/year={year}/data.parquet"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({"date": list(dates)}), path)
        raw = path.read_bytes()
        sources.append({
            "path": relative.as_posix(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    monkeypatch.setattr(support, "CANONICAL_DATE_SOURCES", tuple(sources))
    monkeypatch.setattr(support, "EXPECTED_DATE_COUNT", len(FIXTURE_DATES))
    monkeypatch.setattr(support, "EXPECTED_DATE_SHA256", _digest(FIXTURE_DATES))
    assert support.expected_dates(root) == FIXTURE_DATES
    changed = root / sources[0]["path"]
    changed.write_bytes(changed.read_bytes() + b"tamper")
    with pytest.raises(PilotStopped, match="CANONICAL_DATE_SOURCE_CHANGED"):
        support.expected_dates(root)


@pytest.mark.parametrize("body, reason", [
    (b"<html>blocked</html>", "HTML_OR_RESTRICTION_RESPONSE"),
    (b'{"OutBlock_1":[]}', "ANOMALOUS_EMPTY_RANGE"),
    (_body((FIXTURE_DATES[-1],)), "DATE_COVERAGE_MISMATCH:1/3"),
    (_body(FIXTURE_DATES[:-1]), "DATE_COVERAGE_MISMATCH:2/3"),
])
def test_s1_classifier_fails_closed_on_html_empty_collapse_and_subset(body, reason):
    with pytest.raises(PilotStopped, match=reason):
        support.classify_response(body, FIXTURE_DATES)


def test_s1_classifier_accepts_only_exact_canonical_set():
    result = support.classify_response(_body(), FIXTURE_DATES)
    assert result.classification == "S1_FULL_RANGE_CONFIRMED"
    assert result.source_rows == len(FIXTURE_DATES)
    assert result.observed_dates == FIXTURE_DATES
    with_current = support.classify_response(_body_with_current(), FIXTURE_DATES)
    assert with_current.source_current_datetime == "2026.08.13 PM 07:21:10"


def test_s1_classifier_rejects_extra_row_and_top_level_fields():
    payload = json.loads(_body())
    payload["OutBlock_1"][0]["UNEXPECTED"] = "x"
    with pytest.raises(PilotStopped, match="SCHEMA_MISMATCH:0:extra=UNEXPECTED"):
        support.classify_response(json.dumps(payload).encode(), FIXTURE_DATES)
    payload = json.loads(_body())
    payload["extra"] = []
    with pytest.raises(PilotStopped, match="TOP_LEVEL_SCHEMA_MISMATCH"):
        support.classify_response(json.dumps(payload).encode(), FIXTURE_DATES)
    payload = json.loads(_body())
    payload["CURRENT_DATETIME"] = "2026-08-13 19:21:10"
    with pytest.raises(PilotStopped, match="CURRENT_DATETIME_INVALID"):
        support.classify_response(json.dumps(payload).encode(), FIXTURE_DATES)


def test_cli_requires_all_explicit_confirmations(monkeypatch):
    monkeypatch.setattr("sys.argv", ["diagnose_a007_investor_s1.py"])
    assert runner.main() == 2
    monkeypatch.setattr("sys.argv", [
        "diagnose_a007_investor_s1.py", "--acknowledge-cooldown-ended",
        "--confirm-one-live-request", "--confirm-landing-only",
        "--confirm-scope", "wrong",
    ])
    assert runner.main() == 2


def test_simulated_s1_runs_are_unique_landing_only_and_exactly_one_call(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_s1"
    prior = project / "data/landing/diagnostics/a007_investor_range/prior.txt"
    lock = project / "data/state/d_owned_krx_short_selling.lock"
    env = project / ".env"
    prior.parent.mkdir(parents=True)
    prior.write_text("preserve", encoding="utf-8")
    env.write_text("KRX_ID=user\nKRX_PW=password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(base_runner.importlib.metadata, "version", lambda unused: "1.2.8")
    _patch_fixture_plan(monkeypatch)
    calls = []

    def fake_request(session, method, url, **kwargs):
        calls.append(url)
        if url.endswith("getJsonData.cmd"):
            assert kwargs["allow_redirects"] is False
        return _response(_body())

    monkeypatch.setattr(requests.Session, "request", fake_request)

    authenticated_session = _AuthenticatedSession()

    def authenticate():
        for _ in range(5):
            authenticated_session.get(
                "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
            )
        return _AuthenticatedWrapper(authenticated_session)

    def execute():
        authenticated_session.post(
            "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            data=dict(support.EXPECTED_BUSINESS_DATA),
        )

    results = [
        base_runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing,
            lock_path=lock, session_getter=authenticate,
            execute_probe=execute, diagnostic_support=support,
        )
        for _ in range(2)
    ]
    assert len(calls) == 12
    assert len({result["run_dir"] for result in results}) == 2
    assert prior.read_text(encoding="utf-8") == "preserve"
    assert not lock.exists() and not (project / "data/normalized").exists()
    for result in results:
        assert result["business_requests"] == 1
        assert result["raw_http_requests"] == 6
        run_dir = Path(result["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text())
        provenance = json.loads(
            (run_dir / "response.json.provenance.json").read_text()
        )
        assert manifest["retry_count"] == 0
        assert manifest["raw_http_requests_expected"] == 6
        assert manifest["checkpoint_writes"] is False
        assert manifest["normalized_writes"] is False
        assert manifest["expected_dates"] == list(FIXTURE_DATES)
        assert provenance["expected_dates"] == list(FIXTURE_DATES)
        entries = [
            json.loads(line)
            for line in (run_dir / "call_ledger.jsonl").read_text().splitlines()
        ]
        assert sum(
            item["event"] == "HTTP_RESPONSE" and not item["authentication"]
            for item in entries
        ) == 1
        assert sum(
            item["event"] == "HTTP_RESPONSE" and item["authentication"]
            for item in entries
        ) == 5
        assert entries[-1]["event"] == "DIAGNOSTIC_PASSED"


def test_s1_fails_closed_when_auth_path_uses_fewer_than_five_raw_calls(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_s1"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=user\nKRX_PW=password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(base_runner.importlib.metadata, "version", lambda unused: "1.2.8")
    _patch_fixture_plan(monkeypatch)
    monkeypatch.setattr(
        requests.Session, "request",
        lambda *args, **kwargs: _response(_body()),
    )

    authenticated_session = _AuthenticatedSession()

    def execute():
        authenticated_session.post(
            "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            data=dict(support.EXPECTED_BUSINESS_DATA),
        )

    with pytest.raises(PilotStopped, match="RAW_REQUEST_COUNT_MISMATCH:1/6"):
        base_runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing,
            lock_path=project / "data/state/d_owned_krx_short_selling.lock",
            session_getter=lambda: _AuthenticatedWrapper(authenticated_session),
            execute_probe=execute,
            diagnostic_support=support,
        )
    run_dir = next(landing.iterdir())
    events = [
        json.loads(line)["event"]
        for line in (run_dir / "call_ledger.jsonl").read_text().splitlines()
    ]
    assert events.count("HTTP_RESPONSE") == 1
    assert events[-1] == "DIAGNOSTIC_STOPPED"


@pytest.mark.parametrize(
    "method,data,extra_kwargs,reason",
    [
        ("GET", support.EXPECTED_BUSINESS_DATA, {}, "BUSINESS_METHOD_MISMATCH"),
        ("POST", None, {}, "BUSINESS_DATA_MISSING"),
        ("POST", {**support.EXPECTED_BUSINESS_DATA, "extra": "x"}, {}, "BUSINESS_DATA_MISMATCH"),
        ("POST", {**support.EXPECTED_BUSINESS_DATA, "strtDd": "20240808"}, {}, "BUSINESS_DATA_MISMATCH"),
        ("POST", support.EXPECTED_BUSINESS_DATA, {"params": {"x": "1"}}, "BUSINESS_QUERY_PARAMS_FORBIDDEN"),
    ],
)
def test_wrong_business_transaction_fails_before_network(
    tmp_path, monkeypatch, method, data, extra_kwargs, reason
):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_s1"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=user\nKRX_PW=password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(base_runner.importlib.metadata, "version", lambda unused: "1.2.8")
    _patch_fixture_plan(monkeypatch)
    session = _AuthenticatedSession()
    network_calls = 0

    def fake_request(*args, **kwargs):
        nonlocal network_calls
        network_calls += 1
        return _response(_body())

    monkeypatch.setattr(requests.Session, "request", fake_request)

    def execute():
        session.request(
            method, "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            data=data, **extra_kwargs,
        )

    with pytest.raises(PilotStopped, match=reason):
        base_runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing,
            lock_path=project / "data/state/d_owned_krx_short_selling.lock",
            session_getter=lambda: _AuthenticatedWrapper(session), execute_probe=execute,
            diagnostic_support=support,
        )
    assert network_calls == 0
    run_dir = next(landing.iterdir())
    assert not (run_dir / "response.json").exists()


def test_retry_enabled_auth_session_fails_before_business_network(tmp_path, monkeypatch):
    project = tmp_path / "project"
    landing = project / "data/landing/diagnostics/a007_investor_s1"
    env = project / ".env"
    project.mkdir()
    env.write_text("KRX_ID=user\nKRX_PW=password\n", encoding="utf-8")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(base_runner.importlib.metadata, "version", lambda unused: "1.2.8")
    _patch_fixture_plan(monkeypatch)
    session = _AuthenticatedSession()
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=1)))
    called = False

    def execute():
        nonlocal called
        called = True

    with pytest.raises(PilotStopped, match="RETRY_ENABLED_OR_UNKNOWN:https://"):
        base_runner.run_diagnostic(
            env_file=env, project_root=project, landing_root=landing,
            lock_path=project / "data/state/d_owned_krx_short_selling.lock",
            session_getter=lambda: _AuthenticatedWrapper(session), execute_probe=execute,
            diagnostic_support=support,
        )
    assert not called


def test_zero_retry_is_explicitly_installed_on_every_auth_adapter():
    session = _AuthenticatedSession()
    base_runner._install_verified_zero_retry(session)
    for adapter in session.adapters.values():
        retry = adapter.max_retries
        assert (
            retry.total, retry.connect, retry.read, retry.redirect,
            retry.status, retry.other,
        ) == (0, 0, 0, 0, 0, 0)


def test_offline_verifier_preserves_original_chain_and_appends_idempotent_evidence(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    run_id = "20260813T102056Z_" + "a" * 32
    run_dir = project / "data/landing/diagnostics/a007_investor_s1" / run_id
    run_dir.mkdir(parents=True)
    _patch_fixture_plan(monkeypatch)
    body = _body_with_current()
    body_sha = hashlib.sha256(body).hexdigest()
    manifest = support.manifest_payload(
        run_id=run_id, created_at_utc="2026-08-13T10:20:56+00:00",
        dates=FIXTURE_DATES,
    )
    provenance = {
        "body_sha256": body_sha, "response_bytes": len(body),
        "http_status_code": 200, "raw_sequence": 6, "run_id": run_id,
        "scope_id": support.SCOPE_ID,
        "scope_sha256": support.scope_sha256(FIXTURE_DATES),
        "expected_dates": list(FIXTURE_DATES),
        "ledger_relative_path": (
            f"data/landing/diagnostics/a007_investor_s1/{run_id}/call_ledger.jsonl"
        ),
    }
    ledger = [
        {
            "event": "HTTP_RESPONSE", "authentication": True,
            "raw_sequence": sequence, "status_code": 200,
        }
        for sequence in range(1, 6)
    ] + [
        {
            "event": "SCOPE_STARTED", "bld": support.BUSINESS_BLD,
            "scope": support.SCOPE_ID, "params": dict(support.SCOPE),
            "business_request_limit": 1,
        },
        {
            "event": "HTTP_RESPONSE", "authentication": False,
            "raw_sequence": 6, "status_code": 200, "method": "POST",
            "url": "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            "scope": support.SCOPE_ID, "body_file": "response.json",
            "provenance_file": "response.json.provenance.json",
            "response_sha256": body_sha, "response_bytes": len(body),
            "recorded_at_utc": "2026-08-13T10:21:11+00:00",
        },
        {
            "event": "DIAGNOSTIC_STOPPED", "error": "TOP_LEVEL_SCHEMA_MISMATCH",
        },
    ]
    (run_dir / "response.json").write_bytes(body)
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "response.json.provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    (run_dir / "call_ledger.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in ledger), encoding="utf-8"
    )
    originals = {
        name: (run_dir / name).read_bytes()
        for name in ("response.json", "manifest.json", "response.json.provenance.json", "call_ledger.jsonl")
    }
    first = verifier.verify_retained_run(
        project_root=project, run_dir=run_dir, write_evidence=True
    )
    second = verifier.verify_retained_run(
        project_root=project, run_dir=run_dir, write_evidence=True
    )
    assert first["status"] == "VERIFIED"
    assert second["status"] == "ALREADY_VERIFIED"
    assert first["classification"] == "S1_FULL_RANGE_CONFIRMED"
    assert first["positive_total_dates"] == len(FIXTURE_DATES)
    assert len(list((run_dir / verifier.EVIDENCE_ROOT).glob("*.json"))) == 1
    assert all((run_dir / name).read_bytes() == value for name, value in originals.items())
    provenance["raw_sequence"] = 5
    (run_dir / "response.json.provenance.json").write_text(
        json.dumps(provenance), encoding="utf-8"
    )
    with pytest.raises(PilotStopped, match="OFFLINE_PROVENANCE_CHAIN_MISMATCH"):
        verifier.verify_retained_run(
            project_root=project, run_dir=run_dir, write_evidence=False
        )
