"""Transport-neutral, idempotent recovery runner for reusable role sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from threading import RLock
from typing import Mapping, Protocol

from stock_data.orchestration.workflow_control.runner import (
    ExecutionMetadata,
    execution_metadata_for,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/\\-]{0,254}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class SessionRunnerError(ValueError):
    """Raised when role-session recovery violates its narrow contract."""


class SessionAction(StrEnum):
    INTERRUPT = "interrupt"
    RESUME = "resume"


class SessionBoundary(Protocol):
    """Injected host boundary; implementations must honor ``operation_id``."""

    def execute(self, request: Mapping[str, str]) -> Mapping[str, str]: ...


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionReceipt:
    operation_id: str
    action: SessionAction
    role_key: str
    role_generation: int
    session_id: str
    provenance: str
    status: str
    transport: str = "direct"
    orca_used: bool = False
    production_mutated: bool = False
    execution_profile: str = ""
    execution_profile_digest: str = ""
    workspace_write_enabled: bool = False
    mutation_observed: bool | None = False
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
            if self.production_mutated is not (metadata.mutation_observed is True):
                raise SessionRunnerError(
                    "session mutation claim does not match execution metadata"
                )
        expected = _digest(self.to_dict(include_digest=False))
        if self.receipt_digest and self.receipt_digest != expected:
            raise SessionRunnerError("session receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "operation_id": self.operation_id,
            "action": self.action.value,
            "role_key": self.role_key,
            "role_generation": self.role_generation,
            "session_id": self.session_id,
            "provenance": self.provenance,
            "status": self.status,
            "transport": self.transport,
            "orca_used": self.orca_used,
            "production_mutated": self.production_mutated,
        }
        if self.execution_profile_digest:
            value.update(
                {
                    "execution_profile": self.execution_profile,
                    "execution_profile_digest": self.execution_profile_digest,
                    "workspace_write_enabled": self.workspace_write_enabled,
                    "mutation_observed": self.mutation_observed,
                }
            )
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value


class LocalFakeSessionBoundary:
    """Thread-safe offline double for disabled-transport supervisor canaries."""

    def __init__(self) -> None:
        self.execution_metadata = ExecutionMetadata(
            "local_fake_read_only", False, False
        )
        self._responses: dict[str, tuple[str, dict[str, str]]] = {}
        self._lock = RLock()
        self.calls = 0
        self.actions: list[str] = []

    def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
        with self._lock:
            operation_id = request.get("operation_id", "")
            canonical = _canonical(dict(request))
            existing = self._responses.get(operation_id)
            if existing is not None:
                if existing[0] != canonical:
                    raise SessionRunnerError(
                        "session operation_id was reused with different content"
                    )
                return dict(existing[1])
            action = request.get("action")
            if action not in {item.value for item in SessionAction}:
                raise SessionRunnerError("session boundary received an unknown action")
            response = {
                "status": {
                    SessionAction.INTERRUPT.value: "interrupted",
                    SessionAction.RESUME.value: "resumed",
                }[action],
                "session_id": request.get("session_id", ""),
            }
            self._responses[operation_id] = (canonical, response)
            self.calls += 1
            self.actions.append(action)
            return dict(response)


class InjectedSessionRunner:
    """Apply only identifier-bound interrupt/resume operations through a host."""

    def __init__(self, boundary: SessionBoundary) -> None:
        self._boundary = boundary
        self.execution_metadata = execution_metadata_for(boundary)
        self._receipts: list[SessionReceipt] = []
        self._receipt_lock = RLock()

    @property
    def receipts(self) -> tuple[SessionReceipt, ...]:
        """Return immutable audit evidence for every session-boundary invocation."""

        with self._receipt_lock:
            return tuple(self._receipts)

    def run(
        self,
        action: SessionAction,
        *,
        role_key: str,
        role_generation: int,
        session_id: str,
        provenance: str,
    ) -> SessionReceipt:
        if not isinstance(action, SessionAction):
            raise SessionRunnerError("action must use SessionAction")
        for value, label in ((role_key, "role key"), (session_id, "session id")):
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise SessionRunnerError(f"{label} must be a bounded identifier")
        if (
            not isinstance(role_generation, int)
            or isinstance(role_generation, bool)
            or role_generation < 1
        ):
            raise SessionRunnerError("role generation must be positive")
        if not isinstance(provenance, str) or _DIGEST.fullmatch(provenance) is None:
            raise SessionRunnerError("session provenance must be a SHA-256 digest")
        material: dict[str, object] = {
            "action": action.value,
            "provenance": provenance,
            "role_generation": role_generation,
            "role_key": role_key,
            "session_id": session_id,
            "execution_profile_digest": self.execution_metadata.profile_digest,
        }
        operation_id = "session-op-" + _digest(material)
        request = {key: str(value) for key, value in material.items()}
        request["operation_id"] = operation_id
        response = dict(self._boundary.execute(request))
        if set(response) != {"status", "session_id"}:
            raise SessionRunnerError("session boundary response fields changed")
        expected_status = {
            SessionAction.INTERRUPT: "interrupted",
            SessionAction.RESUME: "resumed",
        }[action]
        if response["status"] != expected_status or response["session_id"] != session_id:
            raise SessionRunnerError("session boundary returned an invalid response")
        receipt = SessionReceipt(
            operation_id=operation_id,
            action=action,
            role_key=role_key,
            role_generation=role_generation,
            session_id=session_id,
            provenance=provenance,
            status=response["status"],
            orca_used=self.execution_metadata.orca_used,
            production_mutated=self.execution_metadata.mutation_observed is True,
            execution_profile=self.execution_metadata.profile_name,
            execution_profile_digest=self.execution_metadata.profile_digest,
            workspace_write_enabled=self.execution_metadata.workspace_write_enabled,
            mutation_observed=self.execution_metadata.mutation_observed,
        )
        with self._receipt_lock:
            self._receipts.append(receipt)
        return receipt
