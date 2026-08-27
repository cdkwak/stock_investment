from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from stock_data.orchestration.recovery_supervisor import (
    FailureKind,
    JournalState,
    OperationScopeLock,
    PromotionStatus,
    RecoveryAction,
    RecoveryClassification,
    RecoverySnapshot,
    RecoverySupervisorError,
    RetryPolicy,
    ScopeLockBusy,
    classify_recovery,
    plan_recovery,
    promote_outputs_atomically,
    recover_pending_promotion,
    recovery_event_pair,
)
from stock_data.orchestration.update_event_log import (
    EventState,
    LocalUpdateEventLog,
    TriggerType,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)
EXPECTED = date(2026, 8, 19)
DATASETS = ("alpha_daily", "beta_daily")


def _snapshot(**overrides: object) -> RecoverySnapshot:
    values: dict[str, object] = {
        "now": NOW,
        "expected_date": EXPECTED,
        "retained_date": date(2026, 8, 18),
        "scheduled_for": NOW - timedelta(hours=1),
        "available_after": NOW - timedelta(hours=2),
        "schedule_attempted": True,
    }
    values.update(overrides)
    return RecoverySnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"active_writer": True}, RecoveryClassification.ACTIVE),
        (
            {"checkpoint_complete": True, "retained_date": EXPECTED},
            RecoveryClassification.RETAINED_SUCCESS,
        ),
        ({"journal_state": JournalState.PROMOTING}, RecoveryClassification.PARTIAL_FAILURE),
        ({"interrupted_run": True}, RecoveryClassification.PARTIAL_FAILURE),
        (
            {"available_after": NOW + timedelta(hours=1)},
            RecoveryClassification.EXPECTED_LAG,
        ),
        ({"schedule_attempted": False}, RecoveryClassification.MISSED_SCHEDULE),
        ({}, RecoveryClassification.STALE),
    ],
)
def test_checkpoint_first_classification_is_explicit(
    overrides: dict[str, object], expected: RecoveryClassification
) -> None:
    assert classify_recovery(_snapshot(**overrides)) is expected


def test_recovery_plan_never_spends_calls_for_retained_active_lag_or_partial() -> None:
    policy = RetryPolicy(provider_call_budget=3, retry_budget=2, backoff_seconds=(5, 20))
    cases = [
        (_snapshot(checkpoint_complete=True, retained_date=EXPECTED), RecoveryAction.API_ZERO_REPLAY),
        (_snapshot(active_writer=True), RecoveryAction.WAIT),
        (_snapshot(available_after=NOW + timedelta(hours=1)), RecoveryAction.WAIT),
        (_snapshot(interrupted_run=True), RecoveryAction.RECOVER_LOCAL),
    ]
    for snapshot, action in cases:
        decision = plan_recovery(snapshot, policy)
        assert decision.action is action
        assert decision.provider_calls_remaining == 0
        assert decision.retries_remaining == 0


def test_retry_budget_is_explicit_and_nonretryable_failures_stop() -> None:
    policy = RetryPolicy(provider_call_budget=3, retry_budget=2, backoff_seconds=(5, 20))
    retryable = plan_recovery(
        _snapshot(
            provider_calls_used=1,
            retries_used=1,
            last_failure=FailureKind.NETWORK,
            requested_scopes=("KOSPI", "KOSDAQ"),
            completed_scopes=("KOSPI",),
        ),
        policy,
    )
    assert retryable.action is RecoveryAction.RUN_BOUNDED
    assert retryable.provider_calls_remaining == 2
    assert retryable.retries_remaining == 1
    assert retryable.backoff_seconds == (20,)
    assert retryable.missing_scopes == ("KOSDAQ",)

    for failure in (
        FailureKind.AUTHENTICATION,
        FailureKind.PERMISSION,
        FailureKind.SCHEMA,
        FailureKind.FINALITY,
        FailureKind.CONTRACT,
        FailureKind.VALIDATION,
    ):
        stopped = plan_recovery(_snapshot(last_failure=failure), policy)
        assert stopped.action is RecoveryAction.STOP
        assert stopped.provider_calls_remaining == 0
        assert stopped.retries_remaining == 0


