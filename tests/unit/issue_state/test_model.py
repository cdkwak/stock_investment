from __future__ import annotations

from dataclasses import replace

import pytest

from issue_state.model import (
    IssueEvent, aggregate_events, evaluate_suppression, release_suppression,
    stable_fingerprint, suppress_issue,
)


def event(identity: str, at: str, outcome: str = "FAILURE") -> IssueEvent:
    return IssueEvent(
        source_schema="runtime-diagnostic/v1", source_event_id=identity,
        occurred_at=at, stable_code="GUI_LOAD_FAILED", domain="GUI",
        target_kind="COMPONENT", target_id="gui:dashboard", outcome=outcome,
        severity="ERROR" if outcome == "FAILURE" else "INFO",
        retryability="NOT_RETRYABLE",
        evidence=("artifacts/runtime_logs/application/event.json@sha256:" + "a" * 64,),
    )


def test_fingerprint_is_stable_and_rejects_noncanonical_target() -> None:
    first = stable_fingerprint(
        stable_code="GUI_LOAD_FAILED", domain="GUI",
        target_kind="COMPONENT", target_id="gui:dashboard",
    )
    assert first == stable_fingerprint(
        stable_code="GUI_LOAD_FAILED", domain="GUI",
        target_kind="COMPONENT", target_id="gui:dashboard",
    )
    with pytest.raises(ValueError, match="target_id"):
        stable_fingerprint(
            stable_code="GUI_LOAD_FAILED", domain="GUI",
            target_kind="COMPONENT", target_id="GUI:Dashboard",
        )


def test_aggregation_is_idempotent_and_preserves_recovery_epochs() -> None:
    first = event("event-1", "2026-08-26T00:00:00Z")
    records = aggregate_events((), (first, first))
    assert records[0].occurrence_count == 1
    assert records[0].source_event_count == 1

    recovered = aggregate_events(records, (event("event-2", "2026-08-26T00:01:00Z", "SUCCESS"),))
    assert recovered[0].state == "RECOVERED"
    assert recovered[0].recovery_count == 1
    assert records[0].state == "ACTIVE"

    recurred = aggregate_events(recovered, (event("event-3", "2026-08-26T00:02:00Z"),))
    assert recurred[0].state == "ACTIVE"
    assert recurred[0].epoch == 2
    assert recurred[0].previous_epochs[0]["occurrence_count"] == 1
    assert recurred[0].occurrence_count == 2


def test_suppression_expiry_and_release_require_a_later_snapshot() -> None:
    record = aggregate_events((), (event("event-1", "2026-08-26T00:00:00Z"),))[0]
    active = suppress_issue(
        record, suppression_id="maintenance-1", reason_code="KNOWN_MAINTENANCE",
        started_at="2026-08-26T00:00:00Z", expires_at="2026-08-27T00:00:00Z",
        actor="local.operator", evidence=record.evidence[0],
    )
    expired = evaluate_suppression(active, evaluated_at="2026-08-27T00:00:00Z")
    assert expired.suppression["state"] == "EXPIRED"
    assert expired.suppression["discovery_after_source_event_count"] == 1

    active_again = suppress_issue(
        expired, suppression_id="maintenance-2", reason_code="KNOWN_MAINTENANCE",
        started_at="2026-08-27T01:00:00Z", expires_at="2026-08-28T01:00:00Z",
        actor="local.operator", evidence=record.evidence[0],
    )
    released = release_suppression(
        active_again, released_at="2026-08-27T02:00:00Z",
        reason_code="MAINTENANCE_COMPLETE", actor="local.operator",
    )
    assert released.suppression["state"] == "RELEASED"
    assert len(released.suppression["history"]) == 1


def test_out_of_order_is_ignored_without_mutation_and_private_evidence_fails_closed() -> None:
    records = aggregate_events((), (event("event-1", "2026-08-26T00:01:00Z"),))
    retained = aggregate_events(records, (event("event-2", "2026-08-26T00:00:00Z"),))
    assert retained[0].to_dict() == records[0].to_dict()
    assert "runtime-diagnostic/v1:event-2" not in retained[0].source_event_ids
    with pytest.raises(ValueError, match="unsafe"):
        replace(event("event-3", "2026-08-26T00:02:00Z"), evidence=("C:/secret.txt@sha256:" + "b" * 64,))
    with pytest.raises(ValueError, match="private"):
        replace(event("event-4", "2026-08-26T00:03:00Z"), evidence=("artifacts/account-123456789012.json@sha256:" + "b" * 64,))


def test_epoch_rotation_keeps_monotonic_total_occurrences_and_recoveries() -> None:
    records = aggregate_events((), (event("failure-0", "2026-08-26T00:00:00Z"),))
    for epoch in range(1, 11):
        records = aggregate_events(records, (
            event(f"success-{epoch}", f"2026-08-26T00:{epoch * 2 - 1:02d}:00Z", "SUCCESS"),
            event(f"failure-{epoch}", f"2026-08-26T00:{epoch * 2:02d}:00Z"),
        ))

    record = records[0]
    assert record.epoch == 11
    assert record.occurrence_count == 11
    assert record.recovery_count == 10
    assert len(record.previous_epochs) == 8
    assert record.historical_epoch_count == 2
    assert record.historical_occurrence_count == 2
