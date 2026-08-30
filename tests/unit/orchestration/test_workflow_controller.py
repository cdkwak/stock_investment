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
    instance.register_role_session(
        RoleIdentity(
            "worker_tests", RoleKind.WORKER, "session-worker-tests",
            "python-control", "repo::C:/workspace", "term-worker-tests", "runtime-a",
            parent_role_key="lead_data",
        ),
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
            WorkerAssignment("worker_code", ("src/stock_data/component.py",)),
            WorkerAssignment("worker_tests", ("tests/unit/test_component.py",)),
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
            if sql.startswith("SELECT checkpoint_digest FROM lead_checkpoint"):
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
