"""Offline fake-agent simulator for workflow recovery tests and demonstrations."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleRecord,
    RoleRegistry,
    RoleState,
)
from stock_data.orchestration.workflow_control.watchdog import (
    DispatchObservation,
    OrcaObservation,
    RecoveryProposal,
    RoleWatchdog,
)


class FakeAgentSimulator:
    """Models agent/terminal loss without starting, signaling, or killing a process."""

    def __init__(
        self,
        registry_path: Path,
        *,
        runtime_id: str,
        heartbeat_timeout: timedelta = timedelta(minutes=5),
    ) -> None:
        self.registry = RoleRegistry(registry_path)
        self.runtime_id = runtime_id
        self.watchdog = RoleWatchdog(heartbeat_timeout=heartbeat_timeout)
        self._terminals: set[str] = set()
        self._dispatches: dict[tuple[str, str], DispatchObservation] = {}

    def start(
        self,
        identity: RoleIdentity,
        *,
        observed_at: datetime,
        lease_for: timedelta,
    ) -> RoleRecord:
        record = self.registry.claim(
            identity, observed_at=observed_at, lease_until=observed_at + lease_for
        )
        if identity.terminal_handle is not None:
            self._terminals.add(identity.terminal_handle)
        if identity.active_task_id is not None and identity.active_dispatch_id is not None:
            self._dispatches[(identity.active_task_id, identity.active_dispatch_id)] = (
                DispatchObservation(
                    task_id=identity.active_task_id,
                    dispatch_id=identity.active_dispatch_id,
                    status="active",
                    agent_process_live=True,
                )
            )
        return record

    def kill_fake_agent(self, role_key: str) -> None:
        """Mark only the in-memory fake process dead; performs no system action."""

        record = self.registry.get(role_key)
        key = (record.identity.active_task_id, record.identity.active_dispatch_id)
        dispatch = self._dispatches.get(key)
        if dispatch is not None:
            self._dispatches[key] = DispatchObservation(
                task_id=dispatch.task_id,
                dispatch_id=dispatch.dispatch_id,
                status="active",
                agent_process_live=False,
            )

    def lose_terminal(self, terminal_handle: str) -> None:
        self._terminals.discard(terminal_handle)

    def observe(self, *, observed_at: datetime) -> tuple[RecoveryProposal, ...]:
        records = self.registry.records()
        snapshot = OrcaObservation(
            observed_at=observed_at,
            runtime_id=self.runtime_id,
            terminal_handles=frozenset(self._terminals),
            dispatches=tuple(self._dispatches.values()),
        )
        queue_states = {
            record.identity.active_task_id: "active"
            for record in records
            if record.identity.active_task_id is not None
        }
        return self.watchdog.inspect(records, snapshot, queue_states=queue_states)

    def recover(
        self,
        proposal: RecoveryProposal,
        *,
        new_terminal_handle: str,
        new_dispatch_id: str,
        observed_at: datetime,
        lease_for: timedelta,
    ) -> RoleRecord:
        """Explicitly apply one fake recovery proposal with exact CAS provenance."""

        if (
            proposal.task_id is None
            or proposal.retry_of_dispatch_id is None
            or proposal.retry_attempt is None
        ):
            raise ValueError("taskless role recovery has no Dispatch retry to apply")
        current = self.registry.get(proposal.role_key)
        if (
            current.state is RoleState.ACTIVE
            and current.identity.active_task_id == proposal.task_id
            and current.identity.active_dispatch_id == new_dispatch_id
            and current.retry_of_dispatch_id == proposal.retry_of_dispatch_id
            and current.retry_attempt == proposal.retry_attempt
            and current.retry_provenance == proposal.provenance
        ):
            self._rebuild_fake_readback(
                proposal,
                new_terminal_handle=new_terminal_handle,
                new_dispatch_id=new_dispatch_id,
            )
            return current
        if (
            current.state is RoleState.RECOVERY_REQUIRED
            and current.generation == proposal.role_generation + 1
            and current.identity.active_task_id == proposal.task_id
            and current.identity.active_dispatch_id == proposal.retry_of_dispatch_id
        ):
            marked = current
        else:
            marked = self.registry.mark_recovery_required(
                proposal.role_key, expected_generation=proposal.role_generation
            )
        record = self.registry.register_retry(
            proposal.role_key,
            expected_generation=marked.generation,
            task_id=proposal.task_id,
            retry_of_dispatch_id=proposal.retry_of_dispatch_id,
            new_dispatch_id=new_dispatch_id,
            terminal_handle=new_terminal_handle,
            runtime_id=self.runtime_id,
            retry_attempt=proposal.retry_attempt,
            retry_provenance=proposal.provenance,
            observed_at=observed_at,
            lease_until=observed_at + lease_for,
        )
        self._rebuild_fake_readback(
            proposal,
            new_terminal_handle=new_terminal_handle,
            new_dispatch_id=new_dispatch_id,
        )
        return record

    def _rebuild_fake_readback(
        self,
        proposal: RecoveryProposal,
        *,
        new_terminal_handle: str,
        new_dispatch_id: str,
    ) -> None:
        assert proposal.task_id is not None
        assert proposal.retry_of_dispatch_id is not None
        prior = self._dispatches.get((proposal.task_id, proposal.retry_of_dispatch_id))
        if prior is not None:
            self._dispatches[(prior.task_id, prior.dispatch_id)] = DispatchObservation(
                task_id=prior.task_id,
                dispatch_id=prior.dispatch_id,
                status="stopped",
                agent_process_live=False,
            )
        self._terminals.add(new_terminal_handle)
        self._dispatches[(proposal.task_id, new_dispatch_id)] = DispatchObservation(
            task_id=proposal.task_id,
            dispatch_id=new_dispatch_id,
            status="active",
            agent_process_live=True,
        )
