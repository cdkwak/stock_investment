from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
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
    MailboxConflict,
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
    RoleAction,
    TaskContract,
    WorkerAssignment,
    RoutingError,
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
    assert ack.recipient_generation_after == pm.generation
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

    same_generation_messages = (
        restarted.deliver_pm_message(
            receipt_key="1" * 64, intent_key="2" * 64, message="first same generation"
        ),
        restarted.deliver_pm_message(
            receipt_key="3" * 64, intent_key="4" * 64, message="second same generation"
        ),
    )
    same_generation = restarted.role_registry.get("project_manager").generation
    for index, pending_message in enumerate(same_generation_messages):
        receipt = restarted.acknowledge_mailbox(
            pending_message,
            recipient_role_key="project_manager",
            expected_generation=same_generation,
            acknowledgement_ref=f"ack-same-{index}",
            observed_at=T0 + timedelta(minutes=1, seconds=40 + index),
        )
        assert receipt.recipient_generation_after == same_generation
    assert restarted.role_registry.get("project_manager").generation == same_generation

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


def test_mailbox_rejects_body_and_digest_tampering_before_ack(tmp_path: Path) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    message_id = instance.deliver_pm_message(
        receipt_key="8" * 64, intent_key="9" * 64, message="original"
    )
    tampered_json = json.dumps(
        {"intent_key": "9" * 64, "message": "tampered", "receipt_key": "8" * 64},
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        connection.execute(
            "UPDATE role_mailbox SET body_json = ?, body_digest = ? WHERE message_id = ?",
            (tampered_json, hashlib.sha256(tampered_json.encode()).hexdigest(), message_id),
        )

    with pytest.raises(WorkflowControllerError, match="integrity|rebound"):
        instance.mailbox("project_manager")
    with pytest.raises(WorkflowControllerError, match="integrity|rebound"):
        instance.acknowledge_mailbox(
            message_id,
            recipient_role_key="project_manager",
            expected_generation=instance.role_registry.get("project_manager").generation,
            acknowledgement_ref="ack-tampered",
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


def test_conflicting_concurrent_reviews_have_one_atomic_durable_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093732-C120", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    candidate = "6" * 64
    instance.submit_worker_candidate(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        worker_role_key="worker_code",
        worker_generation=instance.role_registry.get("worker_code").generation,
        candidate_digest=candidate,
    )

    original_enqueue = instance._enqueue_mailbox
    settlement_barrier = Barrier(2)

    def racing_enqueue(**kwargs: object):
        if kwargs.get("message_type") in {MailboxMessageType.PASS, MailboxMessageType.FIX}:
            settlement_barrier.wait(timeout=2)
        return original_enqueue(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(instance, "_enqueue_mailbox", racing_enqueue)

    def settle(decision: ReviewDecision) -> str:
        try:
            instance.review_worker_candidate(
                task_id=contract.task_id,
                queue_generation=contract.queue_generation,
                worker_role_key="worker_code",
                reviewer_role_key="reviewer_data",
                reviewer_generation=instance.role_registry.get("reviewer_data").generation,
                candidate_digest=candidate,
                decision=decision,
                reason_code=f"CONCURRENT_{decision.value}",
            )
            return "settled"
        except ReviewLoopError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(settle, (ReviewDecision.PASS, ReviewDecision.FIX)))
    assert outcomes.count("settled") == 1
    settlement_messages = [
        item
        for recipient in ("lead_data", "worker_code")
        for item in instance.mailbox(recipient)
        if item.message_type in {MailboxMessageType.PASS, MailboxMessageType.FIX}
    ]
    assert len(settlement_messages) == 1
    with sqlite3.connect(instance.receipt_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_receipt").fetchone()[0] == 1


def test_controller_refuses_reviewer_session_reused_by_worker(tmp_path: Path) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    registry_path = instance.receipt_path.with_name("role_registry.sqlite3")
    with sqlite3.connect(registry_path) as connection:
        connection.execute("DROP INDEX uq_role_registry_codex_session")
        connection.execute(
            "UPDATE role_registry SET codex_session_id = ? WHERE role_key = ?",
            ("session-worker", "reviewer_data"),
        )

    with pytest.raises(RoutingError, match="unique Codex sessions"):
        instance.dispatch_task_contract(
            _task_contract("RQ-20260829T093733-C121", "queue-generation-a"),
            pm_generation=instance.role_registry.get("project_manager").generation,
        )


@pytest.mark.parametrize(
    ("case", "sender_role_key", "sender_action"),
    (
        ("pm_dispatch", "project_manager", RoleAction.ASSIGN_LEAD),
        ("lead_dispatch", "lead_data", RoleAction.DISPATCH_WORKER),
        ("lead_checkpoint", "lead_data", RoleAction.PROGRESS_CHECKPOINT),
        ("worker_candidate", "worker_code", RoleAction.SUBMIT_CANDIDATE),
        ("reviewer_pass", "reviewer_data", RoleAction.REVIEW_PASS),
    ),
)
@pytest.mark.parametrize("iteration", range(3))
def test_sender_generation_race_has_zero_durable_controller_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    sender_role_key: str,
    sender_action: RoleAction,
    iteration: int,
) -> None:
    del iteration
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093734-C122", "queue-generation-a")
    pm_generation = instance.role_registry.get("project_manager").generation
    if case != "pm_dispatch":
        instance.dispatch_task_contract(contract, pm_generation=pm_generation)
    lead_generation = instance.role_registry.get("lead_data").generation
    if case in {"worker_candidate", "reviewer_pass"}:
        instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=lead_generation,
        )
    candidate_digest = "7" * 64
    if case == "reviewer_pass":
        instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest=candidate_digest,
        )

    def controller_projection() -> tuple[tuple[object, ...], ...]:
        with sqlite3.connect(instance.receipt_path) as connection:
            rows: list[tuple[object, ...]] = []
            for table in (
                "hierarchy_task",
                "worker_assignment",
                "lead_checkpoint",
                "role_mailbox",
                "review_receipt",
            ):
                rows.extend(
                    (table, *row)
                    for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                )
        return tuple(rows)

    before = controller_projection()
    preflight_complete = Event()
    continue_action = Event()
    original_require = instance._require_role

    def racing_require(
        role_key: str,
        expected_generation: int,
        **kwargs: object,
    ):
        record = original_require(role_key, expected_generation, **kwargs)  # type: ignore[arg-type]
        if role_key == sender_role_key and kwargs.get("action") is sender_action:
            preflight_complete.set()
            assert continue_action.wait(timeout=3)
        return record

    monkeypatch.setattr(instance, "_require_role", racing_require)

    if case == "pm_dispatch":
        operation = lambda: instance.dispatch_task_contract(
            contract, pm_generation=pm_generation
        )
    elif case == "lead_dispatch":
        operation = lambda: instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=lead_generation,
        )
    elif case == "lead_checkpoint":
        operation = lambda: instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=lead_generation,
            checkpoint_digest="a" * 64,
        )
    elif case == "worker_candidate":
        operation = lambda: instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest=candidate_digest,
        )
    else:
        operation = lambda: instance.review_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            reviewer_role_key="reviewer_data",
            reviewer_generation=instance.role_registry.get("reviewer_data").generation,
            candidate_digest=candidate_digest,
            decision=ReviewDecision.PASS,
            reason_code="PASS_RACE",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(operation)
        assert preflight_complete.wait(timeout=3)
        sender = instance.role_registry.get(sender_role_key)
        instance.role_registry.heartbeat(
            sender_role_key,
            expected_generation=sender.generation,
            observed_at=T0 + timedelta(minutes=5),
            lease_until=T0 + timedelta(hours=1, minutes=5),
        )
        continue_action.set()
        with pytest.raises(StaleRoleGeneration):
            future.result(timeout=3)

    assert controller_projection() == before


