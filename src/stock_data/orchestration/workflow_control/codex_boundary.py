"""Durable, sanitized process boundary for the local Codex CLI.

The boundary deliberately implements the two existing ``execute`` protocols
without importing either runner.  It persists only operation fingerprints,
small response identifiers, and task-to-session routing.  Prompts, stdout,
stderr, and transcript events remain process-local and are discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from threading import Lock, Thread
from typing import BinaryIO, Callable, Mapping, Sequence

from stock_data.orchestration.workflow_control.runner import ExecutionMetadata


_DIRECT_FIELDS = frozenset(
    {
        "action",
        "attempt",
        "retry_of",
        "retry_provenance",
        "role_key",
        "source_event_id",
        "task_id",
        "operation_id",
        "execution_profile_digest",
    }
)
_SESSION_FIELDS = frozenset(
    {
        "action",
        "provenance",
        "role_generation",
        "role_key",
        "session_id",
        "operation_id",
        "execution_profile_digest",
    }
)
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\\-]{0,254}$")
_DIRECT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DIRECT_OPERATION = re.compile(r"^op-[0-9a-f]{64}$")
_SESSION_OPERATION = re.compile(r"^session-op-[0-9a-f]{64}$")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS codex_boundary_operations (
        operation_id TEXT PRIMARY KEY,
        request_digest TEXT NOT NULL,
        request_kind TEXT NOT NULL CHECK (request_kind IN ('direct', 'session')),
        state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'failed')),
        response_json TEXT,
        error_code TEXT,
        CHECK (
            (state = 'completed' AND response_json IS NOT NULL AND error_code IS NULL)
            OR (state = 'failed' AND response_json IS NULL AND error_code IS NOT NULL)
            OR (state = 'pending' AND response_json IS NULL AND error_code IS NULL)
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS codex_boundary_sessions (
        task_id TEXT NOT NULL,
        role_key TEXT NOT NULL,
        session_id TEXT NOT NULL,
        lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'settled')),
        source_operation_id TEXT NOT NULL,
        execution_profile_digest TEXT,
        PRIMARY KEY (task_id, role_key),
        FOREIGN KEY (source_operation_id)
            REFERENCES codex_boundary_operations(operation_id)
    )
    """,
)


class CodexBoundaryError(RuntimeError):
    """Base class for sanitized boundary failures."""


class CodexBoundaryRequestError(CodexBoundaryError, ValueError):
    """The caller supplied a request outside the existing runner contract."""


class CodexBoundaryConflictError(CodexBoundaryError):
    """An operation id was reused with different request content."""


class CodexBoundaryStateError(CodexBoundaryError):
    """A required durable session route is absent or incompatible."""


class CodexBoundaryProcessError(CodexBoundaryError):
    """The bounded Codex subprocess did not produce a valid completion."""


class CodexBoundaryUnsupportedActionError(CodexBoundaryError):
    """The local non-interactive Codex CLI cannot perform the requested action."""


class CodexBoundaryUncertainOperationError(CodexBoundaryError):
    """A prior process may have started, so replay cannot safely spawn again."""


@dataclass(frozen=True, slots=True)
class _Request:
    kind: str
    action: str
    operation_id: str
    request_digest: str
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int
    stdout: bytes
    overflowed: bool


@dataclass(frozen=True, slots=True)
class CodexBoundaryStatus:
    """Sanitized durable counts; no prompt, output, task or session identity."""

    pending_operations: int
    completed_operations: int
    failed_operations: int
    active_sessions: int
    settled_sessions: int


class _OutputBudget:
    """Share one in-memory byte budget while both pipes continue draining."""

    def __init__(self, limit: int) -> None:
        self._remaining = limit
        self._lock = Lock()
        self.overflowed = False

    def retain(self, target: io.BytesIO, chunk: bytes) -> None:
        with self._lock:
            kept = min(self._remaining, len(chunk))
            if kept:
                target.write(chunk[:kept])
                self._remaining -= kept
            if kept != len(chunk):
                self.overflowed = True


ProcessFactory = Callable[..., subprocess.Popen[bytes]]


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_identifier(value: str, label: str, *, direct: bool = False) -> None:
    expression = _DIRECT_IDENTIFIER if direct else _IDENTIFIER
    if expression.fullmatch(value) is None:
        raise CodexBoundaryRequestError(f"{label} must be a bounded identifier")


