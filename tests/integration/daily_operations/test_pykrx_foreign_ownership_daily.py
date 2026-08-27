from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from stock_data.orchestration import pykrx_foreign_ownership_daily as subject


FIXTURE_SOURCE_POLICY = replace(
    subject.SOURCE_POLICY,
    provider_availability_policy="FIXTURE_AVAILABLE",
    finality_policy="FIXTURE_REVIEWED",
    revision_policy="FIXTURE_IMMUTABLE",
    finality_evidence_id="fixture-reviewed-finality-v1",
    execution_authorized=True,
)


def _body(*, duplicate: bool = False, null_field: str | None = None) -> bytes:
    rows = [
        {
            "ISU_SRT_CD": "005930",
            "LIST_SHRS": "5,919,637,922",
            "FORN_HD_QTY": "3,001,000,000",
            "FORN_SHR_RT": "50.70",
            "FORN_ORD_LMT_QTY": "5,919,637,922",
            "FORN_LMT_EXHST_RT": "50.70",
        },
        {
            "ISU_SRT_CD": "000660",
            "LIST_SHRS": "728,002,365",
            "FORN_HD_QTY": "380,000,000",
            "FORN_SHR_RT": "52.20",
            "FORN_ORD_LMT_QTY": "728,002,365",
            "FORN_LMT_EXHST_RT": "52.20",
        },
    ]
    if duplicate:
        rows[1]["ISU_SRT_CD"] = rows[0]["ISU_SRT_CD"]
    if null_field is not None:
        rows[0][null_field] = None
    return json.dumps({"output": rows}, separators=(",", ":")).encode()


def _seed_baseline(root: Path, *, date_key: str = "20260812") -> Path:
    response = root / (
        "data/landing/pykrx/high_value_raw/kr_equity_foreign_ownership_daily/"
        f"plan=baseline/date={date_key}/response.json"
    )
    response.parent.mkdir(parents=True)
    response.write_bytes(_body())
    digest = hashlib.sha256(response.read_bytes()).hexdigest()
    state = root / subject.BASELINE_STATE
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "schema": "pykrx_high_value_raw.v1",
                "dataset": subject.DATASET,
                "status": "RAW_BACKFILL_COMPLETE",
                "expected_dates": 1,
                "completed": {
                    date_key: {
                        "classification": "SUCCESS",
                        "rows": 2,
                        "body_path": response.relative_to(root).as_posix(),
                        "body_sha256": digest,
                        "provenance_path": None,
                    }
                },
                "normalized_writes": False,
            }
        ),
        encoding="utf-8",
    )
    return state


def _authorization(target: str = "2026-08-19") -> subject.ExactDateAuthorization:
    return subject.ExactDateAuthorization(
        intended_date=target,
        completed_date=target,
        provider_available=True,
        finality_evidence="fixture-reviewed-finality-v1",
    )


def _run_fixture(root: Path, **kwargs):
    return subject.run_foreign_ownership_incremental(
        root,
        source_policy=FIXTURE_SOURCE_POLICY,
        **kwargs,
    )


def _capture(
    target: str = "2026-08-19",
    *,
    body: bytes | None = None,
    source: str = subject.SOURCE,
    scope: str = subject.SOURCE_SCOPE,
) -> subject.SourceCapture:
    return subject.SourceCapture(
        body=_body() if body is None else body,
        source=source,
        source_operation=subject.SOURCE_OPERATION,
        source_scope=scope,
        source_date=target,
        request_payload=subject.request_payload(target),
    )


def test_exact_all_market_request_and_unresolved_finality_fail_before_capture(tmp_path):
    _seed_baseline(tmp_path)
    assert subject.request_payload("2026-08-19") == {
        "searchType": "1",
        "mktId": "ALL",
        "trdDd": "20260819",
        "isuLmtRto": "0",
        "bld": subject.SOURCE_BLD,
    }
    called = 0

    def capture(_request):
        nonlocal called
        called += 1
        return _capture()

    with pytest.raises(subject.ForeignOwnershipGateError, match="policy is not executable"):
        subject.run_foreign_ownership_incremental(
            tmp_path,
            target_date="2026-08-19",
            authorization=subject.ExactDateAuthorization(
                intended_date="2026-08-19",
                completed_date="2026-08-19",
                provider_available=True,
                finality_evidence="UNRESOLVED",
            ),
            capture_fn=capture,
        )
    assert called == 0
    assert not (tmp_path / subject.JOURNAL).exists()


def test_success_is_landing_first_checkpointed_and_same_date_replay_is_api_zero(tmp_path):
    _seed_baseline(tmp_path)
    calls = 0

    def capture(request):
        nonlocal calls
        calls += 1
        assert request == subject.request_payload("2026-08-19")
        return _capture()

    first = _run_fixture(
        tmp_path,
        target_date="2026-08-19",
        authorization=_authorization(),
        capture_fn=capture,
    )
    assert first["status"] == "SUCCEEDED"
    assert first["business_calls"] == 1
    assert first["rows"] == 2
    assert first["normalized_writes"] is False
    response = tmp_path / first["landing_path"]
    assert response.read_bytes() == _body()
    checkpoint = json.loads((tmp_path / subject.CHECKPOINT).read_text(encoding="utf-8"))
    assert checkpoint["completed"]["20260819"]["body_sha256"] == hashlib.sha256(_body()).hexdigest()
    assert checkpoint["normalized_writes"] is False

    replay = subject.run_foreign_ownership_incremental(
        tmp_path,
        target_date="2026-08-19",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("replay performed a provider call"),
    )
    assert replay["status"] == "NOOP_ALREADY_SUCCEEDED"
    assert replay["business_calls"] == 0
    assert calls == 1