def _durable_hierarchy_projection(
    instance: WorkflowController,
) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(instance.receipt_path) as connection:
        rows: list[tuple[object, ...]] = []
        for table in (
            "hierarchy_task",
            "worker_assignment",
            "lead_checkpoint",
            "role_mailbox",
            "review_receipt",
        ):
            rows.extend(
                (table, *row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            )
    return tuple(rows)


def _two_worker_contract(instance: WorkflowController) -> TaskContract:
    for identity in (
        RoleIdentity(
            "worker_tests", RoleKind.WORKER, "session-worker-tests",
            "python-control", "repo::C:/workspace", "term-worker-tests", "runtime-a",
            parent_role_key="lead_data",
        ),
        RoleIdentity(
            "reviewer_tests", RoleKind.REVIEWER, "session-reviewer-tests",
            "python-control", "repo::C:/workspace", "term-reviewer-tests", "runtime-a",
            parent_role_key="lead_data",
        ),
    ):
        instance.register_role_session(
            identity,
            observed_at=T0,
            lease_until=T0 + timedelta(hours=1),
        )
    return TaskContract(
        task_id="RQ-20260829T093735-C123",
        queue_generation="queue-generation-a",
        pm_role_key="project_manager",
        lead_role_key="lead_data",
        reviewer_role_key="reviewer_data",
        write_scope=("src/stock_data", "tests/unit"),
        worker_assignments=(
            WorkerAssignment(
                "worker_code", ("src/stock_data/component.py",), "reviewer_data"
            ),
            WorkerAssignment(
                "worker_tests", ("tests/unit/test_component.py",), "reviewer_tests"
            ),
        ),
    )


@pytest.mark.parametrize(
    ("case", "failure_call"),
    (
        ("contract", 1),
        ("fanout", 1),
        ("fanout", 2),
        ("candidate", 1),
        ("candidate", 2),
    ),
)
def test_split_state_and_mailbox_failure_rolls_back_every_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    failure_call: int,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _two_worker_contract(instance) if case == "fanout" else _task_contract(
        "RQ-20260829T093735-C123", "queue-generation-a"
    )
    if case != "contract":
        instance.dispatch_task_contract(
            contract,
            pm_generation=instance.role_registry.get("project_manager").generation,
        )
    if case == "candidate":
        instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
        )
    before = _durable_hierarchy_projection(instance)
    original_insert = instance._insert_mailbox_in_transaction
    calls = 0

    def failing_insert(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise RuntimeError("injected mailbox failure")
        return original_insert(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(instance, "_insert_mailbox_in_transaction", failing_insert)
    if case == "contract":
        operation = lambda: instance.dispatch_task_contract(
            contract,
            pm_generation=instance.role_registry.get("project_manager").generation,
        )
    elif case == "fanout":
        operation = lambda: instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
        )
    else:
        operation = lambda: instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest="8" * 64,
        )

    with pytest.raises(RuntimeError, match="injected mailbox failure"):
        operation()
    assert calls == failure_call
    assert _durable_hierarchy_projection(instance) == before


