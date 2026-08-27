from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.manual.backfill import backfill_pykrx_high_value_raw as subject


def body(dataset: str) -> bytes:
    row = {key: "1" for key in subject.CONFIG[dataset]["fields"]}
    row["ISU_SRT_CD"] = "005930"
    return json.dumps({"output": [row]}).encode()


def fundamental_duplicate_body(*, conflicting: bool) -> bytes:
    dataset = "kr_equity_fundamental_daily"
    first = {key: "1" for key in subject.CONFIG[dataset]["fields"]}
    first.update(ISU_SRT_CD="020560", ISU_ABBRV="아시아나항공", BPS="5,801")
    second = dict(first)
    if conflicting:
        second["BPS"] = "5,800"
    return json.dumps({"output": [first, second]}, ensure_ascii=False).encode()


@pytest.mark.parametrize("dataset", tuple(subject.CONFIG))
def test_source_body_gate_and_exact_request(dataset):
    rows, digest = subject._validate_body(dataset, "20260812", body(dataset))
    assert rows == 1
    assert digest == hashlib.sha256(body(dataset)).hexdigest()
    assert subject.expected_payload(dataset, "20260812")["bld"] == subject.CONFIG[dataset]["bld"]


def test_restriction_and_empty_stop():
    with pytest.raises(subject.PilotStopped, match="HTML_OR_RESTRICTION"):
        subject._validate_body("kr_equity_foreign_ownership_daily", "20260812", b"<html>blocked</html>")
    with pytest.raises(subject.PilotStopped, match="ANOMALOUS_EMPTY"):
        subject._validate_body("kr_equity_fundamental_daily", "20260812", b'{"output":[]}')


def test_equity_fundamental_request_matches_pykrx_core_signature():
    assert subject.expected_payload("kr_equity_fundamental_daily", "20260812") == {
        "mktId": "ALL",
        "trdDd": "20260812",
        "bld": "dbms/MDC/STAT/standard/MDCSTAT03501",
    }


@pytest.mark.parametrize(
    ("conflicting", "classification", "differing"),
    [
        (False, "EXACT_PROVIDER_DUPLICATE", []),
        (True, "CONFLICTING_PROVIDER_DUPLICATE", ["BPS"]),
    ],
)
def test_fundamental_provider_duplicates_preserve_source_ordinals(conflicting, classification, differing):
    analysis = subject._analyze_body(
        "kr_equity_fundamental_daily", "20080328",
        fundamental_duplicate_body(conflicting=conflicting),
    )
    assert analysis["rows"] == 2
    assert analysis["row_identity"] == "source_row_ordinal_1_based"
    assert analysis["provider_duplicate_groups"] == [{
        "entity_key": "020560",
        "source_row_ordinals": [1, 2],
        "classification": classification,
        "differing_fields": differing,
    }]


def test_20080328_retained_regression_is_conflicting_provider_duplicate():
    retained = subject.ROOT / (
        "data/landing/pykrx/high_value_raw/kr_equity_fundamental_daily/"
        "plan=c28d1be22342a7af22e0d8efd0db4d7ec9cce69c90934c19a83030addf5b0e30/"
        "date=20080328/response.json"
    )
    analysis = subject._analyze_body("kr_equity_fundamental_daily", "20080328", retained.read_bytes())
    assert analysis["provider_duplicate_groups"] == [{
        "entity_key": "020560",
        "source_row_ordinals": [1006, 1007],
        "classification": "CONFLICTING_PROVIDER_DUPLICATE",
        "differing_fields": ["BPS"],
    }]


def test_checkpoint_rechecks_landing_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    dataset = "kr_equity_foreign_ownership_daily"
    response = tmp_path / "landing/response.json"
    response.parent.mkdir(parents=True)
    response.write_bytes(body(dataset))
    digest = hashlib.sha256(response.read_bytes()).hexdigest()
    provenance = response.with_name("provenance.json")
    provenance.write_text(json.dumps({"market_date": "20260812", "response_sha256": digest}))
    checkpoint = {"completed": {"20260812": {"rows": 1, "body_path": "landing/response.json", "body_sha256": digest, "provenance_path": "landing/provenance.json"}}}
    subject._verify_completed(dataset, checkpoint)
    response.write_bytes(b"{}")
    with pytest.raises(subject.PilotStopped, match="LANDING_CHECKPOINT_MISMATCH"):
        subject._verify_completed(dataset, checkpoint)


def test_plan_is_date_bound_and_normalized_is_absent():
    dates = ["20080102", "20080103"]
    assert subject.plan_sha("kr_etf_universe_daily", dates) != subject.plan_sha("kr_etf_universe_daily", list(reversed(dates)))
    assert "normalized" not in subject.LANDING_ROOT.as_posix()


def test_etf_ohlcv_rejects_missing_required_field():
    raw = json.loads(body("kr_etf_universe_daily"))
    del raw["output"][0]["ACC_TRDVAL"]
    with pytest.raises(subject.PilotStopped, match="SCHEMA_CHANGE"):
        subject._analyze_etf_ohlcv_body("20260812", json.dumps(raw).encode())


def test_etf_ohlcv_plan_is_bound_to_the_shared_universe_plan():
    dates = ["20080102", "20080103"]
    assert subject.etf_ohlcv_plan_sha(dates, "a" * 64) != subject.etf_ohlcv_plan_sha(dates, "b" * 64)


def test_success_orphan_is_recovered_without_request(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    dataset, date = "kr_equity_foreign_ownership_daily", "20260811"
    run_dir = tmp_path / "landing"
    day = run_dir / f"date={date}"
    day.mkdir(parents=True)
    raw = body(dataset)
    (day / "response.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (run_dir / "call_ledger.jsonl").write_text(json.dumps({"event": "HTTP_RESPONSE", "market_date": date, "response_sha256": digest, "status_code": 200, "recorded_at_utc": "2026-08-15T00:00:00Z"}) + "\n")
    checkpoint = {"completed": {}}
    subject._recover_orphans(dataset, checkpoint, run_dir)
    assert checkpoint["completed"][date]["classification"] == "RECOVERED_SUCCESS_ORPHAN"
    assert (day / "provenance.json").is_file()


def test_conflicting_fundamental_orphan_is_adopted_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "ROOT", tmp_path)
    dataset, date = "kr_equity_fundamental_daily", "20080328"
    run_dir = tmp_path / "landing"
    day = run_dir / f"date={date}"
    day.mkdir(parents=True)
    raw = fundamental_duplicate_body(conflicting=True)
    (day / "response.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    (run_dir / "call_ledger.jsonl").write_text(json.dumps({
        "event": "HTTP_RESPONSE", "market_date": date, "response_sha256": digest,
        "status_code": 200, "recorded_at_utc": "2026-08-16T00:00:00Z",
        "business_sequence": 58,
    }) + "\n")
    checkpoint = {"completed": {}, "business_calls": 57, "normalized_writes": False}
    subject._recover_orphans(dataset, checkpoint, run_dir)
    record = checkpoint["completed"][date]
    assert record["classification"] == "RECOVERED_PROVIDER_DUPLICATE_OBSERVATION"
    assert record["provider_duplicate_groups"][0]["classification"] == "CONFLICTING_PROVIDER_DUPLICATE"
    assert checkpoint["business_calls"] == 58
    assert checkpoint["normalized_writes"] is False


def test_credentials_are_installed_process_only_for_pykrx(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("KRX_ID=user\nKRX_PW=password\n")
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    assert subject._load_credentials(env) == ("user", "password")
    assert not (tmp_path / "token.json").exists()
