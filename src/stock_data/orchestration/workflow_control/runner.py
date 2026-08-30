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


@dataclass(frozen=True, slots=True)
class ExecutionMetadata:
    """Sanitized capability metadata bound to every execution receipt.

    ``mutation_observed`` is deliberately tri-state.  Read-only and local fake
    profiles can prove ``False``.  A workspace-write sandbox exposes the
    capability, but without a scoped filesystem observer it must report
    ``None`` rather than pretending a mutation did or did not occur.
    """

    profile_name: str
    workspace_write_enabled: bool
    mutation_observed: bool | None
    orca_used: bool = False
    profile_digest: str = ""

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.profile_name) is None:
            raise DirectRunnerError("execution profile name must be a bounded identifier")
        if not isinstance(self.workspace_write_enabled, bool):
            raise DirectRunnerError("workspace-write capability must be boolean")
        if self.mutation_observed is not None and not isinstance(
            self.mutation_observed, bool
        ):
            raise DirectRunnerError("mutation observation must be boolean or unknown")
        if self.orca_used:
            raise DirectRunnerError("Python PM execution metadata cannot claim Orca transport")
        expected = _digest(
            {
                "profile_name": self.profile_name,
                "workspace_write_enabled": self.workspace_write_enabled,
                "mutation_observed": self.mutation_observed,
                "orca_used": self.orca_used,
            }
        )
        if self.profile_digest and self.profile_digest != expected:
            raise DirectRunnerError("execution profile digest does not match its content")
        object.__setattr__(self, "profile_digest", expected)

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_name": self.profile_name,
            "profile_digest": self.profile_digest,
            "workspace_write_enabled": self.workspace_write_enabled,
            "mutation_observed": self.mutation_observed,
            "orca_used": self.orca_used,
        }


def execution_metadata_for(boundary: object) -> ExecutionMetadata:
    """Return explicit metadata or a conservative legacy-injected profile."""

    metadata = getattr(boundary, "execution_metadata", None)
    if metadata is None:
        return ExecutionMetadata("legacy_injected_read_only", False, False)
    if not isinstance(metadata, ExecutionMetadata):
        raise DirectRunnerError("boundary execution metadata changed")
    return metadata


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
                raise DirectRunnerError(
                    "runner mutation claim does not match execution metadata"
                )
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


class LocalFakeDirectBoundary:
    """Idempotent local fake used for replay, recovery, and disabled-Orca canaries."""

    def __init__(self) -> None:
        self.execution_metadata = ExecutionMetadata(
            "local_fake_read_only", False, False
        )
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
        self.execution_metadata = execution_metadata_for(boundary)
        self._receipts: list[RunnerReceipt] = []
        self._receipt_lock = RLock()

    @property
    def receipts(self) -> tuple[RunnerReceipt, ...]:
        """Return immutable audit evidence for every boundary invocation."""

        with self._receipt_lock:
            return tuple(self._receipts)

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
            "execution_profile_digest": self.execution_metadata.profile_digest,
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
        receipt = RunnerReceipt(
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