@pytest.mark.parametrize(
    ("case", "recipient_role_key"),
    (
        ("contract", "lead_data"),
        ("fanout", "worker_code"),
        ("candidate", "reviewer_data"),
    ),
)
def test_recipient_generation_change_serializes_without_deadlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    recipient_role_key: str,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093736-C124", "queue-generation-a")
    if case != "contract":
        instance.dispatch_task_contract(
            contract,
            pm_generation=instance.role_registry.get("project_manager").generation,
        )
    if case == "candidate":
        instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
        )
    original_insert = instance._insert_mailbox_in_transaction
    insert_entered = Event()
    release_insert = Event()
    gated = False

    def blocking_insert(*args: object, **kwargs: object):
        nonlocal gated
        if kwargs.get("recipient_role_key") == recipient_role_key and not gated:
            gated = True
            insert_entered.set()
            assert release_insert.wait(timeout=3)
        return original_insert(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(instance, "_insert_mailbox_in_transaction", blocking_insert)
    if case == "contract":
        operation = lambda: instance.dispatch_task_contract(
            contract,
            pm_generation=instance.role_registry.get("project_manager").generation,
        )
    elif case == "fanout":
        operation = lambda: instance.dispatch_workers(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
        )
    else:
        operation = lambda: instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest="9" * 64,
        )

    recipient = instance.role_registry.get(recipient_role_key)
    heartbeat_started = Event()

    def heartbeat_recipient():
        heartbeat_started.set()
        return instance.role_registry.heartbeat(
            recipient_role_key,
            expected_generation=recipient.generation,
            observed_at=T0 + timedelta(minutes=6),
            lease_until=T0 + timedelta(hours=1, minutes=6),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        operation_future = pool.submit(operation)
        assert insert_entered.wait(timeout=3)
        heartbeat_future = pool.submit(heartbeat_recipient)
        assert heartbeat_started.wait(timeout=3)
        assert not heartbeat_future.done()
        release_insert.set()
        operation_future.result(timeout=3)
        renewed = heartbeat_future.result(timeout=3)

    assert renewed.generation == recipient.generation + 1


def test_checkpoint_readback_failure_rolls_back_insert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093737-C125", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    before = _durable_hierarchy_projection(instance)
    original_connect = instance._connect

    class ReadbackFailureConnection:
        def __init__(self) -> None:
            self.connection = original_connect()

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *exc: object):
            return self.connection.__exit__(*exc)

        def execute(self, sql: str, parameters: object = ()):
            if sql.startswith("SELECT checkpoint_digest"):
                raise RuntimeError("injected checkpoint readback failure")
            return self.connection.execute(sql, parameters)

        def commit(self) -> None:
            self.connection.commit()

        def rollback(self) -> None:
            self.connection.rollback()

    monkeypatch.setattr(instance, "_connect", ReadbackFailureConnection)
    with pytest.raises(RuntimeError, match="readback failure"):
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
            checkpoint_digest="b" * 64,
        )

    monkeypatch.setattr(instance, "_connect", original_connect)
    assert _durable_hierarchy_projection(instance) == before


