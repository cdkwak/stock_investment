"""Durable Listener routing with injected, idempotent delivery boundaries.

SQLite is authoritative. The JSONL journal is a sanitized, append-only
projection. This module never mutates Project Goal, Queue, or Orca state;
callers inject PM-owned sinks that deduplicate the stable receipt keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Protocol, Sequence


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_RECIPIENT_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_QUEUE_ID_PATTERN = re.compile(r"RQ-\d{8}T\d{6}-[A-Z0-9]{4}")
_JOURNAL_VERSION = 1


class ListenerValidationError(ValueError):
    """A Listener input or persisted record is not schema-valid."""


class ListenerDigestCollision(RuntimeError):
    """A digest maps to different canonical content."""


class ListenerStateConflict(RuntimeError):
    """SQLite and the append-only journal disagree."""


class PMAuthorityRequired(PermissionError):
    """A Goal/New route lacks explicit Python PM capability."""


class RouteKind(str, Enum):
    GOAL_CHANGE = "goal_change"
    DIRECT_PM = "direct_pm"
    BOUNDED_NEW = "bounded_new"


_ROUTE_FIELDS: dict[RouteKind, str] = {
    RouteKind.GOAL_CHANGE: "goal_text",
    RouteKind.DIRECT_PM: "message",
    RouteKind.BOUNDED_NEW: "summary",
}

_DIRECT_PM_OPTIONAL_FIELDS = frozenset(
    {"generation", "message_type", "queue_id", "recipient", "session_id"}
)
_MAILBOX_MESSAGE_TYPES = frozenset({"direct_message", "operational_wake", "user_intent"})
_PM_RECIPIENT = "project_manager"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ListenerValidationError(f"{field_name} must be a non-empty string")
    return value


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise ListenerValidationError("value must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ListenerValidationError("value must contain only JSON values") from exc
    if not isinstance(json.loads(encoded), dict):
        raise ListenerValidationError("value must encode a JSON object")
    return encoded


def _validate_digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ListenerValidationError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ListenerIntent:
    listener_id: str
    conversation_id: str
    checkpoint_cursor: str
    user_text: str
    received_at: str
    previous_intent_key: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "listener_id",
            "conversation_id",
            "checkpoint_cursor",
            "user_text",
            "received_at",
        ):
            _required_text(getattr(self, name), name)
        if self.previous_intent_key is not None:
            _validate_digest(self.previous_intent_key, "previous_intent_key")

    @property
    def canonical_json(self) -> str:
        return _canonical_json(
            {
                "checkpoint_cursor": self.checkpoint_cursor,
                "conversation_id": self.conversation_id,
                "listener_id": self.listener_id,
                "previous_intent_key": self.previous_intent_key,
                "user_text": self.user_text,
            }
        )

    @property
    def intent_key(self) -> str:
        return _sha256_text(f"listener-intent/v1\n{self.canonical_json}")


@dataclass(frozen=True, slots=True)
class ListenerRoute:
    kind: RouteKind
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RouteKind):
            raise ListenerValidationError("route kind must be a RouteKind")
        canonical = json.loads(_canonical_json(self.payload))
        required = _ROUTE_FIELDS[self.kind]
        fields = set(canonical)
        if self.kind is not RouteKind.DIRECT_PM and fields != {required}:
            raise ListenerValidationError(
                f"{self.kind.value} payload must contain only {required}"
            )
        if self.kind is RouteKind.DIRECT_PM:
            allowed = {required, *_DIRECT_PM_OPTIONAL_FIELDS}
            if required not in fields or not fields <= allowed:
                raise ListenerValidationError(
                    "direct_pm payload contains unsupported or missing fields"
                )
            extended = fields - {required}
            if extended and not {"recipient", "session_id", "generation"} <= fields:
                raise ListenerValidationError(
                    "explicit direct_pm routing requires recipient, session_id and generation"
                )
            if "recipient" in canonical:
                recipient = _required_text(canonical["recipient"], "recipient")
                if _RECIPIENT_PATTERN.fullmatch(recipient) is None:
                    raise ListenerValidationError("recipient is malformed")
                if recipient != _PM_RECIPIENT:
                    raise ListenerValidationError(
                        "direct_pm recipient must be the durable project_manager role"
                    )
            if "generation" in canonical and (
                not isinstance(canonical["generation"], int)
                or isinstance(canonical["generation"], bool)
                or canonical["generation"] < 1
            ):
                raise ListenerValidationError("generation must be a positive integer")
            if "session_id" in canonical and (
                not isinstance(canonical["session_id"], str)
                or _SESSION_ID_PATTERN.fullmatch(canonical["session_id"]) is None
            ):
                raise ListenerValidationError("session_id is malformed")
            if "queue_id" in canonical:
                queue_id = canonical["queue_id"]
                if queue_id is not None and (
                    not isinstance(queue_id, str)
                    or _QUEUE_ID_PATTERN.fullmatch(queue_id) is None
                ):
                    raise ListenerValidationError("queue_id must be null or an exact Queue id")
            if (
                "message_type" in canonical
                and (
                    not isinstance(canonical["message_type"], str)
                    or canonical["message_type"] not in _MAILBOX_MESSAGE_TYPES
                )
            ):
                raise ListenerValidationError("message_type is not supported")
        _required_text(canonical[required], required)

    @classmethod
    def from_mapping(cls, declaration: Mapping[str, Any]) -> "ListenerRoute":
        canonical = json.loads(_canonical_json(declaration))
        if set(canonical) != {"kind", "payload"}:
            raise ListenerValidationError("route declaration must contain only kind and payload")
        kind_value = _required_text(canonical["kind"], "kind")
        try:
            kind = RouteKind(kind_value)
        except ValueError as exc:
            raise ListenerValidationError(f"unknown route kind: {kind_value}") from exc
        payload = canonical["payload"]
        if not isinstance(payload, dict):
            raise ListenerValidationError("route payload must be a JSON object")
        return cls(kind, payload)

    @property
    def canonical_payload_json(self) -> str:
        return _canonical_json(self.payload)


@dataclass(frozen=True, slots=True)
class _ListenerAction:
    intent_key: str
    route: ListenerRoute
    created_at: str

    def __post_init__(self) -> None:
        _validate_digest(self.intent_key, "intent_key")
        if not isinstance(self.route, ListenerRoute):
            raise ListenerValidationError("route must be a ListenerRoute")
        _required_text(self.created_at, "created_at")

    @property
    def canonical_json(self) -> str:
        return _canonical_json(
            {
                "intent_key": self.intent_key,
                "payload": json.loads(self.route.canonical_payload_json),
                "route_kind": self.route.kind.value,
            }
        )

    @property
    def action_key(self) -> str:
        return _sha256_text(f"listener-action/v1\n{self.canonical_json}")


@dataclass(frozen=True, slots=True)
class PMMutationAuthority:
    controller: str
    goal_and_new: bool

    def __post_init__(self) -> None:
        if self.controller != "python_pm" or self.goal_and_new is not True:
            raise ListenerValidationError(
                "PM authority must be the explicit python_pm Goal/New capability"
            )


@dataclass(frozen=True, slots=True)
class PMMailboxIdentity:
    """Current durable PM mailbox owner resolved from the role registry."""

    recipient: str
    session_id: str
    generation: int

    def __post_init__(self) -> None:
        if self.recipient != _PM_RECIPIENT:
            raise ListenerValidationError("PM mailbox identity recipient is invalid")
        if (
            not isinstance(self.session_id, str)
            or _SESSION_ID_PATTERN.fullmatch(self.session_id) is None
        ):
            raise ListenerValidationError("PM mailbox identity session_id is malformed")
        if (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 1
        ):
            raise ListenerValidationError("PM mailbox identity generation is invalid")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    receipt_key: str
    action_key: str
    sink: str
    status: str

    def __post_init__(self) -> None:
        _validate_digest(self.receipt_key, "receipt_key")
        _validate_digest(self.action_key, "action_key")
        _required_text(self.sink, "sink")
        if self.status not in {"pending", "accepted"}:
            raise ListenerValidationError("receipt status must be pending or accepted")


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    action_key: str
    route_kind: RouteKind
    deliveries: tuple[DeliveryReceipt, ...]


@dataclass(frozen=True, slots=True)
class MailboxEnvelope:
    """One immutable, generation-bound delivery to the durable PM mailbox."""

    message_id: str
    parent_id: str
    sender: str
    recipient: str
    session_id: str
    message_type: str
    queue_id: str | None
    generation: int
    body_digest: str
    body: str
    creation_time: str
    delivery_status: str

    def __post_init__(self) -> None:
        _validate_digest(self.message_id, "message_id")
        _validate_digest(self.parent_id, "parent_id")
        _required_text(self.sender, "sender")
        if (
            not isinstance(self.recipient, str)
            or _RECIPIENT_PATTERN.fullmatch(self.recipient) is None
        ):
            raise ListenerValidationError("recipient is malformed")
        if self.recipient != _PM_RECIPIENT:
            raise ListenerValidationError(
                "mailbox recipient must be the durable project_manager role"
            )
        if (
            not isinstance(self.session_id, str)
            or _SESSION_ID_PATTERN.fullmatch(self.session_id) is None
        ):
            raise ListenerValidationError("mailbox session_id is malformed")
        if self.message_type not in _MAILBOX_MESSAGE_TYPES:
            raise ListenerValidationError("message_type is not supported")
        if self.queue_id is not None and (
            not isinstance(self.queue_id, str)
            or _QUEUE_ID_PATTERN.fullmatch(self.queue_id) is None
        ):
            raise ListenerValidationError("queue_id must be null or an exact Queue id")
        if (
            not isinstance(self.generation, int)
            or isinstance(self.generation, bool)
            or self.generation < 1
        ):
            raise ListenerValidationError("generation must be a positive integer")
        _validate_digest(self.body_digest, "body_digest")
        body = _required_text(self.body, "body")
        if _sha256_text(body) != self.body_digest:
            raise ListenerValidationError("body digest does not match mailbox body")
        _required_text(self.creation_time, "creation_time")
        if self.delivery_status not in {"pending", "accepted"}:
            raise ListenerValidationError("mailbox delivery_status is invalid")


class GoalReceiptSink(Protocol):
    def accept_project_goal_receipt(
        self, *, receipt_key: str, intent_key: str, goal_text: str
    ) -> str: ...


class PMMailboxSink(Protocol):
    def deliver_pm_message(
        self, *, receipt_key: str, intent_key: str, message: str
    ) -> str: ...


class EnvelopeMailboxSink(Protocol):
    def deliver_mailbox_envelope(self, envelope: MailboxEnvelope) -> str: ...


class PMMailboxIdentityResolver(Protocol):
    def resolve_pm_mailbox_identity(self) -> PMMailboxIdentity: ...


class NewCandidateSink(Protocol):
    def accept_new_candidate(
        self,
        *,
        receipt_key: str,
        intent_key: str,
        summary: str,
        source_route: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ListenerSinks:
    goal_receipts: GoalReceiptSink | None = None
    pm_mailbox: PMMailboxSink | EnvelopeMailboxSink | None = None
    new_candidates: NewCandidateSink | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS listener_intents (
    intent_key TEXT PRIMARY KEY,
    listener_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    checkpoint_cursor TEXT NOT NULL,
    user_text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    canonical_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS listener_checkpoints (
    listener_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    checkpoint_cursor TEXT NOT NULL,
    last_intent_key TEXT NOT NULL REFERENCES listener_intents(intent_key),
    updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS listener_actions (
    action_key TEXT PRIMARY KEY,
    intent_key TEXT NOT NULL REFERENCES listener_intents(intent_key),
    route_kind TEXT NOT NULL CHECK (
        route_kind IN ('goal_change', 'direct_pm', 'bounded_new')
    ),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    UNIQUE (intent_key, route_kind)
) STRICT;
CREATE TABLE IF NOT EXISTS listener_delivery_receipts (
    receipt_key TEXT PRIMARY KEY,
    action_key TEXT NOT NULL REFERENCES listener_actions(action_key),
    sink TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted')),
    recorded_at TEXT NOT NULL,
    acceptance_ref TEXT,
    UNIQUE (action_key, sink),
    UNIQUE (action_key, step)
) STRICT;
CREATE TABLE IF NOT EXISTS listener_journal_events (
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    exported INTEGER NOT NULL DEFAULT 0 CHECK (exported IN (0, 1))
) STRICT;
"""