@pytest.mark.parametrize(
    ("capture", "message"),
    [
        (_capture(body=b'{"output":[]}'), "valid-empty"),
        (_capture(body=_body(duplicate=True)), "duplicate symbol"),
        (_capture(body=_body(null_field="FORN_HD_QTY")), "null field"),
        (_capture(source="another_provider"), "source identity"),
        (_capture(scope="KOSPI"), "ALL market scope"),
    ],
)
def test_invalid_capture_is_retained_but_never_checkpointed(
    tmp_path, capture, message
):
    baseline = _seed_baseline(tmp_path)
    baseline_before = baseline.read_bytes()
    with pytest.raises(ValueError, match=message):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: capture,
        )
    assert baseline.read_bytes() == baseline_before
    assert not (tmp_path / subject.CHECKPOINT).exists()
    journal = json.loads((tmp_path / subject.JOURNAL).read_text(encoding="utf-8"))
    assert journal["status"] == "FAILED_VALIDATION"
    assert (tmp_path / journal["response_path"]).read_bytes() == capture.body


def test_fetch_failure_preserves_baseline_and_has_no_accepted_capture(tmp_path):
    baseline = _seed_baseline(tmp_path)
    baseline_before = baseline.read_bytes()

    def fail(_request):
        raise ConnectionError("fixture provider failure")

    with pytest.raises(ConnectionError, match="fixture provider failure"):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=fail,
        )
    assert baseline.read_bytes() == baseline_before
    assert not (tmp_path / subject.CHECKPOINT).exists()
    journal = json.loads((tmp_path / subject.JOURNAL).read_text(encoding="utf-8"))
    assert journal["status"] == "FAILED_FETCH"
    assert "response_path" not in journal


def test_restart_recovers_durable_capture_without_a_second_call(tmp_path):
    _seed_baseline(tmp_path)

    def stop_after_capture(phase):
        if phase == "after_capture":
            raise KeyboardInterrupt("simulated process stop")

    with pytest.raises(KeyboardInterrupt):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: _capture(),
            transition_hook=stop_after_capture,
        )

    replay = subject.run_foreign_ownership_incremental(
        tmp_path,
        target_date="2026-08-19",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("recovery performed a provider call"),
    )
    assert replay["business_calls"] == 0
    assert replay["recovery"] == "SUCCEEDED_RECOVERED"
    checkpoint = json.loads((tmp_path / subject.CHECKPOINT).read_text(encoding="utf-8"))
    assert checkpoint["completed"]["20260819"]["rows"] == 2


def test_restart_closes_checkpoint_committed_journal_without_a_second_call(tmp_path):
    _seed_baseline(tmp_path)

    def stop_after_checkpoint(phase):
        if phase == "after_checkpoint":
            raise KeyboardInterrupt("simulated process stop")

    with pytest.raises(KeyboardInterrupt):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: _capture(),
            transition_hook=stop_after_checkpoint,
        )

    replay = subject.run_foreign_ownership_incremental(
        tmp_path,
        target_date="2026-08-19",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("recovery performed a provider call"),
    )
    assert replay["business_calls"] == 0
    assert replay["recovery"] == "SUCCEEDED_RECOVERED"
    journal = json.loads((tmp_path / subject.JOURNAL).read_text(encoding="utf-8"))
    assert journal["status"] == "SUCCEEDED_RECOVERED"


def test_baseline_retained_date_is_verified_api_zero_without_incremental_state(tmp_path):
    _seed_baseline(tmp_path)
    result = subject.run_foreign_ownership_incremental(
        tmp_path,
        target_date="2026-08-12",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("baseline replay performed a call"),
    )
    assert result["status"] == "NOOP_BASELINE_ALREADY_RETAINED"
    assert result["business_calls"] == 0
    assert not (tmp_path / subject.CHECKPOINT).exists()


def test_incomplete_possible_call_without_response_requires_review_not_retry(tmp_path):
    _seed_baseline(tmp_path)
    journal = tmp_path / subject.JOURNAL
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps(
            {
                "schema": subject.CHECKPOINT_SCHEMA,
                "dataset": subject.DATASET,
                "target_date": "20260819",
                "status": "RUNNING",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(subject.ForeignOwnershipGateError, match="no durable response"):
        subject.run_foreign_ownership_incremental(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: pytest.fail("ambiguous call was retried"),
        )
    state = json.loads(journal.read_text(encoding="utf-8"))
    assert state["status"] == "RECOVERY_REQUIRES_REVIEW"
