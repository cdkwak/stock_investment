from __future__ import annotations

from datetime import date
import hashlib
import json

import pytest

from stock_data.orchestration import kr_etf_raw_daily_incremental as subject


TARGET = date(2026, 8, 19)


def _row(symbol: str, *, close: str = "10,100") -> dict[str, str]:
    return {
        "ISU_SRT_CD": symbol, "ISU_CD": f"KR7{symbol}008", "SECUGRP_ID": "EF",
        "ISU_ABBRV": f"ETF {symbol}", "TDD_CLSPRC": close, "NAV": "10,105.25",
        "TDD_OPNPRC": "10,000", "TDD_HGPRC": "10,200", "TDD_LWPRC": "9,900",
        "ACC_TRDVOL": "1,000", "ACC_TRDVAL": "10,050,000",
        "MKTCAP": "100,000,000", "INVSTASST_NETASST_TOTAMT": "101,000,000",
        "LIST_SHRS": "10,000",
    }


def _body(rows=None) -> bytes:
    if rows is None:
        rows = [_row("069500"), _row("357870")]
    return json.dumps({"output": rows}, ensure_ascii=False, sort_keys=True).encode()


def _write_baseline(root) -> tuple[bytes, bytes]:
    landing = root / "data/landing/pykrx/high_value_raw/kr_etf_universe_daily/date=20260812"
    landing.mkdir(parents=True)
    body = _body()
    (landing / "response.json").write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    relative = (landing / "response.json").relative_to(root).as_posix()
    record = {"body_path": relative, "body_sha256": digest, "rows": 2}
    state = root / "data/state/pykrx_high_value_raw"
    state.mkdir(parents=True)
    universe = {
        "dataset": subject.ETF_UNIVERSE_DATASET, "status": "RAW_BACKFILL_COMPLETE",
        "completed": {"20260812": record},
    }
    ohlcv = {
        "dataset": subject.ETF_OHLCV_DATASET, "status": "RAW_BACKFILL_COMPLETE",
        "source_dataset": subject.ETF_UNIVERSE_DATASET, "raw_bytes_copied": False,
        "business_calls": 0, "completed": {"20260812": record},
    }
    upath = state / "kr_etf_universe_daily.json"
    opath = state / "kr_etf_ohlcv_daily.json"
    upath.write_text(json.dumps(universe), encoding="utf-8")
    opath.write_text(json.dumps(ohlcv), encoding="utf-8")
    return upath.read_bytes(), opath.read_bytes()


def _ready_plan(root, **changes):
    values = {
        "project_root": root, "market_date": TARGET,
        "latest_finalized_market_date": TARGET,
        "accepted_market_dates": (TARGET,), "source_scope_reviewed": True,
        "publication_finality_reviewed": True, "revision_policy_reviewed": True,
        "delisting_policy_reviewed": True, "row_count_bounds": (2, 3),
    }
    values.update(changes)
    return subject.plan_etf_raw_daily(**values)


def _capture(body=None):
    return subject.ETFRawCapture(
        market_date=TARGET, body=_body() if body is None else body,
        captured_at_utc="2026-08-20T01:00:00+00:00",
        request_payload={"trdDd": "20260819", "bld": subject.SOURCE_OPERATION},
    )


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"source_scope_reviewed": False}, "FULL_MARKET_SOURCE_SCOPE_REVIEW_REQUIRED"),
        ({"publication_finality_reviewed": False}, "PUBLICATION_FINALITY_REVIEW_REQUIRED"),
        ({"revision_policy_reviewed": False}, "REVISION_POLICY_REVIEW_REQUIRED"),
        ({"delisting_policy_reviewed": False}, "DELISTING_POLICY_REVIEW_REQUIRED"),
        ({"row_count_bounds": None}, "COMPLETENESS_BOUNDS_REVIEW_REQUIRED"),
    ],
)
def test_plan_fails_closed_on_each_unresolved_semantic_gate(tmp_path, override, reason):
    _write_baseline(tmp_path)
    plan = _ready_plan(tmp_path, **override)
    assert (plan.action, plan.reason, plan.estimated_business_calls) == ("BLOCKED", reason, 0)
    with pytest.raises(subject.ETFRawDailyError, match=reason):
        subject.execute_etf_raw_daily(
            plan, project_root=tmp_path,
            capture_builder=lambda _: pytest.fail("blocked plan reached provider"),
        )