def _initialize_listener_store(path: str | Path) -> sqlite3.Connection:
    """Open ``path`` and initialize the durable Listener schema."""

    database_path = Path(path)
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if str(database_path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    connection.executescript(_SCHEMA)
    return connection


def _insert_or_verify(
    connection: sqlite3.Connection,
    *,
    table: str,
    key_column: str,
    key: str,
    canonical_json: str,
    insert_sql: str,
    values: tuple[Any, ...],
) -> bool:
    cursor = connection.execute(insert_sql, values)
    if cursor.rowcount == 1:
        return True
    row = connection.execute(
        f"SELECT canonical_json FROM {table} WHERE {key_column} = ?", (key,)
    ).fetchone()
    if row is None or row["canonical_json"] != canonical_json:
        raise ListenerDigestCollision(
            f"{table}.{key_column} digest maps to different canonical content"
        )
    return False


def _record_intent(connection: sqlite3.Connection, intent: ListenerIntent) -> bool:
    canonical = intent.canonical_json
    return _insert_or_verify(
        connection,
        table="listener_intents",
        key_column="intent_key",
        key=intent.intent_key,
        canonical_json=canonical,
        insert_sql=(
            "INSERT OR IGNORE INTO listener_intents "
            "(intent_key, listener_id, conversation_id, checkpoint_cursor, user_text, "
            "received_at, canonical_json) VALUES (?, ?, ?, ?, ?, ?, ?)"
        ),
        values=(intent.intent_key, intent.listener_id, intent.conversation_id,
                intent.checkpoint_cursor, intent.user_text, intent.received_at, canonical),
    )


def _record_action(connection: sqlite3.Connection, action: _ListenerAction) -> bool:
    canonical = action.canonical_json
    return _insert_or_verify(
        connection,
        table="listener_actions",
        key_column="action_key",
        key=action.action_key,
        canonical_json=canonical,
        insert_sql=(
            "INSERT OR IGNORE INTO listener_actions "
            "(action_key, intent_key, route_kind, payload_json, created_at, canonical_json) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        ),
        values=(action.action_key, action.intent_key, action.route.kind.value,
                action.route.canonical_payload_json, action.created_at, canonical),
    )
def _receipt_key(action_key: str, sink: str) -> str:
    identity = _canonical_json({"action_key": action_key, "sink": sink})
    return _sha256_text(f"listener-delivery/v1\n{identity}")


def _event_line(body: Mapping[str, Any]) -> tuple[str, str]:
    canonical_body = _canonical_json(body)
    event_id = _sha256_text(f"listener-journal/v1\n{canonical_body}")
    line = _canonical_json({"event_id": event_id, **json.loads(canonical_body)})
    return event_id, line


def _queue_event(connection: sqlite3.Connection, body: Mapping[str, Any]) -> None:
    event_id, event_json = _event_line(body)
    cursor = connection.execute(
        "INSERT OR IGNORE INTO listener_journal_events (event_id, event_json) VALUES (?, ?)",
        (event_id, event_json),
    )
    if cursor.rowcount == 0:
        row = connection.execute(
            "SELECT event_json FROM listener_journal_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None or row["event_json"] != event_json:
            raise ListenerDigestCollision("journal event digest collision")


def _checkpoint_event(intent: ListenerIntent) -> Mapping[str, Any]:
    return {
        "checkpoint_cursor": intent.checkpoint_cursor,
        "conversation_id": intent.conversation_id,
        "event_type": "checkpoint",
        "intent_key": intent.intent_key,
        "listener_id": intent.listener_id,
        "received_at": intent.received_at,
        "version": _JOURNAL_VERSION,
    }


def _receipt_event(
    *, receipt_key: str, action_key: str, route_kind: str, sink: str,
    status: str, acceptance_ref: str | None = None,
) -> Mapping[str, Any]:
    event: dict[str, Any] = {
        "action_key": action_key, "event_type": "receipt_state",
        "receipt_key": receipt_key, "route_kind": route_kind,
        "sink": sink, "status": status, "version": _JOURNAL_VERSION,
    }
    if acceptance_ref is not None:
        event["acceptance_ref_sha256"] = _sha256_text(acceptance_ref)
    return event


def _parse_routes(
    declarations: Sequence[ListenerRoute | Mapping[str, Any]],
) -> tuple[ListenerRoute, ...]:
    if isinstance(declarations, (str, bytes)) or not isinstance(declarations, Sequence):
        raise ListenerValidationError("routes must be a non-empty sequence")
    routes: list[ListenerRoute] = []
    seen: set[RouteKind] = set()
    for declaration in declarations:
        route = declaration if isinstance(declaration, ListenerRoute) else ListenerRoute.from_mapping(declaration)
        if route.kind in seen:
            raise ListenerValidationError(f"duplicate route declaration: {route.kind.value}")
        seen.add(route.kind)
        routes.append(route)
    if not routes:
        raise ListenerValidationError("routes must not be empty")
    return tuple(routes)


class ListenerGateway:
    """Validate, persist, and deliver Listener routes through injected sinks."""

    def __init__(
        self, database_path: str | Path, journal_path: str | Path, *,
        sinks: ListenerSinks, pm_authority: PMMutationAuthority | None = None,
        pm_identity_resolver: PMMailboxIdentityResolver | None = None,
        expected_pm_identity: PMMailboxIdentity | None = None,
        allow_legacy_direct_pm: bool = False,
    ) -> None:
        if pm_identity_resolver is not None and expected_pm_identity is not None:
            raise ListenerValidationError(
                "provide pm_identity_resolver or expected_pm_identity, not both"
            )
        if not isinstance(allow_legacy_direct_pm, bool):
            raise ListenerValidationError("allow_legacy_direct_pm must be boolean")
        self._journal_path = Path(journal_path)
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._sinks = sinks
        self._pm_authority = pm_authority
        self._pm_identity_resolver = pm_identity_resolver
        self._expected_pm_identity = expected_pm_identity
        self._allow_legacy_direct_pm = allow_legacy_direct_pm
        self._connection = _initialize_listener_store(database_path)
        try:
            self._validate_state_and_journal()
            self._flush_journal()
        except BaseException:
            self._connection.close()
            raise

    def __enter__(self) -> "ListenerGateway":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def intake(
        self, intent: ListenerIntent,
        route_declarations: Sequence[ListenerRoute | Mapping[str, Any]],
    ) -> tuple[ActionReceipt, ...]:
        if not isinstance(intent, ListenerIntent):
            raise ListenerValidationError("intent must be a ListenerIntent")
        routes = _parse_routes(route_declarations)
        self._validate_delivery_capabilities({route.kind for route in routes})
        self._validate_route_sink_shapes(routes)
        actions = tuple(_ListenerAction(intent.intent_key, route, intent.received_at) for route in routes)

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._validate_state_and_journal()
            self._record_intent_and_checkpoint(intent)
            for action in actions:
                self._record_action_and_receipts(action)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        self._flush_journal()
        self._deliver_action_keys(tuple(action.action_key for action in actions))
        return self._action_receipts(tuple(action.action_key for action in actions))

    def resume_pending(self) -> tuple[ActionReceipt, ...]:
        self._validate_state_and_journal()
        rows = self._connection.execute(
            "SELECT DISTINCT action_key FROM listener_delivery_receipts "
            "WHERE status = 'pending' ORDER BY rowid"
        ).fetchall()
        keys = tuple(row["action_key"] for row in rows)
        kinds = {
            RouteKind(row["route_kind"])
            for row in self._connection.execute(
                "SELECT route_kind FROM listener_actions WHERE action_key IN "
                f"({','.join('?' for _ in keys)})", keys,
            )
        } if keys else set()
        self._validate_delivery_capabilities(kinds)
        self._deliver_action_keys(keys)
        return self._action_receipts(keys)

    def _record_intent_and_checkpoint(self, intent: ListenerIntent) -> None:
        cursor_owner = self._connection.execute(
            "SELECT intent_key FROM listener_intents WHERE listener_id = ? "
            "AND conversation_id = ? AND checkpoint_cursor = ?",
            (intent.listener_id, intent.conversation_id, intent.checkpoint_cursor),
        ).fetchone()
        if cursor_owner is not None and cursor_owner["intent_key"] != intent.intent_key:
            raise ListenerStateConflict("checkpoint cursor maps to conflicting intent content")
        inserted = _record_intent(self._connection, intent)
        current = self._connection.execute(
            "SELECT last_intent_key FROM listener_checkpoints WHERE listener_id = ?",
            (intent.listener_id,),
        ).fetchone()
        if current is None:
            if intent.previous_intent_key is not None:
                raise ListenerStateConflict("initial checkpoint cannot name a predecessor")
            self._connection.execute(
                "INSERT INTO listener_checkpoints "
                "(listener_id, conversation_id, checkpoint_cursor, last_intent_key, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (intent.listener_id, intent.conversation_id, intent.checkpoint_cursor,
                 intent.intent_key, intent.received_at),
            )
        elif inserted:
            if intent.previous_intent_key != current["last_intent_key"]:
                raise ListenerStateConflict("new checkpoint does not continue the accepted intent")
            self._connection.execute(
                "UPDATE listener_checkpoints SET conversation_id = ?, checkpoint_cursor = ?, "
                "last_intent_key = ?, updated_at = ? WHERE listener_id = ?",
                (intent.conversation_id, intent.checkpoint_cursor, intent.intent_key,
                 intent.received_at, intent.listener_id),
            )
        if inserted:
            _queue_event(self._connection, _checkpoint_event(intent))

    def _record_action_and_receipts(self, action: _ListenerAction) -> None:
        _record_action(self._connection, action)
        sinks = (
            ("project_goal_receipt", "goal_to_new")
            if action.route.kind is RouteKind.GOAL_CHANGE else
            ("pm_mailbox",) if action.route.kind is RouteKind.DIRECT_PM else
            ("bounded_new",)
        )
        for step, sink in enumerate(sinks):
            receipt_key = _receipt_key(action.action_key, sink)
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO listener_delivery_receipts "
                "(receipt_key, action_key, sink, step, status, recorded_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (receipt_key, action.action_key, sink, step, action.created_at),
            )
            if cursor.rowcount == 0:
                row = self._connection.execute(
                    "SELECT action_key, sink, step FROM listener_delivery_receipts WHERE receipt_key = ?",
                    (receipt_key,),
                ).fetchone()
                if row is None or (row["action_key"], row["sink"], row["step"]) != (action.action_key, sink, step):
                    raise ListenerDigestCollision("delivery receipt digest collision")
            _queue_event(
                self._connection,
                _receipt_event(receipt_key=receipt_key, action_key=action.action_key,
                               route_kind=action.route.kind.value, sink=sink, status="pending"),
            )

    def _validate_delivery_capabilities(self, kinds: set[RouteKind]) -> None:
        if kinds & {RouteKind.GOAL_CHANGE, RouteKind.BOUNDED_NEW} and self._pm_authority is None:
            raise PMAuthorityRequired("Goal/New delivery requires Python PM authority")
        if RouteKind.GOAL_CHANGE in kinds and (self._sinks.goal_receipts is None or self._sinks.new_candidates is None):
            raise ListenerValidationError("Goal route requires Goal and New sinks")
        if RouteKind.DIRECT_PM in kinds and self._sinks.pm_mailbox is None:
            raise ListenerValidationError("direct route requires a PM mailbox sink")
        if RouteKind.BOUNDED_NEW in kinds and self._sinks.new_candidates is None:
            raise ListenerValidationError("bounded-new route requires a New sink")

    def _validate_route_sink_shapes(self, routes: Sequence[ListenerRoute]) -> None:
        for route in routes:
            if route.kind is not RouteKind.DIRECT_PM:
                continue
            assert self._sinks.pm_mailbox is not None
            if "recipient" in route.payload:
                self._validate_current_pm_identity(route.payload)
                method = getattr(self._sinks.pm_mailbox, "deliver_mailbox_envelope", None)
                if not callable(method):
                    raise ListenerValidationError(
                        "generation-bound direct_pm routing requires an envelope sink"
                    )
            else:
                if not self._allow_legacy_direct_pm:
                    raise ListenerValidationError(
                        "legacy direct_pm routing is disabled; use an explicit PM envelope"
                    )
                method = getattr(self._sinks.pm_mailbox, "deliver_pm_message", None)
                if not callable(method):
                    raise ListenerValidationError(
                        "legacy direct_pm routing requires a legacy sink"
                    )

    def _resolve_pm_identity(self) -> PMMailboxIdentity:
        identity = self._expected_pm_identity
        if self._pm_identity_resolver is not None:
            identity = self._pm_identity_resolver.resolve_pm_mailbox_identity()
        if not isinstance(identity, PMMailboxIdentity):
            raise ListenerValidationError(
                "generation-bound direct_pm routing requires current PM identity"
            )
        return identity

    def _validate_current_pm_identity(self, payload: Mapping[str, Any]) -> PMMailboxIdentity:
        current = self._resolve_pm_identity()
        supplied = (
            payload.get("recipient"),
            payload.get("session_id"),
            payload.get("generation"),
        )
        expected = (current.recipient, current.session_id, current.generation)
        if supplied != expected:
            raise ListenerValidationError(
                "direct_pm recipient, session_id or generation is stale"
            )
        return current

    def _deliver_action_keys(self, action_keys: Sequence[str]) -> None:
        for action_key in action_keys:
            rows = self._connection.execute(
                "SELECT receipt_key FROM listener_delivery_receipts WHERE action_key = ? ORDER BY step",
                (action_key,),
            ).fetchall()
            for row in rows:
                self._deliver_receipt(row["receipt_key"])

    def _deliver_receipt(self, receipt_key: str) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._validate_state_and_journal()
            row = self._connection.execute(
                "SELECT r.*, a.intent_key, a.route_kind, a.payload_json, "
                "i.listener_id, i.received_at "
                "FROM listener_delivery_receipts r JOIN listener_actions a USING (action_key) "
                "JOIN listener_intents i USING (intent_key) "
                "WHERE r.receipt_key = ?", (receipt_key,),
            ).fetchone()
            if row is None:
                raise ListenerStateConflict("delivery receipt disappeared")
            if row["status"] == "accepted":
                self._connection.commit()
                return
            if row["step"] > 0:
                prior = self._connection.execute(
                    "SELECT status FROM listener_delivery_receipts WHERE action_key = ? AND step = ?",
                    (row["action_key"], row["step"] - 1),
                ).fetchone()
                if prior is None or prior["status"] != "accepted":
                    self._connection.commit()
                    return
            payload = json.loads(row["payload_json"])
            acceptance_ref = _required_text(self._call_sink(row, payload), "sink acceptance reference")
            updated = self._connection.execute(
                "UPDATE listener_delivery_receipts SET status = 'accepted', acceptance_ref = ? "
                "WHERE receipt_key = ? AND status = 'pending'",
                (acceptance_ref, receipt_key),
            )
            if updated.rowcount != 1:
                raise ListenerStateConflict("pending receipt changed during delivery")
            _queue_event(
                self._connection,
                _receipt_event(receipt_key=receipt_key, action_key=row["action_key"],
                               route_kind=row["route_kind"], sink=row["sink"],
                               status="accepted", acceptance_ref=acceptance_ref),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        self._flush_journal()

    def _call_sink(self, row: sqlite3.Row, payload: Mapping[str, Any]) -> str:
        receipt_key, intent_key, sink = row["receipt_key"], row["intent_key"], row["sink"]
        if sink == "project_goal_receipt":
            assert self._sinks.goal_receipts is not None
            return self._sinks.goal_receipts.accept_project_goal_receipt(
                receipt_key=receipt_key, intent_key=intent_key, goal_text=payload["goal_text"])
        if sink in {"goal_to_new", "bounded_new"}:
            assert self._sinks.new_candidates is not None
            field = "goal_text" if sink == "goal_to_new" else "summary"
            return self._sinks.new_candidates.accept_new_candidate(
                receipt_key=receipt_key, intent_key=intent_key, summary=payload[field],
                source_route=row["route_kind"])
        if sink == "pm_mailbox":
            assert self._sinks.pm_mailbox is not None
            envelope_delivery = getattr(
                self._sinks.pm_mailbox, "deliver_mailbox_envelope", None
            )
            if "recipient" in payload:
                if not callable(envelope_delivery):
                    raise ListenerValidationError(
                        "generation-bound direct_pm routing requires an envelope sink"
                    )
                body = payload["message"]
                current_identity = self._validate_current_pm_identity(payload)
                envelope = MailboxEnvelope(
                    message_id=receipt_key,
                    parent_id=intent_key,
                    sender=row["listener_id"],
                    recipient=payload.get("recipient", _PM_RECIPIENT),
                    session_id=current_identity.session_id,
                    message_type=payload.get("message_type", "direct_message"),
                    queue_id=payload.get("queue_id"),
                    generation=payload.get("generation", 1),
                    body_digest=_sha256_text(body),
                    body=body,
                    creation_time=row["received_at"],
                    delivery_status="pending",
                )
                return envelope_delivery(envelope)
            legacy_delivery = getattr(self._sinks.pm_mailbox, "deliver_pm_message", None)
            if not self._allow_legacy_direct_pm:
                raise ListenerValidationError(
                    "legacy direct_pm routing is disabled; use an explicit PM envelope"
                )
            if not callable(legacy_delivery):
                raise ListenerValidationError("legacy direct_pm routing requires a legacy sink")
            return legacy_delivery(
                receipt_key=receipt_key, intent_key=intent_key, message=payload["message"])
        raise ListenerStateConflict(f"unknown persisted sink: {sink}")

    def _action_receipts(self, action_keys: Sequence[str]) -> tuple[ActionReceipt, ...]:
        results: list[ActionReceipt] = []
        for key in action_keys:
            action = self._connection.execute(
                "SELECT route_kind FROM listener_actions WHERE action_key = ?", (key,)
            ).fetchone()
            if action is None:
                raise ListenerStateConflict("action disappeared")
            rows = self._connection.execute(
                "SELECT receipt_key, action_key, sink, status FROM listener_delivery_receipts "
                "WHERE action_key = ? ORDER BY step", (key,),
            ).fetchall()
            results.append(ActionReceipt(
                action_key=key, route_kind=RouteKind(action["route_kind"]),
                deliveries=tuple(DeliveryReceipt(row["receipt_key"], row["action_key"],
                                                  row["sink"], row["status"]) for row in rows),
            ))
        return tuple(results)

    def _read_journal(self) -> dict[str, str]:
        if not self._journal_path.exists():
            return {}
        events: dict[str, str] = {}
        with self._journal_path.open("r", encoding="utf-8") as handle:
            for number, raw in enumerate(handle, start=1):
                if not raw.endswith("\n"):
                    raise ListenerStateConflict(f"journal line {number} is incomplete")
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ListenerStateConflict(f"journal line {number} is malformed") from exc
                if not isinstance(value, dict) or "event_id" not in value:
                    raise ListenerStateConflict(f"journal line {number} is not an event object")
                event_id = _validate_digest(value["event_id"], "event_id")
                body = dict(value)
                del body["event_id"]
                expected, canonical = _event_line(body)
                if event_id != expected:
                    raise ListenerStateConflict(f"journal line {number} has an invalid digest")
                if event_id in events and events[event_id] != canonical:
                    raise ListenerStateConflict("journal contains a conflicting event")
                events[event_id] = canonical
        return events

    def _validate_state_and_journal(self) -> None:
        if self._connection.in_transaction:
            self._validate_state_and_journal_snapshot()
            return
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._validate_state_and_journal_snapshot()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def _validate_state_and_journal_snapshot(self) -> None:
        journal = self._read_journal()
        db_events = {
            row["event_id"]: (row["event_json"], row["exported"])
            for row in self._connection.execute(
                "SELECT event_id, event_json, exported FROM listener_journal_events")
        }
        for event_id, event_json in journal.items():
            persisted = db_events.get(event_id)
            if persisted is None or persisted[0] != event_json:
                raise ListenerStateConflict("journal event conflicts with SQLite state")
        for event_id, (event_json, exported) in db_events.items():
            value = json.loads(event_json)
            body = dict(value)
            stored_id = body.pop("event_id", None)
            expected, canonical = _event_line(body)
            if stored_id != event_id or expected != event_id or canonical != event_json:
                raise ListenerStateConflict("SQLite journal event has invalid canonical content")
            if exported and journal.get(event_id) != event_json:
                raise ListenerStateConflict("exported SQLite event is missing from JSONL")
        accepted = {
            row["receipt_key"] for row in self._connection.execute(
                "SELECT receipt_key FROM listener_delivery_receipts WHERE status = 'accepted'")
        }
        accepted_events = {
            value["receipt_key"] for event_json, _ in db_events.values()
            for value in (json.loads(event_json),)
            if value.get("event_type") == "receipt_state" and value.get("status") == "accepted"
        }
        if accepted != accepted_events:
            raise ListenerStateConflict("receipt state conflicts with durable journal state")
        checkpoint_events: dict[str, Mapping[str, Any]] = {}
        for event_json, _exported in db_events.values():
            value = json.loads(event_json)
            if value.get("event_type") != "checkpoint":
                continue
            intent_key = value.get("intent_key")
            if not isinstance(intent_key, str) or intent_key in checkpoint_events:
                raise ListenerStateConflict(
                    "each intent must have exactly one checkpoint event"
                )
            checkpoint_events[intent_key] = value
        intent_keys = {
            row["intent_key"]
            for row in self._connection.execute("SELECT intent_key FROM listener_intents")
        }
        if set(checkpoint_events) != intent_keys:
            raise ListenerStateConflict("intent lacks exactly one durable checkpoint event")
        receipt_events: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for event_json, _exported in db_events.values():
            value = json.loads(event_json)
            event_type = value.get("event_type")
            if event_type == "receipt_state":
                receipt_events.setdefault(
                    (value.get("receipt_key"), value.get("status")), []
                ).append(value)
            elif event_type != "checkpoint":
                raise ListenerStateConflict("journal contains an unknown event type")
        checkpoint_rows = self._connection.execute(
            "SELECT listener_id, conversation_id, checkpoint_cursor, last_intent_key, "
            "updated_at "
            "FROM listener_checkpoints"
        ).fetchall()
        intent_listener_ids = {
            row["listener_id"]
            for row in self._connection.execute(
                "SELECT DISTINCT listener_id FROM listener_intents"
            )
        }
        pointer_listener_ids = [row["listener_id"] for row in checkpoint_rows]
        if (
            len(pointer_listener_ids) != len(set(pointer_listener_ids))
            or set(pointer_listener_ids) != intent_listener_ids
        ):
            raise ListenerStateConflict(
                "listener checkpoint pointers are not a bijection with listener intents"
            )
        if len({row["last_intent_key"] for row in checkpoint_rows}) != len(
            checkpoint_rows
        ):
            raise ListenerStateConflict("listener checkpoint pointers are duplicated")
        for row in checkpoint_rows:
            event = checkpoint_events.get(row["last_intent_key"])
            if event is None or (
                event.get("listener_id"),
                event.get("conversation_id"),
                event.get("checkpoint_cursor"),
                event.get("received_at"),
            ) != (
                row["listener_id"],
                row["conversation_id"],
                row["checkpoint_cursor"],
                row["updated_at"],
            ):
                raise ListenerStateConflict("current checkpoint conflicts with durable journal")
        self._validate_canonical_rows(
            checkpoint_events, receipt_events, checkpoint_rows
        )

    def _validate_canonical_rows(
        self,
        checkpoint_events: Mapping[str, Mapping[str, Any]],
        receipt_events: Mapping[
            tuple[str, str], Sequence[Mapping[str, Any]]
        ],
        checkpoint_rows: Sequence[sqlite3.Row],
    ) -> None:
        if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ListenerStateConflict("listener store has a foreign-key violation")
        intents: dict[str, ListenerIntent] = {}
        for row in self._connection.execute("SELECT * FROM listener_intents"):
            try:
                canonical = json.loads(row["canonical_json"])
                if not isinstance(canonical, dict):
                    raise ListenerValidationError("intent canonical JSON is not an object")
                intent = ListenerIntent(
                    listener_id=row["listener_id"],
                    conversation_id=row["conversation_id"],
                    checkpoint_cursor=row["checkpoint_cursor"],
                    user_text=row["user_text"],
                    received_at=row["received_at"],
                    previous_intent_key=canonical.get("previous_intent_key"),
                )
            except (json.JSONDecodeError, ListenerValidationError) as error:
                raise ListenerStateConflict("persisted intent row is malformed") from error
            if row["canonical_json"] != intent.canonical_json:
                raise ListenerDigestCollision(
                    "intent columns differ from canonical content"
                )
            if intent.intent_key != row["intent_key"]:
                raise ListenerDigestCollision("persisted intent digest mismatch")
            event = checkpoint_events.get(intent.intent_key)
            if event is None:
                raise ListenerStateConflict("intent lacks a durable checkpoint event")
            event_body = dict(event)
            event_body.pop("event_id", None)
            if event_body != _checkpoint_event(intent):
                raise ListenerStateConflict(
                    "intent columns differ from canonical checkpoint event"
                )
            intents[intent.intent_key] = intent

        checkpoints = {row["listener_id"]: row for row in checkpoint_rows}
        intents_by_listener: dict[str, list[ListenerIntent]] = {}
        seen_cursors: set[tuple[str, str, str]] = set()
        for intent in intents.values():
            cursor_identity = (
                intent.listener_id,
                intent.conversation_id,
                intent.checkpoint_cursor,
            )
            if cursor_identity in seen_cursors:
                raise ListenerStateConflict(
                    "listener conversation repeats a checkpoint cursor"
                )
            seen_cursors.add(cursor_identity)
            intents_by_listener.setdefault(intent.listener_id, []).append(intent)
        for listener_id, listener_intents in intents_by_listener.items():
            by_key = {intent.intent_key: intent for intent in listener_intents}
            roots = [
                intent for intent in listener_intents
                if intent.previous_intent_key is None
            ]
            if len(roots) != 1:
                raise ListenerStateConflict(
                    "listener predecessor chain must have exactly one root"
                )
            children: dict[str, list[str]] = {}
            for intent in listener_intents:
                predecessor_key = intent.previous_intent_key
                if predecessor_key is None:
                    continue
                predecessor = intents.get(predecessor_key)
                if predecessor is None or predecessor.listener_id != listener_id:
                    raise ListenerStateConflict(
                        "listener predecessor chain is missing or crosses listeners"
                    )
                children.setdefault(predecessor_key, []).append(intent.intent_key)
            if any(len(values) != 1 for values in children.values()):
                raise ListenerStateConflict("listener predecessor chain branches")
            visited: set[str] = set()
            current = roots[0].intent_key
            while True:
                if current in visited:
                    raise ListenerStateConflict("listener predecessor chain cycles")
                visited.add(current)
                successors = children.get(current, [])
                if not successors:
                    break
                current = successors[0]
            if visited != set(by_key):
                raise ListenerStateConflict("listener predecessor chain is disconnected")
            checkpoint = checkpoints.get(listener_id)
            if checkpoint is None or checkpoint["last_intent_key"] != current:
                raise ListenerStateConflict(
                    "listener checkpoint pointer is not the unique chain head"
                )

        actions: dict[str, _ListenerAction] = {}
        routes: dict[str, ListenerRoute] = {}
        for row in self._connection.execute("SELECT * FROM listener_actions"):
            intent = intents.get(row["intent_key"])
            if intent is None:
                raise ListenerStateConflict("action references an unknown intent")
            try:
                payload = json.loads(row["payload_json"])
                route = ListenerRoute(RouteKind(row["route_kind"]), payload)
                action = _ListenerAction(row["intent_key"], route, row["created_at"])
                canonical = json.loads(row["canonical_json"])
            except (json.JSONDecodeError, ValueError, ListenerValidationError) as error:
                raise ListenerStateConflict("persisted action row is malformed") from error
            if row["payload_json"] != route.canonical_payload_json:
                raise ListenerStateConflict("action payload_json is not canonical")
            if row["canonical_json"] != action.canonical_json:
                raise ListenerStateConflict("action columns differ from canonical content")
            if action.action_key != row["action_key"]:
                raise ListenerDigestCollision("persisted action digest mismatch")
            if action.created_at != intent.received_at:
                raise ListenerStateConflict("action creation time differs from its intent")
            actions[action.action_key] = action
            routes[action.action_key] = route

        receipts_by_action: dict[str, list[sqlite3.Row]] = {}
        for row in self._connection.execute(
            "SELECT * FROM listener_delivery_receipts ORDER BY action_key, step"
        ):
            action = actions.get(row["action_key"])
            if action is None:
                raise ListenerStateConflict("receipt references an unknown action")
            if _receipt_key(row["action_key"], row["sink"]) != row["receipt_key"]:
                raise ListenerDigestCollision("persisted receipt digest mismatch")
            if row["recorded_at"] != action.created_at:
                raise ListenerStateConflict("receipt time differs from its action")
            if (row["status"] == "accepted") != bool(row["acceptance_ref"]):
                raise ListenerStateConflict("receipt acceptance state is inconsistent")
            expected_events = [
                _receipt_event(
                    receipt_key=row["receipt_key"],
                    action_key=row["action_key"],
                    route_kind=action.route.kind.value,
                    sink=row["sink"],
                    status="pending",
                )
            ]
            if row["status"] == "accepted":
                expected_events.append(
                    _receipt_event(
                        receipt_key=row["receipt_key"],
                        action_key=row["action_key"],
                        route_kind=action.route.kind.value,
                        sink=row["sink"],
                        status="accepted",
                        acceptance_ref=row["acceptance_ref"],
                    )
                )
            for expected in expected_events:
                events = receipt_events.get(
                    (row["receipt_key"], expected["status"]), ()
                )
                bodies = []
                for event in events:
                    body = dict(event)
                    body.pop("event_id", None)
                    bodies.append(body)
                if bodies != [expected]:
                    raise ListenerStateConflict(
                        "receipt row differs from its canonical journal event"
                    )
            receipts_by_action.setdefault(row["action_key"], []).append(row)
        known_receipt_keys = {
            row["receipt_key"]
            for rows in receipts_by_action.values()
            for row in rows
        }
        if any(key not in known_receipt_keys for key, _status in receipt_events):
            raise ListenerStateConflict("journal references an unknown receipt")
        for action_key, route in routes.items():
            expected_sinks = (
                ("project_goal_receipt", "goal_to_new")
                if route.kind is RouteKind.GOAL_CHANGE
                else ("pm_mailbox",)
                if route.kind is RouteKind.DIRECT_PM
                else ("bounded_new",)
            )
            actual = receipts_by_action.get(action_key, [])
            if [(row["sink"], row["step"]) for row in actual] != [
                (sink, step) for step, sink in enumerate(expected_sinks)
            ]:
                raise ListenerStateConflict("action receipts differ from canonical route")

    def _flush_journal(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            journal = self._read_journal()
            rows = self._connection.execute(
                "SELECT event_id, event_json FROM listener_journal_events "
                "WHERE exported = 0 ORDER BY rowid").fetchall()
            for row in rows:
                existing = journal.get(row["event_id"])
                if existing is not None and existing != row["event_json"]:
                    raise ListenerStateConflict("journal event conflicts during export")
                if existing is None:
                    with self._journal_path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(row["event_json"] + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    journal[row["event_id"]] = row["event_json"]
                self._connection.execute(
                    "UPDATE listener_journal_events SET exported = 1 WHERE event_id = ?",
                    (row["event_id"],),
                )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
