from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from stock_data.orchestration import pykrx_equity_fundamental_daily as subject


FIXTURE_SOURCE_POLICY = replace(
    subject.SOURCE_POLICY,
    provider_availability_policy="FIXTURE_AVAILABLE",
    finality_policy="FIXTURE_REVIEWED",
    revision_policy="FIXTURE_IMMUTABLE",
    finality_evidence_id="fixture-reviewed-finality-v1",
    execution_authorized=True,
)


def _row(symbol: str = "005930", *, bps: str = "58,000") -> dict[str, object]:
    return {
        "ISU_SRT_CD": symbol,
        "ISU_ABBRV": "삼성전자",
        "TDD_CLSPRC": "71,000",
        "EPS": "-",
        "PER": "-",
        "BPS": bps,
        "PBR": "1.22",
        "DPS": "1,444",
        "DVD_YLD": "2.03",
    }


def _body(
    *, duplicate: str | None = None, mutate: tuple[str, object] | None = None
) -> bytes:
    rows = [_row(), _row("000660", bps="120,000")]
    if duplicate is not None:
        rows[1]["ISU_SRT_CD"] = rows[0]["ISU_SRT_CD"]
        rows[1]["ISU_ABBRV"] = rows[0]["ISU_ABBRV"]
        rows[1]["BPS"] = rows[0]["BPS"] if duplicate == "exact" else "57,999"
    if mutate is not None:
        field, value = mutate
        rows[0][field] = value
    return json.dumps({"output": rows}, ensure_ascii=False, separators=(",", ":")).encode()