def test_one_shared_capture_promotes_both_references_and_replay_is_api_zero(tmp_path):
    baseline_before = _write_baseline(tmp_path)
    result = subject.execute_etf_raw_daily(
        _ready_plan(tmp_path), project_root=tmp_path, capture_builder=lambda _: _capture(),
    )
    assert result["status"] == "SUCCEEDED"
    assert (result["business_calls"], result["promoted_logical_datasets"], result["rows"]) == (1, 2, 2)
    checkpoint = json.loads(
        (tmp_path / "data/state/kr_etf_raw_daily_incremental.json").read_text()
    )
    record = checkpoint["completed_dates"][TARGET.isoformat()]
    universe = record["logical_references"][subject.ETF_UNIVERSE_DATASET]
    ohlcv = record["logical_references"][subject.ETF_OHLCV_DATASET]
    assert universe["body_path"] == ohlcv["body_path"] == record["body_path"]
    assert universe["body_sha256"] == ohlcv["body_sha256"] == record["body_sha256"]
    assert len(list((tmp_path / "data/landing/pykrx/etf_daily_raw").glob("run=*/response.json"))) == 1
    baseline_paths = tmp_path / "data/state/pykrx_high_value_raw"
    assert (baseline_paths / "kr_etf_universe_daily.json").read_bytes() == baseline_before[0]
    assert (baseline_paths / "kr_etf_ohlcv_daily.json").read_bytes() == baseline_before[1]

    replay_plan = _ready_plan(tmp_path)
    assert replay_plan.action == "NOOP_ALREADY_SUCCEEDED"
    replay = subject.execute_etf_raw_daily(
        replay_plan, project_root=tmp_path,
        capture_builder=lambda _: pytest.fail("replay reached provider"),
    )
    assert replay == {
        "status": "NOOP_ALREADY_SUCCEEDED", "business_calls": 0,
        "retry_count": 0, "promoted_logical_datasets": 0,
    }


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([_row("069500"), _row("069500")], "DUPLICATE_DATE_SYMBOL"),
        ([_row("069500", close="10,300"), _row("357870")], "INVALID_ETF_OHLC_HIGH"),
        ([_row("069500")], "FULL_MARKET_COMPLETENESS_OUTSIDE_REVIEWED_BOUNDS"),
    ],
)
def test_invalid_or_incomplete_capture_is_retained_but_checkpoint_unchanged(tmp_path, rows, error):
    _write_baseline(tmp_path)
    checkpoint = tmp_path / "data/state/kr_etf_raw_daily_incremental.json"
    before = subject._checkpoint(tmp_path, TARGET)
    with pytest.raises(subject.ETFRawDailyError, match=error):
        subject.execute_etf_raw_daily(
            _ready_plan(tmp_path), project_root=tmp_path,
            capture_builder=lambda _: _capture(_body(rows)),
        )
    assert not checkpoint.exists() or json.loads(checkpoint.read_text()) == before
    assert len(list((tmp_path / "data/landing/pykrx/etf_daily_raw").glob("run=*/response.json"))) == 1
    journal = json.loads(
        (tmp_path / "data/state/transactions/kr_etf_raw_daily_20260819.json").read_text()
    )
    assert journal["status"] == "FAILED"


def test_valid_empty_is_explicitly_retained_and_never_promoted(tmp_path):
    _write_baseline(tmp_path)
    with pytest.raises(subject.ETFRawValidEmpty, match="VALID_EMPTY"):
        subject.execute_etf_raw_daily(
            _ready_plan(tmp_path), project_root=tmp_path,
            capture_builder=lambda _: _capture(_body([])),
        )
    assert not (tmp_path / "data/state/kr_etf_raw_daily_incremental.json").exists()
    provenance = json.loads(next(
        (tmp_path / "data/landing/pykrx/etf_daily_raw").glob("run=*/provenance.json")
    ).read_text())
    assert (provenance["classification"], provenance["accepted"]) == (
        "VALID_EMPTY_REJECTED", False,
    )
    journal = json.loads(
        (tmp_path / "data/state/transactions/kr_etf_raw_daily_20260819.json").read_text()
    )
    assert journal["status"] == "VALID_EMPTY_RETAINED"


