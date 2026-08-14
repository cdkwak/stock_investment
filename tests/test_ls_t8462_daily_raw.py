import json
from pathlib import Path
import sys

import pytest

import scripts.manual.collect_ls_t8462_daily_raw as daily
from scripts.manual.collect_ls_t8462_daily_raw import (
    INSTITUTION_FIELDS, daily_scopes, institution_reconciliation,
    retention_transition, validate_market_date,
)
from scripts.manual.ls_derivatives_raw_backfill import EXPECTED_ROW_KEYS


class _Response:
    def __init__(self, payload, *, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {"content-type": "application/json", "tr_cont": "N"}
        self.content = json.dumps(payload, sort_keys=True).encode("utf-8")

    def json(self):
        return self._payload


def test_daily_plan_is_exact_18_raw_scopes_with_unresolved_codes_preserved():
    plan = daily_scopes("20260814")
    assert len(plan) == 18
    assert {(item["asset_code"], item["product_code"], item["requested_session_code"]) for item in plan} == {
        (asset, product, session)
        for asset in ("K2I", "MKI") for product in ("F", "C", "P") for session in ("D", "N", "U")
    }
    assert {item["from_date"] for item in plan} == {"20200101"}
    assert {item["to_date"] for item in plan} == {"20260814"}


def test_invalid_market_date_fails_before_network():
    for value in ("2026-08-14", "20260230", "abc"):
        with pytest.raises(ValueError):
            validate_market_date(value)


def test_provider_aggregate_is_authoritative_and_components_are_sidecar_only():
    row = {key: 0 for key in EXPECTED_ROW_KEYS}
    row.update({"date": "20250801", "sv_18": 10, "sv_01": 7})
    scope = next(item for item in daily_scopes("20260814") if item["asset_code"] == "K2I" and item["product_code"] == "C" and item["requested_session_code"] == "U")
    result = institution_reconciliation([row], scope)[0]
    assert "sv_18" not in result
    assert result["institution_provider_aggregate"] == 10
    assert result["institution_components_sum"] == 7
    assert result["institution_aggregate_difference"] == 3
    assert result["institution_aggregate_status"] == "OPTION_SPECIFIC_SEMANTICS"
    assert "sv_15" in INSTITUTION_FIELDS


def test_retention_moves_only_when_next_observed_trading_date_replaces_boundary():
    previous = {"earliest_market_date": "20250718", "second_market_date": "20250721"}
    assert retention_transition(previous, ["20250718", "20250721"]) == "OBSERVED_EARLIEST_ONLY"
    assert retention_transition(previous, ["20250721", "20250722"]) == "ROLLING_RETENTION"
    assert retention_transition(previous, ["20250722"]) == "EARLIEST_MOVED_REVIEW_REQUIRED"


def test_live_shape_is_18_calls_durable_and_same_date_rerun_is_pre_network(tmp_path, monkeypatch):
    token = "runtime-token-value"
    calls = []

    class _Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            block = kwargs["json"]["t8462InBlock"]
            row = {key: "0" for key in EXPECTED_ROW_KEYS}
            row["date"] = "20260818"
            return _Response({
                "rsp_cd": "00000",
                "t8462OutBlock": {
                    "tm_rng": block["tm_rng"],
                    "fot_clsf_cd": block["fot_clsf_cd"],
                    "bsc_asts_id": block["bsc_asts_id"],
                },
                "t8462OutBlock1": [row],
            })

    oauth_calls = []
    monkeypatch.setenv("LS_APP_KEY", "configured-app-key")
    monkeypatch.setenv("LS_APP_SECRET", "configured-app-secret")
    monkeypatch.setenv("LS_BASE_URL", daily.OFFICIAL_BASE_URL)
    monkeypatch.setattr(daily.requests, "Session", _Session)
    monkeypatch.setattr(daily, "post_oauth_once", lambda *args: oauth_calls.append(args) or _Response({"access_token": token}))
    monkeypatch.setattr(daily, "MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(daily, "baseline_retention", lambda _root: {
        daily.scope_id(scope): {"earliest_market_date": "20250718", "second_market_date": "20250721"}
        for scope in daily.scopes()
    })
    argv = ["collect", "--root", str(tmp_path), "--market-date", "20260818", "--confirm-live-daily-raw"]
    monkeypatch.setattr(sys, "argv", argv)

    assert daily.main() == 0
    assert len(oauth_calls) == 1
    assert len(calls) == 18
    run_dir = next((tmp_path / "data/landing/ls_openapi/t8462_daily_raw/20260818").iterdir())
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "DAILY_COLLECTION_COMPLETE"
    assert checkpoint["oauth_calls"] == 1 and checkpoint["data_calls"] == 18
    assert checkpoint["artifact_counts"] == {
        "raw_responses": 18, "provenance_sidecars": 18, "ledger_events": 19,
    }
    assert checkpoint["secret_scan"] == "PASS"
    ledger = [json.loads(line) for line in (run_dir / "call_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(ledger) == 19 and sum(item["outcome"] == "PASS" for item in ledger) == 19
    assert len(list(run_dir.glob("*.response.json"))) == 18
    assert len(list(run_dir.glob("*.provenance.json"))) == 18
    assert all(item["earliest_market_date"] == "20260818" for item in checkpoint["retention"].values())
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["target_market_date_present"]
        for path in run_dir.glob("*.provenance.json")
    )

    assert daily.main() == 3
    assert len(oauth_calls) == 1
    assert len(calls) == 18
