"""SQLite machine truth and deterministic workflow-state projection."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from stock_data.orchestration.workflow_control.contracts import (
    STATE_SCHEMA_VERSION,
    EventKind,
    Priority,
    TaskSnapshot,
    TaskState,
    WorkflowEvent,
    parse_utc,
    utc_text,
)
from stock_data.orchestration.workflow_control.events import (
    SanitizedJsonlLedger,
    canonical_event_json,
)


class WorkflowStateError(RuntimeError):
    pass


class WorkflowEventConflictError(WorkflowStateError):
    pass


_MIGRATION_V1 = (
    """
    CREATE TABLE IF NOT EXISTS schema_metadata (
        name TEXT PRIMARY KEY,
        version INTEGER NOT NULL CHECK (version >= 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        task_id TEXT,
        payload_json TEXT NOT NULL,
        ledger_written INTEGER NOT NULL DEFAULT 0 CHECK (ledger_written IN (0, 1))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS events_occurred_at_idx
    ON events (occurred_at, event_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        priority TEXT,
        domain TEXT,
        updated_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        FOREIGN KEY (last_event_id) REFERENCES events (event_id)
    )
    """,
)

_V1_TABLE_COLUMNS = {
    "schema_metadata": (
        ("name", "TEXT", 0, None, 1),
        ("version", "INTEGER", 1, None, 0),
    ),
    "events": (
        ("event_id", "TEXT", 0, None, 1),
        ("occurred_at", "TEXT", 1, None, 0),
        ("kind", "TEXT", 1, None, 0),
        ("source", "TEXT", 1, None, 0),
        ("task_id", "TEXT", 0, None, 0),
        ("payload_json", "TEXT", 1, None, 0),
        ("ledger_written", "INTEGER", 1, "0", 0),
    ),
    "tasks": (
        ("task_id", "TEXT", 0, None, 1),
        ("state", "TEXT", 1, None, 0),
        ("priority", "TEXT", 0, None, 0),
        ("domain", "TEXT", 0, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
        ("last_event_id", "TEXT", 1, None, 0),
    ),
}
_V1_USER_INDEXES = {
    "schema_metadata": {},
    "events": {"events_occurred_at_idx": ("occurred_at", "event_id")},
    "tasks": {},
}
_V1_FOREIGN_KEYS = {
    "schema_metadata": (),
    "events": (),
    "tasks": (
        ("events", "last_event_id", "event_id", "NO ACTION", "NO ACTION", "NONE"),
    ),
}


def _compact_schema_sql(value: str) -> str:
    compact = "".join(value.lower().split()).translate(
        str.maketrans("", "", '"`[]')
    )
    return compact.replace("ifnotexists", "").rstrip(";")


_V1_TABLE_SQL = {
    "schema_metadata": _compact_schema_sql(_MIGRATION_V1[0]),
    "events": _compact_schema_sql(_MIGRATION_V1[1]),
    "tasks": _compact_schema_sql(_MIGRATION_V1[3]),
}
_V1_INDEX_SQL = {
    "events_occurred_at_idx": _compact_schema_sql(_MIGRATION_V1[2]),
}


class WorkflowStateStore:
    """Persist workflow facts transactionally and project current task state."""

    def __init__(
        self,
        database_path: Path,
        event_log_path: Path,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.database_path = Path(database_path)
        self.ledger = SanitizedJsonlLedger(Path(event_log_path))
        self.timeout_seconds = timeout_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return connection

    def migrate(self) -> None:
        """Apply the complete schema version inside one immediate transaction."""

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if current > STATE_SCHEMA_VERSION:
                    raise WorkflowStateError(
                        f"database schema {current} is newer than supported "
                        f"schema {STATE_SCHEMA_VERSION}"
                    )
                if current < 1:
                    for statement in _MIGRATION_V1:
                        connection.execute(statement)
                    self._validate_v1_schema_shape(connection)
                    connection.execute(
                        "INSERT OR REPLACE INTO schema_metadata(name, version) VALUES (?, ?)",
                        ("workflow-control-state", 1),
                    )
                    connection.execute("PRAGMA user_version = 1")
                self._validate_v1_schema_shape(connection)
                persisted_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if persisted_version != STATE_SCHEMA_VERSION:
                    raise WorkflowStateError("database schema version is inconsistent")
                metadata = connection.execute(
                    "SELECT version FROM schema_metadata WHERE name = ?",
                    ("workflow-control-state",),
                ).fetchone()
                if metadata is None or int(metadata["version"]) != STATE_SCHEMA_VERSION:
                    raise WorkflowStateError("database schema metadata is inconsistent")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _validate_v1_schema_shape(connection: sqlite3.Connection) -> None:
        """Reject any owned v1 object that is only superficially compatible."""

        def incompatible(detail: str) -> None:
            raise WorkflowStateError(
                f"database schema shape is incompatible with v1: {detail}"
            )

        for table_name, expected_columns in _V1_TABLE_COLUMNS.items():
            table = connection.execute(
                "SELECT type, sql FROM sqlite_master WHERE name = ?",
                (table_name,),
            ).fetchone()
            if table is None or table["type"] != "table" or not table["sql"]:
                incompatible(f"missing table {table_name}")

            columns = tuple(
                (
                    str(row["name"]),
                    str(row["type"]).upper(),
                    int(row["notnull"]),
                    None if row["dflt_value"] is None else str(row["dflt_value"]),
                    int(row["pk"]),
                )
                for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            )
            if columns != expected_columns:
                incompatible(f"unexpected columns or constraints on {table_name}")

            compact_sql = _compact_schema_sql(str(table["sql"]))
            if compact_sql != _V1_TABLE_SQL[table_name]:
                incompatible(f"unexpected definition for {table_name}")

            index_rows = connection.execute(
                f'PRAGMA index_list("{table_name}")'
            ).fetchall()
            user_indexes = {
                str(row["name"]): row
                for row in index_rows
                if str(row["origin"]) == "c"
            }
            expected_indexes = _V1_USER_INDEXES[table_name]
            if set(user_indexes) != set(expected_indexes):
                incompatible(f"unexpected indexes on {table_name}")
            for index_name, expected_index_columns in expected_indexes.items():
                index = user_indexes[index_name]
                if int(index["unique"]) != 0 or int(index["partial"]) != 0:
                    incompatible(f"invalid index {index_name}")
                index_schema = connection.execute(
                    "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
                    (index_name,),
                ).fetchone()
                if (
                    index_schema is None
                    or index_schema["type"] != "index"
                    or index_schema["tbl_name"] != table_name
                    or not index_schema["sql"]
                    or _compact_schema_sql(str(index_schema["sql"]))
                    != _V1_INDEX_SQL[index_name]
                ):
                    incompatible(f"invalid definition for {index_name}")
                index_columns = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        f'PRAGMA index_info("{index_name}")'
                    )
                )
                if index_columns != expected_index_columns:
                    incompatible(f"invalid index columns for {index_name}")

            foreign_keys = tuple(
                (
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_update"]),
                    str(row["on_delete"]),
                    str(row["match"]),
                )
                for row in connection.execute(
                    f'PRAGMA foreign_key_list("{table_name}")'
                )
            )
            if foreign_keys != _V1_FOREIGN_KEYS[table_name]:
                incompatible(f"unexpected foreign keys on {table_name}")

        owned_triggers = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'trigger'
              AND tbl_name IN ('schema_metadata', 'events', 'tasks')
            """
        ).fetchall()
        if owned_triggers:
            incompatible("unexpected trigger on an owned table")

    def record(self, event: WorkflowEvent) -> bool:
        """Record one fact and ledger line atomically from the caller's view.

        Returns ``True`` for a new SQLite fact and ``False`` for an identical
        replay.  A prior crash after the append but before SQLite commit is
        reconciled by the ledger's event-id check without adding another line.
        """

        payload_json = canonical_event_json(event)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT payload_json FROM events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                inserted = existing is None
                if existing is not None and existing["payload_json"] != payload_json:
                    raise WorkflowEventConflictError(
                        f"event_id {event.event_id} already has different machine truth"
                    )
                if inserted:
                    connection.execute(
                        """
                        INSERT INTO events(
                            event_id, occurred_at, kind, source, task_id,
                            payload_json, ledger_written
                        ) VALUES (?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            event.event_id,
                            utc_text(event.occurred_at),
                            event.kind.value,
                            event.source.value,
                            event.task_id,
                            payload_json,
                        ),
                    )
                    self._apply_projection(connection, event)
                self.ledger.append(event)
                connection.execute(
                    "UPDATE events SET ledger_written = 1 WHERE event_id = ?",
                    (event.event_id,),
                )
                connection.commit()
                return inserted
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _apply_projection(connection: sqlite3.Connection, event: WorkflowEvent) -> None:
        if event.kind is not EventKind.TASK_TRANSITION:
            return
        assert event.task_id is not None and event.to_state is not None
        current = connection.execute(
            """
            SELECT priority, domain, updated_at, last_event_id
            FROM tasks WHERE task_id = ?
            """,
            (event.task_id,),
        ).fetchone()
        if current is not None:
            current_key = (current["updated_at"], current["last_event_id"])
            if event.sort_key <= current_key:
                return
        priority = event.priority.value if event.priority else None
        domain = event.domain
        if current is not None:
            priority = priority or current["priority"]
            domain = domain or current["domain"]
        connection.execute(
            """
            INSERT INTO tasks(task_id, state, priority, domain, updated_at, last_event_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                state = excluded.state,
                priority = excluded.priority,
                domain = excluded.domain,
                updated_at = excluded.updated_at,
                last_event_id = excluded.last_event_id
            """,
            (
                event.task_id,
                event.to_state.value,
                priority,
                domain,
                utc_text(event.occurred_at),
                event.event_id,
            ),
        )

    def replay(self, events: Iterable[WorkflowEvent]) -> int:
        inserted = 0
        for event in sorted(events, key=lambda item: item.sort_key):
            inserted += int(self.record(event))
        return inserted

    def replay_jsonl(self) -> int:
        return self.replay(tuple(self.ledger.iter_events()))

    def event_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def events(self, *, before: datetime | None = None) -> tuple[WorkflowEvent, ...]:
        query = "SELECT payload_json FROM events"
        parameters: tuple[str, ...] = ()
        if before is not None:
            query += " WHERE occurred_at < ?"
            parameters = (utc_text(before),)
        query += " ORDER BY occurred_at, event_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(WorkflowEvent.from_dict(json.loads(row["payload_json"])) for row in rows)

    def task_snapshots(self) -> tuple[TaskSnapshot, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT task_id, state, priority, domain, updated_at, last_event_id
                FROM tasks ORDER BY task_id
                """
            ).fetchall()
        return tuple(
            TaskSnapshot(
                task_id=row["task_id"],
                state=TaskState(row["state"]),
                priority=Priority(row["priority"]) if row["priority"] else None,
                domain=row["domain"],
                updated_at=parse_utc(row["updated_at"]),
                last_event_id=row["last_event_id"],
            )
            for row in rows
        )
