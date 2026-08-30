"""Fail-closed, single-writer service around :class:`WorkflowController`.

This module intentionally has no transport implementation.  A deployment must
inject a controller backed by its approved direct Codex boundary; local fakes
remain test-only and cannot be selected by this service or its CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import BinaryIO, Callable, Iterable, Mapping

from stock_data.orchestration.workflow_control.controller import (
    ControlGeneration,
    HierarchyResumeReceipt,
    MailboxAcknowledgement,
    MailboxEnvelope,
    PumpReceipt,
    ReviewDecision,
    ReviewLoopReceipt,
    WorkflowController,
)
from stock_data.orchestration.workflow_control.codex_boundary import CodexCliBoundary
from stock_data.orchestration.workflow_control.events import canonical_event_json
from stock_data.orchestration.workflow_control.listener_gateway import (
    MailboxEnvelope as ListenerMailboxEnvelope,
    PMMailboxIdentity,
)
from stock_data.orchestration.workflow_control.contracts import WorkflowEvent, utc_text
from stock_data.orchestration.workflow_control.registry import RoleIdentity, RoleRecord
from stock_data.orchestration.workflow_control.routing import TaskContract
from stock_data.orchestration.workflow_control.runner import (
    ExecutionMetadata,
    RunnerAction,
    RunnerReceipt,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_OPERATION_STATES = frozenset({"working", "idle", "reviewing", "stalled", "stopped"})
_ROLE_KINDS = frozenset({"project_manager", "domain_lead", "worker", "reviewer"})
_PYTHON_PM_SERVICE_KEY = "python_pm"
_LEGACY_GENERATION_DIGEST = "0" * 64


class ControllerServiceError(RuntimeError):
    """Raised for invalid service use or an incomplete control-plane setup."""


class WriterLeaseConflict(ControllerServiceError):
    """Raised when another live service instance owns the writer lease."""


class ServiceMode(StrEnum):
    STATUS = "status"
    CANARY = "canary"
    RUN = "run"
    ROLLBACK = "rollback"


class _ControllerMutex:
    """One OS-held byte-range lock; the OS releases it when its process dies."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self, *, create: bool = True) -> bool:
        if self._handle is not None:
            return True
        if not create and not self.path.exists():
            return False
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
        else:
            handle = self.path.open("r+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now_text() -> str:
    return utc_text(datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ControllerServiceStatus:
    service_key: str
    active: bool
    generation_sequence: int | None
    generation_digest: str | None
    owner_id: str | None
    completed_operations: int
    writer_state: str = "idle"
    pending_boundary_operations: int = 0
    completed_boundary_operations: int = 0
    failed_boundary_operations: int = 0


@dataclass(frozen=True, slots=True)
class ServiceReceipt:
    """Durable outer receipt that references immutable controller evidence."""

    mode: ServiceMode
    operation_id: str
    input_digest: str
    generation_sequence: int
    generation_digest: str
    controller_receipt: PumpReceipt
    execution_profile: str = ""
    execution_profile_digest: str = ""
    workspace_write_enabled: bool = False
    mutation_observed: bool | None = False
    orca_used: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.execution_profile_digest:
            metadata = ExecutionMetadata(
                self.execution_profile,
                self.workspace_write_enabled,
                self.mutation_observed,
                self.orca_used,
                self.execution_profile_digest,
            )
            if (
                self.controller_receipt.execution_profile_digest
                != metadata.profile_digest
                or self.controller_receipt.workspace_write_enabled
                != metadata.workspace_write_enabled
                or self.controller_receipt.mutation_observed
                is not metadata.mutation_observed
                or self.controller_receipt.orca_used != metadata.orca_used
            ):
                raise ControllerServiceError(
                    "service and controller execution metadata differ"
                )
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise ControllerServiceError("service receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "mode": self.mode.value,
            "operation_id": self.operation_id,
            "input_digest": self.input_digest,
            "generation_sequence": self.generation_sequence,
            "generation_digest": self.generation_digest,
            "controller_receipt": self.controller_receipt.to_dict(),
        }
        if self.execution_profile_digest:
            value.update(
                {
                    "execution_profile": self.execution_profile,
                    "execution_profile_digest": self.execution_profile_digest,
                    "workspace_write_enabled": self.workspace_write_enabled,
                    "mutation_observed": self.mutation_observed,
                    "orca_used": self.orca_used,
                }
            )
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ServiceReceipt":
        return cls(
            mode=ServiceMode(str(value["mode"])),
            operation_id=str(value["operation_id"]),
            input_digest=str(value["input_digest"]),
            generation_sequence=int(value["generation_sequence"]),
            generation_digest=str(value["generation_digest"]),
            controller_receipt=PumpReceipt.from_dict(value["controller_receipt"]),  # type: ignore[arg-type]
            execution_profile=str(value.get("execution_profile", "")),
            execution_profile_digest=str(value.get("execution_profile_digest", "")),
            workspace_write_enabled=bool(value.get("workspace_write_enabled", False)),
            mutation_observed=value.get("mutation_observed", False),  # type: ignore[arg-type]
            orca_used=bool(value.get("orca_used", False)),
            receipt_digest=str(value["receipt_digest"]),
        )


@dataclass(frozen=True, slots=True)
class OperationActivity:
    """One sanitized activity projection for a directly controlled operation.

    Session identity is deliberately a SHA-256 fingerprint.  Monitoring can
    correlate a card across polls without persisting a raw terminal/session
    identifier, prompt, or transcript.
    """

    operation_id: str
    role_kind: str
    session_fingerprint: str
    task_id: str | None
    state: str
    heartbeat_at: datetime
    active: bool
    activity_digest: str = ""
    generation_sequence: int = 0
    generation_digest: str = ""

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.operation_id) is None:
            raise ControllerServiceError("operation_id must be a bounded identifier")
        if self.role_kind not in _ROLE_KINDS:
            raise ControllerServiceError("operation role_kind is unsupported")
        if _DIGEST.fullmatch(self.session_fingerprint) is None:
            raise ControllerServiceError("operation session identity must be a SHA-256 fingerprint")
        if self.task_id is not None and _TASK_ID.fullmatch(self.task_id) is None:
            raise ControllerServiceError("operation task_id must be an exact Queue task id")
        if self.state not in _OPERATION_STATES:
            raise ControllerServiceError("operation state is unsupported")
        if not isinstance(self.active, bool):
            raise ControllerServiceError("operation active must be a boolean")
        if self.generation_sequence < 0:
            raise ControllerServiceError("operation generation sequence is invalid")
        if self.generation_sequence == 0:
            # Pre-generation activity payloads remain auditable after the
            # additive database migration, but service-written rows always
            # carry a positive, real control generation.
            if self.generation_digest not in {"", _LEGACY_GENERATION_DIGEST}:
                raise ControllerServiceError("legacy operation generation digest is invalid")
            object.__setattr__(self, "generation_digest", _LEGACY_GENERATION_DIGEST)
        elif _DIGEST.fullmatch(self.generation_digest) is None:
            raise ControllerServiceError("operation generation digest must be SHA-256")
        try:
            heartbeat_text = utc_text(self.heartbeat_at)
        except ValueError as error:
            raise ControllerServiceError("operation heartbeat must be timezone-aware") from error
        object.__setattr__(self, "heartbeat_at", datetime.fromisoformat(heartbeat_text.replace("Z", "+00:00")))
        expected = _digest(self.to_dict(include_digest=False))
        if self.activity_digest and self.activity_digest != expected:
            raise ControllerServiceError("operation activity digest mismatch")
        object.__setattr__(self, "activity_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "operation_id": self.operation_id,
            "role_kind": self.role_kind,
            "session_fingerprint": self.session_fingerprint,
            "task_id": self.task_id,
            "state": self.state,
            "heartbeat_at": utc_text(self.heartbeat_at),
            "active": self.active,
        }
        if self.generation_sequence:
            value["generation_sequence"] = self.generation_sequence
            value["generation_digest"] = self.generation_digest
        if include_digest:
            value["activity_digest"] = self.activity_digest
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OperationActivity":
        heartbeat = value["heartbeat_at"]
        if not isinstance(heartbeat, str):
            raise ControllerServiceError("operation activity heartbeat is invalid")
        return cls(
            operation_id=str(value["operation_id"]),
            role_kind=str(value["role_kind"]),
            session_fingerprint=str(value["session_fingerprint"]),
            task_id=None if value["task_id"] is None else str(value["task_id"]),
            state=str(value["state"]),
            heartbeat_at=datetime.fromisoformat(heartbeat.replace("Z", "+00:00")),
            active=bool(value["active"]),
            activity_digest=str(value["activity_digest"]),
            generation_sequence=int(value.get("generation_sequence", 0)),
            generation_digest=str(value.get("generation_digest", "")),
        )


class _ActivityRecordingRunner:
    """Proxy the controller's real runner without changing its boundary API."""

    def __init__(self, delegate: object, service: "WorkflowControllerService") -> None:
        self._delegate = delegate
        self._service = service
        self.execution_metadata = getattr(delegate, "execution_metadata")

    @property
    def receipts(self) -> object:
        return getattr(self._delegate, "receipts")

    def run(self, action: RunnerAction, **kwargs: object) -> RunnerReceipt:
        role_key = kwargs.get("role_key")
        task_id = kwargs.get("task_id")
        observable = (
            self._service._generation is not None
            and isinstance(role_key, str)
            and _IDENTIFIER.fullmatch(role_key) is not None
            and isinstance(task_id, str)
            and _TASK_ID.fullmatch(task_id) is not None
        )
        role_kind = (
            "domain_lead" if isinstance(role_key, str) and role_key.startswith("lead_")
            else "reviewer" if isinstance(role_key, str) and role_key.startswith("review")
            else "worker" if isinstance(role_key, str) and role_key.startswith("worker")
            else "domain_lead"
        )
        correlation = f"runner-task-{task_id}-{role_kind}"
        if observable:
            # Record before the boundary call so a long-running Codex process
            # is visible to a concurrently refreshed dashboard, not only
            # after the subprocess has already returned.
            self._service._record_activity(
                role_kind, task_id, "working", active=True,
                correlation=correlation,
            )
        try:
            receipt = getattr(self._delegate, "run")(action, **kwargs)
        except BaseException:
            if observable and self._service._generation is not None:
                self._service._record_activity(
                    role_kind, task_id, "stalled", active=True,
                    correlation=correlation,
                )
            raise
        if not isinstance(receipt, RunnerReceipt):
            raise ControllerServiceError("controller runner returned an invalid receipt")
        if self._service._generation is not None:
            # Only the sanitized receipt fields are used.  In particular,
            # ``agent_id`` and any boundary session route are not retained.
            self._service._record_activity(
                role_kind,
                receipt.task_id,
                "stopped" if action is RunnerAction.SETTLE else "working",
                active=action is not RunnerAction.SETTLE,
                correlation=correlation,
            )
        return receipt

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)


