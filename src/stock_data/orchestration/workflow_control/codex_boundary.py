"""Durable, sanitized process boundary for the local Codex CLI.

The boundary deliberately implements the two existing ``execute`` protocols
without importing either runner.  It persists only operation fingerprints,
small response identifiers, and task-to-session routing.  Prompts, stdout,
stderr, and transcript events remain process-local and are discarded.
Terminal failures retain only a versioned, identifier-free process-event
receipt containing exact pins, allowlisted classification, byte counts and
digests.  Raw process bytes never cross this boundary.
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
_SESSION_RECONCILIATION_FIELDS = _SESSION_FIELDS | {"reconciliation_binding"}
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\\-]{0,254}$")
_DIRECT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DIRECT_OPERATION = re.compile(r"^op-[0-9a-f]{64}$")
_SESSION_OPERATION = re.compile(r"^session-op-[0-9a-f]{64}$")
_BOOTSTRAP_EVENT = re.compile(r"^runtime_bootstrap_v[1-9]$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROCESS_EVENT_SCHEMA = "codex-process-event/v1"
_PROCESS_EVENT_CLASSIFIER_VERSION = 1
_PROCESS_EVENT_REASONS = frozenset({"model_capacity", "unknown_failure"})
_PROCESS_EVENT_PREFIX_LIMIT = 64 * 1024

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS codex_boundary_operations (
        operation_id TEXT PRIMARY KEY,
        request_digest TEXT NOT NULL,
        request_kind TEXT NOT NULL CHECK (request_kind IN ('direct', 'session')),
        state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'failed')),
        response_json TEXT,
        error_code TEXT,
        process_event_json TEXT,
        reconciliation_binding TEXT,
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
class CodexProcessEventPins:
    """Exact sanitized identity for one boundary process generation."""

    operation_id: str
    request_digest: str
    generation_sequence: int
    generation_digest: str
    execution_profile_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation_id, str)
            or (
                _DIRECT_OPERATION.fullmatch(self.operation_id) is None
                and _SESSION_OPERATION.fullmatch(self.operation_id) is None
            )
            or _DIGEST.fullmatch(self.request_digest) is None
            or not isinstance(self.generation_sequence, int)
            or isinstance(self.generation_sequence, bool)
            or self.generation_sequence < 0
            or _DIGEST.fullmatch(self.generation_digest) is None
            or _DIGEST.fullmatch(self.execution_profile_digest) is None
        ):
            raise CodexBoundaryRequestError(
                "process-event probe requires exact operation and generation pins"
            )


@dataclass(frozen=True, slots=True)
class CodexProcessEventReceipt:
    """Allowlisted durable evidence for one terminal Codex process event."""

    schema: str
    classifier_version: int
    operation_id: str
    request_digest: str
    generation_sequence: int
    generation_digest: str
    execution_profile_digest: str
    reason: str
    full_stream_byte_length: int
    full_stream_sha256: str
    parser_error: bool
    truncated: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        try:
            CodexProcessEventPins(
                operation_id=self.operation_id,
                request_digest=self.request_digest,
                generation_sequence=self.generation_sequence,
                generation_digest=self.generation_digest,
                execution_profile_digest=self.execution_profile_digest,
            )
        except CodexBoundaryRequestError as error:
            raise CodexBoundaryStateError(
                "stored process-event receipt pins are invalid"
            ) from error
        if (
            self.schema != _PROCESS_EVENT_SCHEMA
            or self.classifier_version != _PROCESS_EVENT_CLASSIFIER_VERSION
            or self.reason not in _PROCESS_EVENT_REASONS
            or not isinstance(self.full_stream_byte_length, int)
            or isinstance(self.full_stream_byte_length, bool)
            or self.full_stream_byte_length < 0
            or _DIGEST.fullmatch(self.full_stream_sha256) is None
            or not isinstance(self.parser_error, bool)
            or not isinstance(self.truncated, bool)
            or _DIGEST.fullmatch(self.receipt_digest) is None
            or self.receipt_digest != _digest(self._material())
        ):
            raise CodexBoundaryStateError("stored process-event receipt is invalid")

    def _material(self) -> dict[str, object]:
        return {
            "classifier_version": self.classifier_version,
            "execution_profile_digest": self.execution_profile_digest,
            "full_stream_byte_length": self.full_stream_byte_length,
            "full_stream_sha256": self.full_stream_sha256,
            "generation_digest": self.generation_digest,
            "generation_sequence": self.generation_sequence,
            "operation_id": self.operation_id,
            "parser_error": self.parser_error,
            "reason": self.reason,
            "request_digest": self.request_digest,
            "schema": self.schema,
            "truncated": self.truncated,
        }

    def to_dict(self) -> dict[str, object]:
        return self._material() | {"receipt_digest": self.receipt_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> CodexProcessEventReceipt:
        expected = {
            "classifier_version",
            "execution_profile_digest",
            "full_stream_byte_length",
            "full_stream_sha256",
            "generation_digest",
            "generation_sequence",
            "operation_id",
            "parser_error",
            "reason",
            "receipt_digest",
            "request_digest",
            "schema",
            "truncated",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise CodexBoundaryStateError("stored process-event receipt fields changed")
        return cls(**dict(value))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CodexBoundaryOperationPin:
    """Identifier-free exact pin for one uncertain durable operation."""

    operation_id: str
    request_digest: str
    request_kind: str
    execution_profile_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation_id, str)
            or not isinstance(self.request_digest, str)
            or not isinstance(self.execution_profile_digest, str)
            or _DIGEST.fullmatch(self.request_digest) is None
            or _DIGEST.fullmatch(self.execution_profile_digest) is None
            or self.request_kind not in {"direct", "session"}
            or (
                self.request_kind == "direct"
                and _DIRECT_OPERATION.fullmatch(self.operation_id) is None
            )
            or (
                self.request_kind == "session"
                and _SESSION_OPERATION.fullmatch(self.operation_id) is None
            )
        ):
            raise CodexBoundaryStateError("Codex boundary operation pin is invalid")


@dataclass(frozen=True, slots=True)
class CodexBoundaryTerminalOperation:
    """Sanitized exact evidence for one terminal durable operation."""

    operation_id: str
    request_digest: str
    request_kind: str
    execution_profile_digest: str
    state: str
    error_code: str

    def __post_init__(self) -> None:
        try:
            CodexBoundaryOperationPin(
                self.operation_id,
                self.request_digest,
                self.request_kind,
                self.execution_profile_digest,
            )
        except CodexBoundaryStateError as error:
            raise CodexBoundaryStateError(
                "Codex boundary terminal operation is invalid"
            ) from error
        if self.state != "failed" or _ERROR_CODE.fullmatch(self.error_code) is None:
            raise CodexBoundaryStateError(
                "Codex boundary terminal operation is invalid"
            )


@dataclass(frozen=True, slots=True)
class CodexBoundaryTerminalOperationMapping:
    """Allowlisted mapping for one unambiguous terminal process receipt."""

    operation_id: str
    request_digest: str
    request_kind: str
    execution_profile_digest: str
    error_code: str
    process_event_receipt_digest: str

    def __post_init__(self) -> None:
        try:
            CodexBoundaryOperationPin(
                self.operation_id,
                self.request_digest,
                self.request_kind,
                self.execution_profile_digest,
            )
        except CodexBoundaryStateError as error:
            raise CodexBoundaryStateError("terminal operation mapping is invalid") from error
        if (
            _ERROR_CODE.fullmatch(self.error_code) is None
            or _DIGEST.fullmatch(self.process_event_receipt_digest) is None
        ):
            raise CodexBoundaryStateError("terminal operation mapping is invalid")


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
    process_event: CodexProcessEventReceipt


@dataclass(frozen=True, slots=True)
class CodexBoundaryStatus:
    """Sanitized durable counts; no prompt, output, task or session identity."""

    pending_operations: int
    completed_operations: int
    failed_operations: int
    active_sessions: int
    settled_sessions: int
    pending_operation_pins: tuple[CodexBoundaryOperationPin, ...] = ()


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


class _StreamCapture:
    """Hash/count a complete pipe while retaining only a bounded local prefix."""

    def __init__(self, prefix_limit: int = _PROCESS_EVENT_PREFIX_LIMIT) -> None:
        self._prefix_limit = prefix_limit
        self._prefix = io.BytesIO()
        self._hasher = hashlib.sha256()
        self.byte_length = 0
        self.truncated = False
        self.read_error = False

    def observe(self, chunk: bytes) -> None:
        self._hasher.update(chunk)
        self.byte_length += len(chunk)
        remaining = self._prefix_limit - self._prefix.tell()
        kept = min(max(remaining, 0), len(chunk))
        if kept:
            self._prefix.write(chunk[:kept])
        if kept != len(chunk):
            self.truncated = True

    @property
    def prefix(self) -> bytes:
        return self._prefix.getvalue()

    @property
    def digest(self) -> str:
        return self._hasher.hexdigest()


ProcessFactory = Callable[..., subprocess.Popen[bytes]]


def background_creationflags() -> int:
    """Return no-console Windows flags for every background Codex wake/launch."""

    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


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
        if (
            fields["source_event_id"].startswith("runtime_bootstrap_v")
            and _BOOTSTRAP_EVENT.fullmatch(fields["source_event_id"]) is None
        ):
            raise CodexBoundaryRequestError(
                "runtime bootstrap event must use a bounded attempt from 1 to 9"
            )
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
    elif keys in {_SESSION_FIELDS, _SESSION_RECONCILIATION_FIELDS}:
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
        reconciliation_binding = fields.get("reconciliation_binding")
        if reconciliation_binding is not None and _DIGEST.fullmatch(reconciliation_binding) is None:
            raise CodexBoundaryRequestError(
                "session reconciliation binding must be a SHA-256 digest"
            )
        operation_material = {
            "action": fields["action"],
            "provenance": fields["provenance"],
            "role_generation": role_generation,
            "role_key": fields["role_key"],
            "session_id": fields["session_id"],
            "execution_profile_digest": fields["execution_profile_digest"],
        }
        if reconciliation_binding is not None:
            operation_material["reconciliation_binding"] = reconciliation_binding
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


def _drain(
    stream: BinaryIO,
    target: io.BytesIO,
    budget: _OutputBudget,
    capture: _StreamCapture,
) -> None:
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            capture.observe(chunk)
            budget.retain(target, chunk)
    except (OSError, ValueError):
        # Process shutdown can close a pipe while its reader is draining.  The
        # process result and JSON completion event remain the success oracle.
        capture.read_error = True
        return


def _process_event_pins(request: _Request) -> CodexProcessEventPins:
    if request.kind == "session":
        generation_sequence = int(request.fields["role_generation"])
        generation_digest = request.fields["provenance"]
    else:
        generation_sequence = int(request.fields["attempt"])
        generation_digest = request.fields["retry_provenance"] or request.request_digest
    return CodexProcessEventPins(
        operation_id=request.operation_id,
        request_digest=request.request_digest,
        generation_sequence=generation_sequence,
        generation_digest=generation_digest,
        execution_profile_digest=request.fields["execution_profile_digest"],
    )


def _decode_process_events(prefixes: Sequence[bytes]) -> tuple[list[dict[str, object]], bool]:
    events: list[dict[str, object]] = []
    parser_error = False
    for prefix in prefixes:
        if not prefix:
            continue
        try:
            text = prefix.decode("utf-8")
        except UnicodeDecodeError:
            parser_error = True
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                parser_error = True
                continue
            if not isinstance(event, dict):
                parser_error = True
                continue
            events.append(event)
    if not events:
        parser_error = True
    return events, parser_error


def _explicit_failure_code(event: Mapping[str, object]) -> str | None:
    if event.get("type") not in {"turn.failed", "error"}:
        return None
    error = event.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("code"), str):
        return str(error["code"])
    code = event.get("code")
    return str(code) if isinstance(code, str) else "unsupported_failure"


def _process_event_receipt(
    pins: CodexProcessEventPins,
    *,
    stdout_capture: _StreamCapture,
    stderr_capture: _StreamCapture,
    output_overflowed: bool,
) -> CodexProcessEventReceipt:
    streams = {
        "stderr": {
            "byte_length": stderr_capture.byte_length,
            "sha256": stderr_capture.digest,
        },
        "stdout": {
            "byte_length": stdout_capture.byte_length,
            "sha256": stdout_capture.digest,
        },
    }
    full_stream_sha256 = hashlib.sha256(
        json.dumps(
            streams, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    ).hexdigest()
    truncated = bool(
        output_overflowed or stdout_capture.truncated or stderr_capture.truncated
    )
    events, parser_error = _decode_process_events(
        (stdout_capture.prefix, stderr_capture.prefix)
    )
    parser_error = bool(
        parser_error or stdout_capture.read_error or stderr_capture.read_error
    )
    failure_codes = [
        code for event in events if (code := _explicit_failure_code(event)) is not None
    ]
    has_completion = any(event.get("type") == "turn.completed" for event in events)
    reason = "unknown_failure"
    if (
        not truncated
        and not parser_error
        and failure_codes
        and set(failure_codes) == {"model_capacity"}
        and not has_completion
    ):
        reason = "model_capacity"
    material: dict[str, object] = {
        "classifier_version": _PROCESS_EVENT_CLASSIFIER_VERSION,
        "execution_profile_digest": pins.execution_profile_digest,
        "full_stream_byte_length": (
            stdout_capture.byte_length + stderr_capture.byte_length
        ),
        "full_stream_sha256": full_stream_sha256,
        "generation_digest": pins.generation_digest,
        "generation_sequence": pins.generation_sequence,
        "operation_id": pins.operation_id,
        "parser_error": parser_error,
        "reason": reason,
        "request_digest": pins.request_digest,
        "schema": _PROCESS_EVENT_SCHEMA,
        "truncated": truncated,
    }
    return CodexProcessEventReceipt(
        **material, receipt_digest=_digest(material)  # type: ignore[arg-type]
    )


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
        "uncertain_recovered": (
            CodexBoundaryUncertainOperationError,
            "the uncertain Codex operation was fenced and requires a fresh generation",
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

    def assert_cli_owned_session(self, *, role_key: str, session_id: str) -> str:
        """Prove that this boundary launched the exact persistent session.

        App-created Codex task IDs are coordination identities and must never be
        adopted as unattended CLI targets.  A valid route therefore requires a
        completed direct ``launch`` operation whose immutable response contains
        the same session ID and execution profile.  The returned proof is
        identifier-free and safe to retain in local receipts.
        """

        _require_identifier(role_key, "role key")
        _require_identifier(session_id, "session id")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.task_id, s.lifecycle, s.source_operation_id,
                       s.execution_profile_digest, o.request_kind, o.state,
                       o.response_json, o.error_code,
                       o.execution_profile_digest AS operation_profile_digest
                FROM codex_boundary_sessions AS s
                JOIN codex_boundary_operations AS o
                  ON o.operation_id = s.source_operation_id
                WHERE s.role_key = ? AND s.session_id = ?
                """,
                (role_key, session_id),
            ).fetchall()
        if len(rows) != 1:
            raise CodexBoundaryStateError(
                "role session is not owned by the unattended CLI boundary"
            )
        row = rows[0]
        try:
            response = json.loads(str(row["response_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise CodexBoundaryStateError(
                "CLI session ownership receipt is invalid"
            ) from error
        if (
            row["lifecycle"] != "active"
            or not str(row["source_operation_id"]).startswith("op-")
            or row["request_kind"] != "direct"
            or row["state"] != "completed"
            or row["error_code"] is not None
            or row["execution_profile_digest"]
            != self.execution_metadata.profile_digest
            or row["operation_profile_digest"]
            != self.execution_metadata.profile_digest
            or response != {"agent_id": session_id, "status": "launched"}
        ):
            raise CodexBoundaryStateError(
                "role session is not owned by a completed CLI launch"
            )
        return _digest(
            {
                "role_key": role_key,
                "session_fingerprint": hashlib.sha256(
                    session_id.encode("utf-8")
                ).hexdigest(),
                "source_operation_id": str(row["source_operation_id"]),
                "task_id": str(row["task_id"]),
                "execution_profile_digest": self.execution_metadata.profile_digest,
            }
        )

    def assert_coordination_session(
        self, *, task_id: str, role_key: str, session_id: str
    ) -> str:
        """Validate one historical app binding without making it executable."""

        if _TASK_ID.fullmatch(task_id) is None:
            raise CodexBoundaryRequestError("task id must be an exact Queue task id")
        _require_identifier(role_key, "role key")
        _require_identifier(session_id, "session id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.lifecycle, s.source_operation_id,
                       s.execution_profile_digest, o.request_kind, o.state,
                       o.response_json, o.error_code,
                       o.execution_profile_digest AS operation_profile_digest
                FROM codex_boundary_sessions AS s
                JOIN codex_boundary_operations AS o
                  ON o.operation_id = s.source_operation_id
                WHERE s.task_id = ? AND s.role_key = ? AND s.session_id = ?
                """,
                (task_id, role_key, session_id),
            ).fetchone()
        try:
            response = None if row is None else json.loads(str(row["response_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise CodexBoundaryStateError(
                "coordination session receipt is invalid"
            ) from error
        if (
            row is None
            or row["lifecycle"] != "active"
            or not str(row["source_operation_id"]).startswith("session-bind-")
            or row["request_kind"] != "session"
            or row["state"] != "completed"
            or row["error_code"] is not None
            or row["execution_profile_digest"]
            != self.execution_metadata.profile_digest
            or row["operation_profile_digest"]
            != self.execution_metadata.profile_digest
            or not isinstance(response, dict)
            or response.get("status") != "bound"
            or _DIGEST.fullmatch(str(response.get("binding_digest", ""))) is None
            or set(response) != {"status", "binding_digest"}
        ):
            raise CodexBoundaryStateError(
                "coordination session migration receipt does not match"
            )
        return _digest(
            {
                "role_key": role_key,
                "session_fingerprint": hashlib.sha256(
                    session_id.encode("utf-8")
                ).hexdigest(),
                "source_operation_id": str(row["source_operation_id"]),
                "task_id": task_id,
            }
        )

    def _route_is_coordination_session(
        self, *, task_id: str, role_key: str, session_id: str
    ) -> bool:
        try:
            self.assert_coordination_session(
                task_id=task_id, role_key=role_key, session_id=session_id,
            )
        except CodexBoundaryStateError:
            return False
        return True

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
                if version not in {0, 1, 2, 3, 4}:
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
                if "process_event_json" not in operation_columns:
                    connection.execute(
                        "ALTER TABLE codex_boundary_operations "
                        "ADD COLUMN process_event_json TEXT"
                    )
                if "reconciliation_binding" not in operation_columns:
                    connection.execute(
                        "ALTER TABLE codex_boundary_operations "
                        "ADD COLUMN reconciliation_binding TEXT"
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
                connection.execute("PRAGMA user_version = 4")
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
            process_event = getattr(error, "_codex_process_event", None)
            self._store_failure(parsed.operation_id, code, process_event=process_event)
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
                        response_json, error_code, execution_profile_digest,
                        reconciliation_binding
                    ) VALUES (?, ?, ?, 'pending', NULL, NULL, ?, ?)
                    """,
                    (
                        request.operation_id,
                        request.request_digest,
                        request.kind,
                        self.execution_metadata.profile_digest,
                        request.fields.get("reconciliation_binding"),
                    ),
                )
                connection.commit()
                return None
            except BaseException:
                connection.rollback()
                raise

    def _store_failure(
        self,
        operation_id: str,
        code: str,
        *,
        process_event: CodexProcessEventReceipt | None = None,
    ) -> None:
        process_event_json = (
            None
            if process_event is None
            else json.dumps(process_event.to_dict(), sort_keys=True, separators=(",", ":"))
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE codex_boundary_operations
                    SET state = 'failed', response_json = NULL, error_code = ?,
                        process_event_json = ?
                    WHERE operation_id = ? AND state = 'pending'
                    """,
                    (code, process_event_json, operation_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def recover_uncertain_operation(
        self, *, operation_id: str, request_digest: str
    ) -> str:
        """Fence one exact pending operation after the service proves no writer lives.

        This boundary method never probes or terminates a process.  The supported
        caller is :meth:`WorkflowControllerService.recover_stranded`, which holds
        the service OS mutex for the entire transition.  Exact replay is
        idempotent and returns the same identifier-free recovery proof.
        """

        if (
            not isinstance(operation_id, str)
            or (
                _DIRECT_OPERATION.fullmatch(operation_id) is None
                and _SESSION_OPERATION.fullmatch(operation_id) is None
            )
            or not isinstance(request_digest, str)
            or _DIGEST.fullmatch(request_digest) is None
        ):
            raise CodexBoundaryRequestError(
                "uncertain operation recovery requires exact digest pins"
            )
        proof = _digest(
            {
                "operation_id": operation_id,
                "request_digest": request_digest,
                "execution_profile_digest": self.execution_metadata.profile_digest,
                "recovery": "uncertain_recovered",
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT request_digest, state, error_code, execution_profile_digest "
                    "FROM codex_boundary_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if (
                    row is None
                    or row["request_digest"] != request_digest
                    or row["execution_profile_digest"]
                    != self.execution_metadata.profile_digest
                ):
                    raise CodexBoundaryConflictError(
                        "uncertain operation recovery pin changed"
                    )
                if row["state"] == "failed" and row["error_code"] == "uncertain_recovered":
                    connection.commit()
                    return proof
                if row["state"] != "pending" or row["error_code"] is not None:
                    raise CodexBoundaryStateError(
                        "uncertain operation is not pending"
                    )
                changed = connection.execute(
                    "UPDATE codex_boundary_operations SET state = 'failed', "
                    "response_json = NULL, error_code = 'uncertain_recovered' "
                    "WHERE operation_id = ? AND request_digest = ? AND state = 'pending'",
                    (operation_id, request_digest),
                ).rowcount
                if changed != 1:
                    raise CodexBoundaryUncertainOperationError(
                        "uncertain operation changed during recovery"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return proof

    @staticmethod
    def _coded_error(
        code: str,
        process_event: CodexProcessEventReceipt | None = None,
    ) -> CodexBoundaryError:
        error = _error_from_code(code)
        setattr(error, "_codex_error_code", code)
        if process_event is not None:
            setattr(error, "_codex_process_event", process_event)
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
            observed_session = self._run_and_parse(
                argv, expected_session=session_id, request=request
            )
            return {"status": "resumed", "session_id": observed_session}, None

        task_id = fields["task_id"]
        role_key = fields["role_key"]
        route = self._task_session(task_id, role_key)
        if request.action == "launch":
            if (
                route is not None
                and route[1] == "active"
                and not self._route_is_coordination_session(
                    task_id=task_id,
                    role_key=role_key,
                    session_id=route[0],
                )
            ):
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
            session_id = self._run_and_parse(
                argv, expected_session=None, request=request
            )
            response = {"status": "launched", "agent_id": session_id}
            return response, (task_id, role_key, session_id, "active")

        if route is None:
            raise self._coded_error("missing_session")
        session_id, _lifecycle, route_profile = route
        if route_profile != self.execution_metadata.profile_digest:
            raise self._coded_error("session_profile_mismatch")
        prompt = self._direct_prompt(request)
        argv = [*self.command, "exec", "resume", session_id, "--json", prompt]
        observed_session = self._run_and_parse(
            argv, expected_session=session_id, request=request
        )
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
        if (
            request.action == "launch"
            and _BOOTSTRAP_EVENT.fullmatch(fields["source_event_id"]) is not None
        ):
            instruction = (
                "Initialize this Python-CLI-owned persistent runtime session and then exit. "
                "Read AGENTS.md and the exact Queue task only to establish role context. "
                "Do not edit files, mutate Queue state, create another role, call providers, "
                "or perform account or broker actions. Return only RUNTIME_SESSION_READY."
            )
        elif request.action == "launch":
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

    def _run_and_parse(
        self,
        argv: list[str],
        *,
        expected_session: str | None,
        request: _Request,
    ) -> str:
        result = self._run_process(argv, pins=_process_event_pins(request))
        if result.overflowed:
            raise self._coded_error("output_limit", result.process_event)
        if result.returncode != 0:
            raise self._coded_error("process_failed", result.process_event)
        try:
            text = result.stdout.decode("utf-8")
            events = [json.loads(line) for line in text.splitlines() if line]
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise self._coded_error("invalid_jsonl", result.process_event) from None
        if not all(isinstance(event, dict) for event in events):
            raise self._coded_error("invalid_jsonl", result.process_event)
        if any(event.get("type") == "turn.failed" for event in events):
            raise self._coded_error("process_failed", result.process_event)
        if not any(event.get("type") == "turn.completed" for event in events):
            raise self._coded_error("missing_completion", result.process_event)
        observed_values = [
            event.get("thread_id")
            for event in events
            if event.get("type") == "thread.started"
        ]
        if expected_session is not None and not observed_values:
            observed_values = [expected_session]
        if not all(isinstance(value, str) for value in observed_values):
            raise self._coded_error("invalid_session", result.process_event)
        observed = set(observed_values)
        session_expression = _IDENTIFIER if expected_session is not None else _DIRECT_IDENTIFIER
        if (
            len(observed) != 1
            or session_expression.fullmatch(next(iter(observed))) is None
        ):
            raise self._coded_error("invalid_session", result.process_event)
        session_id = next(iter(observed))
        if expected_session is not None and session_id != expected_session:
            raise self._coded_error("invalid_session", result.process_event)
        return session_id

    def _run_process(
        self, argv: list[str], *, pins: CodexProcessEventPins
    ) -> _ProcessResult:
        creationflags = background_creationflags()
        process = self._process_factory(
            list(argv),
            cwd=str(self.cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
        )
        stdout_capture = _StreamCapture()
        stderr_capture = _StreamCapture()
        budget = _OutputBudget(self.max_output_bytes)

        def process_event() -> CodexProcessEventReceipt:
            return _process_event_receipt(
                pins,
                stdout_capture=stdout_capture,
                stderr_capture=stderr_capture,
                output_overflowed=budget.overflowed,
            )

        if process.stdout is None or process.stderr is None:
            try:
                process.kill()
            finally:
                raise self._coded_error(
                    "process_start_failed", process_event()
                )
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        readers = (
            Thread(
                target=_drain,
                args=(process.stdout, stdout, budget, stdout_capture),
                daemon=True,
            ),
            Thread(
                target=_drain,
                args=(process.stderr, stderr, budget, stderr_capture),
                daemon=True,
            ),
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
            raise self._coded_error("process_timeout", process_event()) from None
        finally:
            if process.poll() is not None:
                for reader in readers:
                    reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            process.kill()
            raise self._coded_error("process_start_failed", process_event())
        return _ProcessResult(
            returncode=int(returncode),
            stdout=stdout.getvalue(),
            overflowed=budget.overflowed,
            process_event=process_event(),
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
                pending_rows = connection.execute(
                    "SELECT operation_id, request_digest, request_kind, "
                    "execution_profile_digest FROM codex_boundary_operations "
                    "WHERE state = 'pending' ORDER BY operation_id"
                ).fetchall()
        except sqlite3.Error as error:
            raise CodexBoundaryStateError(
                "Codex boundary state could not be inspected"
            ) from error
        pending_pins = tuple(
            CodexBoundaryOperationPin(
                str(row[0]), str(row[1]), str(row[2]), str(row[3])
            )
            for row in pending_rows
        )
        return CodexBoundaryStatus(
            operation_counts.get("pending", 0),
            operation_counts.get("completed", 0),
            operation_counts.get("failed", 0),
            session_counts.get("active", 0),
            session_counts.get("settled", 0),
            pending_pins,
        )

    @staticmethod
    def inspect_process_event(
        state_path: Path,
        *,
        pins: CodexProcessEventPins,
        expected_receipt_digest: str | None = None,
    ) -> CodexProcessEventReceipt:
        """Replay one immutable sanitized process-event receipt without mutation."""

        if (
            expected_receipt_digest is not None
            and _DIGEST.fullmatch(expected_receipt_digest) is None
        ):
            raise CodexBoundaryRequestError(
                "expected process-event receipt must be a SHA-256 digest"
            )
        path = Path(state_path)
        if not path.is_file():
            raise CodexBoundaryStateError("Codex boundary state is absent")
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(codex_boundary_operations)"
                    )
                }
                if "process_event_json" not in columns:
                    raise CodexBoundaryStateError(
                        "process-event diagnostic receipt is unavailable"
                    )
                row = connection.execute(
                    "SELECT request_digest, state, error_code, "
                    "execution_profile_digest, process_event_json "
                    "FROM codex_boundary_operations WHERE operation_id = ?",
                    (pins.operation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise CodexBoundaryStateError(
                "process-event diagnostic receipt could not be inspected"
            ) from error
        if (
            row is None
            or row[0] != pins.request_digest
            or row[1] != "failed"
            or row[2] is None
            or row[3] != pins.execution_profile_digest
        ):
            raise CodexBoundaryConflictError(
                "process-event operation pin changed"
            )
        if row[4] is None:
            raise CodexBoundaryStateError(
                "process-event diagnostic receipt is unavailable"
            )
        try:
            payload = json.loads(str(row[4]))
        except (TypeError, json.JSONDecodeError) as error:
            raise CodexBoundaryStateError(
                "stored process-event receipt is invalid"
            ) from error
        receipt = CodexProcessEventReceipt.from_dict(payload)
        if (
            receipt.operation_id != pins.operation_id
            or receipt.request_digest != pins.request_digest
            or receipt.generation_sequence != pins.generation_sequence
            or receipt.generation_digest != pins.generation_digest
            or receipt.execution_profile_digest != pins.execution_profile_digest
        ):
            raise CodexBoundaryConflictError(
                "process-event generation pin changed"
            )
        if (
            expected_receipt_digest is not None
            and receipt.receipt_digest != expected_receipt_digest
        ):
            raise CodexBoundaryConflictError(
                "process-event replay digest changed"
            )
        return receipt

    @staticmethod
    def inspect_terminal_operation(
        state_path: Path, *, operation_id: str
    ) -> CodexBoundaryTerminalOperation:
        """Read one exact failed operation without creating or migrating state."""

        if (
            _DIRECT_OPERATION.fullmatch(operation_id) is None
            and _SESSION_OPERATION.fullmatch(operation_id) is None
        ):
            raise CodexBoundaryRequestError(
                "terminal operation inspection requires an exact operation id"
            )
        path = Path(state_path)
        if not path.is_file():
            raise CodexBoundaryStateError("Codex boundary state is absent")
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                row = connection.execute(
                    "SELECT request_digest, request_kind, state, response_json, "
                    "error_code, execution_profile_digest "
                    "FROM codex_boundary_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise CodexBoundaryStateError(
                "Codex boundary terminal operation could not be inspected"
            ) from error
        if row is None:
            raise CodexBoundaryStateError("Codex boundary operation is absent")
        if row[2] != "failed" or row[3] is not None or row[4] is None:
            raise CodexBoundaryStateError("Codex boundary operation is not failed")
        return CodexBoundaryTerminalOperation(
            operation_id=operation_id,
            request_digest=str(row[0]),
            request_kind=str(row[1]),
            state=str(row[2]),
            error_code=str(row[4]),
            execution_profile_digest=str(row[5]),
        )

    @staticmethod
    def lookup_terminal_operation_mapping(
        state_path: Path,
        *,
        reconciliation_binding: str,
    ) -> CodexBoundaryTerminalOperationMapping:
        """Return the sole exactly-bound failed operation, or fail closed.

        This is intentionally a read-only diagnostic surface.  It parses only
        the existing sanitized process-event receipt and exposes no request
        payload, response, prompt, output, session, or task content.
        """

        if _DIGEST.fullmatch(reconciliation_binding) is None:
            raise CodexBoundaryStateError("terminal reconciliation binding is invalid")
        path = Path(state_path)
        if not path.is_file():
            raise CodexBoundaryStateError("Codex boundary state is absent")
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                rows = connection.execute(
                    "SELECT operation_id, request_digest, request_kind, error_code, "
                    "execution_profile_digest, process_event_json, reconciliation_binding "
                    "FROM codex_boundary_operations WHERE state = 'failed' "
                    "AND reconciliation_binding = ? ORDER BY operation_id",
                    (reconciliation_binding,),
                ).fetchall()
        except sqlite3.Error as error:
            raise CodexBoundaryStateError(
                "Codex boundary terminal mapping could not be inspected"
            ) from error
        if len(rows) != 1:
            raise CodexBoundaryStateError(
                "Codex boundary terminal mapping is absent or ambiguous"
            )
        row = rows[0]
        if row[5] is None or row[6] != reconciliation_binding:
            raise CodexBoundaryStateError("Codex boundary terminal mapping is unavailable")
        try:
            event = CodexProcessEventReceipt.from_dict(json.loads(str(row[5])))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise CodexBoundaryStateError("Codex boundary terminal mapping is corrupt") from error
        if (
            event.operation_id != row[0]
            or event.request_digest != row[1]
            or event.execution_profile_digest != row[4]
            or row[2] not in {"direct", "session"}
            or not isinstance(row[3], str)
        ):
            raise CodexBoundaryStateError("Codex boundary terminal mapping is corrupt")
        return CodexBoundaryTerminalOperationMapping(
            operation_id=str(row[0]),
            request_digest=str(row[1]),
            request_kind=str(row[2]),
            execution_profile_digest=str(row[4]),
            error_code=str(row[3]),
            process_event_receipt_digest=event.receipt_digest,
        )


__all__ = [
    "CodexBoundaryConflictError",
    "CodexBoundaryError",
    "CodexBoundaryOperationPin",
    "CodexBoundaryTerminalOperation",
    "CodexBoundaryTerminalOperationMapping",
    "CodexBoundaryProcessError",
    "CodexBoundaryRequestError",
    "CodexBoundaryStateError",
    "CodexBoundaryUncertainOperationError",
    "CodexBoundaryUnsupportedActionError",
    "CodexBoundaryStatus",
    "CodexCliBoundary",
    "CodexProcessEventPins",
    "CodexProcessEventReceipt",
]
