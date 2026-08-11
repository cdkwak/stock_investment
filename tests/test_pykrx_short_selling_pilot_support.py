from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.manual import pykrx_short_selling_pilot_support as support


def body(rows, **extra) -> bytes:
    return json.dumps({"OutBlock_1": rows, **extra}, ensure_ascii=False).encode("utf-8")


def complete_row(probe: support.ProbeSpec) -> dict[str, str]:
    return {field: "0" for field in probe.required_fields}


def test_exact_25_call_matrix_order_and_boundaries_are_frozen():
    probes = support.PROBE_MATRIX
    assert len(probes) == support.MAX_BUSINESS_REQUESTS == 25
    assert [probe.sequence for probe in probes] == list(range(1, 26))
    assert len({probe.name for probe in probes}) == 25
    assert [probe.bld.rsplit("/", 1)[-1] for probe in probes[:4]] == [
        "MDCSTAT30101", "MDCSTAT30101", "MDCSTAT30101", "MDCSTAT30101"
    ]
    assert [probe.bld.rsplit("/", 1)[-1] for probe in probes[4:8]] == [
        "MDCSTAT30501", "MDCSTAT30501", "MDCSTAT30501", "MDCSTAT30501"
    ]
    assert all(probe.bld.endswith("MDCSTAT30301") for probe in probes[8:16])
    assert [probe.expectation for probe in probes[21:24]] == ["empty", "empty", "empty"]
    assert probes[-1].scope["symbol"] == "030270"
    assert probes[-1].scope["isin"] == "KR7030270003"


def test_provisional_inventory_keeps_full_source_fields_and_pit_warning():
    trading = support.PROVISIONAL_FIELD_INVENTORY["MDCSTAT30101"]
    assert trading["provisional_mapping"]["ACC_TRDVOL"] == "total_trading_volume"
    assert trading["provisional_mapping"]["TRDVAL_WT"] == "short_trading_value_ratio"
    balance = support.PROVISIONAL_FIELD_INVENTORY["MDCSTAT30501"]
    assert "not a verified historical availability date" in balance["pit_restriction"]
    assert support.PROVISIONAL_FIELD_INVENTORY["MDCSTAT30001"]["qa_only"] is True


def test_valid_empty_coverage_empty_and_source_failure_are_distinct():
    weekend = support.PROBE_MATRIX[21]
    assert support.classify_business_body(weekend, body([]), content_type="application/json") == (
        "VALID_EMPTY", 0
    )
    boundary = support.PROBE_MATRIX[2]
    assert support.classify_business_body(boundary, body([]), content_type="application/json") == (
        "COVERAGE_EMPTY", 0
    )
    current = support.PROBE_MATRIX[0]
    with pytest.raises(support.PilotStopped, match="ANOMALOUS_EMPTY"):
        support.classify_business_body(current, body([]), content_type="application/json")
    with pytest.raises(support.PilotStopped, match="SOURCE_ERROR_PAYLOAD"):
        support.classify_business_body(current, body([], _error_code="AUTH"))


def test_investor_weekend_zero_placeholder_is_valid_empty_not_an_observation():
    probe = support.PROBE_MATRIX[23]
    placeholder = {
        "TRD_DD": "",
        **{field: "0" for field in probe.required_fields[1:]},
    }
    assert support.classify_business_body(probe, body([placeholder])) == (
        "VALID_EMPTY_PLACEHOLDER", 1
    )


@pytest.mark.parametrize(
    ("payload", "content_type", "message"),
    [
        (b"<html>restricted</html>", "text/html", "HTML_OR_RESTRICTION"),
        (b"not-json", "application/json", "NON_JSON_RESPONSE"),
        (json.dumps({"wrong": []}).encode(), "application/json", "EXPECTED_BLOCK_MISSING"),
    ],
)
def test_html_non_json_and_missing_block_stop(payload, content_type, message):
    with pytest.raises(support.PilotStopped, match=message):
        support.classify_business_body(support.PROBE_MATRIX[0], payload, content_type=content_type)


def test_valid_json_is_accepted_despite_krx_text_html_content_type():
    probe = support.PROBE_MATRIX[0]
    payload = body([complete_row(probe)])
    assert support.classify_business_body(
        probe, payload, content_type="text/html;charset=UTF-8"
    ) == ("SUCCESS", 1)


