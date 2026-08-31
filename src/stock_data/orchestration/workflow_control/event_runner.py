"""One-shot, durable-event supervisor for the canonical Python PM.

The runner deliberately observes only the read-only Queue adapter and the
identifier-free Listener journal generation.  It does not create roles, edit
Queue files, or select an alternate control root.  A persistent settlement
ledger makes the externally visible session wake retry-safe across process
interruptions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
from typing import Protocol

from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryError,
    CodexCliBoundary,
)
from stock_data.orchestration.workflow_control.production import (
    build_production_service,
    canonical_control_root,
    canonical_repository_root,
)
from stock_data.orchestration.workflow_control.queue_adapter import (
    QueueSnapshot,
    RequestQueueStatusAdapter,
)
from stock_data.orchestration.workflow_control.registry import RoleKind, RoleRecord, RoleState
from stock_data.orchestration.workflow_control.service import (
    ControllerServiceError,
    WorkflowControllerService,
    WriterLeaseConflict,
)


_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_LISTENER_BYTES = 4 * 1024 * 1024
_MAX_ATTEMPT_ROWS = 256
_ABSENT_DIGEST = "0" * 64
_RUNNER_IDENTITY = "python_pm_event_runner"
EVENT_WAKE_TIMEOUT_SECONDS = 600.0
EVENT_RUNNER_MAX_WAKES_PER_INVOCATION = 1
EVENT_RUNNER_EXECUTION_LIMIT_SECONDS = 900
_TERMINAL_OUTCOMES = frozenset({
    "woken", "progressed", "recovered", "unchanged", "already_running",
    "stale_identity", "failed",
})
_CHECKPOINT_FIELDS = frozenset({
    "event_id", "checkpoint_cursor", "conversation_id", "event_type",
    "intent_key", "listener_id", "received_at", "version",
})
_RECEIPT_FIELDS = frozenset({
    "event_id", "action_key", "event_type", "receipt_key", "route_kind",
    "sink", "status", "version",
})
_ROUTE_SINKS = {
    "goal_change": frozenset({"project_goal_receipt", "goal_to_new"}),
    "direct_pm": frozenset({"pm_mailbox"}),
    "bounded_new": frozenset({"bounded_new"}),
}


class EventRunnerError(RuntimeError):
    """Raised for a bounded, sanitized unattended-runner refusal."""


class QueueReader(Protocol):
    def read_snapshot(self, *, observed_at: datetime) -> QueueSnapshot: ...


ServiceFactory = Callable[[Path, str], WorkflowControllerService]
SessionOwnershipVerifier = Callable[[str, str], str]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventRunnerError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EventRunnerError(f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventRunnerError(f"{label} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class EventRunnerStatus:
    service_identity: str
    completed_generations: int
    pending_generations: int
    last_generation: str | None
    last_attempt: EventRunnerReceipt | None = None
    recovered_generations: int = 0


@dataclass(frozen=True, slots=True)
class EventReconciliationStatus:
    """Read-only exact-pin status for one failed event-runner generation."""

    material_generation: str
    attempt_receipt_digest: str
    state: str
    recovery_proof: str | None
    controller_writer_state: str
    pending_boundary_operations: int
    ready: bool

    def __post_init__(self) -> None:
        if (
            _DIGEST.fullmatch(self.material_generation) is None
            or _DIGEST.fullmatch(self.attempt_receipt_digest) is None
            or self.state not in {"pending_failed", "recovered"}
            or self.controller_writer_state not in {"idle", "active", "unknown"}
            or isinstance(self.pending_boundary_operations, bool)
            or not isinstance(self.pending_boundary_operations, int)
            or self.pending_boundary_operations < 0
            or not isinstance(self.ready, bool)
        ):
            raise EventRunnerError("event reconciliation status is invalid")
        if self.state == "pending_failed":
            if self.recovery_proof is not None or self.ready != (
                self.controller_writer_state == "idle"
                and self.pending_boundary_operations == 0
            ):
                raise EventRunnerError("event reconciliation status is invalid")
        elif (
            _DIGEST.fullmatch(self.recovery_proof or "") is None
            or self.ready
        ):
            raise EventRunnerError("event reconciliation status is invalid")


@dataclass(frozen=True, slots=True)
class EventRunnerReceipt:
    """Allowlisted local receipt; raw roles, sessions and errors never persist."""

    outcome: str
    material_generation: str
    target_count: int
    wake_receipt_digests: tuple[str, ...] = ()
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _TERMINAL_OUTCOMES:
            raise EventRunnerError("event runner outcome is invalid")
        if _DIGEST.fullmatch(self.material_generation) is None:
            raise EventRunnerError("event runner generation is invalid")
        if isinstance(self.target_count, bool) or not isinstance(self.target_count, int) or self.target_count < 0:
            raise EventRunnerError("event runner target count is invalid")
        if not isinstance(self.wake_receipt_digests, tuple) or any(
            not isinstance(item, str) or _DIGEST.fullmatch(item) is None
            for item in self.wake_receipt_digests
        ):
            raise EventRunnerError("event runner wake receipt is invalid")
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise EventRunnerError("event runner receipt digest is invalid")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "outcome": self.outcome,
            "material_generation": self.material_generation,
            "target_count": self.target_count,
            "wake_receipt_digests": list(self.wake_receipt_digests),
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value

    @classmethod
    def from_dict(cls, value: object) -> "EventRunnerReceipt":
        if not isinstance(value, dict) or set(value) != {
            "outcome", "material_generation", "target_count", "wake_receipt_digests", "receipt_digest",
        }:
            raise EventRunnerError("event runner receipt is malformed")
        outcome = value["outcome"]
        generation = value["material_generation"]
        target_count = value["target_count"]
        wake_receipts = value["wake_receipt_digests"]
        receipt_digest = value["receipt_digest"]
        if (
            not isinstance(outcome, str)
            or not isinstance(generation, str)
            or isinstance(target_count, bool)
            or not isinstance(target_count, int)
            or not isinstance(wake_receipts, list)
            or any(not isinstance(item, str) for item in wake_receipts)
            or not isinstance(receipt_digest, str)
        ):
            raise EventRunnerError("event runner receipt is malformed")
        return cls(outcome, generation, target_count, tuple(wake_receipts), receipt_digest)


@dataclass(frozen=True, slots=True)
class EventRunnerRecoveryReceipt:
    """Proof that one failed pending generation was preserved and superseded."""

    prior_generation: str
    prior_attempt_receipt_digest: str
    stranded_recovery_proof: str
    recovery_epoch: str
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or _DIGEST.fullmatch(value) is None
            for value in (
                self.prior_generation,
                self.prior_attempt_receipt_digest,
                self.stranded_recovery_proof,
                self.recovery_epoch,
            )
        ):
            raise EventRunnerError("event runner recovery receipt is invalid")
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise EventRunnerError("event runner recovery receipt digest is invalid")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, str]:
        value = {
            "prior_generation": self.prior_generation,
            "prior_attempt_receipt_digest": self.prior_attempt_receipt_digest,
            "stranded_recovery_proof": self.stranded_recovery_proof,
            "recovery_epoch": self.recovery_epoch,
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value


@dataclass(frozen=True, slots=True)
class _WakeTarget:
    role_key: str
    generation: int
    session_id: str

    @property
    def session_fingerprint(self) -> str:
        return hashlib.sha256(self.session_id.encode("utf-8")).hexdigest()

    def to_dict(self, *, status: str = "pending", receipt_digest: str | None = None) -> dict[str, object]:
        return {
            "role_key": self.role_key,
            "generation": self.generation,
            "session_fingerprint": self.session_fingerprint,
            "status": status,
            "wake_receipt_digest": receipt_digest,
        }

@dataclass(frozen=True, slots=True)
class _StoredTarget:
    role_key: str
    generation: int
    session_fingerprint: str
    status: str
    wake_receipt_digest: str | None

    @classmethod
    def from_dict(cls, value: object) -> "_StoredTarget":
        if not isinstance(value, dict) or set(value) != {
            "role_key", "generation", "session_fingerprint", "status", "wake_receipt_digest",
        }:
            raise EventRunnerError("event runner wake target is malformed")
        role_key, generation, fingerprint, status, receipt = (
            value.get("role_key"), value.get("generation"), value.get("session_fingerprint"),
            value.get("status"), value.get("wake_receipt_digest"),
        )
        if (
            not isinstance(role_key, str)
            or _OWNER.fullmatch(role_key) is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or not isinstance(fingerprint, str)
            or _DIGEST.fullmatch(fingerprint) is None
            or status not in {"pending", "completed"}
            or (status == "pending" and receipt is not None)
            or (
                status == "completed"
                and (not isinstance(receipt, str) or _DIGEST.fullmatch(receipt) is None)
            )
        ):
            raise EventRunnerError("event runner wake target is invalid")
        # Stored target data never retains a raw session id.  The live registry
        # supplies it again only after its hash and generation both preflight.
        return cls(role_key, generation, str(fingerprint), str(status), None if receipt is None else str(receipt))

    def to_dict(self) -> dict[str, object]:
        return {
            "role_key": self.role_key,
            "generation": self.generation,
            "session_fingerprint": self.session_fingerprint,
            "status": self.status,
            "wake_receipt_digest": self.wake_receipt_digest,
        }


class WorkflowEventRunner:
    """Consume one durable generation through one existing Python PM writer."""

    def __init__(
        self,
        repository_root: Path,
        *,
        owner_id: str,
        queue_reader: QueueReader | None = None,
        service_factory: ServiceFactory | None = None,
        session_ownership_verifier: SessionOwnershipVerifier | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None:
        if _OWNER.fullmatch(owner_id) is None:
            raise EventRunnerError("owner id must be a bounded identifier")
        original_root = Path(repository_root).absolute()
        self._reject_reparse_ancestry(original_root)
        self.repository_root = canonical_repository_root(original_root)
        self.control_root = canonical_control_root(self.repository_root)
        self._safe_control_path(self.control_root)
        self.owner_id = owner_id
        self.queue_reader = queue_reader or RequestQueueStatusAdapter(self.repository_root)
        self.service_factory = service_factory or self._production_service
        self.session_ownership_verifier = (
            session_ownership_verifier or self._production_session_ownership
        )
        self.now = now
        self._database = self.control_root / "workflow_event_runner.sqlite3"

    def _production_service(self, root: Path, owner_id: str) -> WorkflowControllerService:
        # This entry point wakes stored sessions; it never pumps Queue lifecycle events.
        from stock_data.orchestration.workflow_control.service import ServiceMode

        return build_production_service(
            root,
            owner_id,
            ServiceMode.RUN,
            timeout_seconds=EVENT_WAKE_TIMEOUT_SECONDS,
        )

    def _production_session_ownership(self, role_key: str, session_id: str) -> str:
        boundary = CodexCliBoundary(
            self.control_root / "codex_boundary.sqlite3",
            cwd=self.repository_root,
            sandbox_mode="workspace-write",
        )
        return boundary.assert_cli_owned_session(
            role_key=role_key,
            session_id=session_id,
        )

    @property
    def listener_journal_path(self) -> Path:
        return self.control_root / "listener_events.jsonl"

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            if callable(is_junction) and is_junction():
                return True
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError as error:
            raise EventRunnerError("control path metadata is unreadable") from error
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))

    @classmethod
    def _reject_reparse_ancestry(cls, path: Path) -> None:
        for boundary in (path, *path.parents):
            if cls._is_reparse(boundary):
                raise EventRunnerError("control path uses a reparse point")

    def _safe_control_path(self, path: Path, *, must_exist: bool = False) -> None:
        root = self.repository_root
        lexical = path.absolute()
        try:
            relative = lexical.relative_to(root)
        except ValueError as error:
            raise EventRunnerError("control path escapes the repository") from error
        current = root
        if self._is_reparse(current):
            raise EventRunnerError("control path uses a reparse point")
        for part in relative.parts:
            current = current / part
            if (current.exists() or current.is_symlink()) and self._is_reparse(current):
                raise EventRunnerError("control path uses a reparse point")
        candidate = lexical.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise EventRunnerError("control path escapes the repository") from error
        if must_exist and not path.exists():
            raise EventRunnerError("required control path is absent")

    def _connect(self) -> sqlite3.Connection:
        self._safe_control_path(self.control_root)
        self._safe_control_path(self._database)
        self.control_root.mkdir(parents=True, exist_ok=True)
        self._safe_control_path(self.control_root, must_exist=True)
        self._safe_control_path(self._database)
        connection = sqlite3.connect(self._database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS event_runner_generation("
            "material_generation TEXT PRIMARY KEY, state TEXT NOT NULL, target_json TEXT NOT NULL, "
            "target_digest TEXT NOT NULL, receipt_json TEXT, receipt_digest TEXT, "
            "created_at TEXT NOT NULL, completed_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS event_runner_attempt("
            "attempt_id TEXT PRIMARY KEY, material_generation TEXT NOT NULL, receipt_json TEXT NOT NULL, "
            "receipt_digest TEXT NOT NULL, recorded_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS event_runner_recovery("
            "prior_generation TEXT PRIMARY KEY, prior_attempt_receipt_digest TEXT NOT NULL, "
            "stranded_recovery_proof TEXT NOT NULL, recovery_epoch TEXT NOT NULL UNIQUE, "
            "receipt_json TEXT NOT NULL, receipt_digest TEXT NOT NULL, recovered_at TEXT NOT NULL)"
        )
        return connection

    def status(self) -> EventRunnerStatus:
        self._safe_control_path(self.control_root)
        self._safe_control_path(self._database)
        if not self._database.is_file():
            return EventRunnerStatus(_RUNNER_IDENTITY, 0, 0, None)
        self._safe_control_path(self._database, must_exist=True)
        database_uri = self._database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT material_generation, state, target_json, target_digest, receipt_json, "
                "receipt_digest, created_at, completed_at FROM event_runner_generation "
                "ORDER BY rowid DESC"
            ).fetchall()
            attempt = connection.execute(
                "SELECT attempt_id, material_generation, receipt_json, receipt_digest, recorded_at "
                "FROM event_runner_attempt ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            has_recovery_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'event_runner_recovery'"
            ).fetchone()
            recovery_rows = (
                connection.execute(
                    "SELECT prior_generation, prior_attempt_receipt_digest, "
                    "stranded_recovery_proof, recovery_epoch, receipt_json, "
                    "receipt_digest, recovered_at FROM event_runner_recovery "
                    "ORDER BY rowid"
                ).fetchall()
                if has_recovery_table is not None
                else []
            )
        for row in rows:
            self._validate_generation_row(row)
        last_attempt = None
        if attempt is not None:
            attempt_id = attempt["attempt_id"]
            material_generation = attempt["material_generation"]
            if (
                not isinstance(attempt_id, str)
                or not attempt_id.startswith("attempt-")
                or not isinstance(material_generation, str)
                or _DIGEST.fullmatch(material_generation) is None
            ):
                raise EventRunnerError("event runner attempt receipt is corrupt")
            _aware_timestamp(attempt["recorded_at"], "event runner attempt timestamp")
            last_attempt = self._receipt_from_columns(
                attempt["receipt_json"], attempt["receipt_digest"], "event runner attempt receipt"
            )
            if last_attempt.material_generation != material_generation:
                raise EventRunnerError("event runner attempt receipt is corrupt")
        for recovery in recovery_rows:
            self._validate_recovery_row(recovery)
        return EventRunnerStatus(
            service_identity=_RUNNER_IDENTITY,
            completed_generations=sum(row["state"] == "completed" for row in rows),
            pending_generations=sum(row["state"] == "pending" for row in rows),
            last_generation=None if not rows else str(rows[0]["material_generation"]),
            last_attempt=last_attempt,
            recovered_generations=len(recovery_rows),
        )

    def reconciliation_status(
        self,
        *,
        material_generation: str,
        expected_attempt_receipt_digest: str,
    ) -> EventReconciliationStatus:
        """Inspect one exact failed generation without creating runner state.

        This makes the recovery precondition public while preserving the
        immutable failed attempt, instead of inferring readiness from the
        newest runner row or a human-readable error message.
        """

        if (
            _DIGEST.fullmatch(material_generation) is None
            or _DIGEST.fullmatch(expected_attempt_receipt_digest) is None
        ):
            raise EventRunnerError("event reconciliation pins are invalid")
        self._safe_control_path(self.control_root)
        self._safe_control_path(self._database, must_exist=True)
        database_uri = self._database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            generations = connection.execute(
                "SELECT material_generation, state, target_json, target_digest, "
                "receipt_json, receipt_digest, created_at, completed_at "
                "FROM event_runner_generation WHERE material_generation = ?",
                (material_generation,),
            ).fetchall()
            attempts = connection.execute(
                "SELECT receipt_json, receipt_digest FROM event_runner_attempt "
                "WHERE material_generation = ? AND receipt_digest = ? "
                "ORDER BY rowid",
                (material_generation, expected_attempt_receipt_digest),
            ).fetchall()
            recovery = connection.execute(
                "SELECT prior_generation, prior_attempt_receipt_digest, "
                "stranded_recovery_proof, recovery_epoch, receipt_json, "
                "receipt_digest, recovered_at FROM event_runner_recovery "
                "WHERE prior_generation = ?",
                (material_generation,),
            ).fetchone()
        # A scheduled retry can persist the same deterministic failed receipt
        # more than once while the stored material generation remains pending.
        # The public exact pin identifies that immutable receipt, not its
        # transient row count.  Still validate every matching row before
        # accepting the status so distinct/corrupt evidence cannot be masked.
        if len(generations) != 1 or not attempts:
            raise EventRunnerError("event reconciliation generation or attempt is absent or ambiguous")
        generation = generations[0]
        self._validate_generation_row(generation)
        for attempt in attempts:
            failed = self._receipt_from_columns(
                attempt["receipt_json"], attempt["receipt_digest"],
                "event reconciliation attempt receipt",
            )
            if (
                failed.outcome != "failed"
                or failed.material_generation != material_generation
                or failed.receipt_digest != expected_attempt_receipt_digest
            ):
                raise EventRunnerError("event reconciliation attempt pin changed")
        service_status = WorkflowControllerService.inspect(self.control_root)
        writer_state = (
            "active" if service_status.active else str(service_status.writer_state)
        )
        if recovery is None:
            if generation["state"] != "pending":
                raise EventRunnerError("event reconciliation generation changed")
            return EventReconciliationStatus(
                material_generation=material_generation,
                attempt_receipt_digest=expected_attempt_receipt_digest,
                state="pending_failed",
                recovery_proof=None,
                controller_writer_state=writer_state,
                pending_boundary_operations=service_status.pending_boundary_operations,
                ready=(
                    writer_state == "idle"
                    and service_status.pending_boundary_operations == 0
                ),
            )
        settled = self._validate_recovery_row(recovery)
        if (
            generation["state"] != "completed"
            or settled.prior_attempt_receipt_digest != expected_attempt_receipt_digest
        ):
            raise EventRunnerError("event reconciliation recovery pin changed")
        return EventReconciliationStatus(
            material_generation=material_generation,
            attempt_receipt_digest=expected_attempt_receipt_digest,
            state="recovered",
            recovery_proof=settled.stranded_recovery_proof,
            controller_writer_state=writer_state,
            pending_boundary_operations=service_status.pending_boundary_operations,
            ready=False,
        )

    @staticmethod
    def _validate_recovery_row(row: sqlite3.Row) -> EventRunnerRecoveryReceipt:
        try:
            value = json.loads(str(row["receipt_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise EventRunnerError("event runner recovery receipt is corrupt") from error
        if not isinstance(value, dict) or _canonical(value) != row["receipt_json"]:
            raise EventRunnerError("event runner recovery receipt is corrupt")
        receipt = EventRunnerRecoveryReceipt(
            prior_generation=str(value.get("prior_generation", "")),
            prior_attempt_receipt_digest=str(
                value.get("prior_attempt_receipt_digest", "")
            ),
            stranded_recovery_proof=str(value.get("stranded_recovery_proof", "")),
            recovery_epoch=str(value.get("recovery_epoch", "")),
            receipt_digest=str(value.get("receipt_digest", "")),
        )
        if (
            set(value) != set(receipt.to_dict())
            or row["prior_generation"] != receipt.prior_generation
            or row["prior_attempt_receipt_digest"]
            != receipt.prior_attempt_receipt_digest
            or row["stranded_recovery_proof"] != receipt.stranded_recovery_proof
            or row["recovery_epoch"] != receipt.recovery_epoch
            or row["receipt_digest"] != receipt.receipt_digest
        ):
            raise EventRunnerError("event runner recovery receipt is corrupt")
        _aware_timestamp(row["recovered_at"], "event runner recovery timestamp")
        return receipt

    @staticmethod
    def _receipt_from_columns(value: object, digest: object, label: str) -> EventRunnerReceipt:
        if not isinstance(value, str) or not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise EventRunnerError(f"{label} is corrupt")
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise EventRunnerError(f"{label} is corrupt") from error
        if _canonical(parsed) != value:
            raise EventRunnerError(f"{label} is corrupt")
        receipt = EventRunnerReceipt.from_dict(parsed)
        if receipt.receipt_digest != digest:
            raise EventRunnerError(f"{label} is corrupt")
        return receipt

    @classmethod
    def _validate_generation_row(cls, row: sqlite3.Row) -> None:
        generation = row["material_generation"]
        state = row["state"]
        if not isinstance(generation, str) or _DIGEST.fullmatch(generation) is None:
            raise EventRunnerError("event runner generation state is corrupt")
        if state not in {"pending", "completed"}:
            raise EventRunnerError("event runner generation state is corrupt")
        targets = cls._stored_targets(row["target_json"], row["target_digest"])
        _aware_timestamp(row["created_at"], "event runner generation timestamp")
        if state == "pending":
            if row["receipt_json"] is not None or row["receipt_digest"] is not None or row["completed_at"] is not None:
                raise EventRunnerError("event runner pending receipt is corrupt")
            return
        receipt = cls._receipt_from_columns(
            row["receipt_json"], row["receipt_digest"], "event runner generation receipt"
        )
        _aware_timestamp(row["completed_at"], "event runner completion timestamp")
        if receipt.material_generation != generation or receipt.target_count > len(targets):
            raise EventRunnerError("event runner generation receipt is corrupt")

    def _listener_generation(self) -> str:
        path = self.listener_journal_path
        self._safe_control_path(self.control_root)
        self._safe_control_path(path)
        if not path.exists():
            return _ABSENT_DIGEST
        self._safe_control_path(path, must_exist=True)
        if path.is_symlink() or self._is_reparse(path) or not path.is_file():
            raise EventRunnerError("listener journal is not a regular file")
        if path.stat().st_size > _MAX_LISTENER_BYTES:
            raise EventRunnerError("listener journal exceeds the bounded observation limit")
        canonical: list[str] = []
        with path.open("rb") as handle:
            for raw in handle.read().splitlines(keepends=True):
                if not raw.endswith(b"\n"):
                    raise EventRunnerError("listener journal line is incomplete")
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise EventRunnerError("listener journal is malformed") from error
                canonical.append(self._validate_listener_event(value))
        return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_listener_event(value: object) -> str:
        if not isinstance(value, dict):
            raise EventRunnerError("listener journal event is malformed")
        event_type = value.get("event_type")
        fields = set(value)
        if event_type == "checkpoint":
            if fields != _CHECKPOINT_FIELDS:
                raise EventRunnerError("listener checkpoint schema is invalid")
            for field in ("checkpoint_cursor", "conversation_id", "listener_id", "received_at"):
                if not isinstance(value[field], str) or not value[field].strip():
                    raise EventRunnerError("listener checkpoint schema is invalid")
            if not isinstance(value["intent_key"], str) or _DIGEST.fullmatch(value["intent_key"]) is None:
                raise EventRunnerError("listener checkpoint schema is invalid")
        elif event_type == "receipt_state":
            status_value = value.get("status")
            expected_fields = _RECEIPT_FIELDS | ({"acceptance_ref_sha256"} if status_value == "accepted" else set())
            if fields != expected_fields or status_value not in {"pending", "accepted"}:
                raise EventRunnerError("listener receipt schema is invalid")
            for field in ("action_key", "receipt_key"):
                if not isinstance(value[field], str) or _DIGEST.fullmatch(value[field]) is None:
                    raise EventRunnerError("listener receipt schema is invalid")
            route_kind, sink = value["route_kind"], value["sink"]
            if not isinstance(route_kind, str) or not isinstance(sink, str) or sink not in _ROUTE_SINKS.get(route_kind, ()):
                raise EventRunnerError("listener receipt schema is invalid")
            if status_value == "accepted" and (
                not isinstance(value["acceptance_ref_sha256"], str)
                or _DIGEST.fullmatch(value["acceptance_ref_sha256"]) is None
            ):
                raise EventRunnerError("listener receipt schema is invalid")
        else:
            raise EventRunnerError("listener journal event type is invalid")
        if isinstance(value.get("version"), bool) or value.get("version") != 1:
            raise EventRunnerError("listener journal version is invalid")
        event_id = value.get("event_id")
        if not isinstance(event_id, str) or _DIGEST.fullmatch(event_id) is None:
            raise EventRunnerError("listener journal event digest is invalid")
        body = dict(value)
        body.pop("event_id")
        try:
            encoded = json.dumps(
                body, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
            )
        except (TypeError, ValueError) as error:
            raise EventRunnerError("listener journal event is malformed") from error
        expected = hashlib.sha256(("listener-journal/v1\n" + encoded).encode("utf-8")).hexdigest()
        if event_id != expected:
            raise EventRunnerError("listener journal event digest is invalid")
        return json.dumps(
            {"event_id": event_id, **json.loads(encoded)},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _queue_generation(snapshot: QueueSnapshot) -> str:
        return _digest({
            "state_counts": list(snapshot.state_counts),
            "active_task_ids": list(snapshot.active_task_ids),
            "compacted_count": snapshot.compacted_count,
            "current_tasks": [
                {
                    "task_id": item.task_id,
                    "state": item.state,
                    "owner": item.owner,
                    "lead_owner": item.lead_owner,
                    "reviewer": item.reviewer,
                    "domain": item.domain,
                    "title": item.title,
                }
                for item in snapshot.current_tasks
            ],
        })

    def _recovery_epoch(self) -> str | None:
        if not self._database.is_file():
            return None
        database_uri = self._database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(database_uri, uri=True, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'event_runner_recovery'"
            ).fetchone() is None:
                return None
            row = connection.execute(
                "SELECT prior_generation, prior_attempt_receipt_digest, "
                "stranded_recovery_proof, recovery_epoch, receipt_json, "
                "receipt_digest, recovered_at FROM event_runner_recovery "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self._validate_recovery_row(row).recovery_epoch

    def _material_generation(self, snapshot: QueueSnapshot) -> str:
        base_generation = _digest({
            "queue_generation": self._queue_generation(snapshot),
            "listener_generation": self._listener_generation(),
        })
        recovery_epoch = self._recovery_epoch()
        if recovery_epoch is None:
            return base_generation
        return _digest({
            "base_generation": base_generation,
            "recovery_epoch": recovery_epoch,
        })

    def _targets(self, service: WorkflowControllerService, snapshot: QueueSnapshot) -> tuple[_WakeTarget, ...]:
        records = {record.identity.role_key: record for record in service.controller.role_registry.records()}
        pm = records.get("project_manager")
        if pm is None or pm.identity.role_kind is not RoleKind.PROJECT_MANAGER:
            raise EventRunnerError("stored project manager identity is unavailable")
        if pm.state not in {RoleState.ACTIVE, RoleState.IDLE}:
            raise EventRunnerError("stored project manager identity is unavailable")
        lead_keys = tuple(sorted({
            item.lead_owner for item in snapshot.current_tasks if item.state in {"active", "review"}
        }))
        selected: list[RoleRecord] = [pm]
        for lead_key in lead_keys:
            lead = records.get(lead_key)
            if (
                lead is None
                or lead.identity.role_kind is not RoleKind.DOMAIN_LEAD
                or lead.state not in {RoleState.ACTIVE, RoleState.IDLE}
            ):
                raise EventRunnerError("stored routed lead identity is unavailable")
            selected.append(lead)
        targets = tuple(
            _WakeTarget(item.identity.role_key, item.generation, item.identity.codex_session_id)
            for item in selected
        )
        for target in targets:
            proof = self.session_ownership_verifier(
                target.role_key, target.session_id,
            )
            if not isinstance(proof, str) or _DIGEST.fullmatch(proof) is None:
                raise EventRunnerError("CLI session ownership proof is invalid")
        return targets

    @staticmethod
    def _stored_targets(value: object, digest: object) -> tuple[_StoredTarget, ...]:
        if not isinstance(value, str) or not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise EventRunnerError("event runner target settlement is corrupt")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise EventRunnerError("event runner target settlement is corrupt") from error
        if not isinstance(parsed, list) or _canonical(parsed) != value or _digest(parsed) != digest:
            raise EventRunnerError("event runner target settlement is corrupt")
        targets = tuple(_StoredTarget.from_dict(item) for item in parsed)
        if not targets or len({item.role_key for item in targets}) != len(targets):
            raise EventRunnerError("event runner target settlement is corrupt")
        return targets

    def _preflight_targets(self, service: WorkflowControllerService, stored: tuple[_StoredTarget, ...]) -> tuple[_WakeTarget, ...]:
        records = {item.identity.role_key: item for item in service.controller.role_registry.records()}
        current: list[_WakeTarget] = []
        for item in stored:
            if item.status == "completed":
                continue
            record = records.get(item.role_key)
            if (record is None or record.state not in {RoleState.ACTIVE, RoleState.IDLE} or
                record.generation != item.generation or
                hashlib.sha256(record.identity.codex_session_id.encode("utf-8")).hexdigest() != item.session_fingerprint):
                raise EventRunnerError("stored role identity changed")
            target = _WakeTarget(
                item.role_key, item.generation, record.identity.codex_session_id,
            )
            proof = self.session_ownership_verifier(
                target.role_key, target.session_id,
            )
            if not isinstance(proof, str) or _DIGEST.fullmatch(proof) is None:
                raise EventRunnerError("CLI session ownership proof is invalid")
            current.append(target)
        return tuple(current)

    def _record_role_activity(
        self, service: WorkflowControllerService, target: _WakeTarget,
    ) -> None:
        """Refresh a role heartbeat only after its wake is durably settled."""

        registry = service.controller.role_registry
        heartbeat = getattr(registry, "heartbeat", None)
        if not callable(heartbeat):
            return
        observed_at = self.now()
        heartbeat(
            target.role_key,
            expected_generation=target.generation,
            observed_at=observed_at,
            lease_until=observed_at + timedelta(hours=24),
        )

    def _reserve(self, generation: str, service: WorkflowControllerService, snapshot: QueueSnapshot) -> tuple[str, tuple[_StoredTarget, ...], EventRunnerReceipt | None]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state, target_json, target_digest, receipt_json, receipt_digest FROM event_runner_generation "
                    "WHERE material_generation = ?", (generation,)
                ).fetchone()
                if row is not None and row["state"] not in {"pending", "completed"}:
                    raise EventRunnerError("event runner generation state is corrupt")
                if row is not None and row["state"] == "completed":
                    receipt = self._receipt_from_columns(
                        row["receipt_json"], row["receipt_digest"], "event runner receipt"
                    )
                    if receipt.material_generation != generation:
                        raise EventRunnerError("event runner receipt is corrupt")
                    connection.commit()
                    return "completed", (), receipt
                if row is not None:
                    targets = self._stored_targets(row["target_json"], row["target_digest"])
                    connection.commit()
                    return "pending", targets, None
                targets = self._targets(service, snapshot)
                stored = tuple(_StoredTarget(
                    item.role_key, item.generation, item.session_fingerprint, "pending", None
                ) for item in targets)
                target_json = _canonical([item.to_dict() for item in stored])
                connection.execute(
                    "INSERT INTO event_runner_generation("
                    "material_generation, state, target_json, target_digest, receipt_json, receipt_digest, created_at, completed_at) "
                    "VALUES (?, 'pending', ?, ?, NULL, NULL, ?, NULL)",
                    (generation, target_json, _digest(json.loads(target_json)), self.now().isoformat()),
                )
                connection.commit()
                return "new", stored, None
            except BaseException:
                connection.rollback()
                raise

    def _completed(self, generation: str) -> EventRunnerReceipt | None:
        """Read a completed settlement before acquiring a new PM writer lease."""

        self._safe_control_path(self.control_root)
        self._safe_control_path(self._database)
        if not self._database.is_file():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_json, receipt_digest FROM event_runner_generation "
                "WHERE material_generation = ? AND state = 'completed'",
                (generation,),
            ).fetchone()
        if row is None:
            return None
        receipt = self._receipt_from_columns(row["receipt_json"], row["receipt_digest"], "event runner receipt")
        if receipt.material_generation != generation:
            raise EventRunnerError("event runner receipt is corrupt")
        return receipt

    def _oldest_pending_generation(self) -> str | None:
        """Return the oldest interrupted generation before observing newer work.

        A wake can change Queue state before every routed role has settled.  If
        the next scheduler tick selected only the new material generation, the
        remaining old Lead wake would be orphaned forever.  Drain one durable
        pending generation first; a following tick then handles current state.
        """

        self._safe_control_path(self.control_root)
        self._safe_control_path(self._database)
        if not self._database.is_file():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT material_generation FROM event_runner_generation "
                "WHERE state = 'pending' ORDER BY rowid LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        generation = row["material_generation"]
        if not isinstance(generation, str) or _DIGEST.fullmatch(generation) is None:
            raise EventRunnerError("event runner generation state is corrupt")
        return generation

    def _complete(self, receipt: EventRunnerReceipt) -> EventRunnerReceipt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state, receipt_json, receipt_digest FROM event_runner_generation WHERE material_generation = ?",
                    (receipt.material_generation,),
                ).fetchone()
                if row is None:
                    raise EventRunnerError("event runner settlement disappeared")
                if row["state"] not in {"pending", "completed"}:
                    raise EventRunnerError("event runner generation state is corrupt")
                if row["state"] == "completed":
                    existing = self._receipt_from_columns(
                        row["receipt_json"], row["receipt_digest"], "event runner receipt"
                    )
                    connection.commit()
                    return existing
                connection.execute(
                    "UPDATE event_runner_generation SET state = 'completed', receipt_json = ?, receipt_digest = ?, completed_at = ? "
                    "WHERE material_generation = ? AND state = 'pending'",
                    (_canonical(receipt.to_dict()), receipt.receipt_digest, self.now().isoformat(), receipt.material_generation),
                )
                connection.commit()
                return receipt
            except BaseException:
                connection.rollback()
                raise

    def _record_attempt(self, receipt: EventRunnerReceipt) -> None:
        recorded_at = self.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                sequence = int(connection.execute(
                    "SELECT COALESCE(MAX(rowid), 0) + 1 FROM event_runner_attempt"
                ).fetchone()[0])
                attempt_id = "attempt-" + _digest({
                    "receipt_digest": receipt.receipt_digest,
                    "recorded_at": recorded_at,
                    "sequence": sequence,
                })
                connection.execute(
                    "INSERT INTO event_runner_attempt(attempt_id, material_generation, receipt_json, receipt_digest, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (attempt_id, receipt.material_generation, _canonical(receipt.to_dict()), receipt.receipt_digest, recorded_at),
                )
                connection.execute(
                    "DELETE FROM event_runner_attempt WHERE rowid NOT IN "
                    "(SELECT rowid FROM event_runner_attempt ORDER BY rowid DESC LIMIT ?)",
                    (_MAX_ATTEMPT_ROWS,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _settle_target(self, generation: str, role_key: str, receipt_digest: str) -> tuple[_StoredTarget, ...]:
        if not isinstance(receipt_digest, str) or _DIGEST.fullmatch(receipt_digest) is None:
            raise EventRunnerError("event runner wake receipt is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT target_json, target_digest FROM event_runner_generation WHERE material_generation = ? AND state = 'pending'",
                    (generation,),
                ).fetchone()
                if row is None:
                    raise EventRunnerError("event runner pending settlement disappeared")
                stored = self._stored_targets(row["target_json"], row["target_digest"])
                updated = tuple(
                    _StoredTarget(item.role_key, item.generation, item.session_fingerprint, "completed", receipt_digest)
                    if item.role_key == role_key and item.status == "pending" else item
                    for item in stored
                )
                if updated == stored:
                    raise EventRunnerError("event runner target settlement is invalid")
                target_json = _canonical([item.to_dict() for item in updated])
                connection.execute(
                    "UPDATE event_runner_generation SET target_json = ?, target_digest = ? WHERE material_generation = ?",
                    (target_json, _digest(json.loads(target_json)), generation),
                )
                connection.commit()
                return updated
            except BaseException:
                connection.rollback()
                raise

    def migrate_pending_role_identity(
        self,
        *,
        role_key: str,
        expected_generation: int,
        expected_session_fingerprint: str,
        cli_record: RoleRecord,
    ) -> int:
        """Rebind only unsettled durable targets during exact CLI migration."""

        if (
            _OWNER.fullmatch(role_key) is None
            or isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 1
            or _DIGEST.fullmatch(expected_session_fingerprint) is None
            or cli_record.identity.role_key != role_key
            or cli_record.generation <= expected_generation
            or cli_record.identity.runtime_id != "codex-cli-owned-v1"
        ):
            raise EventRunnerError("pending role migration identity is invalid")
        proof = self.session_ownership_verifier(
            role_key, cli_record.identity.codex_session_id,
        )
        if not isinstance(proof, str) or _DIGEST.fullmatch(proof) is None:
            raise EventRunnerError("CLI session ownership proof is invalid")
        new_target = _StoredTarget(
            role_key,
            cli_record.generation,
            hashlib.sha256(
                cli_record.identity.codex_session_id.encode("utf-8")
            ).hexdigest(),
            "pending",
            None,
        )
        if not self._database.is_file():
            return 0
        updated_rows = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT material_generation, target_json, target_digest "
                    "FROM event_runner_generation WHERE state = 'pending' ORDER BY rowid"
                ).fetchall()
                for row in rows:
                    targets = self._stored_targets(
                        row["target_json"], row["target_digest"],
                    )
                    matches = [
                        item for item in targets if item.role_key == role_key
                    ]
                    if not matches:
                        continue
                    if len(matches) != 1:
                        raise EventRunnerError(
                            "pending role migration target is ambiguous"
                        )
                    old = matches[0]
                    if old.status == "completed":
                        continue
                    if (
                        old.generation == new_target.generation
                        and old.session_fingerprint
                        == new_target.session_fingerprint
                    ):
                        continue
                    if (
                        old.generation != expected_generation
                        or old.session_fingerprint
                        != expected_session_fingerprint
                    ):
                        raise EventRunnerError(
                            "pending role migration target changed"
                        )
                    migrated = tuple(
                        new_target if item.role_key == role_key else item
                        for item in targets
                    )
                    target_json = _canonical(
                        [item.to_dict() for item in migrated]
                    )
                    connection.execute(
                        "UPDATE event_runner_generation SET target_json = ?, "
                        "target_digest = ? WHERE material_generation = ? "
                        "AND state = 'pending'",
                        (
                            target_json,
                            _digest(json.loads(target_json)),
                            row["material_generation"],
                        ),
                    )
                    updated_rows += 1
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return updated_rows

    def recover_pending_generation(
        self,
        *,
        material_generation: str,
        expected_attempt_receipt_digest: str,
        recovery_proof: str | None = None,
        stranded_recovery_proof: str | None = None,
    ) -> EventRunnerRecoveryReceipt:
        """Preserve one failed generation and rotate the material namespace.

        A fresh generation is impossible until the exact service/boundary
        recovery proof exists, the writer is idle, and no boundary operation is
        pending.  Completed target receipts and every prior attempt remain
        immutable evidence.
        """

        supplied_proofs = tuple(
            item for item in (recovery_proof, stranded_recovery_proof)
            if item is not None
        )
        if (
            len(supplied_proofs) != 1
            or _DIGEST.fullmatch(material_generation) is None
            or _DIGEST.fullmatch(expected_attempt_receipt_digest) is None
            or _DIGEST.fullmatch(supplied_proofs[0]) is None
        ):
            raise EventRunnerError("event runner recovery pins are invalid")
        verified_recovery_proof = supplied_proofs[0]
        WorkflowControllerService.assert_event_recovery_proof(
            self.control_root,
            recovery_proof=verified_recovery_proof,
        )
        service_status = WorkflowControllerService.inspect(self.control_root)
        if (
            service_status.active
            or service_status.writer_state != "idle"
            or service_status.pending_boundary_operations != 0
        ):
            raise EventRunnerError(
                "event runner recovery requires an idle reconciled controller"
            )
        recovered_at = self.now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT prior_generation, prior_attempt_receipt_digest, "
                    "stranded_recovery_proof, recovery_epoch, receipt_json, "
                    "receipt_digest, recovered_at FROM event_runner_recovery "
                    "WHERE prior_generation = ?",
                    (material_generation,),
                ).fetchone()
                if existing is not None:
                    receipt = self._validate_recovery_row(existing)
                    if (
                        receipt.prior_attempt_receipt_digest
                        != expected_attempt_receipt_digest
                        or receipt.stranded_recovery_proof
                        != verified_recovery_proof
                    ):
                        raise EventRunnerError(
                            "event runner recovery replay pins changed"
                        )
                    connection.commit()
                    return receipt
                pending = connection.execute(
                    "SELECT material_generation, target_json, target_digest "
                    "FROM event_runner_generation WHERE state = 'pending' "
                    "ORDER BY rowid"
                ).fetchall()
                if (
                    len(pending) != 1
                    or pending[0]["material_generation"] != material_generation
                ):
                    raise EventRunnerError(
                        "event runner recovery requires one exact pending generation"
                    )
                targets = self._stored_targets(
                    pending[0]["target_json"], pending[0]["target_digest"]
                )
                attempt = connection.execute(
                    "SELECT receipt_json, receipt_digest FROM event_runner_attempt "
                    "WHERE material_generation = ? ORDER BY rowid DESC LIMIT 1",
                    (material_generation,),
                ).fetchone()
                if attempt is None:
                    raise EventRunnerError(
                        "event runner failed attempt evidence is absent"
                    )
                failed = self._receipt_from_columns(
                    attempt["receipt_json"],
                    attempt["receipt_digest"],
                    "event runner failed attempt receipt",
                )
                if (
                    failed.outcome != "failed"
                    or failed.receipt_digest != expected_attempt_receipt_digest
                ):
                    raise EventRunnerError(
                        "event runner failed attempt pin changed"
                    )
                prior_epoch_row = connection.execute(
                    "SELECT recovery_epoch FROM event_runner_recovery "
                    "ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                prior_epoch = (
                    _ABSENT_DIGEST
                    if prior_epoch_row is None
                    else str(prior_epoch_row["recovery_epoch"])
                )
                recovery_epoch = _digest({
                    "material_generation": material_generation,
                    "prior_attempt_receipt_digest": expected_attempt_receipt_digest,
                    "prior_recovery_epoch": prior_epoch,
                    "stranded_recovery_proof": verified_recovery_proof,
                })
                recovery = EventRunnerRecoveryReceipt(
                    prior_generation=material_generation,
                    prior_attempt_receipt_digest=expected_attempt_receipt_digest,
                    stranded_recovery_proof=verified_recovery_proof,
                    recovery_epoch=recovery_epoch,
                )
                wake_digests = tuple(
                    item.wake_receipt_digest
                    for item in targets
                    if item.status == "completed"
                    and item.wake_receipt_digest is not None
                )
                completion = EventRunnerReceipt(
                    "recovered",
                    material_generation,
                    len(targets),
                    wake_digests,
                )
                changed = connection.execute(
                    "UPDATE event_runner_generation SET state = 'completed', "
                    "receipt_json = ?, receipt_digest = ?, completed_at = ? "
                    "WHERE material_generation = ? AND state = 'pending'",
                    (
                        _canonical(completion.to_dict()),
                        completion.receipt_digest,
                        recovered_at,
                        material_generation,
                    ),
                ).rowcount
                if changed != 1:
                    raise EventRunnerError(
                        "event runner generation changed during recovery"
                    )
                connection.execute(
                    "INSERT INTO event_runner_recovery("
                    "prior_generation, prior_attempt_receipt_digest, "
                    "stranded_recovery_proof, recovery_epoch, receipt_json, "
                    "receipt_digest, recovered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        material_generation,
                        expected_attempt_receipt_digest,
                        verified_recovery_proof,
                        recovery_epoch,
                        _canonical(recovery.to_dict()),
                        recovery.receipt_digest,
                        recovered_at,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._record_attempt(completion)
        return recovery

    def run_once(self) -> EventRunnerReceipt:
        observed_at = self.now()
        try:
            snapshot = self.queue_reader.read_snapshot(observed_at=observed_at)
            generation = self._material_generation(snapshot)
            pending_generation = self._oldest_pending_generation()
            if pending_generation is not None:
                generation = pending_generation
        except (EventRunnerError, ValueError, sqlite3.Error):
            # A corrupt external durable input is never interpreted as a wake.
            # Its opaque fixed generation lets status retain a sanitized failure.
            generation = _digest({"runner": _RUNNER_IDENTITY, "input": "invalid"})
            receipt = EventRunnerReceipt("failed", generation, 0)
            try:
                self._record_attempt(receipt)
            except (EventRunnerError, sqlite3.Error):
                pass
            return receipt
        try:
            completed = self._completed(generation)
        except (EventRunnerError, sqlite3.Error):
            return EventRunnerReceipt("failed", generation, 0)
        if completed is not None:
            receipt = EventRunnerReceipt("unchanged", generation, completed.target_count)
            self._record_attempt(receipt)
            return receipt
        try:
            service = self.service_factory(self.repository_root, self.owner_id)
            service.start()
        except WriterLeaseConflict:
            receipt = EventRunnerReceipt("already_running", generation, 0)
            self._record_attempt(receipt)
            return receipt
        except ControllerServiceError:
            receipt = EventRunnerReceipt("failed", generation, 0)
            self._record_attempt(receipt)
            return receipt
        try:
            state, targets, cached = self._reserve(generation, service, snapshot)
            if state == "completed":
                assert cached is not None
                receipt = EventRunnerReceipt("unchanged", generation, cached.target_count)
                self._record_attempt(receipt)
                return receipt
            live_targets = self._preflight_targets(service, targets)
            settled = targets
            for target in live_targets[:EVENT_RUNNER_MAX_WAKES_PER_INVOCATION]:
                stored = next(item for item in settled if item.role_key == target.role_key)
                if stored.status == "completed":
                    continue
                wake_receipt = service.wake_role_session(
                    role_key=target.role_key,
                    expected_generation=target.generation,
                    expected_session_id=target.session_id,
                    source_event_id=generation,
                )
                settled = self._settle_target(generation, target.role_key, wake_receipt)
                self._record_role_activity(service, target)
            wake_digests = tuple(
                item.wake_receipt_digest
                for item in settled
                if item.wake_receipt_digest is not None
            )
            if any(item.status == "pending" for item in settled):
                receipt = EventRunnerReceipt(
                    "progressed", generation, len(targets), wake_digests
                )
                self._record_attempt(receipt)
                return receipt
            receipt = self._complete(EventRunnerReceipt(
                "woken", generation, len(targets), wake_digests,
            ))
            self._record_attempt(receipt)
            return receipt
        except EventRunnerError:
            # A missing identity can be discovered before a generation is
            # reserved.  Returning a bounded no-op leaves no synthetic role or
            # durable lifecycle mutation behind; a later material generation
            # can resolve the stored hierarchy afresh.
            receipt = EventRunnerReceipt("stale_identity", generation, 0)
            try:
                self._complete(receipt)
            except (EventRunnerError, sqlite3.Error):
                pass
            self._record_attempt(receipt)
            return receipt
        except (
            CodexBoundaryError,
            ControllerServiceError,
            WriterLeaseConflict,
            ValueError,
            sqlite3.Error,
        ):
            # Keep a pending settlement on an interrupted external action.  The
            # controller wake outbox makes a later retry of those exact targets safe.
            receipt = EventRunnerReceipt("failed", generation, 0)
            self._record_attempt(receipt)
            return receipt
        finally:
            service.close()


__all__ = [
    "EVENT_RUNNER_EXECUTION_LIMIT_SECONDS",
    "EVENT_RUNNER_MAX_WAKES_PER_INVOCATION",
    "EVENT_WAKE_TIMEOUT_SECONDS",
    "EventRunnerError",
    "EventReconciliationStatus",
    "EventRunnerReceipt",
    "EventRunnerRecoveryReceipt",
    "EventRunnerStatus",
    "WorkflowEventRunner",
]