def _parse_request(request: Mapping[str, str]) -> _Request:
    if not isinstance(request, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.items()
    ):
        raise CodexBoundaryRequestError("boundary requests must contain only text fields")
    fields = dict(request)
    keys = frozenset(fields)
    if keys == _DIRECT_FIELDS:
        kind = "direct"
        operation_expression = _DIRECT_OPERATION
        if fields["action"] not in {"launch", "resume", "settle"}:
            raise CodexBoundaryRequestError("direct action is not supported")
        if _TASK_ID.fullmatch(fields["task_id"]) is None:
            raise CodexBoundaryRequestError("task id must be an exact Queue task id")
        for key in ("role_key", "source_event_id"):
            _require_identifier(fields[key], key.replace("_", " "), direct=True)
        try:
            attempt = int(fields["attempt"])
        except ValueError as error:
            raise CodexBoundaryRequestError("attempt must be a non-negative integer") from error
        if str(attempt) != fields["attempt"] or attempt < 0:
            raise CodexBoundaryRequestError("attempt must be a non-negative integer")
        retry_of = fields["retry_of"] or None
        retry_provenance = fields["retry_provenance"] or None
        if fields["action"] == "resume":
            if attempt < 1 or retry_of is None or retry_provenance is None:
                raise CodexBoundaryRequestError("resume requires exact retry provenance")
        elif attempt != 0 or retry_of is not None or retry_provenance is not None:
            raise CodexBoundaryRequestError(
                "retry fields and nonzero attempts are valid only for resume"
            )
        if retry_of is not None:
            _require_identifier(retry_of, "retry of", direct=True)
        if retry_provenance is not None and _DIGEST.fullmatch(retry_provenance) is None:
            raise CodexBoundaryRequestError("retry provenance must be a SHA-256 digest")
        operation_material: dict[str, object] = {
            "action": fields["action"],
            "attempt": attempt,
            "retry_of": retry_of,
            "retry_provenance": retry_provenance,
            "role_key": fields["role_key"],
            "source_event_id": fields["source_event_id"],
            "task_id": fields["task_id"],
            "execution_profile_digest": fields["execution_profile_digest"],
        }
        expected_operation_id = "op-" + _digest(operation_material)
    elif keys == _SESSION_FIELDS:
        kind = "session"
        operation_expression = _SESSION_OPERATION
        if fields["action"] not in {"interrupt", "resume"}:
            raise CodexBoundaryRequestError("session action is not supported")
        for key in ("role_key", "session_id"):
            _require_identifier(fields[key], key.replace("_", " "))
        try:
            role_generation = int(fields["role_generation"])
        except ValueError as error:
            raise CodexBoundaryRequestError("role generation must be positive") from error
        if str(role_generation) != fields["role_generation"] or role_generation < 1:
            raise CodexBoundaryRequestError("role generation must be positive")
        if _DIGEST.fullmatch(fields["provenance"]) is None:
            raise CodexBoundaryRequestError("session provenance must be a SHA-256 digest")
        operation_material = {
            "action": fields["action"],
            "provenance": fields["provenance"],
            "role_generation": role_generation,
            "role_key": fields["role_key"],
            "session_id": fields["session_id"],
            "execution_profile_digest": fields["execution_profile_digest"],
        }
        expected_operation_id = "session-op-" + _digest(operation_material)
    else:
        raise CodexBoundaryRequestError("boundary request fields changed")

    operation_id = fields["operation_id"]
    if operation_expression.fullmatch(operation_id) is None:
        raise CodexBoundaryRequestError("operation id has an invalid format")
    return _Request(
        kind=kind,
        action=fields["action"],
        operation_id=operation_id,
        request_digest=hashlib.sha256(_canonical(fields).encode("utf-8")).hexdigest(),
        fields=fields | {"_expected_operation_id": expected_operation_id},
    )


def _drain(stream: BinaryIO, target: io.BytesIO, budget: _OutputBudget) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            budget.retain(target, chunk)
    except (OSError, ValueError):
        # Process shutdown can close a pipe while its reader is draining.  The
        # process result and JSON completion event remain the success oracle.
        return


