from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import pytest

from scripts.manual.backfill import backfill_bok_ecos_treasury as runner
from scripts.manual.backfill import bok_ecos_treasury_backfill_support as support
from scripts.manual.pilot import pilot_bok_ecos_treasury_page_semantics as page_runner
from stock_data.contracts.bok_ecos_treasury import (
    BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION as CONTRACT,
)
from stock_data.contracts.registry import CONTRACTS
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.data_v1 import validate_data_v1


def _plan_payload(metadata_hash="a" * 64):
    return {
        "dataset": CONTRACT.name, "contract_version": 1,
        "table_code": "817Y002", "table_name": "1.3.2.1. 시장금리(일별)",
        "cycle": "D", "metadata_summary_sha256": metadata_hash,
        "end_date": "20260813", "max_rows_per_request": 10000,
        "tenors": {
            tenor: {
                "maturity_years": int(tenor[:-1]), "item_code": f"ITEM{tenor}",
                "item_name": f"국고채({tenor[:-1]}년)", "unit_name": "연%",
                "start_date": f"20{index + 10:02d}0101",
            }
            for index, tenor in enumerate(support.TENORS)
        },
    }


def _write_plan(root, payload):
    path = root / "plan.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _response(plan, scope, dates=None, *, total=None):
    dates = dates or (scope.start_date, scope.end_date)
    rows = [{
        "STAT_CODE": plan.table_code, "STAT_NAME": plan.table_name,
        "ITEM_CODE1": scope.item_code, "ITEM_NAME1": scope.item_name,
        "UNIT_NAME": scope.unit_name, "TIME": value, "DATA_VALUE": "3.125",
    } for value in dates]
    payload = {support.OPERATION: {"list_total_count": len(rows) if total is None else total, "row": rows}}
    return json.dumps(payload, ensure_ascii=False).encode()


def _metadata_summary(plan):
    return {
        "six_tenor_identity": [{
            "tenor": scope.tenor, "STAT_CODE": plan.table_code,
            "STAT_NAME": plan.table_name, "ITEM_CODE": scope.item_code,
            "ITEM_NAME": scope.item_name, "CYCLE": plan.cycle,
            "UNIT_NAME": scope.unit_name, "START_TIME": scope.start_date,
            "END_TIME": scope.end_date,
        } for scope in plan.scopes]
    }


class Session:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        path = unquote(url).rstrip("/").split("/")
        item_code = path[-1]
        start, end = path[-3], path[-2]
        plan = self.plan
        scope = next(value for value in plan.scopes if value.item_code == item_code)
        body = _response(plan, scope, dates=(start, end))
        return type("Response", (), {"status_code": 200, "content": body})()