def test_lead_checkpoint_atomically_notifies_pm_and_replays_ack_once(
    tmp_path: Path,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093738-C126", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    lead_generation = instance.role_registry.get("lead_data").generation
    checkpoint = instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
        checkpoint_digest="c" * 64,
    )
    assert instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
        checkpoint_digest="c" * 64,
    ) == checkpoint
    messages = [
        item for item in instance.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    ]
    assert len(messages) == 1
    assert messages[0].body == {
        "checkpoint_digest": "c" * 64,
        "checkpoint_id": checkpoint,
        "lead_role_key": "lead_data",
    }
    with sqlite3.connect(instance.receipt_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lead_checkpoint WHERE checkpoint_id = ?", (checkpoint,)
        ).fetchone()[0] == 1
    pm = instance.role_registry.get("project_manager")
    acknowledgement = instance.acknowledge_mailbox(
        messages[0].message_id,
        recipient_role_key="project_manager",
        expected_generation=pm.generation,
        acknowledgement_ref="ack-lead-checkpoint",
        observed_at=T0 + timedelta(minutes=8),
    )
    restarted = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
    )
    assert not [
        item for item in restarted.mailbox("project_manager", pending_only=True)
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    ]
    assert restarted.acknowledge_mailbox(
        messages[0].message_id,
        recipient_role_key="project_manager",
        expected_generation=pm.generation,
        acknowledgement_ref="ack-lead-checkpoint",
    ) == acknowledgement


def test_pm_generation_race_rejects_checkpoint_with_zero_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093739-C127", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    before = _durable_hierarchy_projection(instance)
    pm_preflight = Event()
    continue_checkpoint = Event()
    original_require = instance._require_role

    def racing_require(role_key: str, expected_generation: int, **kwargs: object):
        record = original_require(role_key, expected_generation, **kwargs)  # type: ignore[arg-type]
        if role_key == "project_manager":
            pm_preflight.set()
            assert continue_checkpoint.wait(timeout=3)
        return record

    monkeypatch.setattr(instance, "_require_role", racing_require)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            instance.record_lead_checkpoint,
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
            checkpoint_digest="d" * 64,
        )
        assert pm_preflight.wait(timeout=3)
        pm = instance.role_registry.get("project_manager")
        instance.role_registry.heartbeat(
            "project_manager",
            expected_generation=pm.generation,
            observed_at=T0 + timedelta(minutes=9),
            lease_until=T0 + timedelta(hours=1, minutes=9),
        )
        continue_checkpoint.set()
        with pytest.raises(StaleRoleGeneration):
            future.result(timeout=3)
    assert _durable_hierarchy_projection(instance) == before


def test_checkpoint_mailbox_failure_rolls_back_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093740-C128", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    before = _durable_hierarchy_projection(instance)
    original_insert = instance._insert_mailbox_in_transaction

    def failing_checkpoint_message(*args: object, **kwargs: object):
        if kwargs.get("message_type") is MailboxMessageType.LEAD_CHECKPOINT:
            raise RuntimeError("injected checkpoint mailbox failure")
        return original_insert(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        instance, "_insert_mailbox_in_transaction", failing_checkpoint_message
    )
    with pytest.raises(RuntimeError, match="checkpoint mailbox failure"):
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
            checkpoint_digest="e" * 64,
        )
    assert _durable_hierarchy_projection(instance) == before


@pytest.mark.parametrize("pm_state", ("none", "multiple"))
def test_checkpoint_requires_exactly_one_live_project_manager(
    tmp_path: Path,
    pm_state: str,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093741-C129", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    registry_path = instance.role_registry.path
    with sqlite3.connect(registry_path) as connection:
        if pm_state == "none":
            connection.execute(
                "UPDATE role_registry SET state = 'stopped' WHERE role_key = 'project_manager'"
            )
        else:
            row = list(connection.execute(
                "SELECT * FROM role_registry WHERE role_key = 'project_manager'"
            ).fetchone())
            row[0] = "project_manager_2"
            row[2] = "session-pm-2"
            connection.execute(
                f"INSERT INTO role_registry VALUES ({','.join('?' for _ in row)})", row
            )
    before = _durable_hierarchy_projection(instance)
    with pytest.raises(RoleRegistryError, match="exactly one live project manager"):
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
            checkpoint_digest="f" * 64,
        )
    assert _durable_hierarchy_projection(instance) == before


def test_candidate_mailbox_wakes_exact_stored_reviewer_once_across_restart(
    tmp_path: Path,
) -> None:
    instance, sessions = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093742-C130", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    review_message, _ = instance.submit_worker_candidate(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        worker_role_key="worker_code",
        worker_generation=instance.role_registry.get("worker_code").generation,
        candidate_digest="1" * 64,
    )
    reviewer = instance.role_registry.get("reviewer_data")
    receipt = instance.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
        message_id=review_message.message_id,
    )
    assert sessions.calls == 1
    assert sessions.actions == ["resume"]

    restarted = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
        session_runner=InjectedSessionRunner(sessions),
    )
    assert restarted.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
        message_id=review_message.message_id,
    ) == receipt
    assert sessions.calls == 1