class WorkflowControllerService:
    """A process-safe writer fence with replayable service-level receipts.

    ``start`` creates one strictly increasing generation.  A different owner
    cannot take over until the owner releases it through ``rollback``/``close``;
    this deliberately refuses automatic lease stealing after a crash.
    """

    def __init__(
        self,
        controller: WorkflowController,
        control_root: Path,
        *,
        owner_id: str,
        service_key: str = "python_pm",
    ) -> None:
        if _IDENTIFIER.fullmatch(owner_id) is None:
            raise ControllerServiceError("owner_id must be a bounded identifier")
        if service_key != _PYTHON_PM_SERVICE_KEY:
            raise ControllerServiceError("only the python_pm service key may own the controller writer")
        self.controller = controller
        self.control_root = Path(control_root)
        self.owner_id = owner_id
        self.service_key = service_key
        self.execution_metadata = controller.execution_metadata
        self.control_root.mkdir(parents=True, exist_ok=True)
        self._database = self.control_root / "workflow_controller_service.sqlite3"
        self._mutex_path = self.control_root / "workflow_controller_service.lock"
        self._mutex: _ControllerMutex | None = None
        self._generation: ControlGeneration | None = None
        self._initialize()
        # Keep all controller launch/resume/settle calls observable, including
        # recovery paths that call ``controller.runner`` directly.  This is a
        # narrow in-process proxy, not a second execution transport.
        self._direct_runner = controller.runner
        controller.runner = _ActivityRecordingRunner(self._direct_runner, self)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS writer_lease("
                "service_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
                "generation_sequence INTEGER NOT NULL, generation_digest TEXT NOT NULL, "
                "acquired_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS generation_history("
                "generation_sequence INTEGER PRIMARY KEY, generation_digest TEXT NOT NULL UNIQUE, "
                "owner_id TEXT NOT NULL, acquired_at TEXT NOT NULL, released_at TEXT, release_reason TEXT)"
            )
            history_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(generation_history)")
            }
            if "release_reason" not in history_columns:
                connection.execute("ALTER TABLE generation_history ADD COLUMN release_reason TEXT")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS service_operation("
                "operation_id TEXT PRIMARY KEY, mode TEXT NOT NULL, input_digest TEXT NOT NULL, "
                "generation_sequence INTEGER NOT NULL, generation_digest TEXT NOT NULL, "
                "created_at TEXT NOT NULL, execution_profile_digest TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS service_receipt("
                "operation_id TEXT PRIMARY KEY, mode TEXT NOT NULL, input_digest TEXT NOT NULL, "
                "payload TEXT NOT NULL, completed_at TEXT NOT NULL, execution_profile_digest TEXT)"
            )
            for table in ("service_operation", "service_receipt"):
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if "execution_profile_digest" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN execution_profile_digest TEXT"
                    )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS operation_activity("
                "operation_id TEXT PRIMARY KEY, role_kind TEXT NOT NULL, "
                "session_fingerprint TEXT NOT NULL, task_id TEXT, state TEXT NOT NULL, "
                "heartbeat_at TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0, 1)), "
                "activity_digest TEXT NOT NULL, generation_sequence INTEGER NOT NULL DEFAULT 0, "
                f"generation_digest TEXT NOT NULL DEFAULT '{_LEGACY_GENERATION_DIGEST}')"
            )
            activity_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(operation_activity)")
            }
            if "generation_sequence" not in activity_columns:
                connection.execute(
                    "ALTER TABLE operation_activity ADD COLUMN generation_sequence INTEGER NOT NULL DEFAULT 0"
                )
            if "generation_digest" not in activity_columns:
                connection.execute(
                    "ALTER TABLE operation_activity ADD COLUMN generation_digest TEXT NOT NULL "
                    f"DEFAULT '{_LEGACY_GENERATION_DIGEST}'"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS operation_activity_receipt("
                "activity_digest TEXT PRIMARY KEY, operation_id TEXT NOT NULL, "
                "payload TEXT NOT NULL, recorded_at TEXT NOT NULL)"
            )

    @staticmethod
    def inspect(control_root: Path, *, service_key: str = "python_pm") -> ControllerServiceStatus:
        if service_key != _PYTHON_PM_SERVICE_KEY:
            raise ControllerServiceError("only the python_pm service key may inspect the controller writer")
        database = Path(control_root) / "workflow_controller_service.sqlite3"
        if not database.exists():
            boundary = CodexCliBoundary.inspect(Path(control_root) / "codex_boundary.sqlite3")
            return ControllerServiceStatus(
                service_key, False, None, None, None, 0,
                "uncertain" if boundary.pending_operations else "idle",
                boundary.pending_operations,
                boundary.completed_operations,
                boundary.failed_operations,
            )
        with sqlite3.connect(database, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            lease = connection.execute(
                "SELECT owner_id, generation_sequence, generation_digest FROM writer_lease "
                "WHERE service_key = ?", (service_key,)
            ).fetchone()
            completed = int(connection.execute("SELECT COUNT(*) FROM service_receipt").fetchone()[0])
        boundary = CodexCliBoundary.inspect(Path(control_root) / "codex_boundary.sqlite3")
        if lease is None:
            writer_state = "uncertain" if boundary.pending_operations else "idle"
        else:
            mutex = _ControllerMutex(Path(control_root) / "workflow_controller_service.lock")
            try:
                mutex_available = mutex.acquire(create=False)
            except OSError:
                mutex_available = False
            finally:
                mutex.release()
            writer_state = "stale" if mutex_available else "live"
        return ControllerServiceStatus(
            service_key=service_key,
            active=lease is not None,
            generation_sequence=None if lease is None else int(lease["generation_sequence"]),
            generation_digest=None if lease is None else str(lease["generation_digest"]),
            owner_id=None if lease is None else str(lease["owner_id"]),
            completed_operations=completed,
            writer_state=writer_state,
            pending_boundary_operations=boundary.pending_operations,
            completed_boundary_operations=boundary.completed_operations,
            failed_boundary_operations=boundary.failed_operations,
        )

    def start(self) -> ControlGeneration:
        if self._generation is not None:
            return self._generation
        if CodexCliBoundary.inspect(
            self.control_root / "codex_boundary.sqlite3"
        ).pending_operations:
            raise ControllerServiceError(
                "uncertain Codex boundary work requires reconciliation before writer start"
            )
        mutex = _ControllerMutex(self._mutex_path)
        if not mutex.acquire():
            raise WriterLeaseConflict("another live Python PM writer holds the OS mutex")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT owner_id, generation_sequence, generation_digest FROM writer_lease "
                    "WHERE service_key = ?", (self.service_key,)
                ).fetchone()
                if existing is not None:
                    # Holding the OS mutex proves this row cannot have a live
                    # process owner.  Preserve its generation as recovered,
                    # then fence all future writes with a newer generation.
                    connection.execute(
                        "UPDATE generation_history SET released_at = ?, release_reason = 'recovered' "
                        "WHERE generation_sequence = ? AND generation_digest = ? AND owner_id = ? "
                        "AND released_at IS NULL",
                        (_now_text(), existing["generation_sequence"], existing["generation_digest"], existing["owner_id"]),
                    )
                    connection.execute(
                        "DELETE FROM writer_lease WHERE service_key = ? AND owner_id = ? "
                        "AND generation_sequence = ? AND generation_digest = ?",
                        (self.service_key, existing["owner_id"], existing["generation_sequence"], existing["generation_digest"]),
                    )
                    connection.execute(
                        "UPDATE operation_activity SET generation_sequence = ?, generation_digest = ? "
                        "WHERE active = 1 AND generation_sequence = 0 AND generation_digest = ?",
                        (existing["generation_sequence"], existing["generation_digest"], _LEGACY_GENERATION_DIGEST),
                    )
                    self._settle_active_activities_in_transaction(connection)
                sequence = int(connection.execute(
                    "SELECT COALESCE(MAX(generation_sequence), 0) + 1 FROM generation_history"
                ).fetchone()[0])
                digest = _digest({
                    "generation_sequence": sequence,
                    "service_key": self.service_key,
                })
                generation = ControlGeneration(sequence, digest)
                acquired_at = _now_text()
                connection.execute(
                    "INSERT INTO writer_lease(service_key, owner_id, generation_sequence, generation_digest, acquired_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self.service_key, self.owner_id, sequence, digest, acquired_at),
                )
                connection.execute(
                    "INSERT INTO generation_history(generation_sequence, generation_digest, owner_id, acquired_at, released_at, release_reason) "
                    "VALUES (?, ?, ?, ?, NULL, NULL)",
                    (sequence, digest, self.owner_id, acquired_at),
                )
                connection.commit()
        except BaseException:
            mutex.release()
            raise
        self._mutex = mutex
        self._generation = generation
        # The service writer itself is the canonical PM lifecycle.  Persist a
        # hashed-only activity card as soon as the durable writer lease exists.
        self._record_pm_activity("working", active=True)
        return generation

    def close(self, *, release_reason: str = "stopped") -> None:
        if self._generation is None:
            return
        # Do this while the exact generation still owns the writer.  A closed
        # process must not leave a dashboard claiming that a PM or Lead is
        # still active merely because its last operation was successful.
        self._stop_active_activities()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM writer_lease WHERE service_key = ? AND owner_id = ? "
                "AND generation_sequence = ? AND generation_digest = ?",
                (self.service_key, self.owner_id, self._generation.sequence, self._generation.digest),
            )
            if deleted.rowcount != 1:
                connection.rollback()
                raise WriterLeaseConflict("writer lease ownership changed before release")
            connection.execute(
                "UPDATE generation_history SET released_at = ? WHERE generation_sequence = ? "
                "AND generation_digest = ? AND owner_id = ? AND released_at IS NULL",
                (_now_text(), self._generation.sequence, self._generation.digest, self.owner_id),
            )
            connection.execute(
                "UPDATE generation_history SET release_reason = ? WHERE generation_sequence = ? "
                "AND generation_digest = ? AND owner_id = ?",
                (release_reason, self._generation.sequence, self._generation.digest, self.owner_id),
            )
            connection.commit()
        self._generation = None
        assert self._mutex is not None
        self._mutex.release()
        self._mutex = None

    def rollback(self) -> ControllerServiceStatus:
        """Stop only this writer; it never invokes an execution boundary."""

        self.close(release_reason="rollback")
        return self.inspect(self.control_root, service_key=self.service_key)

    @classmethod
    def rollback_stale(
        cls,
        control_root: Path,
        *,
        owner_id: str,
        generation_sequence: int,
        generation_digest: str,
        service_key: str = _PYTHON_PM_SERVICE_KEY,
    ) -> ControllerServiceStatus:
        """Fence one observed dead writer without constructing an execution boundary."""

        if _IDENTIFIER.fullmatch(owner_id) is None or generation_sequence < 1:
            raise ControllerServiceError("rollback owner and generation must be exact")
        if _DIGEST.fullmatch(generation_digest) is None or service_key != _PYTHON_PM_SERVICE_KEY:
            raise ControllerServiceError("rollback generation or service key is invalid")
        root = Path(control_root)
        mutex = _ControllerMutex(root / "workflow_controller_service.lock")
        if not mutex.acquire():
            raise WriterLeaseConflict("rollback refused while a live Python PM writer holds the OS mutex")
        database = root / "workflow_controller_service.sqlite3"
        try:
            if CodexCliBoundary.inspect(
                root / "codex_boundary.sqlite3"
            ).pending_operations:
                raise ControllerServiceError(
                    "rollback refused while Codex boundary work is uncertain"
                )
            if not database.exists():
                return cls.inspect(root, service_key=service_key)
            with sqlite3.connect(database, timeout=30, isolation_level=None) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT owner_id, generation_sequence, generation_digest FROM writer_lease WHERE service_key = ?",
                    (service_key,),
                ).fetchone()
                if row is None:
                    connection.commit()
                elif (
                    row["owner_id"] != owner_id
                    or int(row["generation_sequence"]) != generation_sequence
                    or row["generation_digest"] != generation_digest
                ):
                    connection.rollback()
                    raise WriterLeaseConflict("rollback observed writer generation no longer matches")
                else:
                    activity_columns = {
                        str(column[1]) for column in connection.execute("PRAGMA table_info(operation_activity)")
                    }
                    if activity_columns:
                        # ``rollback_stale`` can be the first new-version
                        # entrypoint after a crash, so carry the additive
                        # activity migration here as well as service startup.
                        if "generation_sequence" not in activity_columns:
                            connection.execute(
                                "ALTER TABLE operation_activity ADD COLUMN generation_sequence INTEGER NOT NULL DEFAULT 0"
                            )
                        if "generation_digest" not in activity_columns:
                            connection.execute(
                                "ALTER TABLE operation_activity ADD COLUMN generation_digest TEXT NOT NULL "
                                f"DEFAULT '{_LEGACY_GENERATION_DIGEST}'"
                            )
                        connection.execute(
                            "UPDATE operation_activity SET generation_sequence = ?, generation_digest = ? "
                            "WHERE active = 1 AND generation_sequence = 0 AND generation_digest = ?",
                            (generation_sequence, generation_digest, _LEGACY_GENERATION_DIGEST),
                        )
                        connection.execute(
                            "CREATE TABLE IF NOT EXISTS operation_activity_receipt("
                            "activity_digest TEXT PRIMARY KEY, operation_id TEXT NOT NULL, "
                            "payload TEXT NOT NULL, recorded_at TEXT NOT NULL)"
                        )
                        cls._settle_active_activities_in_transaction(
                            connection,
                            generation_sequence=generation_sequence,
                            generation_digest=generation_digest,
                        )
                    connection.execute(
                        "DELETE FROM writer_lease WHERE service_key = ? AND owner_id = ? "
                        "AND generation_sequence = ? AND generation_digest = ?",
                        (service_key, owner_id, generation_sequence, generation_digest),
                    )
                    connection.execute(
                        "UPDATE generation_history SET released_at = ?, release_reason = 'rollback' "
                        "WHERE generation_sequence = ? AND generation_digest = ? AND owner_id = ? "
                        "AND released_at IS NULL",
                        (_now_text(), generation_sequence, generation_digest, owner_id),
                    )
                    connection.commit()
        finally:
            mutex.release()
        return cls.inspect(root, service_key=service_key)

    def report_activity(self, activity: OperationActivity) -> OperationActivity:
        """Persist a latest activity card and immutable receipt for monitoring.

        This is deliberately independent of Queue files.  An injected boundary
        calls it only after it has a verified direct operation/session mapping.
        """

        if self._generation is None:
            raise ControllerServiceError("writer service must be started before reporting activity")
        activity = self._bind_activity_generation(activity)
        payload = _canonical(activity.to_dict())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT role_kind, session_fingerprint, task_id, generation_sequence, generation_digest, heartbeat_at, activity_digest "
                "FROM operation_activity WHERE operation_id = ?", (activity.operation_id,)
            ).fetchone()
            if existing is not None:
                same_identity = (
                    existing["role_kind"] == activity.role_kind
                    and existing["session_fingerprint"] == activity.session_fingerprint
                    and existing["task_id"] == activity.task_id
                    and int(existing["generation_sequence"]) == activity.generation_sequence
                    and existing["generation_digest"] == activity.generation_digest
                )
                if not same_identity:
                    connection.rollback()
                    raise ControllerServiceError("operation identity cannot be rebound")
                if existing["activity_digest"] == activity.activity_digest:
                    connection.commit()
                    return activity
                if existing["heartbeat_at"] > utc_text(activity.heartbeat_at):
                    connection.rollback()
                    raise ControllerServiceError("operation heartbeat cannot move backwards")
            connection.execute(
                "INSERT INTO operation_activity(operation_id, role_kind, session_fingerprint, task_id, state, heartbeat_at, active, activity_digest, generation_sequence, generation_digest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(operation_id) DO UPDATE SET state = excluded.state, heartbeat_at = excluded.heartbeat_at, "
                "active = excluded.active, activity_digest = excluded.activity_digest",
                (
                    activity.operation_id, activity.role_kind, activity.session_fingerprint,
                    activity.task_id, activity.state, utc_text(activity.heartbeat_at),
                    int(activity.active), activity.activity_digest, activity.generation_sequence,
                    activity.generation_digest,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO operation_activity_receipt(activity_digest, operation_id, payload, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (activity.activity_digest, activity.operation_id, payload, _now_text()),
            )
            connection.commit()
        return activity

    @staticmethod
    def _activity_id(*parts: str) -> str:
        """Return an identifier-only durable activity key.

        The direct boundary can own a raw session route internally, but the
        service monitor only receives this one-way digest.  No prompt, output,
        raw session id, or terminal identity is copied into this database.
        """

        material = "\x1f".join(parts).encode("utf-8")
        return "activity-" + hashlib.sha256(material).hexdigest()

    def _record_activity(
        self,
        role_kind: str,
        task_id: str | None,
        state: str,
        *,
        active: bool,
        correlation: str,
    ) -> OperationActivity:
        generation = self._generation
        if generation is None:
            raise ControllerServiceError("writer service must be started before reporting activity")
        operation_id = self._activity_id(
            self.service_key, generation.digest, role_kind, task_id or "-", correlation,
        )
        fingerprint = hashlib.sha256(
            (generation.digest + "\x1f" + operation_id).encode("utf-8")
        ).hexdigest()
        return self.report_activity(
            OperationActivity(
                operation_id=operation_id,
                role_kind=role_kind,
                session_fingerprint=fingerprint,
                task_id=task_id,
                state=state,
                heartbeat_at=datetime.now(UTC),
                active=active,
                generation_sequence=generation.sequence,
                generation_digest=generation.digest,
            )
        )

    def _record_pm_activity(self, state: str, *, active: bool) -> OperationActivity:
        generation = self._generation
        assert generation is not None
        return self._record_activity(
            "project_manager", None, state, active=active,
            correlation=f"pm-generation-{generation.sequence}",
        )

    def _bind_activity_generation(self, activity: OperationActivity) -> OperationActivity:
        """Bind externally reported activity to this fenced writer generation."""

        generation = self._generation
        assert generation is not None
        if activity.generation_sequence not in {0, generation.sequence}:
            raise ControllerServiceError("operation activity belongs to a different generation")
        if activity.generation_sequence and activity.generation_digest != generation.digest:
            raise ControllerServiceError("operation activity generation digest differs")
        if activity.generation_sequence:
            return activity
        return OperationActivity(
            activity.operation_id, activity.role_kind, activity.session_fingerprint,
            activity.task_id, activity.state, activity.heartbeat_at, activity.active,
            generation_sequence=generation.sequence, generation_digest=generation.digest,
        )

    @staticmethod
    def _settle_active_activities_in_transaction(
        connection: sqlite3.Connection,
        *,
        generation_sequence: int | None = None,
        generation_digest: str | None = None,
    ) -> None:
        """Atomically stop active cards and retain only sanitized revisions."""

        where = "active = 1"
        params: list[object] = []
        if generation_sequence is not None:
            where += " AND generation_sequence = ? AND generation_digest = ?"
            params.extend((generation_sequence, generation_digest))
        rows = connection.execute(
            "SELECT operation_id, role_kind, session_fingerprint, task_id, heartbeat_at, "
            "generation_sequence, generation_digest FROM operation_activity WHERE " + where,
            params,
        ).fetchall()
        now = datetime.now(UTC)
        for row in rows:
            heartbeat = datetime.fromisoformat(str(row["heartbeat_at"]).replace("Z", "+00:00"))
            settled = OperationActivity(
                str(row["operation_id"]), str(row["role_kind"]),
                str(row["session_fingerprint"]), row["task_id"], "stopped",
                max(now, heartbeat), False,
                generation_sequence=int(row["generation_sequence"]),
                generation_digest=str(row["generation_digest"]),
            )
            payload = _canonical(settled.to_dict())
            connection.execute(
                "UPDATE operation_activity SET state = ?, heartbeat_at = ?, active = ?, activity_digest = ? "
                "WHERE operation_id = ?",
                (settled.state, utc_text(settled.heartbeat_at), 0, settled.activity_digest, settled.operation_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO operation_activity_receipt(activity_digest, operation_id, payload, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (settled.activity_digest, settled.operation_id, payload, _now_text()),
            )

    def _stop_active_activities(self) -> None:
        """Set only this writer generation's cards to a truthful final state."""

        generation = self._generation
        assert generation is not None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._settle_active_activities_in_transaction(
                connection, generation_sequence=generation.sequence,
                generation_digest=generation.digest,
            )
            connection.commit()

    def activities(self, *, active_only: bool = False) -> tuple[OperationActivity, ...]:
        query = "SELECT operation_id, role_kind, session_fingerprint, task_id, state, heartbeat_at, active, activity_digest, generation_sequence, generation_digest FROM operation_activity"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY role_kind, operation_id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return tuple(OperationActivity.from_dict(dict(row)) for row in rows)

    def canary(self, events: Iterable[WorkflowEvent]) -> ServiceReceipt:
        return self._execute(ServiceMode.CANARY, events)

    def run(self, events: Iterable[WorkflowEvent]) -> ServiceReceipt:
        return self._execute(ServiceMode.RUN, events)

    def _require_started(self) -> ControlGeneration:
        if self._generation is None:
            raise ControllerServiceError("writer service must be started before hierarchy work")
        return self._generation

    def register_role_session(
        self,
        identity: RoleIdentity,
        *,
        observed_at: datetime,
        lease_until: datetime,
    ) -> RoleRecord:
        self._require_started()
        return self.controller.register_role_session(
            identity, observed_at=observed_at, lease_until=lease_until
        )

    def resume_session_hierarchy(
        self, root_role_key: str = "project_manager"
    ) -> HierarchyResumeReceipt:
        self._require_started()
        return self.controller.resume_session_hierarchy(root_role_key)

    def deliver_pm_message(
        self, *, receipt_key: str, intent_key: str, message: str
    ) -> str:
        """ListenerGateway sink; one live Python writer owns durable delivery."""

        self._require_started()
        return self.controller.deliver_pm_message(
            receipt_key=receipt_key, intent_key=intent_key, message=message
        )

    def resolve_pm_mailbox_identity(self) -> PMMailboxIdentity:
        """Resolve Listener routing from the current durable PM registry row."""

        self._require_started()
        pm = self.controller.role_registry.get("project_manager")
        return PMMailboxIdentity(
            recipient=pm.identity.role_key,
            session_id=pm.identity.codex_session_id,
            generation=pm.generation,
        )

    def deliver_mailbox_envelope(self, envelope: ListenerMailboxEnvelope) -> str:
        """Typed Listener sink; reject stale session or generation before insert."""

        self._require_started()
        if not isinstance(envelope, ListenerMailboxEnvelope):
            raise ControllerServiceError("Listener mailbox delivery requires MailboxEnvelope")
        return self.controller.deliver_listener_mailbox_envelope(envelope)

    def mailbox(
        self, recipient_role_key: str, *, pending_only: bool = False
    ) -> tuple[MailboxEnvelope, ...]:
        self._require_started()
        return self.controller.mailbox(recipient_role_key, pending_only=pending_only)

    def acknowledge_mailbox(
        self,
        message_id: str,
        *,
        recipient_role_key: str,
        expected_generation: int,
        acknowledgement_ref: str,
        observed_at: datetime | None = None,
    ) -> MailboxAcknowledgement:
        self._require_started()
        return self.controller.acknowledge_mailbox(
            message_id,
            recipient_role_key=recipient_role_key,
            expected_generation=expected_generation,
            acknowledgement_ref=acknowledgement_ref,
            observed_at=observed_at,
        )

    def dispatch_task_contract(
        self, contract: TaskContract, *, pm_generation: int
    ) -> MailboxEnvelope:
        self._require_started()
        return self.controller.dispatch_task_contract(
            contract, pm_generation=pm_generation
        )

    def dispatch_workers(
        self,
        *,
        task_id: str,
        queue_generation: str,
        lead_role_key: str,
        lead_generation: int,
    ) -> tuple[MailboxEnvelope, ...]:
        self._require_started()
        return self.controller.dispatch_workers(
            task_id=task_id,
            queue_generation=queue_generation,
            lead_role_key=lead_role_key,
            lead_generation=lead_generation,
        )

    def record_lead_checkpoint(
        self,
        *,
        task_id: str,
        queue_generation: str,
        lead_role_key: str,
        lead_generation: int,
        checkpoint_digest: str,
    ) -> str:
        self._require_started()
        return self.controller.record_lead_checkpoint(
            task_id=task_id,
            queue_generation=queue_generation,
            lead_role_key=lead_role_key,
            lead_generation=lead_generation,
            checkpoint_digest=checkpoint_digest,
        )

    def submit_worker_candidate(
        self,
        *,
        task_id: str,
        queue_generation: str,
        worker_role_key: str,
        worker_generation: int,
        candidate_digest: str,
    ) -> tuple[MailboxEnvelope, MailboxEnvelope]:
        self._require_started()
        return self.controller.submit_worker_candidate(
            task_id=task_id,
            queue_generation=queue_generation,
            worker_role_key=worker_role_key,
            worker_generation=worker_generation,
            candidate_digest=candidate_digest,
        )

    def review_worker_candidate(
        self,
        *,
        task_id: str,
        queue_generation: str,
        worker_role_key: str,
        reviewer_role_key: str,
        reviewer_generation: int,
        candidate_digest: str,
        decision: ReviewDecision,
        reason_code: str,
    ) -> ReviewLoopReceipt:
        self._require_started()
        return self.controller.review_worker_candidate(
            task_id=task_id,
            queue_generation=queue_generation,
            worker_role_key=worker_role_key,
            reviewer_role_key=reviewer_role_key,
            reviewer_generation=reviewer_generation,
            candidate_digest=candidate_digest,
            decision=decision,
            reason_code=reason_code,
        )

    def _execute(self, mode: ServiceMode, events: Iterable[WorkflowEvent]) -> ServiceReceipt:
        if self._generation is None:
            raise ControllerServiceError("writer service must be started before execution")
        ordered = tuple(sorted(events, key=lambda item: item.sort_key))
        if not ordered:
            raise ControllerServiceError("controller execution requires at least one workflow event")
        profile_digest = self.execution_metadata.profile_digest
        input_digest = _digest(
            {
                "events": [json.loads(canonical_event_json(item)) for item in ordered],
                "execution_profile_digest": profile_digest,
            }
        )
        operation_id = "svc-" + _digest(
            {
                "input_digest": input_digest,
                "mode": mode.value,
                "execution_profile_digest": profile_digest,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT mode, input_digest, generation_sequence, generation_digest, execution_profile_digest FROM service_operation "
                "WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if operation is None:
                pending = connection.execute(
                    "SELECT operation_id FROM service_operation WHERE operation_id NOT IN "
                    "(SELECT operation_id FROM service_receipt) LIMIT 1"
                ).fetchone()
                if pending is not None:
                    connection.rollback()
                    raise ControllerServiceError("a prior operation requires exact recovery first")
                assert self._generation is not None
                connection.execute(
                    "INSERT INTO service_operation(operation_id, mode, input_digest, generation_sequence, generation_digest, created_at, execution_profile_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id, mode.value, input_digest, self._generation.sequence,
                        self._generation.digest, _now_text(), profile_digest,
                    ),
                )
                execution_generation = self._generation
            else:
                if (
                    operation["mode"] != mode.value
                    or operation["input_digest"] != input_digest
                    or operation["execution_profile_digest"] != profile_digest
                ):
                    connection.rollback()
                    raise ControllerServiceError("operation id was rebound to different service input")
                execution_generation = ControlGeneration(
                    int(operation["generation_sequence"]), str(operation["generation_digest"])
                )
            row = connection.execute(
                "SELECT payload, mode, input_digest, execution_profile_digest FROM service_receipt WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            connection.commit()
        if row is not None:
            if (
                row["mode"] != mode.value
                or row["input_digest"] != input_digest
                or row["execution_profile_digest"] != profile_digest
            ):
                raise ControllerServiceError("operation id was rebound to different service input")
            self._record_pm_activity("idle", active=True)
            return ServiceReceipt.from_dict(json.loads(row["payload"]))
        try:
            receipt = self.controller.pump(execution_generation, ordered)
        except BaseException:
            # The writer is still held, but its last direct lifecycle attempt
            # did not settle.  Surface that distinction without recording any
            # boundary payload or raw session identity.
            self._record_pm_activity("stalled", active=True)
            raise
        self._record_pm_activity("idle", active=True)
        service_receipt = ServiceReceipt(
            mode=mode,
            operation_id=operation_id,
            input_digest=input_digest,
            generation_sequence=execution_generation.sequence,
            generation_digest=execution_generation.digest,
            controller_receipt=receipt,
            execution_profile=self.execution_metadata.profile_name,
            execution_profile_digest=profile_digest,
            workspace_write_enabled=self.execution_metadata.workspace_write_enabled,
            mutation_observed=self.execution_metadata.mutation_observed,
            orca_used=self.execution_metadata.orca_used,
        )
        payload = _canonical(service_receipt.to_dict())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO service_receipt(operation_id, mode, input_digest, payload, completed_at, execution_profile_digest) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    operation_id, mode.value, input_digest, payload, _now_text(),
                    profile_digest,
                ),
            )
            persisted = connection.execute(
                "SELECT payload FROM service_receipt WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if persisted is None or persisted["payload"] != payload:
                connection.rollback()
                raise ControllerServiceError("concurrent service receipt differs")
            connection.commit()
        return service_receipt


ServiceFactory = Callable[[Path, str], WorkflowControllerService]


def load_service_factory(reference: str) -> ServiceFactory:
    """Resolve an explicitly configured factory without providing any default.

    The reference has the form ``module.submodule:callable``.  This keeps the
    deployment boundary injectable and makes a missing approved boundary fail
    closed instead of silently selecting a fake transport.
    """

    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ControllerServiceError("service factory must be module:callable")
    module_name, attribute = reference.split(":", 1)
    if not module_name or not attribute or not attribute.replace("_", "").isalnum():
        raise ControllerServiceError("service factory must be module:callable")
    from importlib import import_module

    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise ControllerServiceError("configured service factory is not callable")
    return factory  # type: ignore[return-value]