def _seed_baseline(root: Path, *, date_key: str = "20260812") -> Path:
    response = root / (
        "data/landing/pykrx/high_value_raw/kr_equity_fundamental_daily/"
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
                        "row_identity": "source_row_ordinal_1_based",
                        "provider_duplicate_groups": [],
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
    return subject.run_fundamental_incremental(
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
        "mktId": "ALL",
        "trdDd": "20260819",
        "bld": subject.SOURCE_BLD,
    }
    called = 0

    def capture(_request):
        nonlocal called
        called += 1
        return _capture()

    with pytest.raises(subject.FundamentalGateError, match="policy is not executable"):
        subject.run_fundamental_incremental(
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


@pytest.mark.parametrize(
    ("kind", "classification", "differing"),
    [
        ("exact", "EXACT_PROVIDER_DUPLICATE", []),
        ("conflicting", "CONFLICTING_PROVIDER_DUPLICATE", ["BPS"]),
    ],
)
def test_provider_duplicates_keep_source_ordinals_and_distinct_rows(
    kind, classification, differing
):
    analysis = subject._analyze_body(
        _body(duplicate=kind), target_date="2026-08-19"
    )
    assert analysis["rows"] == 2
    assert analysis["distinct_security_codes"] == 1
    assert analysis["row_identity"] == "source_row_ordinal_1_based"
    assert analysis["provider_duplicate_groups"] == [
        {
            "entity_key": "005930",
            "source_row_ordinals": [1, 2],
            "classification": classification,
            "differing_fields": differing,
        }
    ]


def test_success_is_immutable_landing_overlay_and_replay_is_api_zero(tmp_path):
    baseline = _seed_baseline(tmp_path)
    baseline_before = baseline.read_bytes()
    calls = 0

    def capture(request):
        nonlocal calls
        calls += 1
        assert request == subject.request_payload("2026-08-19")
        return _capture(body=_body(duplicate="conflicting"))

    first = _run_fixture(
        tmp_path,
        target_date="2026-08-19",
        authorization=_authorization(),
        capture_fn=capture,
    )
    assert first["status"] == "SUCCEEDED"
    assert first["business_calls"] == 1
    assert first["rows"] == 2
    assert len(first["provider_duplicate_groups"]) == 1
    assert first["normalized_writes"] is False
    assert baseline.read_bytes() == baseline_before

    checkpoint = json.loads((tmp_path / subject.CHECKPOINT).read_text(encoding="utf-8"))
    record = checkpoint["completed"]["20260819"]
    assert checkpoint["overlay_mode"] == subject.OVERLAY_MODE
    assert record["classification"] == "SUCCESS_INCREMENTAL_RAW_WITH_PROVIDER_DUPLICATE"
    assert record["row_identity"] == "source_row_ordinal_1_based"
    response = tmp_path / record["body_path"]
    assert response.read_bytes() == _body(duplicate="conflicting")

    replay = subject.run_fundamental_incremental(
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
        (_capture(body=_body(mutate=("EPS", None))), "null field"),
        (_capture(body=_body(mutate=("PBR", "not-a-number"))), "non-numeric"),
        (_capture(body=_body(mutate=("DVD_YLD", "-0.1"))), "out-of-range"),
        (_capture(source="another_provider"), "source identity"),
        (_capture(scope="KOSPI"), "ALL market scope"),
    ],
)
def test_invalid_or_empty_capture_is_retained_but_never_checkpointed(
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


def test_missing_tokens_are_preserved_and_counted_without_fill():
    body = _body()
    analysis = subject._analyze_body(body, target_date="2026-08-19")
    assert analysis["missing_value_counts"]["EPS"] == 2
    assert analysis["missing_value_counts"]["PER"] == 2
    assert json.loads(body)["output"][0]["EPS"] == "-"
    assert analysis["completeness"] == "EXACT_ALL_MARKET_RESPONSE_NONEMPTY_SCHEMA_COMPLETE"


def test_fetch_or_precheckpoint_failure_preserves_baseline_and_accepts_nothing(tmp_path):
    baseline = _seed_baseline(tmp_path)
    baseline_before = baseline.read_bytes()

    with pytest.raises(ConnectionError, match="fixture provider failure"):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: (_ for _ in ()).throw(
                ConnectionError("fixture provider failure")
            ),
        )
    assert baseline.read_bytes() == baseline_before
    assert not (tmp_path / subject.CHECKPOINT).exists()
    assert json.loads((tmp_path / subject.JOURNAL).read_text())["status"] == "FAILED_FETCH"

    (tmp_path / subject.JOURNAL).unlink()
    with pytest.raises(RuntimeError, match="promotion fixture"):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: _capture(),
            transition_hook=lambda phase: (
                (_ for _ in ()).throw(RuntimeError("promotion fixture"))
                if phase == "before_checkpoint"
                else None
            ),
        )
    assert baseline.read_bytes() == baseline_before
    assert not (tmp_path / subject.CHECKPOINT).exists()
    assert json.loads((tmp_path / subject.JOURNAL).read_text())["status"] == "FAILED_BEFORE_CHECKPOINT"

    replay = subject.run_fundamental_incremental(
        tmp_path,
        target_date="2026-08-19",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("recovery performed a provider call"),
    )
    assert replay["business_calls"] == 0
    assert replay["recovery"] == "SUCCEEDED_RECOVERED"


@pytest.mark.parametrize("stop_phase", ["after_capture", "after_checkpoint"])
def test_restart_recovers_without_a_second_call(tmp_path, stop_phase):
    _seed_baseline(tmp_path)

    def stop(phase):
        if phase == stop_phase:
            raise KeyboardInterrupt("simulated process stop")

    with pytest.raises(KeyboardInterrupt):
        _run_fixture(
            tmp_path,
            target_date="2026-08-19",
            authorization=_authorization(),
            capture_fn=lambda _request: _capture(body=_body(duplicate="conflicting")),
            transition_hook=stop,
        )

    replay = subject.run_fundamental_incremental(
        tmp_path,
        target_date="2026-08-19",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("recovery performed a provider call"),
    )
    assert replay["business_calls"] == 0
    assert replay["recovery"] == "SUCCEEDED_RECOVERED"
    checkpoint = json.loads((tmp_path / subject.CHECKPOINT).read_text(encoding="utf-8"))
    assert checkpoint["completed"]["20260819"]["rows"] == 2


def test_baseline_retained_date_is_verified_api_zero(tmp_path):
    _seed_baseline(tmp_path)
    result = subject.run_fundamental_incremental(
        tmp_path,
        target_date="2026-08-12",
        authorization=None,
        capture_fn=lambda _request: pytest.fail("baseline replay performed a call"),
    )
    assert result["status"] == "NOOP_BASELINE_ALREADY_RETAINED"
    assert result["business_calls"] == 0
    assert not (tmp_path / subject.CHECKPOINT).exists()