@pytest.mark.parametrize(
    ("role_key", "generation_delta", "session_id", "error"),
    (
        ("reviewer_data", 1, "session-reviewer", StaleRoleGeneration),
        ("reviewer_data", 0, "session-reviewer-wrong", StaleRoleGeneration),
    ),
)
def test_reviewer_wake_refuses_stale_or_wrong_role_without_side_effect(
    tmp_path: Path,
    role_key: str,
    generation_delta: int,
    session_id: str,
    error: type[Exception],
) -> None:
    instance, sessions = _hierarchy_controller(tmp_path)
    record = instance.role_registry.get(role_key)
    with pytest.raises(error):
        instance.wake_role_session(
            role_key=role_key,
            expected_generation=record.generation + generation_delta,
            expected_session_id=session_id,
        )
    assert sessions.calls == 0


def test_candidate_wake_refuses_a_different_role(tmp_path: Path) -> None:
    instance, sessions = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093743-C131", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    candidate, _ = instance.submit_worker_candidate(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        worker_role_key="worker_code",
        worker_generation=instance.role_registry.get("worker_code").generation,
        candidate_digest="2" * 64,
    )
    worker = instance.role_registry.get("worker_code")
    with pytest.raises(MailboxConflict, match="different role lifecycle"):
        instance.wake_role_session(
            role_key="worker_code",
            expected_generation=worker.generation,
            expected_session_id=worker.identity.codex_session_id,
            message_id=candidate.message_id,
        )
    assert sessions.calls == 0


def test_failed_reviewer_wake_leaves_retryable_outbox_and_reuses_operation(
    tmp_path: Path,
) -> None:
    class FailOnceSessionBoundary(LocalFakeSessionBoundary):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def execute(self, request: Mapping[str, str]) -> Mapping[str, str]:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("injected wake failure")
            return super().execute(request)

    boundary = FailOnceSessionBoundary()
    instance, _ = _hierarchy_controller(tmp_path)
    instance.session_runner = InjectedSessionRunner(boundary)
    reviewer = instance.role_registry.get("reviewer_data")
    with pytest.raises(RuntimeError, match="wake failure"):
        instance.wake_role_session(
            role_key="reviewer_data",
            expected_generation=reviewer.generation,
            expected_session_id=reviewer.identity.codex_session_id,
        )
    with sqlite3.connect(instance.receipt_path) as connection:
        assert connection.execute(
            "SELECT status FROM role_wake_outbox"
        ).fetchone()[0] == "pending"

    restarted = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
        session_runner=InjectedSessionRunner(boundary),
    )
    receipt = restarted.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    )
    assert restarted.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    ) == receipt
    assert boundary.attempts == 2
    assert boundary.calls == 1


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("task_id", "RQ-20260829T093799-Z999"),
        ("queue_generation", "queue-generation-tampered"),
        ("lead_role_key", "lead_tampered"),
        ("created_at", "2026-08-29T00:00:00Z"),
    ),
)
def test_checkpoint_replay_rejects_any_immutable_row_tamper(
    tmp_path: Path,
    column: str,
    replacement: str,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093744-C132", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    lead = instance.role_registry.get("lead_data")
    checkpoint = instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key=lead.identity.role_key,
        lead_generation=lead.generation,
        checkpoint_digest="3" * 64,
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        connection.execute(
            f"UPDATE lead_checkpoint SET {column} = ? WHERE checkpoint_id = ?",
            (replacement, checkpoint),
        )
    with pytest.raises(MailboxConflict, match="checkpoint.*integrity|rebound"):
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key=lead.identity.role_key,
            lead_generation=lead.generation,
            checkpoint_digest="3" * 64,
        )


def test_checkpoint_replay_routes_once_per_current_pm_lifecycle(
    tmp_path: Path,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093745-C133", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    lead = instance.role_registry.get("lead_data")
    checkpoint = instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key=lead.identity.role_key,
        lead_generation=lead.generation,
        checkpoint_digest="4" * 64,
    )
    pm_before = instance.role_registry.get("project_manager")
    pm_after = instance.role_registry.heartbeat(
        "project_manager",
        expected_generation=pm_before.generation,
        observed_at=T0 + timedelta(minutes=11),
        lease_until=T0 + timedelta(hours=1, minutes=11),
    )
    assert instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key=lead.identity.role_key,
        lead_generation=lead.generation,
        checkpoint_digest="4" * 64,
    ) == checkpoint
    assert instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key=lead.identity.role_key,
        lead_generation=lead.generation,
        checkpoint_digest="4" * 64,
    ) == checkpoint
    deliveries = [
        item for item in instance.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    ]
    assert len(deliveries) == 2
    assert {item.recipient_generation for item in deliveries} == {
        pm_before.generation,
        pm_after.generation,
    }
    assert len({item.message_id for item in deliveries}) == 2


