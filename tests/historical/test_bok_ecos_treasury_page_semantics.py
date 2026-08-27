from __future__ import annotations

import hashlib
import json

import pytest

from scripts.manual.pilot import pilot_bok_ecos_treasury_page_semantics as runner
from scripts.manual.backfill.bok_ecos_treasury_backfill_support import load_plan, plan_sha256
from tests.historical.test_bok_ecos_treasury_backfill import _metadata_summary, _plan_payload, _response, _write_plan


class Session:
    def __init__(self, plan, *, endpoints=True):
        self.plan, self.endpoints, self.calls = plan, endpoints, []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        scope = next(value for value in self.plan.scopes if value.tenor == "3Y")
        dates = (scope.start_date, scope.end_date) if self.endpoints else ("20000103", scope.end_date)
        body = _response(self.plan, scope, dates=dates)
        return type("Response", (), {"status_code": 200, "content": body})()


def prepared(tmp_path):
    payload = _plan_payload()
    payload["tenors"]["3Y"]["start_date"] = "19981113"
    preliminary_path = _write_plan(tmp_path, payload)
    preliminary = load_plan(preliminary_path)
    body = (json.dumps(_metadata_summary(preliminary), ensure_ascii=False,
                       sort_keys=True, indent=2) + "\n").encode()
    digest = hashlib.sha256(body).hexdigest()
    payload = _plan_payload(digest)
    payload["tenors"]["3Y"]["start_date"] = "19981113"
    path = _write_plan(tmp_path, payload)
    plan = load_plan(path)
    body = (json.dumps(_metadata_summary(plan), ensure_ascii=False,
                       sort_keys=True, indent=2) + "\n").encode()
    metadata = tmp_path / "data/landing/diagnostics/bok_ecos_treasury_pilot/metadata_fixture"
    metadata.mkdir(parents=True)
    (metadata / "metadata_summary.json").write_bytes(body)
    return path, plan


def test_page_semantics_pilot_is_exactly_one_call_landing_only(tmp_path, monkeypatch):
    path, plan = prepared(tmp_path)
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret")
    session = Session(plan)
    result = runner.run_pilot(
        project_root=tmp_path, plan_path=path,
        approve_plan_sha256=plan_sha256(plan), session=session,
    )
    assert len(session.calls) == 1
    assert result["status"] == "PAGE_SEMANTICS_PASS_REVIEW_REQUIRED"
    assert result["declared_total"] == result["rows_returned"] == result["unique_dates"] == 2
    assert not (tmp_path / "data/normalized").exists()
    run_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("run_*"))
    assert "literal-secret" not in (run_dir / "call_ledger.jsonl").read_text()
    with pytest.raises(Exception, match="already been attempted"):
        runner.run_pilot(
            project_root=tmp_path, plan_path=path,
            approve_plan_sha256=plan_sha256(plan), session=Session(plan),
        )


def test_page_semantics_pilot_stops_on_endpoint_mismatch(tmp_path, monkeypatch):
    path, plan = prepared(tmp_path)
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret")
    session = Session(plan, endpoints=False)
    with pytest.raises(Exception, match="endpoints differ"):
        runner.run_pilot(
            project_root=tmp_path, plan_path=path,
            approve_plan_sha256=plan_sha256(plan), session=session,
        )
    assert len(session.calls) == 1
    run_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("run_*"))
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    assert checkpoint["status"] == "STOPPED_ENDPOINT_MISMATCH"
    assert not (tmp_path / "data/normalized").exists()


def test_page_semantics_cli_requires_confirmation():
    with pytest.raises(SystemExit, match="one-request confirmation"):
        runner.main([
            "--project-root", ".", "--plan", "missing.json",
            "--approve-plan-sha256", "a" * 64,
        ])
