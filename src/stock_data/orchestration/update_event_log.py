"""Typed, local operational event logging for bounded data updates.

The log is an operational index under ``artifacts/runtime_logs``.  It is not a
dataset, provider ledger, checkpoint, or substitute for immutable Landing
evidence.  Callers must keep their operation outcome independent from the
returned :class:`EventWriteResult` and surface any logging error separately.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from stock_data.orchestration.account_privacy import redact_account_text


SCHEMA_VERSION = "data-update-event/v1"
DEFAULT_RUNTIME_LOG_ROOT = Path("artifacts/runtime_logs/data_updates")
KST = timezone(timedelta(hours=9), name="KST")

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/=-]{0,159}$")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|secret|password|passwd|cookie|signature|account|balance|cash|holding|"
    r"position|valuation|profit[_-]?loss|pnl|portfolio|payload|response|"
    r"request[_-]?body|body|url|uri)",
    re.IGNORECASE,
)
_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"secret|password|passwd|cookie|signature|account(?:_id|_number)?|balance|"
    r"holdings?)\s*[:=]\s*([^\s,;&]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{10,}(?!\d)")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class TriggerType(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    API_ZERO_REPLAY = "API_ZERO_REPLAY"


class EventState(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    API_ZERO_NOOP = "API_ZERO_NOOP"
    EXPECTED_DELAY = "EXPECTED_DELAY"
    VALID_EMPTY = "VALID_EMPTY"
    PARTIAL_INELIGIBLE = "PARTIAL_INELIGIBLE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    PROVIDER_NETWORK_FAILURE = "PROVIDER_NETWORK_FAILURE"
    AUTH_PERMISSION_FAILURE = "AUTH_PERMISSION_FAILURE"
    LOCAL_IO_FAILURE = "LOCAL_IO_FAILURE"
    RECOVERED = "RECOVERED"

    @property
    def terminal(self) -> bool:
        return self is not EventState.STARTED


class ValidationResult(StrEnum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    VALID_EMPTY = "VALID_EMPTY"
    PARTIAL = "PARTIAL"


class CommitResult(StrEnum):
    NOT_RUN = "NOT_RUN"
    SUCCEEDED = "SUCCEEDED"
    NOOP = "NOOP"
    FAILED = "FAILED"


class FreshnessResult(StrEnum):
    CURRENT = "CURRENT"
    EXPECTED_LAG = "EXPECTED_LAG"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"


class FinalityResult(StrEnum):
    CONFIRMED = "CONFIRMED"
    EXPECTED_DELAY = "EXPECTED_DELAY"
    AS_RETRIEVED = "AS_RETRIEVED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReasonCode(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    COMPLETED = "COMPLETED"
    ALREADY_CURRENT_API_ZERO = "ALREADY_CURRENT_API_ZERO"
    PROVIDER_NOT_YET_AVAILABLE = "PROVIDER_NOT_YET_AVAILABLE"
    VALID_EMPTY_ACCEPTED = "VALID_EMPTY_ACCEPTED"
    PARTIAL_SCOPE_INELIGIBLE = "PARTIAL_SCOPE_INELIGIBLE"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    PROVIDER_OR_NETWORK_ERROR = "PROVIDER_OR_NETWORK_ERROR"
    AUTHENTICATION_OR_PERMISSION_DENIED = "AUTHENTICATION_OR_PERMISSION_DENIED"
    LOCAL_READ_WRITE_ERROR = "LOCAL_READ_WRITE_ERROR"
    RECOVERED_AFTER_FAILURE = "RECOVERED_AFTER_FAILURE"


class Transition(StrEnum):
    SINGLE = "SINGLE"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    RECOVERY = "RECOVERY"


FAILURE_STATES = frozenset(
    {
        EventState.PARTIAL_INELIGIBLE,
        EventState.VALIDATION_FAILURE,
        EventState.PROVIDER_NETWORK_FAILURE,
        EventState.AUTH_PERMISSION_FAILURE,
        EventState.LOCAL_IO_FAILURE,
    }
)
SUCCESS_STATES = frozenset(
    {EventState.SUCCEEDED, EventState.API_ZERO_NOOP, EventState.VALID_EMPTY, EventState.RECOVERED}
)
_REASON_BY_STATE = {
    EventState.STARTED: ReasonCode.RUN_STARTED,
    EventState.SUCCEEDED: ReasonCode.COMPLETED,
    EventState.API_ZERO_NOOP: ReasonCode.ALREADY_CURRENT_API_ZERO,
    EventState.EXPECTED_DELAY: ReasonCode.PROVIDER_NOT_YET_AVAILABLE,
    EventState.VALID_EMPTY: ReasonCode.VALID_EMPTY_ACCEPTED,
    EventState.PARTIAL_INELIGIBLE: ReasonCode.PARTIAL_SCOPE_INELIGIBLE,
    EventState.VALIDATION_FAILURE: ReasonCode.VALIDATION_REJECTED,
    EventState.PROVIDER_NETWORK_FAILURE: ReasonCode.PROVIDER_OR_NETWORK_ERROR,
    EventState.AUTH_PERMISSION_FAILURE: ReasonCode.AUTHENTICATION_OR_PERMISSION_DENIED,
    EventState.LOCAL_IO_FAILURE: ReasonCode.LOCAL_READ_WRITE_ERROR,
    EventState.RECOVERED: ReasonCode.RECOVERED_AFTER_FAILURE,
}


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    converted = value.astimezone(timezone.utc)
    if converted.utcoffset() != timedelta(0):  # defensive; UTC always has zero offset
        raise ValueError(f"{field_name} must resolve to UTC")
    return converted


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} is not a valid timestamp") from error
    return _utc(parsed, field_name)


def _date_text(value: date | str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be an ISO date or null") from error


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a non-sensitive stable identifier")
    return value


def redact_text(value: object, *, limit: int = 500) -> str:
    """Return bounded diagnostic text with credential/account/URL material removed."""

    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _KEY_VALUE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)

    def _redact_url(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match.group(0))
            return f"{parsed.scheme}://{parsed.netloc}/[REDACTED_URL]"
        except ValueError:
            return "[REDACTED_URL]"

    text = _URL.sub(_redact_url, text)
    text = _LONG_NUMBER.sub("[REDACTED_NUMBER]", text)
    return redact_account_text(text, limit=limit)


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)


def new_run_id(job_route: str, *, now: datetime | None = None) -> str:
    """Create one stable correlation ID for an execution; callers reuse it for all events."""

    route = _identifier(job_route, "job_route").replace("/", "-").replace(":", "-")
    instant = _utc(now or datetime.now(timezone.utc), "now")
    return f"{route}-{instant:%Y%m%dT%H%M%SZ}-{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class UpdateEvent:
    run_id: str
    event_id: str
    state: EventState
    reason_code: ReasonCode
    job_route: str
    logical_dataset: str
    trigger_type: TriggerType
    requested_scope: Mapping[str, Any]
    started_at_utc: datetime
    event_at_utc: datetime
    ended_at_utc: datetime | None = None
    prior_source_date: str | date | None = None
    resulting_source_date: str | date | None = None
    expected_date: str | date | None = None
    row_counts: Mapping[str, int] = field(default_factory=dict)
    provider_call_count: int = 0
    retry_count: int = 0
    elapsed_ms: int = 0
    validation_result: ValidationResult = ValidationResult.NOT_RUN
    promotion_result: CommitResult = CommitResult.NOT_RUN
    checkpoint_result: CommitResult = CommitResult.NOT_RUN
    freshness_result: FreshnessResult = FreshnessResult.UNKNOWN
    finality_result: FinalityResult = FinalityResult.UNKNOWN
    message: str = ""
    transition: Transition = Transition.SINGLE
    related_run_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identifier(self.run_id, "run_id")
        _identifier(self.event_id, "event_id")
        _identifier(self.job_route, "job_route")
        _identifier(self.logical_dataset, "logical_dataset")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported event schema version")
        if _REASON_BY_STATE.get(self.state) is not self.reason_code:
            raise ValueError("reason_code does not match event state")
        started = _utc(self.started_at_utc, "started_at_utc")
        emitted = _utc(self.event_at_utc, "event_at_utc")
        if emitted < started:
            raise ValueError("event_at_utc precedes started_at_utc")
        if self.state.terminal:
            if self.ended_at_utc is None:
                raise ValueError("terminal event requires ended_at_utc")
            ended = _utc(self.ended_at_utc, "ended_at_utc")
            if ended < started or emitted < ended:
                raise ValueError("terminal timestamps are not monotonic")
        elif self.ended_at_utc is not None:
            raise ValueError("STARTED event cannot have ended_at_utc")
        for name in ("provider_call_count", "retry_count", "elapsed_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for key, count in self.row_counts.items():
            _identifier(str(key), "row_counts key")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError("row_counts values must be non-negative integers")
        for name in ("prior_source_date", "resulting_source_date", "expected_date"):
            _date_text(getattr(self, name), name)
        if self.related_run_id is not None:
            _identifier(self.related_run_id, "related_run_id")

    @classmethod
    def started(
        cls,
        *,
        run_id: str,
        job_route: str,
        logical_dataset: str,
        trigger_type: TriggerType,
        requested_scope: Mapping[str, Any],
        at: datetime,
        expected_date: str | date | None = None,
        prior_source_date: str | date | None = None,
        message: str = "",
    ) -> "UpdateEvent":
        instant = _utc(at, "at")
        return cls(
            run_id=run_id,
            event_id=uuid4().hex,
            state=EventState.STARTED,
            reason_code=ReasonCode.RUN_STARTED,
            job_route=job_route,
            logical_dataset=logical_dataset,
            trigger_type=trigger_type,
            requested_scope=requested_scope,
            started_at_utc=instant,
            event_at_utc=instant,
            expected_date=expected_date,
            prior_source_date=prior_source_date,
            message=message,
        )

    def terminal(
        self,
        *,
        state: EventState,
        reason_code: ReasonCode,
        at: datetime,
        resulting_source_date: str | date | None = None,
        row_counts: Mapping[str, int] | None = None,
        provider_call_count: int = 0,
        retry_count: int = 0,
        validation_result: ValidationResult = ValidationResult.NOT_RUN,
        promotion_result: CommitResult = CommitResult.NOT_RUN,
        checkpoint_result: CommitResult = CommitResult.NOT_RUN,
        freshness_result: FreshnessResult = FreshnessResult.UNKNOWN,
        finality_result: FinalityResult = FinalityResult.UNKNOWN,
        message: str = "",
    ) -> "UpdateEvent":
        if state is EventState.STARTED:
            raise ValueError("terminal event state cannot be STARTED")
        ended = _utc(at, "at")
        elapsed = max(0, int((ended - self.started_at_utc).total_seconds() * 1000))
        return replace(
            self,
            event_id=uuid4().hex,
            state=state,
            reason_code=reason_code,
            event_at_utc=ended,
            ended_at_utc=ended,
            resulting_source_date=resulting_source_date,
            row_counts=row_counts or {},
            provider_call_count=provider_call_count,
            retry_count=retry_count,
            elapsed_ms=elapsed,
            validation_result=validation_result,
            promotion_result=promotion_result,
            checkpoint_result=checkpoint_result,
            freshness_result=freshness_result,
            finality_result=finality_result,
            message=message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "event_id": self.event_id,
            "state": self.state.value,
            "reason_code": self.reason_code.value,
            "transition": self.transition.value,
            "related_run_id": self.related_run_id,
            "job_route": self.job_route,
            "logical_dataset": self.logical_dataset,
            "trigger_type": self.trigger_type.value,
            "requested_scope": _redact_value(self.requested_scope),
            "started_at_utc": _iso_utc(self.started_at_utc),
            "started_at_kst": self.started_at_utc.astimezone(KST).isoformat(timespec="milliseconds"),
            "event_at_utc": _iso_utc(self.event_at_utc),
            "ended_at_utc": _iso_utc(self.ended_at_utc) if self.ended_at_utc else None,
            "event_at_kst": self.event_at_utc.astimezone(KST).isoformat(timespec="milliseconds"),
            "ended_at_kst": (
                self.ended_at_utc.astimezone(KST).isoformat(timespec="milliseconds")
                if self.ended_at_utc else None
            ),
            "prior_source_date": _date_text(self.prior_source_date, "prior_source_date"),
            "resulting_source_date": _date_text(self.resulting_source_date, "resulting_source_date"),
            "expected_date": _date_text(self.expected_date, "expected_date"),
            "row_counts": dict(self.row_counts),
            "provider_call_count": self.provider_call_count,
            "retry_count": self.retry_count,
            "elapsed_ms": self.elapsed_ms,
            "validation_result": self.validation_result.value,
            "promotion_result": self.promotion_result.value,
            "checkpoint_result": self.checkpoint_result.value,
            "freshness_result": self.freshness_result.value,
            "finality_result": self.finality_result.value,
            "message": redact_text(self.message),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "UpdateEvent":
        return cls(
            schema_version=str(payload["schema_version"]),
            run_id=str(payload["run_id"]),
            event_id=str(payload["event_id"]),
            state=EventState(payload["state"]),
            reason_code=ReasonCode(payload["reason_code"]),
            transition=Transition(payload.get("transition", Transition.SINGLE.value)),
            related_run_id=payload.get("related_run_id"),
            job_route=str(payload["job_route"]),
            logical_dataset=str(payload["logical_dataset"]),
            trigger_type=TriggerType(payload["trigger_type"]),
            requested_scope=dict(payload.get("requested_scope", {})),
            started_at_utc=_parse_utc(payload["started_at_utc"], "started_at_utc"),
            event_at_utc=_parse_utc(payload["event_at_utc"], "event_at_utc"),
            ended_at_utc=(
                _parse_utc(payload["ended_at_utc"], "ended_at_utc")
                if payload.get("ended_at_utc") else None
            ),
            prior_source_date=payload.get("prior_source_date"),
            resulting_source_date=payload.get("resulting_source_date"),
            expected_date=payload.get("expected_date"),
            row_counts=dict(payload.get("row_counts", {})),
            provider_call_count=int(payload.get("provider_call_count", 0)),
            retry_count=int(payload.get("retry_count", 0)),
            elapsed_ms=int(payload.get("elapsed_ms", 0)),
            validation_result=ValidationResult(payload.get("validation_result", "NOT_RUN")),
            promotion_result=CommitResult(payload.get("promotion_result", "NOT_RUN")),
            checkpoint_result=CommitResult(payload.get("checkpoint_result", "NOT_RUN")),
            freshness_result=FreshnessResult(payload.get("freshness_result", "UNKNOWN")),
            finality_result=FinalityResult(payload.get("finality_result", "UNKNOWN")),
            message=str(payload.get("message", "")),
        )


@dataclass(frozen=True, slots=True)
class EventLogPolicy:
    max_events: int = 2_000
    retention_days: int = 90
    lock_timeout_seconds: float = 5.0
    stale_lock_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.max_events < 2:
            raise ValueError("max_events must be at least 2")
        if self.retention_days < 1:
            raise ValueError("retention_days must be positive")
        if self.lock_timeout_seconds <= 0 or self.stale_lock_seconds <= 0:
            raise ValueError("lock timeouts must be positive")


@dataclass(frozen=True, slots=True)
class EventWriteResult:
    persisted: bool
    duplicate: bool = False
    path: Path | None = None
    recovered_pending: int = 0
    pruned_events: int = 0
    error_code: str | None = None
    safe_message: str = ""

    @property
    def ok(self) -> bool:
        return self.persisted or self.duplicate


class EventLogReadError(RuntimeError):
    pass


class LocalUpdateEventLog:
    """Atomic per-event JSON store safe for independent concurrent writers."""

    def __init__(
        self,
        root: Path = DEFAULT_RUNTIME_LOG_ROOT,
        *,
        policy: EventLogPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.root = Path(root)
        self.policy = policy or EventLogPolicy()
        self.clock = clock
        self.events_root = self.root / "events"
        self.pending_root = self.root / ".pending"
        self.lock_path = self.root / ".write.lock"

    def append(self, event: UpdateEvent) -> EventWriteResult:
        """Persist an event without raising local-I/O errors into update control flow."""

        try:
            with self._lock():
                recovered = self._recover_pending()
                existing = self._events_unlocked()
                duplicate = self._duplicate_or_conflict(event, existing)
                if duplicate is not None:
                    return EventWriteResult(
                        persisted=False,
                        duplicate=True,
                        path=duplicate,
                        recovered_pending=recovered,
                        safe_message="event already persisted",
                    )
                enriched = self._derive_transition(event, existing)
                payload = json.dumps(
                    enriched.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8") + b"\n"
                destination = self._event_path(enriched)
                pending = self.pending_root / f"{enriched.event_id}.pending"
                self.pending_root.mkdir(parents=True, exist_ok=True)
                self._write_pending(pending, payload)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(pending, destination)
                self._fsync_directory(destination.parent)
                try:
                    pruned = self._prune(protected_run_id=enriched.run_id)
                except OSError as error:
                    return EventWriteResult(
                        persisted=True,
                        path=destination,
                        recovered_pending=recovered,
                        error_code="ROTATION_FAILED",
                        safe_message=redact_text(error),
                    )
                return EventWriteResult(
                    persisted=True,
                    path=destination,
                    recovered_pending=recovered,
                    pruned_events=pruned,
                )
        except (OSError, ValueError, EventLogReadError) as error:
            return EventWriteResult(
                persisted=False,
                error_code="LOCAL_LOG_WRITE_FAILED",
                safe_message=redact_text(error),
            )

    def read_events(self) -> tuple[UpdateEvent, ...]:
        events = self._events_unlocked()
        return tuple(event for _, event in sorted(events, key=lambda item: (
            item[1].event_at_utc, item[1].event_id
        )))

    def _events_unlocked(self) -> list[tuple[Path, UpdateEvent]]:
        if not self.events_root.exists():
            return []
        found: list[tuple[Path, UpdateEvent]] = []
        for path in self.events_root.glob("*/*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                found.append((path, UpdateEvent.from_dict(payload)))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise EventLogReadError(f"malformed event file {path.name}: {redact_text(error)}") from None
        return found

    def _duplicate_or_conflict(
        self, event: UpdateEvent, existing: list[tuple[Path, UpdateEvent]]
    ) -> Path | None:
        same_run = [(path, prior) for path, prior in existing if prior.run_id == event.run_id]
        for path, prior in same_run:
            if prior.event_id == event.event_id:
                return path
        same_phase = [(path, prior) for path, prior in same_run if prior.state.terminal == event.state.terminal]
        if same_phase:
            phase = "terminal" if event.state.terminal else "started"
            prior_path, prior = same_phase[0]
            comparable = lambda item: {
                key: value for key, value in item.to_dict().items()
                if key not in {"event_id", "event_at_utc", "event_at_kst", "ended_at_utc", "elapsed_ms"}
            }
            if comparable(prior) == comparable(event):
                return prior_path
            raise ValueError(f"run already has a different {phase} event")
        return None

    def _derive_transition(
        self, event: UpdateEvent, existing: list[tuple[Path, UpdateEvent]]
    ) -> UpdateEvent:
        if not event.state.terminal or event.transition is not Transition.SINGLE:
            return event
        candidates = [
            prior for _, prior in existing
            if prior.state.terminal
            and prior.run_id != event.run_id
            and prior.job_route == event.job_route
            and prior.logical_dataset == event.logical_dataset
        ]
        if not candidates:
            return event
        prior = max(candidates, key=lambda item: (item.event_at_utc, item.event_id))
        if event.state in FAILURE_STATES and prior.state in FAILURE_STATES:
            return replace(event, transition=Transition.REPEATED_FAILURE, related_run_id=prior.run_id)
        if event.state in SUCCESS_STATES and prior.state in FAILURE_STATES:
            return replace(event, transition=Transition.RECOVERY, related_run_id=prior.run_id)
        return event

    def _event_path(self, event: UpdateEvent) -> Path:
        day = event.event_at_utc.date().isoformat()
        stamp = event.event_at_utc.strftime("%H%M%S.%fZ")
        return self.events_root / day / f"{stamp}-{event.event_id}.json"

    def _write_pending(self, path: Path, payload: bytes) -> None:
        if path.exists():
            if path.read_bytes() == payload:
                return
            raise ValueError("pending event ID collision")
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def _recover_pending(self) -> int:
        if not self.pending_root.exists():
            return 0
        recovered = 0
        for pending in self.pending_root.glob("*.pending"):
            try:
                payload = pending.read_bytes()
                event = UpdateEvent.from_dict(json.loads(payload))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            destination = self._event_path(event)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.read_bytes() != payload:
                    raise ValueError("recovered event ID collision")
                pending.unlink()
            else:
                os.replace(pending, destination)
                self._fsync_directory(destination.parent)
            recovered += 1
        return recovered

    def _prune(self, *, protected_run_id: str) -> int:
        events = self._events_unlocked()
        now = _utc(self.clock(), "clock")
        cutoff = now - timedelta(days=self.policy.retention_days)
        groups: dict[str, list[tuple[Path, UpdateEvent]]] = {}
        for item in events:
            groups.setdefault(item[1].run_id, []).append(item)
        removed = 0
        for run_id, items in list(groups.items()):
            if run_id == protected_run_id:
                continue
            latest = max(event.event_at_utc for _, event in items)
            if latest < cutoff:
                for path, _ in items:
                    path.unlink()
                    removed += 1
                groups.pop(run_id)
        total = sum(len(items) for items in groups.values())
        oldest_first = sorted(
            groups.items(), key=lambda pair: max(event.event_at_utc for _, event in pair[1])
        )
        for run_id, items in oldest_first:
            if total <= self.policy.max_events:
                break
            if run_id == protected_run_id:
                continue
            for path, _ in items:
                path.unlink()
                removed += 1
            total -= len(items)
        return removed

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.policy.lock_timeout_seconds
        token = uuid4().hex.encode("ascii")
        while True:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(token)
                    handle.flush()
                    os.fsync(handle.fileno())
                break
            except (FileExistsError, PermissionError):
                # Windows can report sharing violations as PermissionError while
                # another writer is closing the exclusively-created lock file.
                # The root mkdir above already established the parent boundary,
                # so both errors here are lock contention until the bounded
                # timeout expires.
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > self.policy.stale_lock_seconds:
                        try:
                            self.lock_path.unlink()
                            continue
                        except PermissionError:
                            pass
                except (FileNotFoundError, PermissionError):
                    pass
                if time.monotonic() >= deadline:
                    raise OSError("event-log writer lock timeout") from None
                time.sleep(0.01)
            except OSError:
                raise
        try:
            yield
        finally:
            for _ in range(20):
                try:
                    if not self.lock_path.is_file() or self.lock_path.read_bytes() != token:
                        break
                    self.lock_path.unlink()
                    break
                except PermissionError:
                    time.sleep(0.005)
                except OSError:
                    break

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def event_digest(event: UpdateEvent) -> str:
    """Stable content digest useful when linking checkpoint/run evidence."""

    payload = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "CommitResult",
    "DEFAULT_RUNTIME_LOG_ROOT",
    "EventLogPolicy",
    "EventLogReadError",
    "EventState",
    "EventWriteResult",
    "FinalityResult",
    "FreshnessResult",
    "LocalUpdateEventLog",
    "ReasonCode",
    "SCHEMA_VERSION",
    "Transition",
    "TriggerType",
    "UpdateEvent",
    "ValidationResult",
    "event_digest",
    "new_run_id",
    "redact_text",
]
