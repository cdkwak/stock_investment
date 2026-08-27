from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import stock_data.orchestration.update_event_log as module
from stock_data.orchestration.update_event_log import (
    CommitResult,
    EventLogPolicy,
    EventState,
    FinalityResult,
    FreshnessResult,
    LocalUpdateEventLog,
    ReasonCode,
    SCHEMA_VERSION,
    Transition,
    TriggerType,
    UpdateEvent,
    ValidationResult,
    event_digest,
    new_run_id,
)


UTC = timezone.utc
START = datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC)


def _started(
    run_id: str,
    *,
    trigger: TriggerType = TriggerType.MANUAL,
    at: datetime = START,
    dataset: str = "example_daily",
) -> UpdateEvent:
    return UpdateEvent.started(
        run_id=run_id,
        job_route="example/daily",
        logical_dataset=dataset,
        trigger_type=trigger,
        requested_scope={"market_date": "2026-08-19", "symbols": ["AAA", "BBB"]},
        at=at,
        expected_date="2026-08-19",
        prior_source_date="2026-08-18",
        message="bounded run started",
    )


def _terminal(
    run_id: str,
    state: EventState = EventState.SUCCEEDED,
    *,
    reason: ReasonCode = ReasonCode.COMPLETED,
    at: datetime = START,
    dataset: str = "example_daily",
) -> UpdateEvent:
    start = _started(run_id, at=at, dataset=dataset)
    return start.terminal(
        state=state,
        reason_code=reason,
        at=at + timedelta(seconds=2),
        resulting_source_date="2026-08-19",
        row_counts={"landing": 2, "normalized": 2},
        provider_call_count=1,
        retry_count=0,
        validation_result=ValidationResult.PASSED,
        promotion_result=CommitResult.SUCCEEDED,
        checkpoint_result=CommitResult.SUCCEEDED,
        freshness_result=FreshnessResult.CURRENT,
        finality_result=FinalityResult.CONFIRMED,
        message="validated and committed",
    )


def test_versioned_schema_has_stable_run_correlation_utc_kst_and_required_results(tmp_path: Path) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    for index, trigger in enumerate(TriggerType):
        run_id = new_run_id("example/daily", now=START + timedelta(seconds=index))
        started = _started(run_id, trigger=trigger, at=START + timedelta(seconds=index * 10))
        if trigger is TriggerType.API_ZERO_REPLAY:
            terminal = started.terminal(
                state=EventState.API_ZERO_NOOP,
                reason_code=ReasonCode.ALREADY_CURRENT_API_ZERO,
                at=started.started_at_utc + timedelta(seconds=1),
                resulting_source_date="2026-08-19",
                provider_call_count=0,
                validation_result=ValidationResult.PASSED,
                promotion_result=CommitResult.NOOP,
                checkpoint_result=CommitResult.NOOP,
                freshness_result=FreshnessResult.CURRENT,
                finality_result=FinalityResult.CONFIRMED,
                message="already current",
            )
        else:
            terminal = started.terminal(
                state=EventState.SUCCEEDED,
                reason_code=ReasonCode.COMPLETED,
                at=started.started_at_utc + timedelta(seconds=1),
                provider_call_count=1,
                validation_result=ValidationResult.PASSED,
                promotion_result=CommitResult.SUCCEEDED,
                checkpoint_result=CommitResult.SUCCEEDED,
                freshness_result=FreshnessResult.CURRENT,
                finality_result=FinalityResult.CONFIRMED,
            )
        assert store.append(started).ok
        assert store.append(terminal).ok

    rows = store.read_events()
    assert len(rows) == 6
    for event in rows:
        payload = event.to_dict()
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["started_at_kst"].endswith("+09:00")
        assert payload["event_at_utc"].endswith("Z")
        assert payload["event_at_kst"].endswith("+09:00")
        if event.state.terminal:
            assert payload["ended_at_kst"].endswith("+09:00")
        assert payload["run_id"]
        assert len(event_digest(event)) == 64
    replay = [row for row in rows if row.trigger_type is TriggerType.API_ZERO_REPLAY]
    assert [row.state for row in replay] == [EventState.STARTED, EventState.API_ZERO_NOOP]
    assert replay[-1].provider_call_count == 0


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (EventState.EXPECTED_DELAY, ReasonCode.PROVIDER_NOT_YET_AVAILABLE),
        (EventState.VALID_EMPTY, ReasonCode.VALID_EMPTY_ACCEPTED),
        (EventState.PARTIAL_INELIGIBLE, ReasonCode.PARTIAL_SCOPE_INELIGIBLE),
        (EventState.VALIDATION_FAILURE, ReasonCode.VALIDATION_REJECTED),
        (EventState.PROVIDER_NETWORK_FAILURE, ReasonCode.PROVIDER_OR_NETWORK_ERROR),
        (EventState.AUTH_PERMISSION_FAILURE, ReasonCode.AUTHENTICATION_OR_PERMISSION_DENIED),
        (EventState.LOCAL_IO_FAILURE, ReasonCode.LOCAL_READ_WRITE_ERROR),
        (EventState.RECOVERED, ReasonCode.RECOVERED_AFTER_FAILURE),
    ],
)
def test_terminal_states_and_reason_codes_round_trip(
    tmp_path: Path, state: EventState, reason: ReasonCode
) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    event = _terminal(f"run-{state.value}", state, reason=reason)
    assert store.append(event).persisted
    restored = store.read_events()[0]
    assert (restored.state, restored.reason_code) == (state, reason)


