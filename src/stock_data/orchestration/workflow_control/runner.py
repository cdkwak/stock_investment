"""Injected direct agent runner with deterministic, side-effect-bounded receipts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from threading import RLock
from typing import Mapping, Protocol


_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class DirectRunnerError(ValueError):
    """Raised when a direct-runner request or response violates the contract."""


class RunnerAction(StrEnum):
    LAUNCH = "launch"
    RESUME = "resume"
    SETTLE = "settle"


class DirectAgentBoundary(Protocol):
    """Narrow injected boundary; implementations must honor ``operation_id``."""

    def execute(self, request: Mapping[str, str]) -> Mapping[str, str]: ...


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RunnerReceipt:
    operation_id: str
    action: RunnerAction
    task_id: str
    role_key: str
    generation: str
    status: str
    agent_id: str
    attempt: int
    retry_of: str | None
    retry_provenance: str | None
    transport: str = "direct"
    orca_used: bool = False
    production_mutated: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        material = self.to_dict(include_digest=False)
        expected = _digest(material)
        if self.receipt_digest and self.receipt_digest != expected:
            raise DirectRunnerError("runner receipt digest does not match immutable content")
        object.__setattr__(self, "receipt_digest", expected)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "operation_id": self.operation_id,
            "action": self.action.value,
            "task_id": self.task_id,
            "role_key": self.role_key,
            "generation": self.generation,
            "status": self.status,
            "agent_id": self.agent_id,
            "attempt": self.attempt,
            "retry_of": self.retry_of,
            "retry_provenance": self.retry_provenance,
            "transport": self.transport,
            "orca_used": self.orca_used,
            "production_mutated": self.production_mutated,
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value


class LocalFakeDirectBoundary:
    """Idempotent local fake used for replay, recovery, and disabled-Orca canaries."""

    def __init__(self) -> None:
        self._responses: dict[str, tuple[str, dict[str, str]]] = {}
        self._lock = RLock()
        self.calls = 0

    def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
        with self._lock:
            operation_id = request.get("operation_id", "")
            canonical = _canonical(dict(request))
            existing = self._responses.get(operation_id)
            if existing is not None:
                if existing[0] != canonical:
                    raise DirectRunnerError("operation_id was reused with different content")
                return dict(existing[1])
            action = request.get("action")
            if action not in {item.value for item in RunnerAction}:
                raise DirectRunnerError("fake boundary received an unknown action")
            response = {
                "status": {
                    RunnerAction.LAUNCH.value: "launched",
                    RunnerAction.RESUME.value: "resumed",
                    RunnerAction.SETTLE.value: "settled",
                }[action],
                "agent_id": "local-" + hashlib.sha256(
                    f"{request.get('role_key')}|{request.get('task_id')}".encode("utf-8")
                ).hexdigest()[:20],
            }
            self._responses[operation_id] = (canonical, response)
            self.calls += 1
            return dict(response)


class InjectedDirectRunner:
    """Run launch/resume/settlement through a supplied local implementation.

    Orca is deliberately absent from this class.  A caller may wrap this runner
    in an optional transport adapter, but direct execution and its canary never
    require Orca IPC and never activate production.
    """

    def __init__(self, boundary: DirectAgentBoundary) -> None:
        self._boundary = boundary

    def run(
        self,
        action: RunnerAction,
        *,
        task_id: str,
        role_key: str,
        generation: str,
        source_event_id: str,
        attempt: int = 0,
        retry_of: str | None = None,
        retry_provenance: str | None = None,
    ) -> RunnerReceipt:
        if not isinstance(action, RunnerAction):
            raise DirectRunnerError("action must use RunnerAction")
        if _TASK_ID.fullmatch(task_id) is None:
            raise DirectRunnerError("task_id must be an exact Queue task id")
        if _IDENTIFIER.fullmatch(role_key) is None or _IDENTIFIER.fullmatch(source_event_id) is None:
            raise DirectRunnerError("role and event identifiers must be bounded")
        if _DIGEST.fullmatch(generation) is None:
            raise DirectRunnerError("generation must be a SHA-256 digest")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
            raise DirectRunnerError("attempt must be a non-negative integer")
        if action is RunnerAction.RESUME:
            if attempt < 1 or retry_of is None or retry_provenance is None:
                raise DirectRunnerError("resume requires exact retry provenance")
        elif retry_of is not None or retry_provenance is not None:
            raise DirectRunnerError("retry provenance is valid only for resume")
        for value, label in ((retry_of, "retry_of"),):
            if value is not None and _IDENTIFIER.fullmatch(value) is None:
                raise DirectRunnerError(f"{label} must be a bounded identifier")
        if retry_provenance is not None and _DIGEST.fullmatch(retry_provenance) is None:
            raise DirectRunnerError("retry provenance must be a SHA-256 digest")
        operation_material: dict[str, object] = {
            "action": action.value,
            "attempt": attempt,
            "retry_of": retry_of,
            "retry_provenance": retry_provenance,
            "role_key": role_key,
            "source_event_id": source_event_id,
            "task_id": task_id,
        }
        operation_id = "op-" + _digest(operation_material)
        request = {
            key: "" if value is None else str(value)
            for key, value in operation_material.items()
        }
        request["operation_id"] = operation_id
        response = dict(self._boundary.execute(request))
        if set(response) != {"status", "agent_id"}:
            raise DirectRunnerError("direct boundary response fields changed")
        expected_status = {
            RunnerAction.LAUNCH: "launched",
            RunnerAction.RESUME: "resumed",
            RunnerAction.SETTLE: "settled",
        }[action]
        if response["status"] != expected_status:
            raise DirectRunnerError("direct boundary returned an invalid status")
        if _IDENTIFIER.fullmatch(response["agent_id"]) is None:
            raise DirectRunnerError("direct boundary returned an invalid agent id")
        return RunnerReceipt(
            operation_id=operation_id,
            action=action,
            task_id=task_id,
            role_key=role_key,
            generation=generation,
            status=response["status"],
            agent_id=response["agent_id"],
            attempt=attempt,
            retry_of=retry_of,
            retry_provenance=retry_provenance,
        )