def _prepared_project(tmp_path):
    preliminary = _plan_payload()
    preliminary_path = _write_plan(tmp_path, preliminary)
    preliminary_plan = support.load_plan(preliminary_path)
    summary_body = (json.dumps(
        _metadata_summary(preliminary_plan), ensure_ascii=False,
        sort_keys=True, indent=2,
    ) + "\n").encode()
    digest = hashlib.sha256(summary_body).hexdigest()
    payload = _plan_payload(digest)
    plan_path = _write_plan(tmp_path, payload)
    plan = support.load_plan(plan_path)
    summary_body = (json.dumps(
        _metadata_summary(plan), ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n").encode()
    assert hashlib.sha256(summary_body).hexdigest() == digest
    metadata = tmp_path / "data/landing/diagnostics/bok_ecos_treasury_pilot/metadata_fixture"
    metadata.mkdir(parents=True)
    (metadata / "metadata_summary.json").write_bytes(summary_body)
    return plan_path, plan


def test_contract_is_distinct_from_toss_and_registered():
    assert CONTRACT.name in CONTRACTS
    assert CONTRACT.name != "kr_treasury_yield_daily"
    assert CONTRACT.primary_key == ("capture_id", "source_item_code", "source_item_ordinal")
    assert "open" not in CONTRACT.column_names and "yield_percent" in CONTRACT.column_names
    assert "published_at_utc" in CONTRACT.column_names


def test_plan_has_exact_six_request_strategy_and_parser_preserves_unknown_availability(tmp_path):
    plan = support.load_plan(_write_plan(tmp_path, _plan_payload()))
    assert len(plan.scopes) == support.MAX_REQUESTS == 6
    scope = plan.scopes[0]
    body = _response(plan, scope)
    frame = support.parse_response(
        body, plan, scope, capture_id="capture", captured_at_utc="2026-08-13T12:00:00+00:00",
        landing_response_sha256=hashlib.sha256(body).hexdigest(),
    )
    assert len(frame) == 2 and frame["yield_percent"].tolist()[0].as_tuple().exponent == -3
    assert frame["published_at_utc"].isna().all() and frame["revision_id"].isna().all()
    assert frame["availability_status"].eq(support.AVAILABILITY).all()


def test_parser_rejects_truncation_identity_and_excess_precision(tmp_path):
    plan = support.load_plan(_write_plan(tmp_path, _plan_payload()))
    scope = plan.scopes[0]
    kwargs = dict(capture_id="x", captured_at_utc="2026-08-13T12:00:00+00:00",
                  landing_response_sha256="b" * 64)
    with pytest.raises(support.BackfillError, match="truncated"):
        support.parse_response(_response(plan, scope, total=3), plan, scope, **kwargs)
    payload = json.loads(_response(plan, scope))
    payload[support.OPERATION]["row"][0]["STAT_NAME"] = "different"
    with pytest.raises(support.BackfillError, match="identity"):
        support.parse_response(json.dumps(payload).encode(), plan, scope, **kwargs)
    payload = json.loads(_response(plan, scope))
    payload[support.OPERATION]["row"][0]["DATA_VALUE"] = "3.1251"
    with pytest.raises(support.BackfillError, match="precision"):
        support.parse_response(json.dumps(payload).encode(), plan, scope, **kwargs)


def test_backfill_is_six_requests_landing_first_and_resumable(tmp_path, monkeypatch):
    plan_path, plan = _prepared_project(tmp_path)
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret")
    first = Session(); first.plan = plan
    sleeps = []
    def stop_after_two(seconds):
        if len(sleeps) == 1:
            raise RuntimeError("clean stop")
        sleeps.append(seconds)
    with pytest.raises(RuntimeError, match="clean stop"):
        runner.run_backfill(
            project_root=tmp_path, plan_path=plan_path,
            approve_plan_sha256=support.plan_sha256(plan), session=first,
            sleep_fn=stop_after_two, jitter_fn=lambda low, high: 4.0,
        )
    assert len(first.calls) == 2
    run_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("run_*"))
    second = Session(); second.plan = plan
    result = runner.run_backfill(
        project_root=tmp_path, plan_path=plan_path,
        approve_plan_sha256=support.plan_sha256(plan), resume_run_dir=run_dir,
        session=second, sleep_fn=lambda seconds: None,
        jitter_fn=lambda low, high: 4.0,
    )
    assert len(second.calls) == 4
    assert result["status"] == "DATA_COMPLETE_REVIEW_REQUIRED"
    assert result["raw_requests"] == 6 and result["rows_in_capture"] == 12
    ledger = (run_dir / "call_ledger.jsonl").read_text(encoding="utf-8")
    assert "literal-secret" not in ledger
    restored = read_dataset(
        tmp_path / runner.NORMALIZED_RELATIVE, CONTRACT,
        lambda frame: validate_data_v1(frame, CONTRACT, allow_empty=False),
    )
    assert len(restored) == 12


def test_live_cli_requires_explicit_confirmation():
    with pytest.raises(SystemExit, match="explicit live"):
        runner.main([
            "--project-root", ".", "--plan", "missing.json",
            "--approve-plan-sha256", "a" * 64,
        ])


def test_backfill_adopts_exact_page_pilot_and_makes_only_five_requests(tmp_path, monkeypatch):
    payload = _plan_payload()
    payload["tenors"]["3Y"]["start_date"] = "19981113"
    preliminary_path = _write_plan(tmp_path, payload)
    preliminary = support.load_plan(preliminary_path)
    body = (json.dumps(_metadata_summary(preliminary), ensure_ascii=False,
                       sort_keys=True, indent=2) + "\n").encode()
    digest = hashlib.sha256(body).hexdigest()
    payload["metadata_summary_sha256"] = digest
    plan_path = _write_plan(tmp_path, payload)
    plan = support.load_plan(plan_path)
    metadata = tmp_path / "data/landing/diagnostics/bok_ecos_treasury_pilot/metadata_fixture"
    metadata.mkdir(parents=True)
    (metadata / "metadata_summary.json").write_bytes(body)
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret")

    class PageSession:
        def __init__(self): self.calls = []
        def get(self, url, timeout):
            self.calls.append((url, timeout))
            scope = next(value for value in plan.scopes if value.tenor == "3Y")
            content = _response(plan, scope, dates=(scope.start_date, scope.end_date))
            return type("Response", (), {"status_code": 200, "content": content})()

    page_session = PageSession()
    page_runner.run_pilot(
        project_root=tmp_path, plan_path=plan_path,
        approve_plan_sha256=support.plan_sha256(plan), session=page_session,
    )
    page_dir = next((tmp_path / page_runner.LANDING_RELATIVE).glob("run_*"))
    backfill_session = Session(); backfill_session.plan = plan
    result = runner.run_backfill(
        project_root=tmp_path, plan_path=plan_path,
        approve_plan_sha256=support.plan_sha256(plan),
        adopt_3y_page_run_dir=page_dir, session=backfill_session,
        sleep_fn=lambda seconds: None, jitter_fn=lambda low, high: 4.0,
    )
    assert len(page_session.calls) == 1
    assert len(backfill_session.calls) == 5
    assert all("ITEM3Y" not in unquote(url) for url, _ in backfill_session.calls)
    assert result["raw_requests"] == 5 and result["source_responses"] == 6
    assert result["adopted_scopes"] == ["3Y"]
    run_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("run_*"))
    ledger = [json.loads(line) for line in (run_dir / "call_ledger.jsonl").read_text().splitlines()]
    assert sum(row["event"] == "ADOPTED_RESPONSE" for row in ledger) == 1
    assert sum(row["event"] == "HTTP_RESPONSE" for row in ledger) == 5
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    assert checkpoint["completed"]["3Y"]["adopted"] is True
    assert checkpoint["completed"]["3Y"]["source_run"].startswith("data/landing/diagnostics/")
