"""Durable, identifier-only registry for reusable workflow roles."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from stock_data.orchestration.workflow_control.contracts import parse_utc, utc_text


REGISTRY_SCHEMA_VERSION = 3
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\\-]{0,254}$")
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")


class RoleRegistryError(ValueError):
    """Base error for invalid or conflicting registry operations."""


class RoleClaimConflict(RoleRegistryError):
    """Raised when a durable role already has a live owner."""


class StaleRoleGeneration(RoleRegistryError):
    """Raised when a caller tries to mutate an obsolete registry generation."""


class RoleRegistrySchemaError(RoleRegistryError):
    """Raised when durable storage is not the exact identifier-only schema."""


class RoleKind(StrEnum):
    PROJECT_MANAGER = "project_manager"
    DOMAIN_LEAD = "domain_lead"
    WORKER = "worker"
    REVIEWER = "reviewer"


class RoleState(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    STOPPED = "stopped"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class RoleIdentity:
    role_key: str
    role_kind: RoleKind
    codex_session_id: str
    orca_run_id: str
    worktree_id: str
    terminal_handle: str | None
    runtime_id: str
    active_task_id: str | None = None
    active_dispatch_id: str | None = None
    parent_role_key: str | None = None

    def __post_init__(self) -> None:
        _require_match(self.role_key, _ROLE_KEY, "role key")
        if not isinstance(self.role_kind, RoleKind):
            raise RoleRegistryError("role kind must use RoleKind")
        for name in ("codex_session_id", "orca_run_id", "worktree_id", "runtime_id"):
            _require_match(getattr(self, name), _IDENTIFIER, name.replace("_", " "))
        if self.terminal_handle is not None:
            _require_match(self.terminal_handle, _IDENTIFIER, "terminal handle")
        if self.active_task_id is not None:
            _require_match(self.active_task_id, _TASK_ID, "active task id")
        if self.active_dispatch_id is not None:
            _require_match(self.active_dispatch_id, _IDENTIFIER, "active dispatch id")
        if self.parent_role_key is not None:
            _require_match(self.parent_role_key, _ROLE_KEY, "parent role key")
            if self.parent_role_key == self.role_key:
                raise RoleRegistryError("a role cannot parent itself")
        if (self.active_task_id is None) != (self.active_dispatch_id is None):
            raise RoleRegistryError("active task and dispatch identifiers must be paired")


@dataclass(frozen=True, slots=True)
class RoleRecord:
    identity: RoleIdentity
    state: RoleState
    generation: int
    heartbeat_at: datetime
    lease_until: datetime
    retry_of_dispatch_id: str | None = None
    retry_attempt: int = 0
    retry_provenance: str | None = None
    last_message_id: str | None = None


def _require_match(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise RoleRegistryError(f"{label} is not a bounded identifier")
    return value


class RoleRegistry:
    """SQLite-backed registry with generation-fenced, atomic role claims.

    The schema deliberately contains identifiers, enums, timestamps, and
    counters only. It has no general metadata, message, prompt, transcript,
    credential, or account-identifier field.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, REGISTRY_SCHEMA_VERSION):
                connection.rollback()
                raise RoleRegistryError("unsupported role registry schema")
            if version == 1:
                self._migrate_v1(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS role_registry (
                    role_key TEXT PRIMARY KEY,
                    role_kind TEXT NOT NULL CHECK(role_kind IN ('project_manager', 'domain_lead', 'worker', 'reviewer')),
                    codex_session_id TEXT NOT NULL,
                    orca_run_id TEXT NOT NULL,
                    worktree_id TEXT NOT NULL,
                    terminal_handle TEXT,
                    runtime_id TEXT NOT NULL,
                    active_task_id TEXT,
                    active_dispatch_id TEXT,
                    state TEXT NOT NULL CHECK(state IN ('active', 'idle', 'stopped', 'recovery_required')),
                    generation INTEGER NOT NULL CHECK(generation >= 1),
                    heartbeat_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    retry_of_dispatch_id TEXT,
                    retry_attempt INTEGER NOT NULL DEFAULT 0 CHECK(retry_attempt >= 0),
                    retry_provenance TEXT,
                    parent_role_key TEXT,
                    last_message_id TEXT,
                    CHECK((active_task_id IS NULL) = (active_dispatch_id IS NULL))
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS role_dispatch_history (
                    role_key TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL CHECK(attempt >= 0),
                    provenance TEXT,
                    PRIMARY KEY(role_key, dispatch_id),
                    FOREIGN KEY(role_key) REFERENCES role_registry(role_key)
                )
                """
            )
            self._require_unique_sessions(connection)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_role_registry_codex_session "
                "ON role_registry(codex_session_id)"
            )
            self._validate_schema(connection)
            connection.execute(f"PRAGMA user_version = {REGISTRY_SCHEMA_VERSION}")
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _migrate_v1(connection: sqlite3.Connection) -> None:
        """Upgrade the exact identifier-only v1 registry without rebinding rows."""

        RoleRegistry._validate_v1_schema(connection)
        project_managers = connection.execute(
            "SELECT role_key FROM role_registry WHERE role_kind = ? ORDER BY role_key",
            (RoleKind.PROJECT_MANAGER.value,),
        ).fetchall()
        if len(project_managers) != 1:
            raise RoleRegistrySchemaError(
                "v1 migration requires exactly one durable project manager"
            )
        RoleRegistry._require_unique_sessions(connection)
        connection.execute("ALTER TABLE role_dispatch_history RENAME TO role_dispatch_history_v1")
        connection.execute("ALTER TABLE role_registry RENAME TO role_registry_v1")
        connection.execute(
            """
            CREATE TABLE role_registry (
                role_key TEXT PRIMARY KEY,
                role_kind TEXT NOT NULL CHECK(role_kind IN ('project_manager', 'domain_lead', 'worker', 'reviewer')),
                codex_session_id TEXT NOT NULL,
                orca_run_id TEXT NOT NULL,
                worktree_id TEXT NOT NULL,
                terminal_handle TEXT,
                runtime_id TEXT NOT NULL,
                active_task_id TEXT,
                active_dispatch_id TEXT,
                state TEXT NOT NULL CHECK(state IN ('active', 'idle', 'stopped', 'recovery_required')),
                generation INTEGER NOT NULL CHECK(generation >= 1),
                heartbeat_at TEXT NOT NULL,
                lease_until TEXT NOT NULL,
                retry_of_dispatch_id TEXT,
                retry_attempt INTEGER NOT NULL DEFAULT 0 CHECK(retry_attempt >= 0),
                retry_provenance TEXT,
                parent_role_key TEXT,
                last_message_id TEXT,
                CHECK((active_task_id IS NULL) = (active_dispatch_id IS NULL))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO role_registry(
                role_key, role_kind, codex_session_id, orca_run_id, worktree_id,
                terminal_handle, runtime_id, active_task_id, active_dispatch_id,
                state, generation, heartbeat_at, lease_until, retry_of_dispatch_id,
                retry_attempt, retry_provenance, parent_role_key, last_message_id
            )
            SELECT role_key, role_kind, codex_session_id, orca_run_id, worktree_id,
                   terminal_handle, runtime_id, active_task_id, active_dispatch_id,
                   state, generation, heartbeat_at, lease_until, retry_of_dispatch_id,
                   retry_attempt, retry_provenance, NULL, NULL
            FROM role_registry_v1
            """
        )
        connection.execute(
            "UPDATE role_registry SET parent_role_key = ? WHERE role_kind = ?",
            (
                str(project_managers[0]["role_key"]),
                RoleKind.DOMAIN_LEAD.value,
            ),
        )
        connection.execute(
            """
            CREATE TABLE role_dispatch_history (
                role_key TEXT NOT NULL,
                task_id TEXT NOT NULL,
                dispatch_id TEXT NOT NULL,
                attempt INTEGER NOT NULL CHECK(attempt >= 0),
                provenance TEXT,
                PRIMARY KEY(role_key, dispatch_id),
                FOREIGN KEY(role_key) REFERENCES role_registry(role_key)
            )
            """
        )
        connection.execute(
            "INSERT INTO role_dispatch_history SELECT * FROM role_dispatch_history_v1"
        )
        connection.execute("DROP TABLE role_dispatch_history_v1")
        connection.execute("DROP TABLE role_registry_v1")

    @staticmethod
    def _require_unique_sessions(connection: sqlite3.Connection) -> None:
        try:
            duplicate = connection.execute(
                "SELECT codex_session_id FROM role_registry GROUP BY codex_session_id "
                "HAVING COUNT(*) > 1 ORDER BY codex_session_id LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError as error:
            raise RoleRegistrySchemaError(
                "role registry schema shape is incompatible"
            ) from error
        if duplicate is not None:
            raise RoleRegistrySchemaError(
                "Codex session id must be unique across durable roles"
            )

    @staticmethod
    def _validate_v1_schema(connection: sqlite3.Connection) -> None:
        expected_columns = (
            "role_key", "role_kind", "codex_session_id", "orca_run_id",
            "worktree_id", "terminal_handle", "runtime_id", "active_task_id",
            "active_dispatch_id", "state", "generation", "heartbeat_at",
            "lease_until", "retry_of_dispatch_id", "retry_attempt", "retry_provenance",
        )
        actual = tuple(
            str(row[1]) for row in connection.execute("PRAGMA table_info(role_registry)")
        )
        history = tuple(
            str(row[1]) for row in connection.execute("PRAGMA table_info(role_dispatch_history)")
        )
        if actual != expected_columns or history != (
            "role_key", "task_id", "dispatch_id", "attempt", "provenance",
        ):
            raise RoleRegistrySchemaError("role registry v1 schema shape is incompatible")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        expected = (
            ("role_key", "TEXT", 0, None, 1),
            ("role_kind", "TEXT", 1, None, 0),
            ("codex_session_id", "TEXT", 1, None, 0),
            ("orca_run_id", "TEXT", 1, None, 0),
            ("worktree_id", "TEXT", 1, None, 0),
            ("terminal_handle", "TEXT", 0, None, 0),
            ("runtime_id", "TEXT", 1, None, 0),
            ("active_task_id", "TEXT", 0, None, 0),
            ("active_dispatch_id", "TEXT", 0, None, 0),
            ("state", "TEXT", 1, None, 0),
            ("generation", "INTEGER", 1, None, 0),
            ("heartbeat_at", "TEXT", 1, None, 0),
            ("lease_until", "TEXT", 1, None, 0),
            ("retry_of_dispatch_id", "TEXT", 0, None, 0),
            ("retry_attempt", "INTEGER", 1, "0", 0),
            ("retry_provenance", "TEXT", 0, None, 0),
            ("parent_role_key", "TEXT", 0, None, 0),
            ("last_message_id", "TEXT", 0, None, 0),
        )
        actual = tuple(
            (row[1], row[2], row[3], row[4], row[5])
            for row in connection.execute("PRAGMA table_info(role_registry)")
        )
        schema_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'role_registry'"
        ).fetchone()
        normalized_sql = "" if schema_row is None else "".join(schema_row[0].lower().split())
        required_checks = (
            "check(role_kindin('project_manager','domain_lead','worker','reviewer'))",
            "check(statein('active','idle','stopped','recovery_required'))",
            "check(generation>=1)",
            "check(retry_attempt>=0)",
            "check((active_task_idisnull)=(active_dispatch_idisnull))",
        )
        if actual != expected or any(check not in normalized_sql for check in required_checks):
            connection.rollback()
            raise RoleRegistrySchemaError("role registry schema shape is incompatible")
        history_expected = (
            ("role_key", "TEXT", 1, None, 1),
            ("task_id", "TEXT", 1, None, 0),
            ("dispatch_id", "TEXT", 1, None, 2),
            ("attempt", "INTEGER", 1, None, 0),
            ("provenance", "TEXT", 0, None, 0),
        )
        history_actual = tuple(
            (row[1], row[2], row[3], row[4], row[5])
            for row in connection.execute("PRAGMA table_info(role_dispatch_history)")
        )
        history_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'role_dispatch_history'"
        ).fetchone()
        history_sql = "" if history_row is None else "".join(history_row[0].lower().split())
        history_checks = (
            "primarykey(role_key,dispatch_id)",
            "foreignkey(role_key)referencesrole_registry(role_key)",
            "check(attempt>=0)",
        )
        if history_actual != history_expected or any(
            check not in history_sql for check in history_checks
        ):
            connection.rollback()
            raise RoleRegistrySchemaError("role dispatch history schema shape is incompatible")
        indexes = {
            str(row[1])
            for row in connection.execute("PRAGMA index_list(role_registry)")
            if int(row[2]) == 1
        }
        if "uq_role_registry_codex_session" not in indexes:
            connection.rollback()
            raise RoleRegistrySchemaError("role registry session uniqueness is missing")

    def claim(
        self,
        identity: RoleIdentity,
        *,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        heartbeat_text, lease_text = _validated_window(observed_at, lease_until)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if identity.parent_role_key is not None:
                parent = connection.execute(
                    "SELECT role_kind FROM role_registry WHERE role_key = ?",
                    (identity.parent_role_key,),
                ).fetchone()
                if parent is None:
                    connection.rollback()
                    raise RoleRegistryError("parent role is not registered")
                expected_parent = (
                    RoleKind.PROJECT_MANAGER.value
                    if identity.role_kind is RoleKind.DOMAIN_LEAD
                    else RoleKind.DOMAIN_LEAD.value
                    if identity.role_kind in {RoleKind.WORKER, RoleKind.REVIEWER}
                    else None
                )
                if expected_parent is None or parent["role_kind"] != expected_parent:
                    connection.rollback()
                    raise RoleRegistryError("role parent kind violates the PM hierarchy")
            elif identity.role_kind is RoleKind.PROJECT_MANAGER:
                other_pm = connection.execute(
                    "SELECT role_key FROM role_registry WHERE role_kind = ? AND role_key != ?",
                    (RoleKind.PROJECT_MANAGER.value, identity.role_key),
                ).fetchone()
                if other_pm is not None:
                    connection.rollback()
                    raise RoleClaimConflict("only one durable project manager may be registered")
            row = connection.execute(
                "SELECT * FROM role_registry WHERE role_key = ?", (identity.role_key,)
            ).fetchone()
            if row is not None:
                existing = _record_from_row(row)
                if existing.identity == identity and existing.state is RoleState.ACTIVE:
                    connection.commit()
                    return existing
                connection.rollback()
                raise RoleClaimConflict("role key is already registered")
            try:
                connection.execute(
                    """
                    INSERT INTO role_registry (
                        role_key, role_kind, codex_session_id, orca_run_id, worktree_id,
                        terminal_handle, runtime_id, active_task_id, active_dispatch_id,
                        state, generation, heartbeat_at, lease_until, retry_attempt,
                        parent_role_key, last_message_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, NULL)
                    """,
                    (
                        identity.role_key, identity.role_kind.value,
                        identity.codex_session_id, identity.orca_run_id,
                        identity.worktree_id, identity.terminal_handle,
                        identity.runtime_id, identity.active_task_id,
                        identity.active_dispatch_id, RoleState.ACTIVE.value,
                        heartbeat_text, lease_text, identity.parent_role_key,
                    ),
                )
            except sqlite3.IntegrityError as error:
                connection.rollback()
                if "codex_session_id" in str(error):
                    raise RoleClaimConflict(
                        "Codex session is already assigned to another role"
                    ) from error
                raise
            if identity.active_task_id is not None and identity.active_dispatch_id is not None:
                connection.execute(
                    """
                    INSERT INTO role_dispatch_history (
                        role_key, task_id, dispatch_id, attempt, provenance
                    ) VALUES (?, ?, ?, 0, NULL)
                    """,
                    (
                        identity.role_key,
                        identity.active_task_id,
                        identity.active_dispatch_id,
                    ),
                )
            connection.commit()
        return self.get(identity.role_key)

    def get(self, role_key: str) -> RoleRecord:
        _require_match(role_key, _ROLE_KEY, "role key")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM role_registry WHERE role_key = ?", (role_key,)
            ).fetchone()
        if row is None:
            raise RoleRegistryError("role key is not registered")
        return _record_from_row(row)

    @contextmanager
    def generation_guard(
        self,
        role_key: str,
        *,
        expected_generation: int,
        expected_session_id: str,
    ) -> Iterator[RoleRecord]:
        """Serialize one external durable action against role lifecycle changes.

        ``BEGIN IMMEDIATE`` reserves the registry writer lane until the caller's
        separate controller transaction has committed or rolled back. Lifecycle
        CAS operations therefore linearize entirely before or after that action.
        """

        with self.generations_guard(
            ((role_key, expected_generation, expected_session_id),)
        ) as records:
            yield records[0]

    @contextmanager
    def generations_guard(
        self,
        expectations: tuple[tuple[str, int, str], ...],
    ) -> Iterator[tuple[RoleRecord, ...]]:
        """Atomically validate and reserve several role generations/sessions."""

        if not expectations:
            raise RoleRegistryError("generation guard requires at least one role")
        role_keys = tuple(item[0] for item in expectations)
        if len(role_keys) != len(set(role_keys)):
            raise RoleRegistryError("generation guard repeats a role")
        for role_key, generation, session_id in expectations:
            _require_match(role_key, _ROLE_KEY, "role key")
            _require_match(session_id, _IDENTIFIER, "Codex session id")
            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 1
            ):
                raise RoleRegistryError("expected generation must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                records: list[RoleRecord] = []
                for role_key, generation, session_id in expectations:
                    row = connection.execute(
                        "SELECT * FROM role_registry WHERE role_key = ?", (role_key,)
                    ).fetchone()
                    if row is None:
                        raise RoleRegistryError("role key is not registered")
                    record = _record_from_row(row)
                    if (
                        record.generation != generation
                        or record.identity.codex_session_id != session_id
                    ):
                        raise StaleRoleGeneration("role generation or session changed")
                    records.append(record)
                yield tuple(records)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def records(self) -> tuple[RoleRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM role_registry ORDER BY role_key"
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def heartbeat(
        self,
        role_key: str,
        *,
        expected_generation: int,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        heartbeat_text, lease_text = _validated_window(observed_at, lease_until)
        return self._cas_update(
            role_key,
            expected_generation,
            "heartbeat_at = ?, lease_until = ?, generation = generation + 1",
            (heartbeat_text, lease_text),
        )

    def settle(
        self,
        role_key: str,
        *,
        expected_generation: int,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        """Release one completed task while retaining the reusable role session."""

        heartbeat_text, lease_text = _validated_window(observed_at, lease_until)
        current = self.get(role_key)
        if current.state is not RoleState.ACTIVE:
            raise RoleRegistryError("only an active role can settle to idle")
        if current.identity.active_task_id is None:
            raise RoleRegistryError("taskless roles do not have a task to settle")
        return self._cas_update(
            role_key,
            expected_generation,
            "active_task_id = NULL, active_dispatch_id = NULL, state = ?, "
            "heartbeat_at = ?, lease_until = ?, retry_of_dispatch_id = NULL, "
            "retry_attempt = 0, retry_provenance = NULL, generation = generation + 1",
            (RoleState.IDLE.value, heartbeat_text, lease_text),
        )

    def assign(
        self,
        role_key: str,
        *,
        expected_generation: int,
        task_id: str,
        dispatch_id: str,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        """Assign a never-used task attempt to an existing idle role session."""

        _require_match(task_id, _TASK_ID, "task id")
        _require_match(dispatch_id, _IDENTIFIER, "dispatch id")
        heartbeat_text, lease_text = _validated_window(observed_at, lease_until)
        current = self.get(role_key)
        if current.generation != expected_generation:
            raise StaleRoleGeneration("role generation changed")
        if current.state is not RoleState.IDLE:
            raise RoleRegistryError("new task assignment requires an idle role")
        if (
            current.identity.active_task_id is not None
            or current.identity.active_dispatch_id is not None
        ):
            raise RoleRegistryError("idle role retained an active Queue attempt")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            used = connection.execute(
                "SELECT 1 FROM role_dispatch_history WHERE role_key = ? AND dispatch_id = ?",
                (role_key, dispatch_id),
            ).fetchone()
            if used is not None:
                connection.rollback()
                raise RoleRegistryError("assignment requires a never-used dispatch identifier")
            cursor = connection.execute(
                """
                UPDATE role_registry SET
                    active_task_id = ?, active_dispatch_id = ?, state = ?,
                    heartbeat_at = ?, lease_until = ?, retry_of_dispatch_id = NULL,
                    retry_attempt = 0, retry_provenance = NULL,
                    generation = generation + 1
                WHERE role_key = ? AND generation = ? AND state = ?
                  AND active_task_id IS NULL AND active_dispatch_id IS NULL
                """,
                (
                    task_id, dispatch_id, RoleState.ACTIVE.value,
                    heartbeat_text, lease_text, role_key, expected_generation,
                    RoleState.IDLE.value,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StaleRoleGeneration("role generation changed")
            connection.execute(
                """
                INSERT INTO role_dispatch_history (
                    role_key, task_id, dispatch_id, attempt, provenance
                ) VALUES (?, ?, ?, 0, NULL)
                """,
                (role_key, task_id, dispatch_id),
            )
            connection.commit()
        return self.get(role_key)

    def mark_recovery_required(
        self,
        role_key: str,
        *,
        expected_generation: int,
    ) -> RoleRecord:
        current = self.get(role_key)
        if current.generation == expected_generation and current.state is RoleState.RECOVERY_REQUIRED:
            return current
        return self._cas_update(
            role_key,
            expected_generation,
            "state = ?, generation = generation + 1",
            (RoleState.RECOVERY_REQUIRED.value,),
        )

    def register_retry(
        self,
        role_key: str,
        *,
        expected_generation: int,
        task_id: str,
        retry_of_dispatch_id: str,
        new_dispatch_id: str,
        terminal_handle: str,
        runtime_id: str,
        retry_attempt: int,
        retry_provenance: str,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        _require_match(task_id, _TASK_ID, "task id")
        for value, label in (
            (retry_of_dispatch_id, "prior dispatch id"),
            (new_dispatch_id, "new dispatch id"),
            (terminal_handle, "terminal handle"),
            (runtime_id, "runtime id"),
            (retry_provenance, "retry provenance"),
        ):
            _require_match(value, _IDENTIFIER, label)
        if not isinstance(retry_attempt, int) or isinstance(retry_attempt, bool) or retry_attempt < 1:
            raise RoleRegistryError("retry attempt must be a positive integer")
        heartbeat_text, lease_text = _validated_window(observed_at, lease_until)
        current = self.get(role_key)
        if (
            current.generation == expected_generation
            and current.identity.active_task_id == task_id
            and current.identity.active_dispatch_id == new_dispatch_id
            and current.retry_of_dispatch_id == retry_of_dispatch_id
            and current.retry_attempt == retry_attempt
            and current.retry_provenance == retry_provenance
            and current.state is RoleState.ACTIVE
        ):
            return current
        if current.generation != expected_generation:
            raise StaleRoleGeneration("role generation changed")
        if current.state is not RoleState.RECOVERY_REQUIRED:
            raise RoleRegistryError("retry requires recovery_required state")
        if current.identity.active_task_id != task_id:
            raise RoleRegistryError("retry must preserve the exact Queue task")
        if current.identity.active_dispatch_id != retry_of_dispatch_id:
            raise RoleRegistryError("retry provenance must name the current dispatch")
        if retry_attempt != current.retry_attempt + 1:
            raise RoleRegistryError("retry attempt is not the exact next attempt")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            used = connection.execute(
                """
                SELECT 1 FROM role_dispatch_history
                WHERE role_key = ? AND dispatch_id = ?
                """,
                (role_key, new_dispatch_id),
            ).fetchone()
            if used is not None:
                connection.rollback()
                raise RoleRegistryError("retry requires a never-used dispatch identifier")
            cursor = connection.execute(
                """
                UPDATE role_registry SET
                    active_dispatch_id = ?, terminal_handle = ?, runtime_id = ?,
                    state = ?, heartbeat_at = ?, lease_until = ?,
                    retry_of_dispatch_id = ?, retry_attempt = ?, retry_provenance = ?,
                    generation = generation + 1
                WHERE role_key = ? AND generation = ? AND state = ?
                  AND active_task_id = ? AND active_dispatch_id = ?
                """,
                (
                    new_dispatch_id, terminal_handle, runtime_id, RoleState.ACTIVE.value,
                    heartbeat_text, lease_text, retry_of_dispatch_id, retry_attempt,
                    retry_provenance, role_key, expected_generation,
                    RoleState.RECOVERY_REQUIRED.value, task_id, retry_of_dispatch_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StaleRoleGeneration("role generation changed")
            connection.execute(
                """
                INSERT INTO role_dispatch_history (
                    role_key, task_id, dispatch_id, attempt, provenance
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (role_key, task_id, new_dispatch_id, retry_attempt, retry_provenance),
            )
            connection.commit()
        return self.get(role_key)

    def acknowledge_message(
        self,
        role_key: str,
        *,
        expected_generation: int,
        message_id: str,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        """Advance one role mailbox cursor exactly once under a generation fence."""

        _require_match(message_id, _IDENTIFIER, "message id")
        heartbeat_text, lease_text = _validated_window(observed_at, lease_until)
        current = self.get(role_key)
        if current.last_message_id == message_id:
            return current
        if current.generation != expected_generation:
            raise StaleRoleGeneration("role generation changed")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE role_registry SET last_message_id = ?, heartbeat_at = ?, "
                "lease_until = ? WHERE role_key = ? AND generation = ?",
                (
                    message_id, heartbeat_text, lease_text, role_key,
                    expected_generation,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                replayed = self.get(role_key)
                if replayed.last_message_id == message_id:
                    return replayed
                raise StaleRoleGeneration("role generation changed")
            connection.commit()
        return self.get(role_key)

    def hierarchy(self, root_role_key: str = "project_manager") -> tuple[RoleRecord, ...]:
        """Return a deterministic parent-before-child durable session hierarchy."""

        records = {item.identity.role_key: item for item in self.records()}
        if root_role_key not in records:
            raise RoleRegistryError("hierarchy root is not registered")
        project_managers = tuple(
            item for item in records.values()
            if item.identity.role_kind is RoleKind.PROJECT_MANAGER
            and item.state is not RoleState.STOPPED
        )
        if (
            len(project_managers) != 1
            or project_managers[0].identity.role_key != root_role_key
        ):
            raise RoleRegistryError("hierarchy requires exactly one live project manager")
        ordered: list[RoleRecord] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(role_key: str) -> None:
            if role_key in visiting:
                raise RoleRegistryError("role hierarchy contains a cycle")
            if role_key in visited:
                return
            visiting.add(role_key)
            ordered.append(records[role_key])
            children = sorted(
                key for key, value in records.items()
                if value.identity.parent_role_key == role_key
            )
            for child in children:
                visit(child)
            visiting.remove(role_key)
            visited.add(role_key)

        visit(root_role_key)
        live_keys = {
            key for key, record in records.items()
            if record.state is not RoleState.STOPPED
        }
        if not live_keys <= visited:
            raise RoleRegistryError("role hierarchy contains an orphan session")
        return tuple(ordered)

    def _cas_update(
        self,
        role_key: str,
        expected_generation: int,
        assignment_sql: str,
        values: tuple[object, ...],
    ) -> RoleRecord:
        _require_match(role_key, _ROLE_KEY, "role key")
        if not isinstance(expected_generation, int) or expected_generation < 1:
            raise StaleRoleGeneration("expected generation is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE role_registry SET {assignment_sql} WHERE role_key = ? AND generation = ?",
                (*values, role_key, expected_generation),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StaleRoleGeneration("role generation changed")
            connection.commit()
        return self.get(role_key)


def _validated_window(observed_at: datetime, lease_until: datetime) -> tuple[str, str]:
    heartbeat_text = utc_text(observed_at)
    lease_text = utc_text(lease_until)
    if lease_text <= heartbeat_text:
        raise RoleRegistryError("lease must end after the heartbeat")
    return heartbeat_text, lease_text


def _record_from_row(row: sqlite3.Row) -> RoleRecord:
    return RoleRecord(
        identity=RoleIdentity(
            role_key=row["role_key"],
            role_kind=RoleKind(row["role_kind"]),
            codex_session_id=row["codex_session_id"],
            orca_run_id=row["orca_run_id"],
            worktree_id=row["worktree_id"],
            terminal_handle=row["terminal_handle"],
            runtime_id=row["runtime_id"],
            active_task_id=row["active_task_id"],
            active_dispatch_id=row["active_dispatch_id"],
            parent_role_key=row["parent_role_key"],
        ),
        state=RoleState(row["state"]),
        generation=row["generation"],
        heartbeat_at=parse_utc(row["heartbeat_at"]),
        lease_until=parse_utc(row["lease_until"]),
        retry_of_dispatch_id=row["retry_of_dispatch_id"],
        retry_attempt=row["retry_attempt"],
        retry_provenance=row["retry_provenance"],
        last_message_id=row["last_message_id"],
    )
