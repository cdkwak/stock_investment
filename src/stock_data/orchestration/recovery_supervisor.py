"""Fail-closed planning and local recovery primitives for bounded data updates.

This module never calls a provider.  It reads durable facts, decides whether a
caller may wait, replay locally, recover locally, or spend a reviewed budget,
and provides a journaled multi-output promotion primitive for callers that do
not already own one.  Runtime integrations remain operation-specific.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from stock_data.orchestration.update_event_log import (
    CommitResult,
    EventState,
    FinalityResult,
    FreshnessResult,
    ReasonCode,
    TriggerType,
    UpdateEvent,
    ValidationResult,
    new_run_id,
)


RECOVERY_CHECKPOINT_VERSION = "data-update-recovery-checkpoint/v1"
RECOVERY_JOURNAL_VERSION = "data-update-recovery-journal/v1"


class RecoverySupervisorError(RuntimeError):
    """Raised when durable evidence is missing, conflicting, or unsafe."""


class ScopeLockBusy(RecoverySupervisorError):
    """Raised when another live writer owns an operation/dataset scope."""


class RecoveryClassification(StrEnum):
    MISSED_SCHEDULE = "MISSED_SCHEDULE"
    EXPECTED_LAG = "EXPECTED_LAG"
    ACTIVE = "ACTIVE"
    RETAINED_SUCCESS = "RETAINED_SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    STALE = "STALE"


class RecoveryAction(StrEnum):
    WAIT = "WAIT"
    API_ZERO_REPLAY = "API_ZERO_REPLAY"
    RECOVER_LOCAL = "RECOVER_LOCAL"
    RUN_BOUNDED = "RUN_BOUNDED"
    STOP = "STOP"


class FailureKind(StrEnum):
    NONE = "NONE"
    NETWORK = "NETWORK"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    AUTHENTICATION = "AUTHENTICATION"
    PERMISSION = "PERMISSION"
    SCHEMA = "SCHEMA"
    FINALITY = "FINALITY"
    CONTRACT = "CONTRACT"
    VALIDATION = "VALIDATION"
    LOCAL_IO = "LOCAL_IO"


class JournalState(StrEnum):
    CLEAN = "CLEAN"
    STAGED = "STAGED"
    PROMOTING = "PROMOTING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"


class PromotionStatus(StrEnum):
    COMMITTED = "COMMITTED"
    API_ZERO_NOOP = "API_ZERO_NOOP"
    RECOVERED = "RECOVERED"


_NON_RETRYABLE_DEFAULT = frozenset(
    {
        FailureKind.AUTHENTICATION,
        FailureKind.PERMISSION,
        FailureKind.SCHEMA,
        FailureKind.FINALITY,
        FailureKind.CONTRACT,
        FailureKind.VALIDATION,
    }
)
_HELD_SCOPE_LOCKS: set[str] = set()
_HELD_SCOPE_LOCKS_GUARD = threading.Lock()


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _scope_key(operation: str, datasets: Sequence[str]) -> str:
    entries = tuple(datasets)
    if not operation or not entries or any(not item for item in entries):
        raise ValueError("operation and datasets must be non-empty")
    canonical = operation + "\0" + "\0".join(sorted(set(entries)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoverySupervisorError(f"durable JSON is unreadable: {path.name}") from error
    if not isinstance(value, dict):
        raise RecoverySupervisorError(f"durable JSON is not an object: {path.name}")
    return value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    provider_call_budget: int
    retry_budget: int
    backoff_seconds: tuple[int, ...] = ()
    non_retryable: frozenset[FailureKind] = _NON_RETRYABLE_DEFAULT

    def __post_init__(self) -> None:
        if self.provider_call_budget < 0 or self.retry_budget < 0:
            raise ValueError("retry budgets must be non-negative")
        if any(value < 0 for value in self.backoff_seconds):
            raise ValueError("backoff values must be non-negative")
        if len(self.backoff_seconds) < self.retry_budget:
            raise ValueError("backoff_seconds must cover every allowed retry")

    def remaining(
        self,
        *,
        provider_calls_used: int,
        retries_used: int,
        failure_kind: FailureKind,
    ) -> tuple[int, int]:
        if provider_calls_used < 0 or retries_used < 0:
            raise ValueError("used budgets must be non-negative")
        if failure_kind in self.non_retryable:
            return (0, 0)
        return (
            max(0, self.provider_call_budget - provider_calls_used),
            max(0, self.retry_budget - retries_used),
        )


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    now: datetime
    expected_date: date
    retained_date: date | None
    scheduled_for: datetime
    available_after: datetime | None
    requested_scopes: tuple[str, ...] = ()
    completed_scopes: tuple[str, ...] = ()
    active_writer: bool = False
    checkpoint_complete: bool = False
    journal_state: JournalState = JournalState.CLEAN
    interrupted_run: bool = False
    schedule_attempted: bool = False
    provider_calls_used: int = 0
    retries_used: int = 0
    last_failure: FailureKind = FailureKind.NONE

    def __post_init__(self) -> None:
        _aware(self.now, "now")
        _aware(self.scheduled_for, "scheduled_for")
        if self.available_after is not None:
            _aware(self.available_after, "available_after")
        if self.provider_calls_used < 0 or self.retries_used < 0:
            raise ValueError("used budgets must be non-negative")
        if len(set(self.requested_scopes)) != len(self.requested_scopes):
            raise ValueError("requested_scopes must be unique")
        if not set(self.completed_scopes).issubset(self.requested_scopes):
            raise ValueError("completed_scopes must be part of requested_scopes")


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    classification: RecoveryClassification
    action: RecoveryAction
    provider_calls_remaining: int
    retries_remaining: int
    backoff_seconds: tuple[int, ...]
    missing_scopes: tuple[str, ...]
    next_action: str
    failure_kind: FailureKind


def classify_recovery(snapshot: RecoverySnapshot) -> RecoveryClassification:
    """Classify from durable/checkpoint facts before considering new calls."""

    if snapshot.active_writer:
        return RecoveryClassification.ACTIVE
    retained_complete = (
        snapshot.checkpoint_complete
        and snapshot.retained_date is not None
        and snapshot.retained_date >= snapshot.expected_date
        and snapshot.journal_state in {
            JournalState.CLEAN,
            JournalState.COMMITTED,
            JournalState.ROLLED_BACK,
        }
    )
    if retained_complete:
        return RecoveryClassification.RETAINED_SUCCESS
    if snapshot.interrupted_run or snapshot.journal_state in {
        JournalState.STAGED,
        JournalState.PROMOTING,
    }:
        return RecoveryClassification.PARTIAL_FAILURE
    now = _aware(snapshot.now, "now")
    if snapshot.available_after is not None and now < _aware(
        snapshot.available_after, "available_after"
    ):
        return RecoveryClassification.EXPECTED_LAG
    if now >= _aware(snapshot.scheduled_for, "scheduled_for") and not snapshot.schedule_attempted:
        return RecoveryClassification.MISSED_SCHEDULE
    return RecoveryClassification.STALE


def plan_recovery(snapshot: RecoverySnapshot, policy: RetryPolicy) -> RecoveryDecision:
    classification = classify_recovery(snapshot)
    missing = tuple(scope for scope in snapshot.requested_scopes if scope not in snapshot.completed_scopes)
    if classification is RecoveryClassification.RETAINED_SUCCESS:
        return RecoveryDecision(
            classification, RecoveryAction.API_ZERO_REPLAY, 0, 0, (), (),
            "verify retained outputs and checkpoint locally", snapshot.last_failure,
        )
    if classification in {RecoveryClassification.EXPECTED_LAG, RecoveryClassification.ACTIVE}:
        return RecoveryDecision(
            classification, RecoveryAction.WAIT, 0, 0, (), missing,
            "wait for provider eligibility or the active writer", snapshot.last_failure,
        )
    if classification is RecoveryClassification.PARTIAL_FAILURE:
        return RecoveryDecision(
            classification, RecoveryAction.RECOVER_LOCAL, 0, 0, (), missing,
            "recover or roll back the durable journal before any provider call",
            snapshot.last_failure,
        )
    calls, retries = policy.remaining(
        provider_calls_used=snapshot.provider_calls_used,
        retries_used=snapshot.retries_used,
        failure_kind=snapshot.last_failure,
    )
    if calls == 0:
        return RecoveryDecision(
            classification, RecoveryAction.STOP, 0, 0, (), missing,
            "stop: failure is non-retryable or the reviewed call budget is exhausted",
            snapshot.last_failure,
        )
    return RecoveryDecision(
        classification,
        RecoveryAction.RUN_BOUNDED,
        calls,
        retries,
        policy.backoff_seconds[snapshot.retries_used : snapshot.retries_used + retries],
        missing,
        "run only the missing reviewed scope from the last durable checkpoint",
        snapshot.last_failure,
    )


def recovery_event_pair(
    *,
    decision: RecoveryDecision,
    operation: str,
    logical_dataset: str,
    datasets: Sequence[str],
    trigger_type: TriggerType,
    expected_date: date,
    retained_date: date | None,
    at: datetime,
) -> tuple[UpdateEvent, UpdateEvent]:
    """Represent a zero-call supervisor assessment using the UR-048 schema."""

    now = _aware(at, "at")
    requested_scope = {
        "scope_key": _scope_key(operation, datasets),
        "recovery_classification": decision.classification.value,
        "recovery_action": decision.action.value,
        "provider_calls_remaining": decision.provider_calls_remaining,
        "retries_remaining": decision.retries_remaining,
        "backoff_seconds": list(decision.backoff_seconds),
        "missing_scopes": list(decision.missing_scopes),
        "failure_kind": decision.failure_kind.value,
        "next_action": decision.next_action,
    }
    started = UpdateEvent.started(
        run_id=new_run_id(f"recovery/{operation}", now=now),
        job_route=f"recovery/{operation}",
        logical_dataset=logical_dataset,
        trigger_type=trigger_type,
        requested_scope=requested_scope,
        at=now,
        expected_date=expected_date,
        prior_source_date=retained_date,
        message="recovery assessment started",
    )
    state = EventState.SUCCEEDED
    reason = ReasonCode.COMPLETED
    freshness = FreshnessResult.UNKNOWN
    validation = ValidationResult.PASSED
    promotion = CommitResult.NOT_RUN
    checkpoint = CommitResult.NOT_RUN
    if decision.classification is RecoveryClassification.RETAINED_SUCCESS:
        state = EventState.API_ZERO_NOOP
        reason = ReasonCode.ALREADY_CURRENT_API_ZERO
        freshness = FreshnessResult.CURRENT
        promotion = CommitResult.NOOP
        checkpoint = CommitResult.NOOP
    elif decision.classification is RecoveryClassification.EXPECTED_LAG:
        state = EventState.EXPECTED_DELAY
        reason = ReasonCode.PROVIDER_NOT_YET_AVAILABLE
        freshness = FreshnessResult.EXPECTED_LAG
        validation = ValidationResult.NOT_RUN
    elif decision.classification is RecoveryClassification.PARTIAL_FAILURE:
        state = EventState.PARTIAL_INELIGIBLE
        reason = ReasonCode.PARTIAL_SCOPE_INELIGIBLE
        validation = ValidationResult.PARTIAL
    elif decision.classification in {
        RecoveryClassification.MISSED_SCHEDULE,
        RecoveryClassification.STALE,
    }:
        freshness = FreshnessResult.STALE
    terminal = started.terminal(
        state=state,
        reason_code=reason,
        at=now,
        resulting_source_date=retained_date,
        provider_call_count=0,
        retry_count=0,
        validation_result=validation,
        promotion_result=promotion,
        checkpoint_result=checkpoint,
        freshness_result=freshness,
        finality_result=FinalityResult.UNKNOWN,
        message="recovery assessment completed without a provider call",
    )
    return started, terminal


class OperationScopeLock:
    """OS-owned per-operation/scope lock, automatically released on process exit."""

    def __init__(
        self,
        root: Path,
        *,
        operation: str,
        datasets: Sequence[str],
        run_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.scope_key = _scope_key(operation, datasets)
        self.path = Path(root) / ".locks" / f"{self.scope_key}.lock"
        self._token = uuid4().hex
        self._body = _json_bytes(
            {
                "version": 1,
                "scope_key": self.scope_key,
                "run_id": run_id,
                "pid": os.getpid(),
                "token": self._token,
                "acquired_at_utc": _aware(clock(), "clock").isoformat(),
            }
        )
        self._held = False
        self._stream: object | None = None

    @staticmethod
    def _lock_stream(stream: object) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ScopeLockBusy("operation/dataset scope already has a live writer") from error
        else:
            import fcntl

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ScopeLockBusy("operation/dataset scope already has a live writer") from error

    @staticmethod
    def _unlock_stream(stream: object) -> None:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def acquire(self) -> "OperationScopeLock":
        if self._held:
            raise RecoverySupervisorError("scope lock is already held")
        process_key = str(self.path.resolve())
        with _HELD_SCOPE_LOCKS_GUARD:
            if process_key in _HELD_SCOPE_LOCKS:
                raise ScopeLockBusy("operation/dataset scope already has a live writer")
            _HELD_SCOPE_LOCKS.add(process_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = self.path.open("a+b")
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            self._lock_stream(stream)
            stream.seek(0)
            stream.truncate()
            stream.write(self._body)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            if "stream" in locals():
                stream.close()
            with _HELD_SCOPE_LOCKS_GUARD:
                _HELD_SCOPE_LOCKS.discard(process_key)
            raise
        self._stream = stream
        self._held = True
        return self

    def release(self) -> None:
        if not self._held:
            raise RecoverySupervisorError("scope lock is not held")
        stream = self._stream
        if stream is None:
            raise RecoverySupervisorError("owned scope lock stream is missing")
        try:
            stream.seek(0)
            if stream.read() != self._body:
                raise RecoverySupervisorError("scope lock ownership differs; refusing release")
            self._unlock_stream(stream)
        finally:
            stream.close()
            self._stream = None
            self._held = False
            with _HELD_SCOPE_LOCKS_GUARD:
                _HELD_SCOPE_LOCKS.discard(str(self.path.resolve()))

    def __enter__(self) -> "OperationScopeLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    status: PromotionStatus
    provider_call_count: int
    target_date: date
    output_sha256: Mapping[str, str]


def _checkpoint_body(
    *, operation: str, scope_key: str, target_date: date, hashes: Mapping[str, str]
) -> bytes:
    return _json_bytes(
        {
            "schema_version": RECOVERY_CHECKPOINT_VERSION,
            "operation": operation,
            "scope_key": scope_key,
            "target_date": target_date.isoformat(),
            "status": "SUCCEEDED",
            "output_sha256": dict(hashes),
        }
    )


def _remove_backup_tree(path: Path, *, journal_path: Path) -> None:
    resolved = path.resolve()
    if (
        resolved.parent != journal_path.resolve().parent
        or not resolved.name.startswith(f".{journal_path.stem}.")
        or not resolved.name.endswith(".backup")
    ):
        raise RecoverySupervisorError("backup directory topology differs")
    if path.exists():
        shutil.rmtree(path)


def _validate_journal_identity(
    payload: Mapping[str, object],
    *,
    operation: str,
    scope_key: str,
    output_paths: Sequence[Path],
    checkpoint_path: Path,
) -> list[dict[str, object]]:
    if (
        payload.get("schema_version") != RECOVERY_JOURNAL_VERSION
        or payload.get("operation") != operation
        or payload.get("scope_key") != scope_key
        or Path(str(payload.get("checkpoint_path", ""))).resolve() != checkpoint_path.resolve()
    ):
        raise RecoverySupervisorError("recovery journal identity differs")
    records = payload.get("outputs")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise RecoverySupervisorError("recovery journal outputs are invalid")
    recorded = {Path(str(item.get("path", ""))).resolve() for item in records}
    expected = {path.resolve() for path in output_paths}
    if recorded != expected:
        raise RecoverySupervisorError("recovery journal output scope differs")
    return records


def recover_pending_promotion(
    *,
    operation: str,
    datasets: Sequence[str],
    output_paths: Sequence[Path],
    checkpoint_path: Path,
    journal_path: Path,
) -> PromotionStatus | None:
    """Restore an interrupted promotion using only its exact reviewed paths."""

    if not journal_path.exists():
        return None
    payload = _read_json(journal_path)
    scope_key = _scope_key(operation, datasets)
    records = _validate_journal_identity(
        payload,
        operation=operation,
        scope_key=scope_key,
        output_paths=output_paths,
        checkpoint_path=checkpoint_path,
    )
    try:
        state = JournalState(str(payload.get("state")))
    except ValueError as error:
        raise RecoverySupervisorError("recovery journal state is invalid") from error
    backup_root = Path(str(payload.get("backup_root", ""))).resolve()
    transaction_id = str(payload.get("transaction_id", ""))
    if len(transaction_id) != 32 or any(character not in "0123456789abcdef" for character in transaction_id):
        raise RecoverySupervisorError("recovery journal transaction identity is invalid")
    expected_backup = (
        journal_path.resolve().parent / f".{journal_path.stem}.{transaction_id}.backup"
    )
    if backup_root != expected_backup:
        raise RecoverySupervisorError("recovery journal backup scope differs")
    if state in {JournalState.COMMITTED, JournalState.ROLLED_BACK}:
        _remove_backup_tree(backup_root, journal_path=journal_path)
        return None

    restore_records = list(records)
    checkpoint_record = payload.get("checkpoint")
    if not isinstance(checkpoint_record, dict):
        raise RecoverySupervisorError("recovery journal checkpoint record is invalid")
    if Path(str(checkpoint_record.get("path", ""))).resolve() != checkpoint_path.resolve():
        raise RecoverySupervisorError("recovery journal checkpoint scope differs")
    restore_records.append(checkpoint_record)
    for record in restore_records:
        if bool(record.get("existed")):
            backup = Path(str(record.get("backup", ""))).resolve()
            digest = str(record.get("prior_sha256", ""))
            if (
                backup.parent != backup_root
                or not backup.is_file()
                or _sha256(backup.read_bytes()) != digest
            ):
                raise RecoverySupervisorError("rollback backup is missing or invalid")
    for record in restore_records:
        target = Path(str(record["path"])).resolve()
        if bool(record.get("existed")):
            _atomic_bytes(target, Path(str(record["backup"])).resolve().read_bytes())
        else:
            target.unlink(missing_ok=True)
    payload = {**payload, "state": JournalState.ROLLED_BACK.value}
    _atomic_bytes(journal_path, _json_bytes(payload))
    _remove_backup_tree(backup_root, journal_path=journal_path)
    return PromotionStatus.RECOVERED


def promote_outputs_atomically(
    *,
    operation: str,
    datasets: Sequence[str],
    target_date: date,
    outputs: Mapping[Path, bytes],
    checkpoint_path: Path,
    journal_path: Path,
    after_output: Callable[[int], None] | None = None,
) -> PromotionOutcome:
    """Promote an exact output set and checkpoint, rolling back on any failure."""

    if not outputs:
        raise ValueError("outputs must not be empty")
    resolved = {Path(path).resolve(): bytes(body) for path, body in outputs.items()}
    checkpoint_path = checkpoint_path.resolve()
    journal_path = journal_path.resolve()
    if len(resolved) != len(outputs):
        raise ValueError("output paths must remain unique after resolution")
    if checkpoint_path == journal_path or checkpoint_path in resolved or journal_path in resolved:
        raise ValueError("output, checkpoint, and journal paths must be distinct")
    scope_key = _scope_key(operation, datasets)
    hashes = {str(path): _sha256(body) for path, body in sorted(resolved.items(), key=lambda x: str(x[0]))}

    recover_pending_promotion(
        operation=operation,
        datasets=datasets,
        output_paths=tuple(resolved),
        checkpoint_path=checkpoint_path,
        journal_path=journal_path,
    )

    if checkpoint_path.exists():
        checkpoint = _read_json(checkpoint_path)
        if checkpoint.get("schema_version") == RECOVERY_CHECKPOINT_VERSION:
            same_identity = (
                checkpoint.get("operation") == operation
                and checkpoint.get("scope_key") == scope_key
                and checkpoint.get("target_date") == target_date.isoformat()
            )
            if same_identity:
                if checkpoint.get("output_sha256") != hashes:
                    raise RecoverySupervisorError("same-date checkpoint conflicts with requested outputs")
                if not all(path.is_file() and _sha256(path.read_bytes()) == hashes[str(path)] for path in resolved):
                    raise RecoverySupervisorError("checkpointed output is missing or changed")
                return PromotionOutcome(PromotionStatus.API_ZERO_NOOP, 0, target_date, hashes)

    transaction_id = uuid4().hex
    backup_root = journal_path.parent / f".{journal_path.stem}.{transaction_id}.backup"
    backup_root.mkdir(parents=True, exist_ok=False)

    def backup_record(path: Path, index: int) -> dict[str, object]:
        existed = path.is_file()
        record: dict[str, object] = {"path": str(path), "existed": existed}
        if existed:
            body = path.read_bytes()
            backup = backup_root / f"{index:03d}.bak"
            descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            record.update({"backup": str(backup.resolve()), "prior_sha256": _sha256(body)})
        return record

    try:
        records = [backup_record(path, index) for index, path in enumerate(resolved)]
        checkpoint_record = backup_record(checkpoint_path, len(records))
    except BaseException:
        _remove_backup_tree(backup_root, journal_path=journal_path)
        raise
    journal: dict[str, object] = {
        "schema_version": RECOVERY_JOURNAL_VERSION,
        "transaction_id": transaction_id,
        "operation": operation,
        "scope_key": scope_key,
        "target_date": target_date.isoformat(),
        "state": JournalState.STAGED.value,
        "backup_root": str(backup_root.resolve()),
        "outputs": records,
        "checkpoint": checkpoint_record,
        "checkpoint_path": str(checkpoint_path.resolve()),
        "promoted": [],
    }
    try:
        _atomic_bytes(journal_path, _json_bytes(journal))
    except BaseException:
        _remove_backup_tree(backup_root, journal_path=journal_path)
        raise
    promoted: list[str] = []
    try:
        for index, (path, body) in enumerate(resolved.items(), start=1):
            _atomic_bytes(path, body)
            promoted.append(str(path))
            journal.update({"state": JournalState.PROMOTING.value, "promoted": promoted.copy()})
            _atomic_bytes(journal_path, _json_bytes(journal))
            if after_output is not None:
                after_output(index)
        _atomic_bytes(
            checkpoint_path,
            _checkpoint_body(
                operation=operation,
                scope_key=scope_key,
                target_date=target_date,
                hashes=hashes,
            ),
        )
        journal.update({"state": JournalState.COMMITTED.value})
        _atomic_bytes(journal_path, _json_bytes(journal))
    except Exception as error:
        try:
            recover_pending_promotion(
                operation=operation,
                datasets=datasets,
                output_paths=tuple(resolved),
                checkpoint_path=checkpoint_path,
                journal_path=journal_path,
            )
        except Exception as rollback_error:
            raise RecoverySupervisorError("promotion failed and rollback could not be proven") from rollback_error
        raise RecoverySupervisorError("promotion failed; prior accepted bytes were restored") from error
    _remove_backup_tree(backup_root, journal_path=journal_path)
    return PromotionOutcome(PromotionStatus.COMMITTED, 0, target_date, hashes)


__all__ = [
    "FailureKind",
    "JournalState",
    "OperationScopeLock",
    "PromotionOutcome",
    "PromotionStatus",
    "RECOVERY_CHECKPOINT_VERSION",
    "RECOVERY_JOURNAL_VERSION",
    "RecoveryAction",
    "RecoveryClassification",
    "RecoveryDecision",
    "RecoverySnapshot",
    "RecoverySupervisorError",
    "RetryPolicy",
    "ScopeLockBusy",
    "classify_recovery",
    "plan_recovery",
    "promote_outputs_atomically",
    "recover_pending_promotion",
    "recovery_event_pair",
]
