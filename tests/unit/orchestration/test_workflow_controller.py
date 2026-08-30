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
    MailboxMessageType,
    ReviewDecision,
    ReviewLoopError,
    StaleControlGeneration,
    StaleQueueGeneration,
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
    TaskContract,
    WorkerAssignment,
    route_execution_boundary,
)
from stock_data.orchestration.workflow_control.runner import (
    InjectedDirectRunner,
    LocalFakeDirectBoundary,
)
from stock_data.orchestration.workflow_control.session_runner import (
    InjectedSessionRunner,
    LocalFakeSessionBoundary,
)
from stock_data.orchestration.workflow_control.state import WorkflowStateStore
from stock_data.orchestration.workflow_control.watchdog import (
    RecoveryProposal,
    RecoveryReason,
)
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleKind,
    RoleRegistryError,
    RoleState,
    StaleRoleGeneration,
)


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


def _hierarchy_controller(
    tmp_path: Path,
) -> tuple[WorkflowController, LocalFakeSessionBoundary]:
    sessions = LocalFakeSessionBoundary()
    instance = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
        session_runner=InjectedSessionRunner(sessions),
    )
    identities = (
        RoleIdentity(
            "project_manager", RoleKind.PROJECT_MANAGER, "session-pm",
            "python-control", "repo::C:/workspace", "term-pm", "runtime-a",
        ),
        RoleIdentity(
            "lead_data", RoleKind.DOMAIN_LEAD, "session-lead",
            "python-control", "repo::C:/workspace", "term-lead", "runtime-a",
            parent_role_key="project_manager",
        ),
        RoleIdentity(
            "worker_code", RoleKind.WORKER, "session-worker",
            "python-control", "repo::C:/workspace", "term-worker", "runtime-a",
            parent_role_key="lead_data",
        ),
        RoleIdentity(
            "reviewer_data", RoleKind.REVIEWER, "session-reviewer",
            "python-control", "repo::C:/workspace", "term-reviewer", "runtime-a",
            parent_role_key="lead_data",
        ),
    )
    for identity_value in identities:
        instance.register_role_session(
            identity_value, observed_at=T0, lease_until=T0 + timedelta(hours=1)
        )
    return instance, sessions


def _task_contract(task_id: str, generation: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        queue_generation=generation,
        pm_role_key="project_manager",
        lead_role_key="lead_data",
        reviewer_role_key="reviewer_data",
        write_scope=("src/stock_data",),
        worker_assignments=(
            WorkerAssignment("worker_code", ("src/stock_data/component.py",)),
        ),
    )