def test_strict_redaction_removes_secrets_accounts_payloads_and_sensitive_urls(tmp_path: Path) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    event = UpdateEvent.started(
        run_id="redaction-run",
        job_route="secure/daily",
        logical_dataset="secure_daily",
        trigger_type=TriggerType.MANUAL,
        requested_scope={
            "symbol": "SAFE",
            "authorization": "Bearer top-secret",
            "nested": {
                "api_key": "key-123",
                "account_id": "123456789012",
                "balance": 999_999,
                "holdings": ["PRIVATE"],
                "payload": {"raw": "provider body"},
                "url": "https://provider.example/data?token=query-secret",
            },
        },
        at=START,
        message=(
            "Authorization: Bearer header-secret password=hunter2 account=123456789012 "
            "account_number=123-45-678901 balance=999999 positions=PRIVATE "
            "https://provider.example/path?api_key=query-secret"
        ),
    )
    result = store.append(event)
    assert result.persisted and result.path is not None
    persisted = result.path.read_text(encoding="utf-8")
    for forbidden in (
        "top-secret", "key-123", "123456789012", "999999", "PRIVATE", "provider body",
        "query-secret", "header-secret", "hunter2", "123-45-678901", "PRIVATE",
    ):
        assert forbidden not in persisted
    payload = json.loads(persisted)
    assert payload["requested_scope"]["symbol"] == "SAFE"
    assert payload["requested_scope"]["authorization"] == "[REDACTED]"
    assert payload["requested_scope"]["nested"]["account_id"] == "[REDACTED]"
    assert "[REDACTED_URL]" in payload["message"]


def test_quoted_json_account_values_are_redacted_before_event_persistence(tmp_path: Path) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    private_message = (
        '{"balance":123456,"accountSeq":42,"cash":{"available":999},'
        '"holdings":["PRIVATE",1],"symbol":"SAFE"}'
    )
    event = UpdateEvent.started(
        run_id="quoted-json-redaction-run",
        job_route="secure/daily",
        logical_dataset="secure_daily",
        trigger_type=TriggerType.MANUAL,
        requested_scope={"symbol": "SAFE"},
        at=START,
        message=private_message,
    )

    result = store.append(event)

    assert result.persisted and result.path is not None
    persisted = result.path.read_text(encoding="utf-8")
    for forbidden in ("123456", '"accountSeq":42', "available", "999", "PRIVATE"):
        assert forbidden not in persisted
    payload = json.loads(persisted)
    assert payload["message"].count("[REDACTED_ACCOUNT]") == 1
    assert payload["message"].count("[REDACTED_ACCOUNT_VALUE]") == 3
    assert '"symbol":"SAFE"' in payload["message"]


def test_concurrent_writers_commit_complete_json_without_duplicates(tmp_path: Path) -> None:
    store = LocalUpdateEventLog(
        tmp_path / "events",
        policy=EventLogPolicy(max_events=200, retention_days=30, lock_timeout_seconds=10),
    )
    events = [_terminal(f"concurrent-{index}") for index in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store.append, events + events))
    assert all(result.ok for result in results), [
        (result.error_code, result.safe_message) for result in results if not result.ok
    ]
    assert sum(result.persisted for result in results) == 40
    assert sum(result.duplicate for result in results) == 40
    rows = store.read_events()
    assert len(rows) == 40
    assert len({row.run_id for row in rows}) == 40
    for path in (tmp_path / "events" / "events").glob("*/*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION


def test_interrupted_replace_preserves_prior_event_and_next_writer_recovers_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    prior = _terminal("prior-run")
    interrupted = _terminal("interrupted-run", at=START + timedelta(minutes=1))
    later = _terminal("later-run", at=START + timedelta(minutes=2))
    assert store.append(prior).persisted
    original_replace = module.os.replace

    def fail_pending_replace(source: object, destination: object) -> None:
        if str(source).endswith(".pending"):
            raise OSError("replace failed token=do-not-persist")
        original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_pending_replace)
    failed = store.append(interrupted)
    assert not failed.ok and failed.error_code == "LOCAL_LOG_WRITE_FAILED"
    assert "do-not-persist" not in failed.safe_message
    assert [event.run_id for event in store.read_events()] == ["prior-run"]
    assert list((tmp_path / "events" / ".pending").glob("*.pending"))

    monkeypatch.setattr(module.os, "replace", original_replace)
    recovered = store.append(later)
    assert recovered.persisted and recovered.recovered_pending == 1
    assert [event.run_id for event in store.read_events()] == [
        "prior-run", "interrupted-run", "later-run"
    ]