def test_capture_failure_preserves_prior_checkpoint(tmp_path):
    _write_baseline(tmp_path)
    checkpoint = tmp_path / "data/state/kr_etf_raw_daily_incremental.json"
    initial = subject._checkpoint(tmp_path, TARGET)
    subject._atomic_json(checkpoint, initial)
    before = checkpoint.read_bytes()

    def fail(_):
        raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        subject.execute_etf_raw_daily(_ready_plan(tmp_path), project_root=tmp_path, capture_builder=fail)
    assert checkpoint.read_bytes() == before


def test_exception_after_checkpoint_promotion_rolls_back_exact_prior_state(tmp_path, monkeypatch):
    _write_baseline(tmp_path)
    checkpoint = tmp_path / "data/state/kr_etf_raw_daily_incremental.json"
    subject._atomic_json(checkpoint, subject._checkpoint(tmp_path, TARGET))
    before = checkpoint.read_bytes()
    original = subject._verify_completed

    def fail_after_promotion(root, record, market_date):
        if market_date == TARGET:
            raise OSError("injected post-promotion failure")
        return original(root, record, market_date)

    monkeypatch.setattr(subject, "_verify_completed", fail_after_promotion)
    with pytest.raises(OSError, match="post-promotion"):
        subject.execute_etf_raw_daily(
            _ready_plan(tmp_path), project_root=tmp_path, capture_builder=lambda _: _capture(),
        )
    assert checkpoint.read_bytes() == before
    journal = json.loads(
        (tmp_path / "data/state/transactions/kr_etf_raw_daily_20260819.json").read_text()
    )
    assert journal["status"] == "FAILED"


@pytest.mark.parametrize("interrupted_status", ["STAGED", "CHECKPOINT_PROMOTED"])
def test_restart_recovers_interrupted_checkpoint_promotion(tmp_path, interrupted_status):
    _write_baseline(tmp_path)
    checkpoint = tmp_path / "data/state/kr_etf_raw_daily_incremental.json"
    original = subject._checkpoint(tmp_path, TARGET)
    subject._atomic_json(checkpoint, original)
    before = checkpoint.read_bytes()
    stage = tmp_path / "data/staging/kr_etf_raw_daily/run=interrupted"
    stage.mkdir(parents=True)
    previous = stage / "previous_checkpoint.json"
    previous.write_bytes(before)
    changed = dict(original)
    changed["completed_dates"] = {TARGET.isoformat(): {"partial": True}}
    subject._atomic_json(checkpoint, changed)
    journal = tmp_path / "data/state/transactions/kr_etf_raw_daily_20260819.json"
    subject._atomic_json(journal, {
        "schema": "kr_etf_raw_daily.transaction.v1", "run_id": "interrupted",
        "market_date": TARGET.isoformat(), "logical_datasets": list(subject.LOGICAL_DATASETS),
        "checkpoint_existed": True, "previous_checkpoint_path": str(previous.resolve()),
        "landing_response_path": "data/landing/retained.json",
        "status": interrupted_status,
    })
    assert subject.recover_etf_raw_daily(tmp_path, TARGET) == "RECOVERED"
    assert checkpoint.read_bytes() == before
    assert json.loads(journal.read_text())["status"] == "RECOVERED"


def test_capture_scope_call_budget_and_exact_date_are_enforced(tmp_path):
    _write_baseline(tmp_path)
    bad = subject.ETFRawCapture(
        market_date=TARGET, body=_body(), captured_at_utc="2026-08-20T00:00:00Z",
        request_payload={"trdDd": "20260818", "bld": subject.SOURCE_OPERATION},
        business_calls=2, retry_count=0,
    )
    with pytest.raises(subject.ETFRawDailyError, match="CALL_BUDGET"):
        subject.execute_etf_raw_daily(
            _ready_plan(tmp_path), project_root=tmp_path, capture_builder=lambda _: bad,
        )
    assert not (tmp_path / "data/state/kr_etf_raw_daily_incremental.json").exists()
