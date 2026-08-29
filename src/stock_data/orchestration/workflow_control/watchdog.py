"""Read-only reconciliation of durable roles against sanitized Orca facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
import json
from typing import Iterable

from stock_data.orchestration.workflow_control.registry import RoleRecord, RoleState


class RecoveryReason(StrEnum):
    STALE_HEARTBEAT = "STALE_HEARTBEAT"
    TERMINAL_MISSING = "TERMINAL_MISSING"
    STALE_DISPATCH = "STALE_DISPATCH"


@dataclass(frozen=True, slots=True)
class DispatchObservation:
    task_id: str
    dispatch_id: str
    status: str
    agent_process_live: bool


@dataclass(frozen=True, slots=True)
class OrcaObservation:
    observed_at: datetime
    runtime_id: str
    terminal_handles: frozenset[str]
    dispatches: tuple[DispatchObservation, ...]


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


class RoleWatchdog:
    """Pure observer: produces proposals and never writes Registry or Queue state."""

    def __init__(self, *, heartbeat_timeout: timedelta) -> None:
        if heartbeat_timeout <= timedelta(0):
            raise ValueError("heartbeat timeout must be positive")
        self.heartbeat_timeout = heartbeat_timeout

    def inspect(
        self,
        records: Iterable[RoleRecord],
        observation: OrcaObservation,
    ) -> tuple[RecoveryProposal, ...]:
        dispatches = {
            (item.task_id, item.dispatch_id): item for item in observation.dispatches
        }
        proposals: list[RecoveryProposal] = []
        for record in sorted(records, key=lambda item: item.identity.role_key):
            identity = record.identity
            if record.state not in (RoleState.ACTIVE, RoleState.RECOVERY_REQUIRED):
                continue
            reason = self._reason(record, observation, dispatches)
            if reason is None:
                continue
            has_active_attempt = (
                identity.active_task_id is not None
                and identity.active_dispatch_id is not None
            )
            action = (
                "SETTLE_THEN_RETRY_SAME_TASK"
                if has_active_attempt
                else "RESUME_ROLE_SESSION"
            )
            retry_attempt = record.retry_attempt + 1 if has_active_attempt else None
            material = {
                "action": action,
                "reason": reason.value,
                "retry_attempt": retry_attempt,
                "retry_of_dispatch_id": identity.active_dispatch_id,
                "role_generation": record.generation,
                "role_key": identity.role_key,
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
                )
            )
        return tuple(proposals)

    def _reason(
        self,
        record: RoleRecord,
        observation: OrcaObservation,
        dispatches: dict[tuple[str, str], DispatchObservation],
    ) -> RecoveryReason | None:
        identity = record.identity
        if (
            identity.runtime_id != observation.runtime_id
            or identity.terminal_handle is None
            or identity.terminal_handle not in observation.terminal_handles
        ):
            return RecoveryReason.TERMINAL_MISSING
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
            return RecoveryReason.STALE_HEARTBEAT
        return None


def proposal_fingerprint(proposal: RecoveryProposal) -> str:
    """Expose the deterministic provenance token without any runtime payload."""

    return proposal.provenance