def _error_from_code(code: str) -> CodexBoundaryError:
    errors: dict[str, tuple[type[CodexBoundaryError], str]] = {
        "active_session_exists": (
            CodexBoundaryStateError,
            "an active session already owns this task and role",
        ),
        "missing_session": (
            CodexBoundaryStateError,
            "no durable Codex session exists for this task and role",
        ),
        "session_profile_mismatch": (
            CodexBoundaryStateError,
            "Codex session execution profile does not match this boundary",
        ),
        "interrupt_unsupported": (
            CodexBoundaryUnsupportedActionError,
            "session interruption is unsupported by the local non-interactive Codex CLI",
        ),
        "executable_unavailable": (
            CodexBoundaryProcessError,
            "the local Codex executable is unavailable",
        ),
        "process_start_failed": (
            CodexBoundaryProcessError,
            "the local Codex process could not start",
        ),
        "process_timeout": (
            CodexBoundaryProcessError,
            "the local Codex process exceeded its timeout",
        ),
        "output_limit": (
            CodexBoundaryProcessError,
            "the local Codex process exceeded its output limit",
        ),
        "process_failed": (
            CodexBoundaryProcessError,
            "the local Codex process exited unsuccessfully",
        ),
        "invalid_jsonl": (
            CodexBoundaryProcessError,
            "the local Codex process returned invalid JSON events",
        ),
        "operation_mismatch": (
            CodexBoundaryRequestError,
            "operation id does not match request content",
        ),
        "missing_completion": (
            CodexBoundaryProcessError,
            "the local Codex process did not report a completed turn",
        ),
        "invalid_session": (
            CodexBoundaryProcessError,
            "the local Codex process returned an invalid session identity",
        ),
    }
    error_type, message = errors.get(
        code,
        (CodexBoundaryProcessError, "the local Codex process failed"),
    )
    return error_type(message)


