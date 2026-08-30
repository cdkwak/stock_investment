"""Read-only reconciliation of durable roles against sanitized Orca facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
import json
import re
from typing import Iterable, Mapping

from stock_data.orchestration.workflow_control.registry import RoleRecord, RoleState


class RecoveryReason(StrEnum):
    STALE_HEARTBEAT = "STALE_HEARTBEAT"
    TERMINAL_MISSING = "TERMINAL_MISSING"
    STALE_DISPATCH = "STALE_DISPATCH"
    INTERACTIVE_INPUT = "INTERACTIVE_INPUT"
    TRANSPORT_UNAVAILABLE = "TRANSPORT_UNAVAILABLE"


class TerminalCondition(StrEnum):
    """Sanitized terminal condition; raw prompts are never persisted."""

    UNKNOWN = "UNKNOWN"
    PROGRESS = "PROGRESS"
    AGENT_IDLE = "AGENT_IDLE"
    INPUT_REQUIRED = "INPUT_REQUIRED"


_POWERSHELL_PARAMETER_PROMPT = re.compile(
    r"Supply values for the following parameters:.*(?:\n|\r\n?)\s*[A-Za-z][A-Za-z0-9_-]*:\s*$",
    re.IGNORECASE | re.DOTALL,
)
_POWERSHELL_CONFIRMATION = re.compile(
    r"(?:Are you sure you want to continue|Confirm\s*\r?\n|\[[Yy]\].*\[[Nn]\])",
    re.IGNORECASE | re.DOTALL,
)
_POWERSHELL_CONTINUATION = re.compile(r"(?m)^\s*>>\s*$")


def classify_terminal_preview(preview: str) -> TerminalCondition:
    """Classify only bounded input-wait signatures and discard the raw text.

    The caller may supply a terminal preview transiently.  This function emits
    an enum only; previews, prompts, command text, and transcripts never enter
    the durable registry or workflow event ledger.
    """

    if not isinstance(preview, str):
        raise TypeError("terminal preview must be text")
    bounded = preview[-4096:]
    if (
        _POWERSHELL_PARAMETER_PROMPT.search(bounded)
        or _POWERSHELL_CONFIRMATION.search(bounded)
        or _POWERSHELL_CONTINUATION.search(bounded)
    ):
        return TerminalCondition.INPUT_REQUIRED
    return TerminalCondition.UNKNOWN


@dataclass(frozen=True, slots=True)
class DispatchObservation:
    task_id: str
    dispatch_id: str
    status: str
    agent_process_live: bool


@dataclass(frozen=True, slots=True)
class TerminalObservation:
    terminal_handle: str
    connected: bool
    agent_process_live: bool
    last_output_at: datetime | None = None
    condition: TerminalCondition = TerminalCondition.UNKNOWN
    condition_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class OrcaObservation:
    observed_at: datetime
    runtime_id: str
    terminal_handles: frozenset[str]
    dispatches: tuple[DispatchObservation, ...]
    runtime_reachable: bool = True
    terminals: tuple[TerminalObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryProposal:
    role_key: str
    role_generation: int
    state: RoleState
    reason: RecoveryReason
    task_id: str | None
    retry_of_dispatch_id: str | None
    retry_attempt: int | None
    action: str
    provenance: str
    session_id: str | None = None


class RoleWatchdog:
    """Pure observer: produces proposals and never writes Registry or Queue state."""

    def __init__(
        self,
        *,
        heartbeat_timeout: timedelta,
        prompt_timeout: timedelta = timedelta(seconds=30),
    ) -> None:
        if heartbeat_timeout <= timedelta(0):
            raise ValueError("heartbeat timeout must be positive")
        if prompt_timeout <= timedelta(0):
            raise ValueError("prompt timeout must be positive")
        self.heartbeat_timeout = heartbeat_timeout
        self.prompt_timeout = prompt_timeout

    def inspect(
        self,
        records: Iterable[RoleRecord],
        observation: OrcaObservation,
        *,
        queue_states: Mapping[str, object] | None = None,
    ) -> tuple[RecoveryProposal, ...]:
        queue_states = queue_states or {}
        dispatches = {
            (item.task_id, item.dispatch_id): item for item in observation.dispatches
        }
        terminals = {item.terminal_handle: item for item in observation.terminals}
        proposals: list[RecoveryProposal] = []
        for record in sorted(records, key=lambda item: item.identity.role_key):
            identity = record.identity
            if record.state not in (RoleState.ACTIVE, RoleState.RECOVERY_REQUIRED):
                continue
            reason = self._reason(record, observation, dispatches, terminals)
            if reason is None:
                continue
            has_active_attempt = (
                identity.active_task_id is not None
                and identity.active_dispatch_id is not None
            )
            queue_state = queue_states.get(identity.active_task_id or "")
            action = self._action(
                reason=reason,
                has_active_attempt=has_active_attempt,
                queue_state=queue_state,
            )
            retry_attempt = (
                record.retry_attempt + 1
                if action in {"SETTLE_THEN_RETRY_SAME_TASK", "RETRY_REVIEW_ONLY"}
                else None
            )
            material = {
                "action": action,
                "reason": reason.value,
                "retry_attempt": retry_attempt,
                "retry_of_dispatch_id": identity.active_dispatch_id,
                "role_generation": record.generation,
                "role_key": identity.role_key,
                "session_id": identity.codex_session_id,
                "state": RoleState.RECOVERY_REQUIRED.value,
                "task_id": identity.active_task_id,
            }
            provenance = hashlib.sha256(
                json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            proposals.append(
                RecoveryProposal(
                    role_key=identity.role_key,
                    role_generation=record.generation,
                    state=RoleState.RECOVERY_REQUIRED,
                    reason=reason,
                    task_id=identity.active_task_id,
                    retry_of_dispatch_id=identity.active_dispatch_id,
                    retry_attempt=retry_attempt,
                    action=action,
                    provenance=provenance,
                    session_id=identity.codex_session_id,
                )
            )
        return tuple(proposals)

    @staticmethod
    def _queue_state(value: object) -> str | None:
        if value is None:
            return None
        raw = getattr(value, "value", value)
        return str(raw).strip().lower() or None

    def _action(
        self,
        *,
        reason: RecoveryReason,
        has_active_attempt: bool,
        queue_state: object,
    ) -> str:
        if reason is RecoveryReason.TRANSPORT_UNAVAILABLE:
            return "WAIT_FOR_DIRECT_HEALTH_PROBE"
        if not has_active_attempt:
            if reason is RecoveryReason.INTERACTIVE_INPUT:
                return "INTERRUPT_THEN_RESUME_ROLE_SESSION"
            return "RESUME_ROLE_SESSION"
        state = self._queue_state(queue_state)
        if state is None:
            return "WAIT_FOR_QUEUE_RECONCILIATION"
        if state == "review":
            return "RETRY_REVIEW_ONLY"
        if state in {"ready", "waiting", "blocked", "done"}:
            return "SETTLE_STALE_EXECUTION_RECEIPT"
        return "SETTLE_THEN_RETRY_SAME_TASK"

    def _reason(
        self,
        record: RoleRecord,
        observation: OrcaObservation,
        dispatches: dict[tuple[str, str], DispatchObservation],
        terminals: dict[str, TerminalObservation],
    ) -> RecoveryReason | None:
        identity = record.identity
        if not observation.runtime_reachable:
            return RecoveryReason.TRANSPORT_UNAVAILABLE
        terminal = (
            terminals.get(identity.terminal_handle)
            if identity.terminal_handle is not None
            else None
        )
        terminal_present = (
            identity.terminal_handle is not None
            and (
                identity.terminal_handle in observation.terminal_handles
                or (terminal is not None and terminal.connected)
            )
        )
        if (
            identity.runtime_id != observation.runtime_id
            or identity.terminal_handle is None
            or not terminal_present
        ):
            return RecoveryReason.TERMINAL_MISSING
        if terminal is not None and terminal.condition is TerminalCondition.INPUT_REQUIRED:
            since = terminal.condition_since or terminal.last_output_at
            if since is None or since <= observation.observed_at - self.prompt_timeout:
                return RecoveryReason.INTERACTIVE_INPUT
        if identity.active_task_id is not None and identity.active_dispatch_id is not None:
            dispatch = dispatches.get(
                (identity.active_task_id, identity.active_dispatch_id)
            )
            if dispatch is None or dispatch.status in {"failed", "stopped", "cancelled"}:
                return RecoveryReason.STALE_DISPATCH
            if dispatch.status == "active" and not dispatch.agent_process_live:
                return RecoveryReason.STALE_DISPATCH
        stale_before = observation.observed_at - self.heartbeat_timeout
        if record.heartbeat_at < stale_before or record.lease_until <= observation.observed_at:
            if (
                terminal is not None
                and terminal.agent_process_live
                and terminal.last_output_at is not None
                and terminal.last_output_at >= stale_before
            ):
                return None
            return RecoveryReason.STALE_HEARTBEAT
        return None


def proposal_fingerprint(proposal: RecoveryProposal) -> str:
    """Expose the deterministic provenance token without any runtime payload."""

    return proposal.provenance
