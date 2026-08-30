"""One deterministic recovery cycle over Queue, role, and transport facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Iterable, Mapping

from stock_data.orchestration.workflow_control.controller import (
    ControlGeneration,
    RecoveryReceipt,
    WorkflowController,
)
from stock_data.orchestration.workflow_control.registry import RoleRecord
from stock_data.orchestration.workflow_control.contracts import utc_text
from stock_data.orchestration.workflow_control.watchdog import (
    OrcaObservation,
    RecoveryProposal,
    RoleWatchdog,
)


@dataclass(frozen=True, slots=True)
class SupervisorCycleReceipt:
    observed_at: datetime
    proposal_provenance: tuple[str, ...]
    recovery_actions: tuple[str, ...]
    recovery_runner_receipts: tuple[str, ...]
    wake_signal_ids: tuple[str, ...] = ()
    wakeup_runner_receipts: tuple[str, ...] = ()
    unhandled_wake_signal_ids: tuple[str, ...] = ()
    production_mutated: bool = False


class WakeKind(StrEnum):
    WORKER_DONE = "worker_done"
    QUESTION = "question"
    ESCALATION = "escalation"
    QUEUE_TRANSITION = "queue_transition"


_SIGNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class WakeSignal:
    """Allowlisted material event; message bodies and prompts are excluded."""

    signal_id: str
    role_key: str
    kind: WakeKind
    occurred_at: datetime

    def __post_init__(self) -> None:
        if _SIGNAL_ID.fullmatch(self.signal_id) is None:
            raise ValueError("wake signal id must be a bounded identifier")
        if _ROLE_KEY.fullmatch(self.role_key) is None:
            raise ValueError("wake role key must be a bounded identifier")
        if not isinstance(self.kind, WakeKind):
            raise ValueError("wake kind must use WakeKind")
        utc_text(self.occurred_at)

    @property
    def provenance(self) -> str:
        material = json.dumps(
            {
                "kind": self.kind.value,
                "occurred_at": utc_text(self.occurred_at),
                "role_key": self.role_key,
                "signal_id": self.signal_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class WorkflowSupervisor:
    """Apply pure watchdog decisions through injected, idempotent runners.

    The supervisor neither edits the Canonical Queue nor assumes that a visible
    terminal is healthy.  It consumes sanitized observations, uses Queue state
    only to choose the correct recovery phase, and delegates all effects to the
    controller's injected boundaries.
    """

    def __init__(self, watchdog: RoleWatchdog, controller: WorkflowController) -> None:
        self.watchdog = watchdog
        self.controller = controller

    def run_cycle(
        self,
        records: Iterable[RoleRecord],
        observation: OrcaObservation,
        *,
        queue_states: Mapping[str, object],
        generation: ControlGeneration,
        wake_signals: Iterable[WakeSignal] = (),
    ) -> SupervisorCycleReceipt:
        ordered_records = tuple(sorted(records, key=lambda item: item.identity.role_key))
        record_by_role = {item.identity.role_key: item for item in ordered_records}
        terminals = {item.terminal_handle: item for item in observation.terminals}
        dispatches = {
            (item.task_id, item.dispatch_id): item for item in observation.dispatches
        }
        proposals = self.watchdog.inspect(
            ordered_records,
            observation,
            queue_states=queue_states,
        )
        recoveries: list[RecoveryReceipt] = []
        for proposal in proposals:
            record = record_by_role[proposal.role_key]
            identity = record.identity
            terminal = (
                terminals.get(identity.terminal_handle)
                if identity.terminal_handle is not None
                else None
            )
            connected = observation.runtime_reachable and (
                (terminal is not None and terminal.connected)
                or (
                    identity.terminal_handle is not None
                    and identity.terminal_handle in observation.terminal_handles
                )
            )
            if terminal is not None:
                process_live = terminal.agent_process_live
            elif identity.active_task_id is not None and identity.active_dispatch_id is not None:
                dispatch = dispatches.get(
                    (identity.active_task_id, identity.active_dispatch_id)
                )
                process_live = bool(dispatch and dispatch.agent_process_live)
            else:
                process_live = False
            recoveries.append(
                self.controller.recover(
                    proposal,
                    generation=generation,
                    connected_terminal=connected,
                    agent_process_live=process_live,
                )
            )
        recovered_roles = {item.role_key for item in proposals}
        wake_ids: list[str] = []
        wake_receipts: list[str] = []
        unhandled: list[str] = []
        for signal in sorted(wake_signals, key=lambda item: (item.occurred_at, item.signal_id)):
            wake_ids.append(signal.signal_id)
            record = record_by_role.get(signal.role_key)
            if record is None:
                unhandled.append(signal.signal_id)
                continue
            if signal.role_key in recovered_roles:
                continue
            wake_receipts.append(
                self.controller.wake_role_session(
                    role_key=signal.role_key,
                    role_generation=record.generation,
                    session_id=record.identity.codex_session_id,
                    provenance=signal.provenance,
                )
            )
        return SupervisorCycleReceipt(
            observed_at=observation.observed_at,
            proposal_provenance=tuple(item.provenance for item in proposals),
            recovery_actions=tuple(item.action for item in recoveries),
            recovery_runner_receipts=tuple(
                digest
                for item in recoveries
                for digest in item.runner_receipt_digests
            ),
            wake_signal_ids=tuple(wake_ids),
            wakeup_runner_receipts=tuple(wake_receipts),
            unhandled_wake_signal_ids=tuple(unhandled),
        )


def proposal_by_role(
    proposals: Iterable[RecoveryProposal],
) -> dict[str, RecoveryProposal]:
    """Return a deterministic role index for adapter/readback code."""

    return {item.role_key: item for item in proposals}