def test_completed_wake_receipt_tamper_is_rejected_on_replay(tmp_path: Path) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    reviewer = instance.role_registry.get("reviewer_data")
    instance.wake_role_session(
        role_key=reviewer.identity.role_key,
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        connection.execute(
            "UPDATE role_wake_outbox SET runner_receipt_digest = ?",
            ("f" * 64,),
        )
    with pytest.raises(MailboxConflict, match="wake.*integrity|receipt.*rebound"):
        instance.wake_role_session(
            role_key=reviewer.identity.role_key,
            expected_generation=reviewer.generation,
            expected_session_id=reviewer.identity.codex_session_id,
        )


def test_deleted_acknowledged_checkpoint_mailbox_fails_closed_on_replay(
    tmp_path: Path,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093746-C134", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    lead = instance.role_registry.get("lead_data")
    instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key=lead.identity.role_key,
        lead_generation=lead.generation,
        checkpoint_digest="5" * 64,
    )
    message = next(
        item for item in instance.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    )
    pm = instance.role_registry.get("project_manager")
    instance.acknowledge_mailbox(
        message.message_id,
        recipient_role_key=pm.identity.role_key,
        expected_generation=pm.generation,
        acknowledgement_ref="ack-checkpoint-delete",
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        connection.execute(
            "DELETE FROM role_mailbox WHERE message_id = ?", (message.message_id,)
        )
    with pytest.raises(MailboxConflict, match="delivery.*missing|ledger"):
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key=lead.identity.role_key,
            lead_generation=lead.generation,
            checkpoint_digest="5" * 64,
        )
    assert not [
        item for item in instance.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    ]


def test_two_workers_route_only_to_their_frozen_unique_reviewers_across_restart(
    tmp_path: Path,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    for identity in (
        RoleIdentity(
            "worker_tests", RoleKind.WORKER, "session-worker-tests",
            "python-control", "repo::C:/workspace", "term-worker-tests", "runtime-a",
            parent_role_key="lead_data",
        ),
        RoleIdentity(
            "reviewer_tests", RoleKind.REVIEWER, "session-reviewer-tests",
            "python-control", "repo::C:/workspace", "term-reviewer-tests", "runtime-a",
            parent_role_key="lead_data",
        ),
    ):
        instance.register_role_session(
            identity, observed_at=T0, lease_until=T0 + timedelta(hours=1)
        )
    contract = TaskContract(
        task_id="RQ-20260829T093747-C135",
        queue_generation="queue-generation-a",
        pm_role_key="project_manager",
        lead_role_key="lead_data",
        reviewer_role_key="reviewer_data",
        write_scope=("src/stock_data", "tests/unit"),
        worker_assignments=(
            WorkerAssignment(
                "worker_code", ("src/stock_data/component.py",),
                reviewer_role_key="reviewer_data",
            ),
            WorkerAssignment(
                "worker_tests", ("tests/unit/test_component.py",),
                reviewer_role_key="reviewer_tests",
            ),
        ),
    )
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        assignments = {
            row[0]: tuple(row[1:])
            for row in connection.execute(
                "SELECT worker_role_key, reviewer_role_key, reviewer_generation, "
                "reviewer_session_id FROM worker_assignment ORDER BY worker_role_key"
            )
        }
    assert assignments == {
        "worker_code": ("reviewer_data", 1, "session-reviewer"),
        "worker_tests": ("reviewer_tests", 1, "session-reviewer-tests"),
    }
    fanout = instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    assert {
        item.recipient_role_key: item.body["reviewer_role_key"] for item in fanout
    } == {
        "worker_code": "reviewer_data",
        "worker_tests": "reviewer_tests",
    }
    first, _ = instance.submit_worker_candidate(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        worker_role_key="worker_code",
        worker_generation=instance.role_registry.get("worker_code").generation,
        candidate_digest="6" * 64,
    )
    second, _ = instance.submit_worker_candidate(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        worker_role_key="worker_tests",
        worker_generation=instance.role_registry.get("worker_tests").generation,
        candidate_digest="7" * 64,
    )
    assert (first.recipient_role_key, second.recipient_role_key) == (
        "reviewer_data", "reviewer_tests"
    )
    restarted = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
    )
    replay, _ = restarted.submit_worker_candidate(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        worker_role_key="worker_tests",
        worker_generation=restarted.role_registry.get("worker_tests").generation,
        candidate_digest="7" * 64,
    )
    assert replay.message_id == second.message_id
    assert len([
        item for item in restarted.mailbox("reviewer_tests")
        if item.message_type is MailboxMessageType.CANDIDATE
    ]) == 1
    with pytest.raises(RoleRegistryError, match="preassigned Reviewer"):
        instance.review_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_tests",
            reviewer_role_key="reviewer_data",
            reviewer_generation=instance.role_registry.get("reviewer_data").generation,
            candidate_digest="7" * 64,
            decision=ReviewDecision.PASS,
            reason_code="WRONG_PAIR",
        )


