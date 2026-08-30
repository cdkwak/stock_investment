from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
from threading import Barrier, BrokenBarrierError, Event, Lock
from typing import Mapping

import pytest

from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    EventSource,
    Priority,
    TaskState,
    WorkflowEvent,
)
from stock_data.orchestration.workflow_control.controller import (
    ControlGeneration,
    StaleControlGeneration,
    WorkflowController,
    WorkflowControllerError,
)
from stock_data.orchestration.workflow_control.policy import (
    ActionClass,
    ReceiptDecision,
    evaluate_authority,
)
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot
from stock_data.orchestration.workflow_control.routing import (
    BoundaryRequest,
    ExecutionBoundary,
    QueueWorkItem,
    route_execution_boundary,
)
from stock_data.orchestration.workflow_control.runner import (
    InjectedDirectRunner,
    LocalFakeDirectBoundary,
)
from stock_data.orchestration.workflow_control.state import WorkflowStateStore
from stock_data.orchestration.workflow_control.watchdog import (
    RecoveryProposal,
    RecoveryReason,
)
from stock_data.orchestration.workflow_control.registry import RoleState


UTC = timezone.utc
T0 = datetime(2026, 8, 29, tzinfo=UTC)
TASK = "RQ-20260829T093730-C118"
GEN_A = ControlGeneration(1, "a" * 64)
GEN_B = ControlGeneration(2, "b" * 64)


def transition(
    event_id: str,
    minutes: int,
    to_state: TaskState,
    *,
    from_state: TaskState | None = None,
) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id,
        occurred_at=T0 + timedelta(minutes=minutes),
        kind=EventKind.TASK_TRANSITION,
        source=EventSource.QUEUE,
        task_id=TASK,
        from_state=from_state,
        to_state=to_state,
        priority=Priority.P1,
        domain="infra",
        reason_code="QUEUE_TRANSITION",
    )


def controller(tmp_path: Path) -> tuple[WorkflowController, LocalFakeDirectBoundary]:
    fake = LocalFakeDirectBoundary()
    state = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    return (
        WorkflowController(
            state,
            InjectedDirectRunner(fake),
            tmp_path / "controller.sqlite3",
        ),
        fake,
    )


def test_event_pump_launches_settles_and_replays_exact_receipt(tmp_path: Path) -> None:
    pump, fake = controller(tmp_path)
    events = (
        transition("active", 1, TaskState.ACTIVE, from_state=TaskState.READY),
        transition("review", 2, TaskState.REVIEW, from_state=TaskState.ACTIVE),
    )

    first = pump.pump(GEN_A, events)
    replay = pump.pump(GEN_A, reversed(events))

    assert replay == first
    assert first.accepted_event_ids == ("active", "review")
    assert len(first.runner_receipt_digests) == 2
    assert fake.calls == 2
    assert not first.production_mutated
    assert pump.state_store.task_snapshots()[0].state is TaskState.REVIEW


def test_duplicate_input_is_rejected_without_relaunch_on_new_generation(
    tmp_path: Path,
) -> None:
    pump, fake = controller(tmp_path)
    event = transition("active", 1, TaskState.ACTIVE)
    pump.pump(GEN_A, (event,))

    receipt = pump.pump(GEN_B, (event,))

    assert receipt.accepted_event_ids == ()
    assert receipt.duplicate_event_ids == ("active",)
    assert receipt.runner_receipt_digests == ()
    assert fake.calls == 1


def test_concurrent_generations_fence_same_event_before_direct_side_effect(
    tmp_path: Path,
) -> None:
    barrier = Barrier(2)

    class RacingBoundary(LocalFakeDirectBoundary):
        def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
            try:
                barrier.wait(timeout=0.2)
            except BrokenBarrierError:
                pass
            return dict(super().execute(request))

    fake = RacingBoundary()
    state = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    pump = WorkflowController(
        state, InjectedDirectRunner(fake), tmp_path / "controller.sqlite3"
    )
    event = transition("active", 1, TaskState.ACTIVE)

    def run(generation: ControlGeneration) -> str:
        try:
            receipt = pump.pump(generation, (event,))
        except StaleControlGeneration:
            return "stale_generation"
        if receipt.accepted_event_ids:
            return "accepted"
        if receipt.duplicate_event_ids:
            return "duplicate"
        return "unexpected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(run, (GEN_A, GEN_B)))

    assert outcomes.count("accepted") == 1
    assert set(outcomes) <= {"accepted", "duplicate", "stale_generation"}
    assert fake.calls == 1
    assert pump.state_store.event_count() == 1


