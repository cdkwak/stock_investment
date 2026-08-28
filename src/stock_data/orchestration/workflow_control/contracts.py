"""Versioned, privacy-bounded contracts for offline workflow control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping


EVENT_SCHEMA_VERSION = "workflow-control-event/v1"
STATE_SCHEMA_VERSION = 1
STATE_PROJECTION_SCHEMA_VERSION = "workflow-control-state-projection/v1"
DIGEST_SCHEMA_VERSION = "workflow-control-digest/v1"

_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_SAFE_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class WorkflowContractError(ValueError):
    """Raised when a workflow fact does not satisfy the persisted contract."""


class EventKind(StrEnum):
    TASK_TRANSITION = "TASK_TRANSITION"
    REVIEW_RESULT = "REVIEW_RESULT"
    REWORK_REQUESTED = "REWORK_REQUESTED"
    ESCALATION = "ESCALATION"
    SESSION_STARTED = "SESSION_STARTED"
    QUEUE_SNAPSHOT = "QUEUE_SNAPSHOT"


class EventSource(StrEnum):
    QUEUE = "QUEUE"
    ORCA = "ORCA"
    SYSTEM = "SYSTEM"


class TaskState(StrEnum):
    NEW = "new"
    WAITING = "waiting"
    READY = "ready"
    ACTIVE = "active"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"


class ReviewOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


def utc_text(value: datetime) -> str:
    """Return one canonical millisecond UTC representation."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WorkflowContractError("timestamp must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise WorkflowContractError("timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkflowContractError("timestamp is not valid ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WorkflowContractError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def stable_fingerprint(value: str) -> str:
    """Hash a raw correlation identifier so the raw identifier is never retained."""

    if not isinstance(value, str) or not value:
        raise WorkflowContractError("fingerprint source must be non-empty text")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_enum(enum_type: type[StrEnum], value: object) -> StrEnum | None:
    if value is None:
        return None
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise WorkflowContractError(f"invalid {enum_type.__name__}") from error


def _optional_non_negative(value: object, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorkflowContractError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class WorkflowEvent:
    """One sanitized workflow fact.

    The contract intentionally has no message, prompt, transcript, arbitrary
    metadata, terminal handle, account identifier, or credential field.
    Correlation values that originate outside the Queue use SHA-256
    fingerprints rather than raw identifiers.
    """

    event_id: str
    occurred_at: datetime
    kind: EventKind
    source: EventSource
    task_id: str | None = None
    from_state: TaskState | None = None
    to_state: TaskState | None = None
    priority: Priority | None = None
    domain: str | None = None
    outcome: ReviewOutcome | None = None
    reason_code: str | None = None
    recurrence_fingerprint: str | None = None
    session_fingerprint: str | None = None
    runnable_count: int | None = None
    active_worker_count: int | None = None
    schema_version: str = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise WorkflowContractError("unsupported workflow event schema")
        if not isinstance(self.kind, EventKind) or not isinstance(self.source, EventSource):
            raise WorkflowContractError("kind and source must use workflow enums")
        if self.from_state is not None and not isinstance(self.from_state, TaskState):
            raise WorkflowContractError("from_state must use TaskState")
        if self.to_state is not None and not isinstance(self.to_state, TaskState):
            raise WorkflowContractError("to_state must use TaskState")
        if self.priority is not None and not isinstance(self.priority, Priority):
            raise WorkflowContractError("priority must use Priority")
        if self.outcome is not None and not isinstance(self.outcome, ReviewOutcome):
            raise WorkflowContractError("outcome must use ReviewOutcome")
        if not isinstance(self.event_id, str) or _EVENT_ID.fullmatch(self.event_id) is None:
            raise WorkflowContractError("event_id is not a safe stable identifier")
        canonical_occurred_at = parse_utc(utc_text(self.occurred_at))
        object.__setattr__(self, "occurred_at", canonical_occurred_at)
        if self.task_id is not None and (
            not isinstance(self.task_id, str) or _TASK_ID.fullmatch(self.task_id) is None
        ):
            raise WorkflowContractError("task_id must be an exact Queue task id")
        if self.domain is not None and (
            not isinstance(self.domain, str) or _SAFE_SLUG.fullmatch(self.domain) is None
        ):
            raise WorkflowContractError("domain must be a bounded lowercase slug")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str)
            or _REASON_CODE.fullmatch(self.reason_code) is None
        ):
            raise WorkflowContractError("reason_code must be a bounded symbolic code")
        for name in ("recurrence_fingerprint", "session_fingerprint"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None
            ):
                raise WorkflowContractError(f"{name} must be a SHA-256 digest")
        _optional_non_negative(self.runnable_count, "runnable_count")
        _optional_non_negative(self.active_worker_count, "active_worker_count")
        self._validate_kind()

    def _validate_kind(self) -> None:
        if self.kind is EventKind.TASK_TRANSITION:
            if self.task_id is None or self.to_state is None:
                raise WorkflowContractError("TASK_TRANSITION requires task_id and to_state")
        elif self.kind is EventKind.REVIEW_RESULT:
            if self.task_id is None or self.outcome is None:
                raise WorkflowContractError("REVIEW_RESULT requires task_id and outcome")
        elif self.kind is EventKind.REWORK_REQUESTED:
            if self.task_id is None:
                raise WorkflowContractError("REWORK_REQUESTED requires task_id")
        elif self.kind is EventKind.ESCALATION:
            if self.recurrence_fingerprint is None:
                raise WorkflowContractError("ESCALATION requires recurrence_fingerprint")
        elif self.kind is EventKind.SESSION_STARTED:
            if self.session_fingerprint is None:
                raise WorkflowContractError("SESSION_STARTED requires session_fingerprint")
        elif self.kind is EventKind.QUEUE_SNAPSHOT:
            if self.runnable_count is None or self.active_worker_count is None:
                raise WorkflowContractError(
                    "QUEUE_SNAPSHOT requires runnable_count and active_worker_count"
                )

    @property
    def sort_key(self) -> tuple[str, str]:
        return utc_text(self.occurred_at), self.event_id

    def to_dict(self) -> dict[str, Any]:
        """Return the complete allowlisted JSON representation."""

        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "occurred_at": utc_text(self.occurred_at),
            "kind": self.kind.value,
            "source": self.source.value,
            "task_id": self.task_id,
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value if self.to_state else None,
            "priority": self.priority.value if self.priority else None,
            "domain": self.domain,
            "outcome": self.outcome.value if self.outcome else None,
            "reason_code": self.reason_code,
            "recurrence_fingerprint": self.recurrence_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "runnable_count": self.runnable_count,
            "active_worker_count": self.active_worker_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowEvent":
        """Parse a persisted event and reject every non-contract field."""

        allowed = {
            "schema_version",
            "event_id",
            "occurred_at",
            "kind",
            "source",
            "task_id",
            "from_state",
            "to_state",
            "priority",
            "domain",
            "outcome",
            "reason_code",
            "recurrence_fingerprint",
            "session_fingerprint",
            "runnable_count",
            "active_worker_count",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise WorkflowContractError("persisted event contains non-contract fields")
        try:
            return cls(
                schema_version=payload["schema_version"],
                event_id=payload["event_id"],
                occurred_at=parse_utc(payload["occurred_at"]),
                kind=EventKind(payload["kind"]),
                source=EventSource(payload["source"]),
                task_id=payload.get("task_id"),
                from_state=_optional_enum(TaskState, payload.get("from_state")),
                to_state=_optional_enum(TaskState, payload.get("to_state")),
                priority=_optional_enum(Priority, payload.get("priority")),
                domain=payload.get("domain"),
                outcome=_optional_enum(ReviewOutcome, payload.get("outcome")),
                reason_code=payload.get("reason_code"),
                recurrence_fingerprint=payload.get("recurrence_fingerprint"),
                session_fingerprint=payload.get("session_fingerprint"),
                runnable_count=payload.get("runnable_count"),
                active_worker_count=payload.get("active_worker_count"),
            )
        except KeyError as error:
            raise WorkflowContractError(f"missing workflow event field: {error.args[0]}") from error
        except (TypeError, ValueError) as error:
            if isinstance(error, WorkflowContractError):
                raise
            raise WorkflowContractError("workflow event contains an invalid enum") from error

    @classmethod
    def from_source(cls, payload: Mapping[str, Any]) -> "WorkflowEvent":
        """Project an untrusted source mapping onto the privacy allowlist.

        Unknown values, including prompts, transcripts, terminal/account
        identifiers, credentials, tokens, response bodies, and arbitrary free
        text, are excluded before validation and can never reach persistence.
        A source-controlled event ID is also discarded; the persisted ID is a
        full SHA-256 digest of the validated canonical allowlist projection.
        """

        allowed = {
            "schema_version",
            "occurred_at",
            "kind",
            "source",
            "task_id",
            "from_state",
            "to_state",
            "priority",
            "domain",
            "outcome",
            "reason_code",
            "recurrence_fingerprint",
            "session_fingerprint",
            "runnable_count",
            "active_worker_count",
        }
        projected = {key: value for key, value in payload.items() if key in allowed}
        projected.setdefault("schema_version", EVENT_SCHEMA_VERSION)
        sanitized = cls.from_dict({"event_id": "source-material", **projected})
        material = sanitized.to_dict()
        material.pop("event_id")
        canonical_material = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        material["event_id"] = "source-" + stable_fingerprint(canonical_material)
        return cls.from_dict(material)


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    state: TaskState
    updated_at: datetime
    last_event_id: str
    priority: Priority | None = None
    domain: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or _TASK_ID.fullmatch(self.task_id) is None:
            raise WorkflowContractError("task snapshot has an invalid task id")
        if (
            not isinstance(self.last_event_id, str)
            or _EVENT_ID.fullmatch(self.last_event_id) is None
        ):
            raise WorkflowContractError("task snapshot has an invalid event id")
        if not isinstance(self.state, TaskState):
            raise WorkflowContractError("task snapshot has an invalid state")
        if self.priority is not None and not isinstance(self.priority, Priority):
            raise WorkflowContractError("task snapshot has an invalid priority")
        utc_text(self.updated_at)
        if self.domain is not None and (
            not isinstance(self.domain, str) or _SAFE_SLUG.fullmatch(self.domain) is None
        ):
            raise WorkflowContractError("task snapshot has an invalid domain")