def test_ur048_event_output_exposes_decision_and_remains_api_zero(tmp_path: Path) -> None:
    decision = plan_recovery(
        _snapshot(schedule_attempted=False),
        RetryPolicy(provider_call_budget=2, retry_budget=1, backoff_seconds=(10,)),
    )
    started, terminal = recovery_event_pair(
        decision=decision,
        operation="example-daily",
        logical_dataset="alpha_daily",
        datasets=DATASETS,
        trigger_type=TriggerType.SCHEDULED,
        expected_date=EXPECTED,
        retained_date=date(2026, 8, 18),
        at=NOW,
    )
    assert terminal.run_id == started.run_id
    assert terminal.provider_call_count == 0
    assert terminal.state is EventState.SUCCEEDED
    assert terminal.requested_scope["recovery_classification"] == "MISSED_SCHEDULE"
    assert terminal.requested_scope["provider_calls_remaining"] == 2
    assert terminal.requested_scope["next_action"]
    store = LocalUpdateEventLog(tmp_path / "events")
    assert store.append(started).ok
    assert store.append(terminal).ok
    assert len(store.read_events()) == 2


def test_scope_lock_prevents_overlap_and_releases_after_exception(tmp_path: Path) -> None:
    first = OperationScopeLock(
        tmp_path, operation="example-daily", datasets=DATASETS, run_id="run-one"
    )
    second = OperationScopeLock(
        tmp_path, operation="example-daily", datasets=reversed(DATASETS), run_id="run-two"
    )
    with pytest.raises(RuntimeError, match="boom"):
        with first:
            with pytest.raises(ScopeLockBusy):
                second.acquire()
            raise RuntimeError("boom")
    with second:
        assert second.path.is_file()


def test_scope_lock_is_os_released_after_process_crash(tmp_path: Path) -> None:
    code = (
        "import os,sys; from pathlib import Path; "
        "from stock_data.orchestration.recovery_supervisor import OperationScopeLock; "
        "lock=OperationScopeLock(Path(sys.argv[1]),operation='crash-daily',"
        "datasets=('alpha_daily',),run_id='crash-run').acquire(); os._exit(7)"
    )
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    assert result.returncode == 7
    with OperationScopeLock(
        tmp_path, operation="crash-daily", datasets=("alpha_daily",), run_id="restart-run"
    ):
        pass


def _promotion_paths(tmp_path: Path) -> tuple[dict[Path, bytes], Path, Path]:
    first = tmp_path / "data" / "alpha.bin"
    second = tmp_path / "data" / "beta.bin"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"accepted-alpha")
    second.write_bytes(b"accepted-beta")
    checkpoint = tmp_path / "state" / "checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'{"prior":"accepted"}\n')
    return ({first: b"new-alpha", second: b"new-beta"}, checkpoint, tmp_path / "state" / "tx.json")


def test_multi_output_success_updates_checkpoint_only_after_every_output(tmp_path: Path) -> None:
    outputs, checkpoint, journal = _promotion_paths(tmp_path)
    observed_checkpoint: list[bytes] = []
    result = promote_outputs_atomically(
        operation="example-daily",
        datasets=DATASETS,
        target_date=EXPECTED,
        outputs=outputs,
        checkpoint_path=checkpoint,
        journal_path=journal,
        after_output=lambda _: observed_checkpoint.append(checkpoint.read_bytes()),
    )
    assert result.status is PromotionStatus.COMMITTED
    assert result.provider_call_count == 0
    assert observed_checkpoint == [b'{"prior":"accepted"}\n'] * 2
    assert {path.read_bytes() for path in outputs} == {b"new-alpha", b"new-beta"}
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["status"] == "SUCCEEDED"
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "COMMITTED"


def test_promotion_exception_restores_every_prior_valid_byte_and_checkpoint(tmp_path: Path) -> None:
    outputs, checkpoint, journal = _promotion_paths(tmp_path)
    prior_outputs = {path: path.read_bytes() for path in outputs}
    prior_checkpoint = checkpoint.read_bytes()

    def fail_after_first(index: int) -> None:
        if index == 1:
            raise RuntimeError("injected promotion failure")

    with pytest.raises(RecoverySupervisorError, match="restored"):
        promote_outputs_atomically(
            operation="example-daily",
            datasets=DATASETS,
            target_date=EXPECTED,
            outputs=outputs,
            checkpoint_path=checkpoint,
            journal_path=journal,
            after_output=fail_after_first,
        )
    assert {path: path.read_bytes() for path in outputs} == prior_outputs
    assert checkpoint.read_bytes() == prior_checkpoint
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "ROLLED_BACK"