def test_rotation_and_retention_remove_complete_old_runs_and_keep_recent_correlation(tmp_path: Path) -> None:
    now = START + timedelta(days=10)
    store = LocalUpdateEventLog(
        tmp_path / "events",
        policy=EventLogPolicy(max_events=4, retention_days=3),
        clock=lambda: now,
    )
    old_start = _started("old-run", at=START)
    assert store.append(old_start).persisted
    assert store.append(old_start.terminal(
        state=EventState.SUCCEEDED,
        reason_code=ReasonCode.COMPLETED,
        at=START + timedelta(seconds=1),
    )).persisted
    for index in range(3):
        at = now - timedelta(minutes=3 - index)
        assert store.append(_terminal(f"recent-{index}", at=at)).persisted
    rows = store.read_events()
    assert {row.run_id for row in rows} == {"recent-0", "recent-1", "recent-2"}
    assert len(rows) <= 4


def test_repeated_failure_and_recovery_transitions_are_derived_per_route_dataset(tmp_path: Path) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    first = _terminal(
        "failure-one", EventState.PROVIDER_NETWORK_FAILURE,
        reason=ReasonCode.PROVIDER_OR_NETWORK_ERROR, at=START,
    )
    second = _terminal(
        "failure-two", EventState.VALIDATION_FAILURE,
        reason=ReasonCode.VALIDATION_REJECTED, at=START + timedelta(minutes=1),
    )
    recovered = _terminal("success-three", at=START + timedelta(minutes=2))
    for event in (first, second, recovered):
        assert store.append(event).persisted
    rows = store.read_events()
    assert rows[0].transition is Transition.SINGLE
    assert rows[1].transition is Transition.REPEATED_FAILURE
    assert rows[1].related_run_id == "failure-one"
    assert rows[2].transition is Transition.RECOVERY
    assert rows[2].related_run_id == "failure-two"


def test_semantic_duplicate_prevention_allows_one_start_and_one_terminal_per_run(tmp_path: Path) -> None:
    store = LocalUpdateEventLog(tmp_path / "events")
    start = _started("stable-run")
    assert store.append(start).persisted
    assert store.append(start).duplicate
    rebuilt_start = _started("stable-run")
    assert store.append(rebuilt_start).duplicate
    terminal = start.terminal(
        state=EventState.API_ZERO_NOOP,
        reason_code=ReasonCode.ALREADY_CURRENT_API_ZERO,
        at=START + timedelta(seconds=1),
        provider_call_count=0,
        validation_result=ValidationResult.PASSED,
        promotion_result=CommitResult.NOOP,
        checkpoint_result=CommitResult.NOOP,
    )
    assert store.append(terminal).persisted
    assert store.append(terminal).duplicate
    conflict = start.terminal(
        state=EventState.VALIDATION_FAILURE,
        reason_code=ReasonCode.VALIDATION_REJECTED,
        at=START + timedelta(seconds=2),
    )
    rejected = store.append(conflict)
    assert not rejected.ok and rejected.error_code == "LOCAL_LOG_WRITE_FAILED"
    assert len(store.read_events()) == 2


def test_local_logging_failure_is_visible_sanitized_and_does_not_touch_operation_outcome(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("existing unrelated artifact", encoding="utf-8")
    operation_outcome = {
        "provider_call_count": 1,
        "promotion": "SUCCEEDED",
        "checkpoint": "SUCCEEDED",
    }
    before = dict(operation_outcome)
    result = LocalUpdateEventLog(root).append(_terminal("write-failure"))
    assert not result.ok
    assert result.error_code == "LOCAL_LOG_WRITE_FAILED"
    assert result.safe_message
    assert operation_outcome == before
    assert root.read_text(encoding="utf-8") == "existing unrelated artifact"


def test_invalid_timestamp_and_identifier_errors_never_echo_input_values() -> None:
    with pytest.raises(ValueError, match="job_route") as error:
        UpdateEvent.started(
            run_id="safe-run",
            job_route="https://provider.example/?token=secret",
            logical_dataset="dataset",
            trigger_type=TriggerType.MANUAL,
            requested_scope={},
            at=START,
        )
    assert "secret" not in str(error.value)
    with pytest.raises(ValueError, match="timezone-aware"):
        _started("naive-run", at=datetime(2026, 8, 20, 1, 2, 3))


def test_state_reason_pair_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="reason_code"):
        _started("reason-mismatch").terminal(
            state=EventState.SUCCEEDED,
            reason_code=ReasonCode.PROVIDER_OR_NETWORK_ERROR,
            at=START + timedelta(seconds=1),
        )