def test_worker_candidate_rejects_rotated_preassigned_reviewer(tmp_path: Path) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093748-C136", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    reviewer = instance.role_registry.get("reviewer_data")
    instance.role_registry.heartbeat(
        "reviewer_data",
        expected_generation=reviewer.generation,
        observed_at=T0 + timedelta(minutes=12),
        lease_until=T0 + timedelta(hours=1, minutes=12),
    )
    with pytest.raises(StaleRoleGeneration):
        instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest="8" * 64,
        )
    assert not instance.mailbox("reviewer_data")


def test_fix_budget_is_per_worker_pair_and_third_fix_replans_whole_task(
    tmp_path: Path,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _two_worker_contract(instance)
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    digests = {"worker_code": "9" * 64, "worker_tests": "a" * 64}
    reviewers = {"worker_code": "reviewer_data", "worker_tests": "reviewer_tests"}
    for worker_key, digest in digests.items():
        instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key=worker_key,
            worker_generation=instance.role_registry.get(worker_key).generation,
            candidate_digest=digest,
        )
        reviewer_key = reviewers[worker_key]
        receipt = instance.review_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key=worker_key,
            reviewer_role_key=reviewer_key,
            reviewer_generation=instance.role_registry.get(reviewer_key).generation,
            candidate_digest=digest,
            decision=ReviewDecision.FIX,
            reason_code=f"FIX_{worker_key}_1",
        )
        assert receipt.fix_count == 1

    for attempt in (2, 3):
        instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest=digests["worker_code"],
        )
        receipt = instance.review_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            reviewer_role_key="reviewer_data",
            reviewer_generation=instance.role_registry.get("reviewer_data").generation,
            candidate_digest=digests["worker_code"],
            decision=ReviewDecision.FIX,
            reason_code=f"FIX_worker_code_{attempt}",
        )
        assert receipt.fix_count == attempt
    with sqlite3.connect(instance.receipt_path) as connection:
        pair_counts = dict(connection.execute(
            "SELECT worker_role_key, fix_count FROM worker_assignment"
        ))
        task_state = connection.execute(
            "SELECT fix_count, state FROM hierarchy_task WHERE task_id = ?",
            (contract.task_id,),
        ).fetchone()
    assert pair_counts == {"worker_code": 3, "worker_tests": 1}
    assert task_state == (3, "replan_required")


def test_legacy_durable_rows_migrate_to_integrity_and_pair_ledgers(
    tmp_path: Path,
) -> None:
    instance, sessions = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093749-C137", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    lead = instance.role_registry.get("lead_data")
    checkpoint = instance.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead.generation,
        checkpoint_digest="b" * 64,
    )
    reviewer = instance.role_registry.get("reviewer_data")
    wake_receipt = instance.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        connection.execute(
            "UPDATE worker_assignment SET reviewer_role_key = NULL, "
            "reviewer_generation = NULL, reviewer_session_id = NULL, "
            "assignment_digest = NULL"
        )
        connection.execute("DELETE FROM lead_checkpoint_delivery")

    restarted = WorkflowController(
        WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
        InjectedDirectRunner(LocalFakeDirectBoundary()),
        tmp_path / "controller.sqlite3",
        session_runner=InjectedSessionRunner(sessions),
    )
    with sqlite3.connect(restarted.receipt_path) as connection:
        assert connection.execute(
            "SELECT row_digest FROM lead_checkpoint"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT outbox_digest FROM role_wake_outbox"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT reviewer_role_key, reviewer_generation, reviewer_session_id, "
            "assignment_digest FROM worker_assignment"
        ).fetchone() == ("reviewer_data", 1, "session-reviewer", connection.execute(
            "SELECT assignment_digest FROM worker_assignment"
        ).fetchone()[0])
        assert connection.execute(
            "SELECT COUNT(*) FROM lead_checkpoint_delivery"
        ).fetchone()[0] == 1
    assert restarted.record_lead_checkpoint(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=lead.generation,
        checkpoint_digest="b" * 64,
    ) == checkpoint
    assert restarted.wake_role_session(
        role_key="reviewer_data",
        expected_generation=reviewer.generation,
        expected_session_id=reviewer.identity.codex_session_id,
    ) == wake_receipt


@pytest.mark.parametrize("legacy_kind", ("checkpoint_created_at", "wake_receipt"))
def test_unverifiable_digestless_legacy_rows_fail_even_when_tamper_is_formatted(
    tmp_path: Path,
    legacy_kind: str,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093752-C140", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    if legacy_kind == "checkpoint_created_at":
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
            checkpoint_digest="f" * 64,
        )
        with sqlite3.connect(instance.receipt_path) as connection:
            connection.execute(
                "UPDATE lead_checkpoint SET created_at = ?, row_digest = NULL",
                ("2026-08-29T12:34:56.000Z",),
            )
    else:
        reviewer = instance.role_registry.get("reviewer_data")
        instance.wake_role_session(
            role_key="reviewer_data",
            expected_generation=reviewer.generation,
            expected_session_id=reviewer.identity.codex_session_id,
        )
        with sqlite3.connect(instance.receipt_path) as connection:
            connection.execute(
                "UPDATE role_wake_outbox SET runner_receipt_digest = ?, "
                "outbox_digest = NULL",
                ("a" * 64,),
            )
    with pytest.raises(MailboxConflict, match="unverifiable digestless"):
        WorkflowController(
            WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
            InjectedDirectRunner(LocalFakeDirectBoundary()),
            tmp_path / "controller.sqlite3",
        )