def test_restart_recovers_crash_journal_without_repeating_provider_work(tmp_path: Path) -> None:
    outputs, checkpoint, journal = _promotion_paths(tmp_path)
    prior_outputs = {path: path.read_bytes() for path in outputs}
    prior_checkpoint = checkpoint.read_bytes()

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_after_first(index: int) -> None:
        if index == 1:
            raise SimulatedProcessCrash

    with pytest.raises(SimulatedProcessCrash):
        promote_outputs_atomically(
            operation="example-daily",
            datasets=DATASETS,
            target_date=EXPECTED,
            outputs=outputs,
            checkpoint_path=checkpoint,
            journal_path=journal,
            after_output=crash_after_first,
        )
    assert next(iter(outputs)).read_bytes() == b"new-alpha"
    recovered = recover_pending_promotion(
        operation="example-daily",
        datasets=DATASETS,
        output_paths=tuple(outputs),
        checkpoint_path=checkpoint,
        journal_path=journal,
    )
    assert recovered is PromotionStatus.RECOVERED
    assert {path: path.read_bytes() for path in outputs} == prior_outputs
    assert checkpoint.read_bytes() == prior_checkpoint


def test_successful_same_date_replay_is_api_zero_and_byte_idempotent(tmp_path: Path) -> None:
    outputs, checkpoint, journal = _promotion_paths(tmp_path)
    first = promote_outputs_atomically(
        operation="example-daily",
        datasets=DATASETS,
        target_date=EXPECTED,
        outputs=outputs,
        checkpoint_path=checkpoint,
        journal_path=journal,
    )
    accepted = {path: path.read_bytes() for path in (*outputs, checkpoint, journal)}
    second = promote_outputs_atomically(
        operation="example-daily",
        datasets=DATASETS,
        target_date=EXPECTED,
        outputs=outputs,
        checkpoint_path=checkpoint,
        journal_path=journal,
    )
    assert first.status is PromotionStatus.COMMITTED
    assert second.status is PromotionStatus.API_ZERO_NOOP
    assert second.provider_call_count == 0
    assert {path: path.read_bytes() for path in (*outputs, checkpoint, journal)} == accepted


def test_changed_same_date_output_fails_closed_without_overwriting(tmp_path: Path) -> None:
    outputs, checkpoint, journal = _promotion_paths(tmp_path)
    promote_outputs_atomically(
        operation="example-daily",
        datasets=DATASETS,
        target_date=EXPECTED,
        outputs=outputs,
        checkpoint_path=checkpoint,
        journal_path=journal,
    )
    accepted = {path: path.read_bytes() for path in outputs}
    changed = dict(outputs)
    changed[next(iter(changed))] = b"conflicting-same-date"
    with pytest.raises(RecoverySupervisorError, match="conflicts"):
        promote_outputs_atomically(
            operation="example-daily",
            datasets=DATASETS,
            target_date=EXPECTED,
            outputs=changed,
            checkpoint_path=checkpoint,
            journal_path=journal,
        )
    assert {path: path.read_bytes() for path in outputs} == accepted


def test_tampered_journal_cannot_expand_rollback_or_delete_unrelated_directory(
    tmp_path: Path,
) -> None:
    outputs, checkpoint, journal = _promotion_paths(tmp_path)

    class SimulatedProcessCrash(BaseException):
        pass

    with pytest.raises(SimulatedProcessCrash):
        promote_outputs_atomically(
            operation="example-daily",
            datasets=DATASETS,
            target_date=EXPECTED,
            outputs=outputs,
            checkpoint_path=checkpoint,
            journal_path=journal,
            after_output=lambda _: (_ for _ in ()).throw(SimulatedProcessCrash()),
        )
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    marker = unrelated / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["backup_root"] = str(unrelated.resolve())
    journal.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RecoverySupervisorError, match="backup scope"):
        recover_pending_promotion(
            operation="example-daily",
            datasets=DATASETS,
            output_paths=tuple(outputs),
            checkpoint_path=checkpoint,
            journal_path=journal,
        )
    assert marker.read_text(encoding="utf-8") == "keep"