def test_persistent_hierarchy_mailbox_ack_and_stale_generation_fence(
    tmp_path: Path,
) -> None:
    instance, sessions = _hierarchy_controller(tmp_path)

    first = instance.resume_session_hierarchy()
    restarted = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
        session_runner=InjectedSessionRunner(sessions),
    )
    replay = restarted.resume_session_hierarchy()

    assert first.role_keys == (
        "project_manager", "lead_data", "reviewer_data", "worker_code"
    )
    assert replay.session_ids == first.session_ids
    assert sessions.calls == 4

    receipt_key = "a" * 64
    message_id = restarted.deliver_pm_message(
        receipt_key=receipt_key, intent_key="b" * 64, message="resume current work"
    )
    pm = restarted.role_registry.get("project_manager")
    ack = restarted.acknowledge_mailbox(
        message_id,
        recipient_role_key="project_manager",
        expected_generation=pm.generation,
        acknowledgement_ref="ack-pm-message",
        observed_at=T0 + timedelta(minutes=1),
    )
    assert ack.recipient_generation_after == pm.generation + 1
    assert restarted.deliver_pm_message(
        receipt_key=receipt_key, intent_key="b" * 64, message="resume current work"
    ) == message_id
    assert not restarted.mailbox("project_manager", pending_only=True)
    assert restarted.acknowledge_mailbox(
        message_id,
        recipient_role_key="project_manager",
        expected_generation=pm.generation,
        acknowledgement_ref="ack-pm-message",
    ) == ack

    concurrent_message = restarted.deliver_pm_message(
        receipt_key="e" * 64, intent_key="f" * 64, message="one concurrent delivery"
    )
    concurrent_pm = restarted.role_registry.get("project_manager")

    def acknowledge_once(_: int):
        return restarted.acknowledge_mailbox(
            concurrent_message,
            recipient_role_key="project_manager",
            expected_generation=concurrent_pm.generation,
            acknowledgement_ref="ack-concurrent",
            observed_at=T0 + timedelta(minutes=1, seconds=30),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent_receipts = tuple(pool.map(acknowledge_once, (1, 2)))
    assert concurrent_receipts[0] == concurrent_receipts[1]

    next_message = restarted.deliver_pm_message(
        receipt_key="c" * 64, intent_key="d" * 64, message="new generation work"
    )
    current = restarted.role_registry.get("project_manager")
    restarted.role_registry.heartbeat(
        "project_manager",
        expected_generation=current.generation,
        observed_at=T0 + timedelta(minutes=2),
        lease_until=T0 + timedelta(hours=1, minutes=2),
    )
    with pytest.raises(StaleRoleGeneration):
        restarted.acknowledge_mailbox(
            next_message,
            recipient_role_key="project_manager",
            expected_generation=current.generation,
            acknowledgement_ref="ack-stale",
        )


def test_task_contract_worker_reviewer_fix_pass_and_third_fix_replan(
    tmp_path: Path,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    pm = instance.role_registry.get("project_manager")
    contract = _task_contract("RQ-20260829T093731-C119", "queue-generation-a")
    delivered = instance.dispatch_task_contract(contract, pm_generation=pm.generation)
    assert delivered.message_type is MailboxMessageType.TASK_CONTRACT
    lead = instance.role_registry.get("lead_data")
    fanout = instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead.generation,
    )
    assert [item.recipient_role_key for item in fanout] == ["worker_code"]
    checkpoint = instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead.generation,
        checkpoint_digest="f" * 64,
    )
    assert instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead.generation,
        checkpoint_digest="f" * 64,
    ) == checkpoint
    with pytest.raises(RoleRegistryError, match="kind"):
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="worker_code",
            lead_generation=instance.role_registry.get("worker_code").generation,
            checkpoint_digest="e" * 64,
        )
    with pytest.raises(StaleRoleGeneration):
        instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=lead.generation + 1,
        )
    with pytest.raises(StaleQueueGeneration):
        instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation="queue-generation-old",
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest="1" * 64,
        )

    reviewer_generation = instance.role_registry.get("reviewer_data").generation
    worker_generation = instance.role_registry.get("worker_code").generation
    for index, candidate in enumerate(("1" * 64, "2" * 64, "3" * 64), start=1):
        review_message, lead_visibility = instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=worker_generation,
            candidate_digest=candidate,
        )
        assert review_message.recipient_role_key == "reviewer_data"
        assert lead_visibility.recipient_role_key == "lead_data"
        result = instance.review_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            reviewer_role_key="reviewer_data",
            reviewer_generation=reviewer_generation,
            candidate_digest=candidate,
            decision=ReviewDecision.FIX,
            reason_code=f"FIX_ROUND_{index}",
        )
    assert result.fix_count == 3
    assert result.state == "replan_required"
    assert {
        item.recipient_role_key
        for item in instance.mailbox("project_manager") + instance.mailbox("lead_data")
        if item.message_type is MailboxMessageType.REPLAN_REQUIRED
    } == {"project_manager", "lead_data"}
    with pytest.raises(ReviewLoopError, match="replan"):
        instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=worker_generation,
            candidate_digest="4" * 64,
        )

    replan = _task_contract(contract.task_id, "queue-generation-b")
    instance.dispatch_task_contract(replan, pm_generation=pm.generation)
    instance.dispatch_workers(
        task_id=replan.task_id,
        queue_generation=replan.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead.generation,
    )
    instance.submit_worker_candidate(
        task_id=replan.task_id,
        queue_generation=replan.queue_generation,
        worker_role_key="worker_code",
        worker_generation=worker_generation,
        candidate_digest="5" * 64,
    )
    passed = instance.review_worker_candidate(
        task_id=replan.task_id,
        queue_generation=replan.queue_generation,
        worker_role_key="worker_code",
        reviewer_role_key="reviewer_data",
        reviewer_generation=reviewer_generation,
        candidate_digest="5" * 64,
        decision=ReviewDecision.PASS,
        reason_code="PASS_VERIFIED",
    )
    assert passed.state == "passed_to_lead"
    assert instance.review_worker_candidate(
        task_id=replan.task_id,
        queue_generation=replan.queue_generation,
        worker_role_key="worker_code",
        reviewer_role_key="reviewer_data",
        reviewer_generation=reviewer_generation,
        candidate_digest="5" * 64,
        decision=ReviewDecision.PASS,
        reason_code="PASS_VERIFIED",
    ) == passed