class CodexCliBoundary:
    """Implement both direct-runner protocols over ``codex exec``.

    ``state_path`` is required so operation idempotency and task/session routing
    survive controller and boundary re-instantiation.  The command is a
    pre-tokenized argv prefix; shell command strings are never accepted.
    """

    def __init__(
        self,
        state_path: Path,
        *,
        command: Sequence[str] = ("codex",),
        cwd: Path | None = None,
        sandbox_mode: str = "read-only",
        timeout_seconds: float = 1_800.0,
        max_output_bytes: int = 1_048_576,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        self.state_path = Path(state_path)
        self.cwd = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError("command must be a non-empty argv sequence")
        self.command = tuple(command)
        if not all(isinstance(item, str) and item and "\x00" not in item for item in self.command):
            raise ValueError("command argv entries must be non-empty text")
        if sandbox_mode not in {"read-only", "workspace-write"}:
            raise ValueError("sandbox mode must be read-only or workspace-write")
        self.sandbox_mode = sandbox_mode
        self.execution_metadata = ExecutionMetadata(
            "codex_read_only" if sandbox_mode == "read-only" else "codex_workspace_write",
            sandbox_mode == "workspace-write",
            False if sandbox_mode == "read-only" else None,
        )
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise ValueError("timeout must be numeric")
        if not 1 <= float(timeout_seconds) <= 3_600:
            raise ValueError("timeout must be between one second and one hour")
        if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool):
            raise ValueError("output limit must be an integer")
        if not 1_024 <= max_output_bytes <= 16_777_216:
            raise ValueError("output limit must be between 1 KiB and 16 MiB")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes
        self._process_factory = process_factory
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.state_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists() and (
            self.state_path.is_symlink() or not self.state_path.is_file()
        ):
            raise CodexBoundaryStateError("Codex boundary state must be a regular file")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA:
                    connection.execute(statement)
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in {0, 1, 2}:
                    raise CodexBoundaryStateError("Codex boundary state schema is unsupported")
                operation_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(codex_boundary_operations)"
                    )
                }
                if "execution_profile_digest" not in operation_columns:
                    connection.execute(
                        "ALTER TABLE codex_boundary_operations "
                        "ADD COLUMN execution_profile_digest TEXT"
                    )
                session_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(codex_boundary_sessions)"
                    )
                }
                if "execution_profile_digest" not in session_columns:
                    connection.execute(
                        "ALTER TABLE codex_boundary_sessions "
                        "ADD COLUMN execution_profile_digest TEXT"
                    )
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
        parsed = _parse_request(request)
        if (
            parsed.fields["execution_profile_digest"]
            != self.execution_metadata.profile_digest
        ):
            raise CodexBoundaryRequestError(
                "request execution profile does not match the boundary"
            )
        cached = self._claim_or_replay(parsed)
        if cached is not None:
            return cached
        expected_operation_id = parsed.fields.pop("_expected_operation_id")
        if parsed.operation_id != expected_operation_id:
            self._store_failure(parsed.operation_id, "operation_mismatch")
            raise _error_from_code("operation_mismatch")

        try:
            response, session_update = self._execute_claimed(parsed)
        except CodexBoundaryError as error:
            code = getattr(error, "_codex_error_code", "process_failed")
            self._store_failure(parsed.operation_id, code)
            raise
        except FileNotFoundError:
            self._store_failure(parsed.operation_id, "executable_unavailable")
            raise _error_from_code("executable_unavailable") from None
        except OSError:
            self._store_failure(parsed.operation_id, "process_start_failed")
            raise _error_from_code("process_start_failed") from None

        response_json = _canonical(response)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if session_update is not None:
                    task_id, role_key, session_id, lifecycle = session_update
                    connection.execute(
                        """
                        INSERT INTO codex_boundary_sessions(
                            task_id, role_key, session_id, lifecycle, source_operation_id,
                            execution_profile_digest
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(task_id, role_key) DO UPDATE SET
                            session_id = excluded.session_id,
                            lifecycle = excluded.lifecycle,
                            source_operation_id = excluded.source_operation_id,
                            execution_profile_digest = excluded.execution_profile_digest
                        """,
                        (
                            task_id,
                            role_key,
                            session_id,
                            lifecycle,
                            parsed.operation_id,
                            self.execution_metadata.profile_digest,
                        ),
                    )
                changed = connection.execute(
                    """
                    UPDATE codex_boundary_operations
                    SET state = 'completed', response_json = ?, error_code = NULL
                    WHERE operation_id = ? AND state = 'pending'
                    """,
                    (response_json, parsed.operation_id),
                ).rowcount
                if changed != 1:
                    raise CodexBoundaryUncertainOperationError(
                        "operation state changed before completion could be recorded"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return dict(response)

    def _claim_or_replay(self, request: _Request) -> dict[str, str] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT request_digest, request_kind, state, response_json, error_code,
                           execution_profile_digest
                    FROM codex_boundary_operations WHERE operation_id = ?
                    """,
                    (request.operation_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["request_digest"] != request.request_digest
                        or existing["request_kind"] != request.kind
                        or existing["execution_profile_digest"]
                        != self.execution_metadata.profile_digest
                    ):
                        raise CodexBoundaryConflictError(
                            "operation id was reused with different content"
                        )
                    if existing["state"] == "completed":
                        response = json.loads(existing["response_json"])
                        if not isinstance(response, dict) or not all(
                            isinstance(key, str) and isinstance(value, str)
                            for key, value in response.items()
                        ):
                            raise CodexBoundaryStateError(
                                "stored Codex boundary response is invalid"
                            )
                        expected_fields = (
                            {"status", "agent_id"}
                            if request.kind == "direct"
                            else {"status", "session_id"}
                        )
                        expected_status = (
                            {
                                "launch": "launched",
                                "resume": "resumed",
                                "settle": "settled",
                            }[request.action]
                            if request.kind == "direct"
                            else "resumed"
                        )
                        identity_key = (
                            "agent_id" if request.kind == "direct" else "session_id"
                        )
                        identity_expression = (
                            _DIRECT_IDENTIFIER
                            if request.kind == "direct"
                            else _IDENTIFIER
                        )
                        if (
                            set(response) != expected_fields
                            or response["status"] != expected_status
                            or identity_expression.fullmatch(
                                response.get(identity_key, "")
                            )
                            is None
                        ):
                            raise CodexBoundaryStateError(
                                "stored Codex boundary response is invalid"
                            )
                        connection.commit()
                        return response
                    if existing["state"] == "failed":
                        raise _error_from_code(str(existing["error_code"]))
                    raise CodexBoundaryUncertainOperationError(
                        "operation was previously claimed and its outcome is uncertain"
                    )
                connection.execute(
                    """
                    INSERT INTO codex_boundary_operations(
                        operation_id, request_digest, request_kind, state,
                        response_json, error_code, execution_profile_digest
                    ) VALUES (?, ?, ?, 'pending', NULL, NULL, ?)
                    """,
                    (
                        request.operation_id,
                        request.request_digest,
                        request.kind,
                        self.execution_metadata.profile_digest,
                    ),
                )
                connection.commit()
                return None
            except BaseException:
                connection.rollback()
                raise

    def _store_failure(self, operation_id: str, code: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE codex_boundary_operations
                    SET state = 'failed', response_json = NULL, error_code = ?
                    WHERE operation_id = ? AND state = 'pending'
                    """,
                    (code, operation_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _coded_error(code: str) -> CodexBoundaryError:
        error = _error_from_code(code)
        setattr(error, "_codex_error_code", code)
        return error

    def _task_session(
        self, task_id: str, role_key: str
    ) -> tuple[str, str, str | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, lifecycle, execution_profile_digest
                FROM codex_boundary_sessions
                WHERE task_id = ? AND role_key = ?
                """,
                (task_id, role_key),
            ).fetchone()
        if row is None:
            return None
        return (
            str(row["session_id"]),
            str(row["lifecycle"]),
            None
            if row["execution_profile_digest"] is None
            else str(row["execution_profile_digest"]),
        )

    def _session_profiles(self, session_id: str) -> set[str | None]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT execution_profile_digest "
                "FROM codex_boundary_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        return {
            None if row["execution_profile_digest"] is None else str(row["execution_profile_digest"])
            for row in rows
        }

    def _execute_claimed(
        self, request: _Request
    ) -> tuple[dict[str, str], tuple[str, str, str, str] | None]:
        fields = request.fields
        if request.kind == "session":
            if request.action == "interrupt":
                raise self._coded_error("interrupt_unsupported")
            session_id = fields["session_id"]
            profiles = self._session_profiles(session_id)
            if profiles and profiles != {self.execution_metadata.profile_digest}:
                raise self._coded_error("session_profile_mismatch")
            if not profiles and self.execution_metadata.workspace_write_enabled:
                raise self._coded_error("session_profile_mismatch")
            prompt = self._session_resume_prompt(request)
            argv = [*self.command, "exec", "resume", session_id, "--json", prompt]
            observed_session = self._run_and_parse(argv, expected_session=session_id)
            return {"status": "resumed", "session_id": observed_session}, None

        task_id = fields["task_id"]
        role_key = fields["role_key"]
        route = self._task_session(task_id, role_key)
        if request.action == "launch":
            if route is not None and route[1] == "active":
                raise self._coded_error("active_session_exists")
            prompt = self._direct_prompt(request)
            argv = [
                *self.command,
                "exec",
                "--json",
                "--color",
                "never",
            ]
            if self.sandbox_mode == "read-only":
                # Current Codex CLI rejects --approve-for-me together with an
                # explicit sandbox.  Canary must retain the stronger explicit
                # read-only boundary and needs no mutation approvals.
                argv.extend(("--sandbox", "read-only"))
            else:
                # --approve-for-me selects the CLI's reviewed workspace-write
                # mode; combining it with --sandbox is invalid.
                argv.append("--approve-for-me")
            argv.extend(("--cd", str(self.cwd), prompt))
            session_id = self._run_and_parse(argv, expected_session=None)
            response = {"status": "launched", "agent_id": session_id}
            return response, (task_id, role_key, session_id, "active")

        if route is None:
            raise self._coded_error("missing_session")
        session_id, _lifecycle, route_profile = route
        if route_profile != self.execution_metadata.profile_digest:
            raise self._coded_error("session_profile_mismatch")
        prompt = self._direct_prompt(request)
        argv = [*self.command, "exec", "resume", session_id, "--json", prompt]
        observed_session = self._run_and_parse(argv, expected_session=session_id)
        if request.action == "resume":
            response = {"status": "resumed", "agent_id": observed_session}
            lifecycle = "active"
        else:
            response = {"status": "settled", "agent_id": observed_session}
            lifecycle = "settled"
        return response, (task_id, role_key, observed_session, lifecycle)

    @staticmethod
    def _direct_prompt(request: _Request) -> str:
        fields = request.fields
        prefix = (
            f"Python PM operation {request.operation_id}. "
            f"Exact Queue task: {fields['task_id']}. Role: {fields['role_key']}. "
        )
        if request.action == "launch":
            instruction = (
                "Read AGENTS.md and locate the exact task packet by task id. Complete only "
                "that assigned task and its checks. Keep Queue mutation within "
                "scripts/request_queue.py. Return a concise sanitized result."
            )
        elif request.action == "resume":
            instruction = (
                f"Resume the same task for bounded retry {fields['attempt']}. Re-read current "
                "authoritative state, continue only the exact assigned task, run its checks, "
                "and return a concise sanitized result."
            )
        else:
            instruction = (
                "Settle this session now. Make no further repository, Queue, account, or "
                "external changes; return only a concise sanitized final status and exit."
            )
        return prefix + instruction + " Never reveal secrets or direct account identifiers."

    @staticmethod
    def _session_resume_prompt(request: _Request) -> str:
        fields = request.fields
        return (
            f"Python PM wake operation {request.operation_id}. Resume role "
            f"{fields['role_key']} generation {fields['role_generation']}. Re-read AGENTS.md "
            "and current authoritative repository state, then handle only the next safe "
            "assigned work. Return a concise sanitized result. Never reveal secrets or "
            "direct account identifiers."
        )

    def _run_and_parse(self, argv: list[str], *, expected_session: str | None) -> str:
        result = self._run_process(argv)
        if result.overflowed:
            raise self._coded_error("output_limit")
        if result.returncode != 0:
            raise self._coded_error("process_failed")
        try:
            text = result.stdout.decode("utf-8")
            events = [json.loads(line) for line in text.splitlines() if line]
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise self._coded_error("invalid_jsonl") from None
        if not all(isinstance(event, dict) for event in events):
            raise self._coded_error("invalid_jsonl")
        if any(event.get("type") == "turn.failed" for event in events):
            raise self._coded_error("process_failed")
        if not any(event.get("type") == "turn.completed" for event in events):
            raise self._coded_error("missing_completion")
        observed_values = [
            event.get("thread_id")
            for event in events
            if event.get("type") == "thread.started"
        ]
        if expected_session is not None and not observed_values:
            observed_values = [expected_session]
        if not all(isinstance(value, str) for value in observed_values):
            raise self._coded_error("invalid_session")
        observed = set(observed_values)
        session_expression = _IDENTIFIER if expected_session is not None else _DIRECT_IDENTIFIER
        if (
            len(observed) != 1
            or session_expression.fullmatch(next(iter(observed))) is None
        ):
            raise self._coded_error("invalid_session")
        session_id = next(iter(observed))
        if expected_session is not None and session_id != expected_session:
            raise self._coded_error("invalid_session")
        return session_id

    def _run_process(self, argv: list[str]) -> _ProcessResult:
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        )
        process = self._process_factory(
            list(argv),
            cwd=str(self.cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
        )
        if process.stdout is None or process.stderr is None:
            try:
                process.kill()
            finally:
                raise self._coded_error("process_start_failed")
        budget = _OutputBudget(self.max_output_bytes)
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        readers = (
            Thread(target=_drain, args=(process.stdout, stdout, budget), daemon=True),
            Thread(target=_drain, args=(process.stderr, stderr, budget), daemon=True),
        )
        for reader in readers:
            reader.start()
        try:
            returncode = process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for reader in readers:
                reader.join(timeout=1)
            raise self._coded_error("process_timeout") from None
        finally:
            if process.poll() is not None:
                for reader in readers:
                    reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            process.kill()
            raise self._coded_error("process_start_failed")
        return _ProcessResult(
            returncode=int(returncode),
            stdout=stdout.getvalue(),
            overflowed=budget.overflowed,
        )

    @staticmethod
    def inspect(state_path: Path) -> CodexBoundaryStatus:
        """Inspect an existing boundary store without creating or migrating it."""

        path = Path(state_path)
        if not path.exists():
            return CodexBoundaryStatus(0, 0, 0, 0, 0)
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                operation_counts = {
                    str(state): int(count)
                    for state, count in connection.execute(
                        "SELECT state, COUNT(*) FROM codex_boundary_operations GROUP BY state"
                    )
                }
                session_counts = {
                    str(state): int(count)
                    for state, count in connection.execute(
                        "SELECT lifecycle, COUNT(*) FROM codex_boundary_sessions GROUP BY lifecycle"
                    )
                }
        except sqlite3.Error as error:
            raise CodexBoundaryStateError(
                "Codex boundary state could not be inspected"
            ) from error
        return CodexBoundaryStatus(
            operation_counts.get("pending", 0),
            operation_counts.get("completed", 0),
            operation_counts.get("failed", 0),
            session_counts.get("active", 0),
            session_counts.get("settled", 0),
        )


__all__ = [
    "CodexBoundaryConflictError",
    "CodexBoundaryError",
    "CodexBoundaryProcessError",
    "CodexBoundaryRequestError",
    "CodexBoundaryStateError",
    "CodexBoundaryUncertainOperationError",
    "CodexBoundaryUnsupportedActionError",
    "CodexBoundaryStatus",
    "CodexCliBoundary",
]