def test_schema_drift_and_unexpected_weekend_rows_stop():
    current = support.PROBE_MATRIX[0]
    with pytest.raises(support.PilotStopped, match="SCHEMA_MISMATCH"):
        support.classify_business_body(current, body([{"ISU_CD": "005930"}]))
    weekend = support.PROBE_MATRIX[21]
    with pytest.raises(support.PilotStopped, match="UNEXPECTED_NONEMPTY"):
        support.classify_business_body(weekend, body([complete_row(weekend)]))


def test_success_preserves_zero_as_a_real_row():
    probe = support.PROBE_MATRIX[17]
    classification, rows = support.classify_business_body(probe, body([complete_row(probe)]))
    assert classification == "SUCCESS"
    assert rows == 1


def test_redaction_removes_named_and_actual_secret_values(tmp_path):
    secret_id = "actual-id-value"
    secret_pw = "actual-password-value"
    ledger = support.AppendOnlyLedger(
        tmp_path / "ledger.jsonl", credential_values=(secret_id, secret_pw)
    )
    ledger.append(
        "ERROR", KRX_ID=secret_id,
        detail=f"KRX_PW={secret_pw} token=abc and {secret_id}",
        Cookie="cookie-value",
    )
    content = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")
    assert secret_id not in content
    assert secret_pw not in content
    assert "cookie-value" not in content
    assert "[REDACTED]" in content
    assert ledger.records()[0]["event"] == "ERROR"