@pytest.mark.parametrize("legacy_kind", ("checkpoint", "wake"))
def test_legacy_migration_refuses_to_self_sign_tampered_digestless_rows(
    tmp_path: Path,
    legacy_kind: str,
) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093750-C138", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    if legacy_kind == "checkpoint":
        instance.record_lead_checkpoint(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            lead_role_key="lead_data",
            lead_generation=instance.role_registry.get("lead_data").generation,
            checkpoint_digest="c" * 64,
        )
        with sqlite3.connect(instance.receipt_path) as connection:
            connection.execute(
                "UPDATE lead_checkpoint SET task_id = ?, row_digest = NULL",
                ("RQ-20260829T093799-Z998",),
            )
    else:
        reviewer = instance.role_registry.get("reviewer_data")
        instance.wake_role_session(
            role_key="reviewer_data",
            expected_generation=reviewer.generation,
            expected_session_id=reviewer.identity.codex_session_id,
        )
        with sqlite3.connect(instance.receipt_path) as connection:
            connection.execute(
                "UPDATE role_wake_outbox SET provenance = ?, outbox_digest = NULL",
                ("d" * 64,),
            )
    with pytest.raises(MailboxConflict, match="unverifiable digestless"):
        WorkflowController(
            WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
            InjectedDirectRunner(LocalFakeDirectBoundary()),
            tmp_path / "controller.sqlite3",
        )


@pytest.mark.parametrize("tamper_kind", ("forged_mailbox", "assignment"))
def test_candidate_wake_revalidates_exact_pending_worker_reviewer_pair(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    instance, sessions = _hierarchy_controller(tmp_path)
    contract = _task_contract("RQ-20260829T093751-C139", "queue-generation-a")
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    instance.dispatch_workers(
        task_id=contract.task_id,
        queue_generation=contract.queue_generation,
        lead_role_key="lead_data",
        lead_generation=instance.role_registry.get("lead_data").generation,
    )
    reviewer = instance.role_registry.get("reviewer_data")
    if tamper_kind == "forged_mailbox":
        candidate = instance._enqueue_mailbox(
            sender_role_key="worker_code",
            recipient_role_key="reviewer_data",
            recipient_generation=reviewer.generation,
            message_type=MailboxMessageType.CANDIDATE,
            body={"candidate_digest": "e" * 64, "worker_role_key": "worker_code"},
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
        )
    else:
        candidate, _ = instance.submit_worker_candidate(
            task_id=contract.task_id,
            queue_generation=contract.queue_generation,
            worker_role_key="worker_code",
            worker_generation=instance.role_registry.get("worker_code").generation,
            candidate_digest="e" * 64,
        )
        with sqlite3.connect(instance.receipt_path) as connection:
            connection.execute(
                "UPDATE worker_assignment SET reviewer_session_id = ?",
                ("session-reviewer-forged",),
            )
    with pytest.raises((MailboxConflict, ReviewLoopError, StaleRoleGeneration)):
        instance.wake_role_session(
            role_key="reviewer_data",
            expected_generation=reviewer.generation,
            expected_session_id=reviewer.identity.codex_session_id,
            message_id=candidate.message_id,
        )
    with sqlite3.connect(instance.receipt_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM role_wake_outbox").fetchone()[0] == 0
    assert sessions.calls == 0


def test_legacy_multi_worker_null_reviewer_mapping_fails_closed(tmp_path: Path) -> None:
    instance, _ = _hierarchy_controller(tmp_path)
    contract = _two_worker_contract(instance)
    instance.dispatch_task_contract(
        contract,
        pm_generation=instance.role_registry.get("project_manager").generation,
    )
    with sqlite3.connect(instance.receipt_path) as connection:
        connection.execute(
            "UPDATE worker_assignment SET reviewer_role_key = NULL, "
            "reviewer_generation = NULL, reviewer_session_id = NULL, "
            "assignment_digest = NULL"
        )
    with pytest.raises(MailboxConflict, match="legacy multi-Worker.*Reviewer mapping"):
        WorkflowController(
            WorkflowStateStore(tmp_path / "state.sqlite3", tmp_path / "events.jsonl"),
            InjectedDirectRunner(LocalFakeDirectBoundary()),
            tmp_path / "controller.sqlite3",
        )
