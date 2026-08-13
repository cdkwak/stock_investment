from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import requests

from stock_data.pipelines.kbsec_daily_snapshot import DailyCaptureSession, collect_daily_snapshot


FIXTURE = Path(__file__).parent / "fixtures/kbsec_ivsa0070.json"


class TwoResponseAdapter(requests.adapters.BaseAdapter):
    def __init__(self, market_status: int = 200, market_payload: dict | None = None):
        self.count = 0; self.market_status = market_status; self.market_payload = market_payload

    def send(self, request, **kwargs):
        self.count += 1
        response = requests.Response(); response.request = request
        response.headers["Content-Type"] = "application/json"
        if self.count == 1:
            response.status_code = 200
            payload = {
                "dataHeader": {"resultCode": "200", "processCode": "0000"},
                "dataBody": {"access_token": "unit-token", "expires_in": 3600},
            }
        else:
            response.status_code = self.market_status
            payload = self.market_payload or json.loads(FIXTURE.read_text(encoding="utf-8"))
        response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return response

    def close(self):
        pass


class TokenFailureAdapter(requests.adapters.BaseAdapter):
    def __init__(self): self.count = 0
    def send(self, request, **kwargs):
        self.count += 1
        response = requests.Response(); response.request = request; response.status_code = 500
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({
            "dataHeader": {"resultCode": "9999", "processCode": "E021", "processMessage": "blocked"}
        }).encode()
        return response
    def close(self): pass


def _environment(monkeypatch) -> None:
    monkeypatch.setenv("KBSEC_BASE_URL", "https://example.test")
    monkeypatch.setenv("KBSEC_APP_KEY", "unit-key")
    monkeypatch.setenv("KBSEC_APP_SECRET", "unit-secret")


def test_daily_success_is_landing_first_and_append_only(tmp_path: Path, monkeypatch) -> None:
    _environment(monkeypatch)
    adapter = TwoResponseAdapter(); session = DailyCaptureSession(); session.mount("https://", adapter)
    result = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        now=datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc), session=session,
    )
    assert result["status"] == "COMPLETE" and result["request_count"] == 2
    run = tmp_path / result["landing_run"]
    assert (run / "market_response.body").is_file()
    assert (run / "provenance.json").is_file()
    assert len((run / "call_ledger.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert all(result["normalized_counts"].values())
    second = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        now=datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc), session=DailyCaptureSession(),
    )
    assert second["status"] == "NOT_EXECUTED_ALREADY_ATTEMPTED_TODAY"


def test_token_failure_blocks_future_attempt_without_new_evidence(tmp_path: Path, monkeypatch) -> None:
    _environment(monkeypatch)
    adapter = TokenFailureAdapter(); session = DailyCaptureSession(); session.mount("https://", adapter)
    first = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        now=datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc), session=session,
    )
    assert first["status"] == "TOKEN_FAILED" and first["request_count"] == 1
    assert first["market_request_count"] == 0 and adapter.count == 1
    later = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        now=datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc), session=DailyCaptureSession(),
    )
    assert later == {"status": "NOT_EXECUTED_AUTH_REVIEW_REQUIRED", "network_calls": 0}


def test_one_off_validation_may_replace_only_retired_same_day_path(tmp_path: Path, monkeypatch) -> None:
    _environment(monkeypatch)
    state = {
        "schema": "stock_data.kbsec_daily_snapshot_state", "version": 1,
        "access_status": "AUTH_FIXED",
        "runs": [{"run_id": "old", "capture_date_kst": "2026-08-14", "status": "TOKEN_FAILED_RETIRED_PATH"}],
    }
    path = tmp_path / "data/state/kbsec_daily_snapshot.json"
    path.parent.mkdir(parents=True); path.write_text(json.dumps(state), encoding="utf-8")
    adapter = TwoResponseAdapter(); session = DailyCaptureSession(); session.mount("https://", adapter)
    result = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        confirm_one_off_auth_validation=True,
        now=datetime(2026, 8, 13, 22, 0, tzinfo=timezone.utc), session=session,
    )
    assert result["status"] == "COMPLETE"
    assert result["run_kind"] == "CORRECTED_AUTH_ONE_OFF_VALIDATION"
    assert result["request_count"] == 2
    scheduled = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        now=datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc), session=DailyCaptureSession(),
    )
    assert scheduled["status"] == "NOT_EXECUTED_ALREADY_ATTEMPTED_TODAY"


def test_post_close_date_validation_lands_and_compares_without_normalizing(tmp_path: Path, monkeypatch) -> None:
    _environment(monkeypatch)
    pre = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pre["dataBody"]["inq_dy_tm"] = "20260814"
    pre_run = tmp_path / "data/landing/kbsec/daily_snapshot/pre"
    pre_run.mkdir(parents=True)
    (pre_run / "market_response.json").write_text(json.dumps(pre), encoding="utf-8")
    state = {
        "schema": "stock_data.kbsec_daily_snapshot_state", "version": 1,
        "access_status": "AUTH_FIXED", "daily_snapshot_status": "DATE_SEMANTICS_REVIEW_REQUIRED",
        "runs": [{
            "run_id": "pre", "run_kind": "CORRECTED_AUTH_ONE_OFF_VALIDATION",
            "capture_date_kst": "2026-08-14", "status": "COMPLETE",
            "landing_run": "data/landing/kbsec/daily_snapshot/pre",
        }],
    }
    state_path = tmp_path / "data/state/kbsec_daily_snapshot.json"
    state_path.parent.mkdir(parents=True); state_path.write_text(json.dumps(state), encoding="utf-8")
    post = json.loads(FIXTURE.read_text(encoding="utf-8"))
    post["dataBody"]["inq_dy_tm"] = "20260814"
    post["dataBody"]["kspi_up_is_c"] = "777"
    post["dataBody"]["dt_5"] = "20260812"
    adapter = TwoResponseAdapter(market_payload=post)
    session = DailyCaptureSession(); session.mount("https://", adapter)
    result = collect_daily_snapshot(
        tmp_path, known_secrets=("unit-key", "unit-secret"),
        now=datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc), session=session,
    )
    assert result["status"] == "RAW_CAPTURED_DATE_REVIEW_REQUIRED"
    assert result["run_kind"] == "SCHEDULED_POST_CLOSE_DATE_VALIDATION"
    assert result["request_count"] == 2 and result["normalized_writes"] is False
    run = tmp_path / result["landing_run"]
    comparison = json.loads((run / "slice_date_comparison.json").read_text(encoding="utf-8"))
    assert comparison["slices"]["market_breadth"]["classification_candidate"] == "CURRENT_DAY_CLOSE"
    assert comparison["slices"]["market_liquidity"]["classification_candidate"] == "LAGGED_SOURCE_DATE"
    assert not (tmp_path / "data/normalized").exists()