def test_atomic_new_write_never_overwrites_and_resume_verifies_hash(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    probe = support.PROBE_MATRIX[0]
    path = run_dir / support.landing_body_name(probe)
    payload = body([complete_row(probe)])
    support.write_bytes_atomic_new(path, payload)
    with pytest.raises(support.ResumeSafetyError, match="overwrite"):
        support.write_bytes_atomic_new(path, b"replacement")
    checkpoint = support.initial_checkpoint("run")
    checkpoint["completed"][probe.name] = {
        "body_file": path.name,
        "body_sha256": hashlib.sha256(payload).hexdigest(),
    }
    support.verify_completed_artifacts(run_dir, checkpoint)
    path.write_bytes(b"tampered")
    with pytest.raises(support.ResumeSafetyError, match="mismatch"):
        support.verify_completed_artifacts(run_dir, checkpoint)


def test_resume_rejects_non_prefix_completion_even_when_artifact_hash_matches(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    probe = support.PROBE_MATRIX[1]
    path = run_dir / support.landing_body_name(probe)
    payload = body([complete_row(probe)])
    path.write_bytes(payload)
    checkpoint = support.initial_checkpoint("run")
    checkpoint["completed"][probe.name] = {
        "body_file": path.name,
        "body_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with pytest.raises(support.ResumeSafetyError, match="prefix"):
        support.verify_completed_artifacts(run_dir, checkpoint)


def test_orphan_landing_body_fails_closed_on_resume(tmp_path):
    probe = support.PROBE_MATRIX[0]
    (tmp_path / support.landing_body_name(probe)).write_bytes(b"orphan")
    checkpoint = support.initial_checkpoint("run")
    with pytest.raises(support.ResumeSafetyError, match="orphan"):
        support.validate_no_orphan_artifact(tmp_path, probe, checkpoint)


def test_exact_content_type_false_positive_orphan_is_recovered(tmp_path):
    probe = support.PROBE_MATRIX[0]
    payload = body([complete_row(probe)])
    path = tmp_path / support.landing_body_name(probe)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    checkpoint = support.initial_checkpoint("run")
    checkpoint.update({
        "status": "STOPPED",
        "stop_type": "PilotStopped",
        "stop_reason": f"HTML_OR_RESTRICTION_RESPONSE:{probe.name}",
        "raw_http_requests": 6,
    })
    ledger = [{
        "event": "HTTP_RESPONSE", "authentication": False,
        "probe": probe.name, "body_file": path.name,
        "status_code": 200, "response_sha256": digest,
    }]

    recovered, recovery_kind = support.recover_verified_content_type_orphan(
        tmp_path, checkpoint, ledger
    )

    assert recovered["rows"] == 1
    assert recovery_kind == "content_type_false_positive"
    assert checkpoint["completed"][probe.name]["body_sha256"] == digest
    assert checkpoint["status"] == "RECOVERED_CONTENT_TYPE_FALSE_POSITIVE"
    assert "stop_reason" not in checkpoint
    support.verify_completed_artifacts(tmp_path, checkpoint)


def test_exact_investor_placeholder_orphan_is_recovered(tmp_path):
    probe = support.PROBE_MATRIX[23]
    placeholder = {
        "TRD_DD": "",
        **{field: "0" for field in probe.required_fields[1:]},
    }
    payload = body([placeholder])
    path = tmp_path / support.landing_body_name(probe)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    checkpoint = support.initial_checkpoint("run")
    for prior in support.PROBE_MATRIX[:23]:
        prior_path = tmp_path / support.landing_body_name(prior)
        prior_payload = body([])
        prior_path.write_bytes(prior_payload)
        checkpoint["completed"][prior.name] = {
            "body_file": prior_path.name,
            "body_sha256": hashlib.sha256(prior_payload).hexdigest(),
        }
    checkpoint.update({
        "status": "STOPPED",
        "stop_type": "PilotStopped",
        "stop_reason": f"UNEXPECTED_NONEMPTY:{probe.name}",
    })
    ledger = [{
        "event": "HTTP_RESPONSE",
        "authentication": False,
        "probe": probe.name,
        "body_file": path.name,
        "status_code": 200,
        "response_sha256": digest,
    }]

    recovered, recovery_kind = support.recover_verified_content_type_orphan(
        tmp_path, checkpoint, ledger
    )

    assert recovered["classification"] == "VALID_EMPTY_PLACEHOLDER"
    assert recovery_kind == "investor_weekend_zero_placeholder"
    assert len(checkpoint["completed"]) == 24


def test_checkpoint_run_and_matrix_identity_are_required(tmp_path):
    path = tmp_path / "checkpoint.json"
    checkpoint = support.initial_checkpoint("run-a")
    support.write_json_atomic(path, checkpoint)
    assert support.load_checkpoint(path, run_id="run-a")["matrix_sha256"] == support.matrix_sha256()
    with pytest.raises(support.ResumeSafetyError, match="run_id"):
        support.load_checkpoint(path, run_id="run-b")
    checkpoint["matrix_sha256"] = "wrong"
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(support.ResumeSafetyError, match="matrix"):
        support.load_checkpoint(path, run_id="run-a")


def test_d_owned_lock_rejects_overlap_and_releases_only_own_token(tmp_path):
    lock = tmp_path / "d.lock"
    with support.d_owned_run_lock(lock, run_id="run"):
        assert lock.exists()
        with pytest.raises(support.PilotLocked):
            with support.d_owned_run_lock(lock, run_id="other"):
                pass
    assert not lock.exists()


def test_lock_tamper_is_retained_and_fails_closed(tmp_path):
    lock = tmp_path / "d.lock"
    with pytest.raises(support.LockOwnershipError):
        with support.d_owned_run_lock(lock, run_id="run"):
            lock.write_text(json.dumps({"run_id": "other", "token": "other"}), encoding="utf-8")
    assert lock.exists()


def test_business_throttle_is_sequential_and_at_least_five_seconds():
    clock = {"now": 100.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    throttle = support.BusinessThrottle(
        sleep_fn=sleep,
        monotonic_fn=lambda: clock["now"],
        jitter_fn=lambda low, high: 0.1,
    )
    assert throttle.before_business_request() == 0
    clock["now"] += 1.0
    assert throttle.before_business_request() == pytest.approx(4.1)
    assert sleeps == [pytest.approx(4.1)]
    assert clock["now"] == pytest.approx(105.1)


def test_raw_request_count_reconstruction_is_append_only_sequence_based():
    records = [
        {"event": "HTTP_RESPONSE", "raw_sequence": 2},
        {"event": "PROBE_COMPLETED", "raw_sequence": 999},
        {"event": "HTTP_ERROR", "raw_sequence": 7},
    ]
    assert support.reconstruct_raw_request_count(records) == 7
