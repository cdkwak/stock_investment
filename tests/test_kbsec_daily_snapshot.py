from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import requests

from stock_data.pipelines.kbsec_daily_snapshot import DailyCaptureSession, collect_daily_snapshot


FIXTURE = Path(__file__).parent / "fixtures/kbsec_ivsa0070.json"


class TwoResponseAdapter(requests.adapters.BaseAdapter):
    def __init__(self, market_status: int = 200):
        self.count = 0; self.market_status = market_status

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
            payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
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