def test_newer_review_cannot_overtake_unsettled_active_launch(tmp_path: Path) -> None:
    launch_entered = Event()
    release_launch = Event()
    action_lock = Lock()
    actions: list[str] = []

    class BlockingLaunchBoundary(LocalFakeDirectBoundary):
        def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
            with action_lock:
                actions.append(request["action"])
            if request["action"] == "launch":
                launch_entered.set()
                assert release_launch.wait(timeout=2)
            return super().execute(request)

    fake = BlockingLaunchBoundary()
    state = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    pump = WorkflowController(
        state, InjectedDirectRunner(fake), tmp_path / "controller.sqlite3"
    )
    active = transition("active", 1, TaskState.ACTIVE, from_state=TaskState.READY)
    review = transition("review", 2, TaskState.REVIEW, from_state=TaskState.ACTIVE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        active_future = pool.submit(pump.pump, GEN_A, (active,))
        assert launch_entered.wait(timeout=2)
        review_future = pool.submit(pump.pump, GEN_B, (review,))
        assert not review_future.done()
        assert actions == ["launch"]
        release_launch.set()
        active_future.result(timeout=2)
        review_future.result(timeout=2)

    assert actions == ["launch", "settle"]
    assert fake.calls == 2
    assert pump.state_store.task_snapshots()[0].state is TaskState.REVIEW


def test_pump_recovers_idempotently_after_machine_truth_commit(tmp_path: Path) -> None:
    fake = LocalFakeDirectBoundary()
    state = WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl")
    event = transition("active", 1, TaskState.ACTIVE)
    state.record(event)  # Simulate a crash before controller disposition/receipt commit.
    pump = WorkflowController(
        state, InjectedDirectRunner(fake), tmp_path / "controller.sqlite3"
    )

    receipt = pump.pump(GEN_A, (event,))
    replay = pump.pump(GEN_A, (event,))

    assert receipt.accepted_event_ids == ("active",)
    assert replay == receipt
    assert fake.calls == 1


def test_stale_generation_and_stale_lifecycle_event_fail_closed(tmp_path: Path) -> None:
    pump, fake = controller(tmp_path)
    pump.pump(GEN_B, (transition("active-new", 5, TaskState.ACTIVE),))

    with pytest.raises(StaleControlGeneration, match="stale"):
        pump.pump(GEN_A, (transition("done-old-generation", 6, TaskState.DONE),))

    current = ControlGeneration(3, "c" * 64)
    receipt = pump.pump(current, (transition("ready-old-time", 1, TaskState.READY),))
    assert receipt.stale_event_ids == ("ready-old-time",)
    assert pump.state_store.task_snapshots()[0].state is TaskState.ACTIVE
    assert fake.calls == 1


def test_offline_replay_is_deterministic_with_orca_absent(tmp_path: Path) -> None:
    events = (
        transition("active", 1, TaskState.ACTIVE),
        transition("done", 2, TaskState.DONE, from_state=TaskState.ACTIVE),
    )
    left, left_fake = controller(tmp_path / "left")
    right, right_fake = controller(tmp_path / "right")

    left_receipt = left.pump(GEN_A, events)
    right_receipt = right.pump(GEN_A, reversed(events))

    assert left_receipt == right_receipt
    assert left_fake.calls == right_fake.calls == 2
    assert all(not receipt.production_mutated for receipt in (left_receipt, right_receipt))


def test_controller_consumes_canonical_queue_snapshot_and_selects_routes(
    tmp_path: Path,
) -> None:
    pump, fake = controller(tmp_path)
    snapshot = QueueSnapshot(
        observed_at=T0,
        state_counts=(("new", 0), ("waiting", 0), ("ready", 1), ("active", 0),
                      ("review", 0), ("blocked", 0), ("done", 1)),
        active_task_ids=(),
        compacted_count=0,
    )
    receipt = pump.pump_queue_snapshot(GEN_A, snapshot)
    item = QueueWorkItem(
        task_id=TASK,
        state="ready",
        priority="P1",
        lead_owner="lead_infra",
        write_scope=("src/stock_data/new.py",),
        writer_lane="data",
    )

    assert len(receipt.accepted_event_ids) == 1
    assert receipt.runner_receipt_digests == ()
    assert fake.calls == 0
    assert pump.plan_routes((item,)).selected == (item,)


def proposal(*, attempt: int = 1) -> RecoveryProposal:
    material = {
        "action": "SETTLE_THEN_RETRY_SAME_TASK",
        "reason": RecoveryReason.STALE_DISPATCH.value,
        "retry_attempt": attempt,
        "retry_of_dispatch_id": "dispatch-a",
        "role_generation": 4,
        "role_key": "lead_infra",
        "session_id": None,
        "state": RoleState.RECOVERY_REQUIRED.value,
        "task_id": TASK,
    }
    provenance = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RecoveryProposal(
        role_key="lead_infra",
        role_generation=4,
        state=RoleState.RECOVERY_REQUIRED,
        reason=RecoveryReason.STALE_DISPATCH,
        task_id=TASK,
        retry_of_dispatch_id="dispatch-a",
        retry_attempt=attempt,
        action="SETTLE_THEN_RETRY_SAME_TASK",
        provenance=provenance,
    )


def test_stale_dispatch_is_recovered_even_when_terminal_still_looks_connected(
    tmp_path: Path,
) -> None:
    pump, fake = controller(tmp_path)

    connected = pump.recover(
        proposal(), generation=GEN_A, connected_terminal=True, agent_process_live=True
    )
    recovered = pump.recover(
        proposal(), generation=GEN_A, connected_terminal=True, agent_process_live=False
    )
    replay = pump.recover(
        proposal(), generation=GEN_A, connected_terminal=True, agent_process_live=False
    )

    assert connected.action == "SETTLED_AND_RETRIED_SAME_TASK"
    assert connected.runner_receipt_digests == recovered.runner_receipt_digests
    assert recovered.action == "SETTLED_AND_RETRIED_SAME_TASK"
    assert recovered.task_id == TASK
    assert recovered.retry_attempt == 1
    assert recovered.retry_provenance == proposal().provenance
    assert replay.runner_receipt_digests == recovered.runner_receipt_digests
    assert fake.calls == 2


def test_recovery_is_bounded_and_never_retries_an_unverified_live_agent(
    tmp_path: Path,
) -> None:
    pump, fake = controller(tmp_path)

    waiting = pump.recover(
        proposal(), generation=GEN_A, connected_terminal=False, agent_process_live=True
    )
    assert waiting.action == "WAIT_FOR_VERIFIED_TERMINAL"
    assert fake.calls == 0
    with pytest.raises(WorkflowControllerError, match="bounded"):
        pump.recover(
            proposal(attempt=4),
            generation=GEN_A,
            connected_terminal=False,
            agent_process_live=False,
        )


def test_authority_boundaries_remain_fail_closed_for_control_plane() -> None:
    route = route_execution_boundary(BoundaryRequest())
    denied_route = route_execution_boundary(BoundaryRequest(requests_mutation=True))
    prohibited = evaluate_authority(
        ActionClass.ORDER_MUTATION,
        independent_review_passed=True,
        standing_authority=True,
    )

    assert route.boundary is ExecutionBoundary.SANDBOX
    assert denied_route.boundary is ExecutionBoundary.DENIED
    assert prohibited.decision is ReceiptDecision.REFUSED
