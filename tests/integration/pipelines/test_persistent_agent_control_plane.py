from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtGui, QtWidgets

from stock_data.gui.operations_dashboard import (
    OperationsDashboard,
    render_dashboard_png,
)
from stock_data.orchestration.workflow_control import (
    ControllerServiceError,
    EventKind,
    EventSource,
    GoalItem,
    GoalQueueReconciler,
    GoalRevision,
    InjectedDirectRunner,
    InjectedSessionRunner,
    ListenerGateway,
    ListenerIntent,
    ListenerRoute,
    ListenerSinks,
    LocalFakeDirectBoundary,
    LocalFakeSessionBoundary,
    MailboxConflict,
    MailboxMessageType,
    MonitoringSnapshotAdapter,
    PMMutationAuthority,
    Priority,
    QueueInventory,
    QueueSnapshot,
    QueueState,
    QueueTaskSnapshot,
    ReconciliationAction,
    ReviewDecision,
    ReviewLoopError,
    RoleAction,
    RoleIdentity,
    RoleKind,
    RoleRegistry,
    RoleRegistryError,
    RouteKind,
    RoutingError,
    ServiceReceipt,
    StaleQueueGeneration,
    StaleRoleGeneration,
    TaskContract,
    TaskState,
    WorkerAssignment,
    WorkflowController,
    WorkflowControllerError,
    WorkflowControllerService,
    WorkflowEvent,
    WorkflowRole,
    WorkflowStateStore,
    require_role_authority,
)


T0 = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
TASK_ID = "RQ-20260831T010000-A101"
SECOND_TASK_ID = "RQ-20260831T010001-B202"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _ProposalSink:
    """Public Listener sink that proves Goal reconciliation stays proposal-only."""

    def __init__(
        self,
        reconciler: GoalQueueReconciler,
        goal: GoalRevision,
        queue: QueueInventory,
    ) -> None:
        self.reconciler = reconciler
        self.goal = goal
        self.queue = queue
        self.proposals = ()
        self.new_receipts: set[str] = set()

    def accept_project_goal_receipt(
        self, *, receipt_key: str, intent_key: str, goal_text: str
    ) -> str:
        assert goal_text == "지속 가능한 자동화 목표를 현재 작업에 맞춰 주세요"
        self.proposals = self.reconciler.reconcile(
            self.goal,
            self.queue,
            expected_queue_generation=self.queue.generation,
        )
        return f"goal-proposal-{receipt_key[:16]}"

    def accept_new_candidate(
        self,
        *,
        receipt_key: str,
        intent_key: str,
        summary: str,
        source_route: str,
    ) -> str:
        self.new_receipts.add(receipt_key)
        return f"new-intent-{receipt_key[:16]}"


class _QueueReader:
    def __init__(self, snapshot: QueueSnapshot) -> None:
        self.current = snapshot

    def read_snapshot(self, *, observed_at: datetime) -> QueueSnapshot:
        return replace(self.current, observed_at=observed_at)


def _role(
    key: str,
    kind: RoleKind,
    session: str,
    *,
    parent: str | None = None,
    task: str | None = None,
    dispatch: str | None = None,
) -> RoleIdentity:
    return RoleIdentity(
        role_key=key,
        role_kind=kind,
        codex_session_id=session,
        orca_run_id="legacy-orca-denied",
        worktree_id="provider-free-temp-root",
        terminal_handle=None,
        runtime_id="python-only-runtime",
        active_task_id=task,
        active_dispatch_id=dispatch,
        parent_role_key=parent,
    )


def _build_service(
    root: Path,
    *,
    owner: str,
    direct_boundary: LocalFakeDirectBoundary,
    session_boundary: LocalFakeSessionBoundary,
) -> tuple[WorkflowControllerService, WorkflowStateStore, RoleRegistry]:
    state = WorkflowStateStore(
        root / "workflow_state.sqlite3",
        root / "workflow_events.jsonl",
    )
    roles = RoleRegistry(root / "role_registry.sqlite3")
    controller = WorkflowController(
        state,
        InjectedDirectRunner(direct_boundary),
        root / "hierarchy.sqlite3",
        session_runner=InjectedSessionRunner(session_boundary),
        role_registry=roles,
    )
    service = WorkflowControllerService(controller, root, owner_id=owner)
    service.start()
    return service, state, roles


def _queue_task(
    suffix: str,
    state: QueueState,
    fingerprint: str,
    title: str,
    *,
    links: tuple[str, ...] = (),
) -> QueueTaskSnapshot:
    return QueueTaskSnapshot(
        task_id=f"RQ-20260831T01010{suffix[-1]}-{suffix}",
        state=state,
        fingerprint=fingerprint,
        generation=_digest(f"task-{suffix}"),
        fields={"lead_owner": "lead_data", "title": title},
        goal_item_ids=links,
        review_generation="review-pinned" if state is QueueState.REVIEW else None,
    )


def _goal_and_queue() -> tuple[GoalRevision, QueueInventory]:
    tasks = (
        _queue_task("A001", QueueState.READY, "fp-amend", "old amend", links=("amend",)),
        _queue_task("A002", QueueState.ACTIVE, "fp-replan", "old replan", links=("replan",)),
        _queue_task("A003", QueueState.REVIEW, "fp-review", "old review", links=("review",)),
        _queue_task("A004", QueueState.DONE, "fp-reopen", "old reopen", links=("reopen",)),
        _queue_task("A005", QueueState.WAITING, "fp-link", "link"),
        _queue_task("A006", QueueState.READY, "fp-noop", "noop", links=("noop",)),
    )
    queue = QueueInventory(tasks, frozenset(QueueState))
    goal = GoalRevision(
        "goal-revision-8",
        (
            GoalItem("create", "fp-create", "create", {"priority": "P1"}, preferred_lead="lead_data"),
            GoalItem("amend", "fp-amend", "new amend", {}),
            GoalItem("replan", "fp-replan", "new replan", {}),
            GoalItem("review", "fp-review", "new review", {}),
            GoalItem("reopen", "fp-reopen", "new reopen", {}),
            GoalItem("link", "fp-link", "link", {}),
            GoalItem("noop", "fp-noop", "noop", {}),
        ),
    )
    return goal, queue


def _transition(
    index: int,
    to_state: TaskState,
    from_state: TaskState | None,
) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=f"persistent-lifecycle-{index}",
        occurred_at=T0 + timedelta(minutes=index),
        kind=EventKind.TASK_TRANSITION,
        source=EventSource.QUEUE,
        task_id=TASK_ID,
        from_state=from_state,
        to_state=to_state,
        priority=Priority.P1,
        domain="infra",
        reason_code=f"PM_LIFECYCLE_{to_state.value.upper()}",
    )


def _contract(
    queue_generation: str,
    reviewer: str,
    *,
    secondary_reviewer: str = "reviewer_two",
) -> TaskContract:
    return TaskContract(
        task_id=TASK_ID,
        queue_generation=queue_generation,
        pm_role_key="project_manager",
        lead_role_key="lead_data",
        reviewer_role_key=reviewer,
        write_scope=("src/stock_data/alpha.py", "tests/unit/test_alpha.py"),
        worker_assignments=(
            WorkerAssignment(
                "worker_alpha", ("src/stock_data/alpha.py",), reviewer
            ),
            WorkerAssignment(
                "worker_beta", ("tests/unit/test_alpha.py",), secondary_reviewer
            ),
        ),
    )


def _ack(
    service: WorkflowControllerService,
    message_id: str,
    recipient: str,
    generation: int,
    reference: str,
    *,
    observed_at: datetime = T0 + timedelta(minutes=20),
):
    return service.acknowledge_mailbox(
        message_id,
        recipient_role_key=recipient,
        expected_generation=generation,
        acknowledgement_ref=reference,
        observed_at=observed_at,
    )


def test_reconcile_restart_replay_safety_parts_1_to_10_persistent_control_plane(
    tmp_path: Path,
) -> None:
    """Traceability: 1 Listener; 2 Reconciler; 3 Queue; 4 PM contract;
    5 hierarchy/review; 6 authority; 7 persistence; 8 sessions;
    9 idempotency/safety; 10 Korean read-only GUI.
    """
    root = tmp_path / "persistent-control-root"
    direct_boundary = LocalFakeDirectBoundary()
    session_boundary = LocalFakeSessionBoundary()
    service, state, roles = _build_service(
        root,
        owner="pm-process-one",
        direct_boundary=direct_boundary,
        session_boundary=session_boundary,
    )
    identities = (
        _role("project_manager", RoleKind.PROJECT_MANAGER, "codex-pm-stored"),
        _role("lead_data", RoleKind.DOMAIN_LEAD, "codex-lead-data", parent="project_manager", task=TASK_ID, dispatch="dispatch-lead-data"),
        _role("lead_gui", RoleKind.DOMAIN_LEAD, "codex-lead-gui", parent="project_manager", task=SECOND_TASK_ID, dispatch="dispatch-lead-gui"),
        _role("worker_alpha", RoleKind.WORKER, "codex-worker-alpha", parent="lead_data", task=TASK_ID, dispatch="dispatch-worker-alpha"),
        _role("worker_beta", RoleKind.WORKER, "codex-worker-beta", parent="lead_data", task=TASK_ID, dispatch="dispatch-worker-beta"),
        _role("reviewer_one", RoleKind.REVIEWER, "codex-reviewer-one", parent="lead_data", task=TASK_ID, dispatch="dispatch-reviewer-one"),
        _role("reviewer_two", RoleKind.REVIEWER, "codex-reviewer-two", parent="lead_data", task=TASK_ID, dispatch="dispatch-reviewer-two"),
        _role("reviewer_wrong", RoleKind.REVIEWER, "codex-reviewer-wrong", parent="lead_data", task=TASK_ID, dispatch="dispatch-reviewer-wrong"),
    )
    for identity in identities:
        service.register_role_session(
            identity,
            observed_at=T0,
            lease_until=T0 + timedelta(hours=4),
        )

    # Parts 1-2: durable Listener entry and all seven proposal-only actions.
    goal, queue = _goal_and_queue()
    queue_generation_before = queue.generation
    queue_titles_before = tuple(task.fields["title"] for task in queue.tasks)
    proposal_sink = _ProposalSink(GoalQueueReconciler(), goal, queue)
    pm_identity = service.resolve_pm_mailbox_identity()
    intent = ListenerIntent(
        listener_id="listener-main",
        conversation_id="chat-before-restart",
        checkpoint_cursor="turn-1",
        user_text="민감한 원문은 제어 이벤트 JSONL에 남기지 마세요",
        received_at="2026-08-31T01:00:00Z",
    )
    proposal_message = "goal-proposals:" + ",".join(
        action.value for action in ReconciliationAction
    )
    routes = (
        ListenerRoute(
            RouteKind.GOAL_CHANGE,
            {"goal_text": "지속 가능한 자동화 목표를 현재 작업에 맞춰 주세요"},
        ),
        ListenerRoute(
            RouteKind.DIRECT_PM,
            {
                "recipient": pm_identity.recipient,
                "session_id": pm_identity.session_id,
                "generation": pm_identity.generation,
                "message_type": "user_intent",
                "message": proposal_message,
            },
        ),
    )
    listener_paths = (root / "listener.sqlite3", root / "listener_events.jsonl")
    with ListenerGateway(
        *listener_paths,
        sinks=ListenerSinks(proposal_sink, service, proposal_sink),
        pm_authority=PMMutationAuthority("python_pm", True),
        pm_identity_resolver=service,
    ) as listener:
        first_listener_receipts = listener.intake(intent, routes)
        assert len(first_listener_receipts) == 2, "part 1 Listener must accept two typed routes"
        listener.intake(intent, routes)
    action_map = {item.goal_item_id: item.action for item in proposal_sink.proposals}
    assert action_map == {
        "create": ReconciliationAction.CREATE,
        "amend": ReconciliationAction.AMEND,
        "replan": ReconciliationAction.REPLAN,
        "review": ReconciliationAction.INVALIDATE_REVIEW,
        "reopen": ReconciliationAction.REOPEN,
        "link": ReconciliationAction.LINK,
        "noop": ReconciliationAction.NOOP,
    }, "part 2 must expose all proposal-only reconciliation actions"
    assert queue.generation == queue_generation_before
    assert tuple(task.fields["title"] for task in queue.tasks) == queue_titles_before
    pm_goal_messages = service.mailbox("project_manager")
    assert [item.message_type for item in pm_goal_messages] == [
        MailboxMessageType.USER_INTENT
    ]
    assert pm_goal_messages[0].body["message"] == proposal_message

    # Parts 3 and 8: canonical lifecycle facts and same-session process restart.
    lifecycle = (
        _transition(1, TaskState.NEW, None),
        _transition(2, TaskState.READY, TaskState.NEW),
        _transition(3, TaskState.ACTIVE, TaskState.READY),
        _transition(4, TaskState.REVIEW, TaskState.ACTIVE),
    )
    first_lifecycle_receipt = service.run(lifecycle)
    assert state.task_snapshots()[0].state is TaskState.REVIEW, "part 3 lifecycle must reach Review"
    assert state.event_count() == 4
    workflow_prefix = (root / "workflow_events.jsonl").read_text(encoding="utf-8")
    assert service.run(lifecycle) == first_lifecycle_receipt
    assert state.event_count() == 4
    service.close()

    restarted, state, roles = _build_service(
        root,
        owner="pm-process-two",
        direct_boundary=direct_boundary,
        session_boundary=session_boundary,
    )
    resumed = restarted.resume_session_hierarchy()
    assert resumed.session_ids[0] == "codex-pm-stored", "part 8 must resume the stored PM Codex session"
    assert set(resumed.session_ids) == {identity.codex_session_id for identity in identities}
    calls_after_resume = session_boundary.calls
    assert restarted.resume_session_hierarchy().runner_receipt_digests == resumed.runner_receipt_digests
    assert session_boundary.calls == calls_after_resume, "part 9 duplicate resume must not create sessions"
    assert restarted.run(lifecycle) == first_lifecycle_receipt
    assert state.event_count() == 4

    pm_identity_after_restart = restarted.resolve_pm_mailbox_identity()
    assert pm_identity_after_restart.session_id == "codex-pm-stored"
    next_intent = ListenerIntent(
        listener_id="listener-main",
        conversation_id="new-chat-after-restart",
        checkpoint_cursor="turn-2",
        user_text="새 채팅에서도 같은 PM 세션을 사용해 주세요",
        received_at="2026-08-31T01:10:00Z",
        previous_intent_key=intent.intent_key,
    )
    next_route = ListenerRoute(
        RouteKind.DIRECT_PM,
        {
            "recipient": pm_identity_after_restart.recipient,
            "session_id": pm_identity_after_restart.session_id,
            "generation": pm_identity_after_restart.generation,
            "message_type": "operational_wake",
            "message": "새 채팅 체크포인트를 같은 PM에게 전달",
        },
    )
    with ListenerGateway(
        *listener_paths,
        sinks=ListenerSinks(proposal_sink, restarted, proposal_sink),
        pm_authority=PMMutationAuthority("python_pm", True),
        pm_identity_resolver=restarted,
    ) as listener:
        listener.intake(next_intent, (next_route,))
        listener.intake(next_intent, (next_route,))
    assert len(restarted.mailbox("project_manager")) == 2

    # Parts 4-6 and 9: immutable PM contract, disjoint fan-out, authority and ACK fences.
    pm_message = restarted.mailbox("project_manager")[0]
    pm_generation = roles.get("project_manager").generation
    ack_one = _ack(restarted, pm_message.message_id, "project_manager", pm_generation, "ack-pm-intent")
    ack_two = _ack(restarted, pm_message.message_id, "project_manager", pm_generation, "ack-pm-intent")
    assert ack_two == ack_one, "part 9 duplicate ACK must return immutable settlement"
    with pytest.raises(MailboxConflict):
        _ack(restarted, pm_message.message_id, "project_manager", pm_generation, "ack-rebound")
    pm_generation = roles.get("project_manager").generation

    generation_one = _digest("queue-generation-one")
    contract_one = _contract(generation_one, "reviewer_one")
    with pytest.raises(StaleRoleGeneration):
        restarted.dispatch_task_contract(contract_one, pm_generation=pm_generation - 1)
    contract_message = restarted.dispatch_task_contract(contract_one, pm_generation=pm_generation)
    assert restarted.dispatch_task_contract(contract_one, pm_generation=pm_generation).message_id == contract_message.message_id
    with pytest.raises(StaleQueueGeneration):
        restarted.dispatch_task_contract(
            replace(contract_one, worker_profile="fast", contract_digest=""),
            pm_generation=pm_generation,
        )
    lead_generation = roles.get("lead_data").generation
    _ack(restarted, contract_message.message_id, "lead_data", lead_generation, "ack-task-contract")
    lead_generation = roles.get("lead_data").generation
    with pytest.raises(StaleRoleGeneration):
        restarted.dispatch_workers(
            task_id=TASK_ID,
            queue_generation=generation_one,
            lead_role_key="lead_data",
            lead_generation=lead_generation - 1,
        )
    assignments = restarted.dispatch_workers(
        task_id=TASK_ID,
        queue_generation=generation_one,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
    )
    assert {item.recipient_role_key for item in assignments} == {"worker_alpha", "worker_beta"}
    assert {
        tuple(item.body["write_scope"]) for item in assignments
    } == {("src/stock_data/alpha.py",), ("tests/unit/test_alpha.py",)}
    assert {
        item.recipient_role_key: item.body["reviewer_role_key"]
        for item in assignments
    } == {
        "worker_alpha": "reviewer_one",
        "worker_beta": "reviewer_two",
    }
    assert len({
        item.body["reviewer_session_id"] for item in assignments
    }) == 2, "part 5 each Worker must have a unique stored Reviewer session"
    replayed_assignments = restarted.dispatch_workers(
        task_id=TASK_ID,
        queue_generation=generation_one,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
    )
    assert tuple(item.message_id for item in replayed_assignments) == tuple(item.message_id for item in assignments)
    with pytest.raises(RoutingError):
        TaskContract(
            task_id=TASK_ID,
            queue_generation="overlap",
            pm_role_key="project_manager",
            lead_role_key="lead_data",
            reviewer_role_key="reviewer_one",
            write_scope=("src",),
            worker_assignments=(
                WorkerAssignment(
                    "worker_alpha", ("src/stock_data",), "reviewer_one"
                ),
                WorkerAssignment(
                    "worker_beta",
                    ("src/stock_data/alpha.py",),
                    "reviewer_two",
                ),
            ),
        )
    require_role_authority(WorkflowRole.PROJECT_MANAGER, RoleAction.STRUCTURAL_DECISION)
    require_role_authority(WorkflowRole.LEAD, RoleAction.PROGRESS_CHECKPOINT)
    require_role_authority(WorkflowRole.WORKER, RoleAction.SUBMIT_CANDIDATE)
    require_role_authority(WorkflowRole.REVIEWER, RoleAction.REVIEW_FIX)
    with pytest.raises(RoutingError):
        require_role_authority(WorkflowRole.WORKER, RoleAction.STRUCTURAL_DECISION)
    with pytest.raises(RoutingError):
        require_role_authority(WorkflowRole.REVIEWER, RoleAction.DISPATCH_WORKER)
    assert state.event_count() == 4, "part 6 role mailboxes must not mutate Queue lifecycle"

    worker_assignment = next(item for item in assignments if item.recipient_role_key == "worker_alpha")
    worker_generation = roles.get("worker_alpha").generation
    _ack(restarted, worker_assignment.message_id, "worker_alpha", worker_generation, "ack-worker-scope")
    worker_generation = roles.get("worker_alpha").generation

    # Part 5: the parallel beta pair independently routes to Reviewer two and PASSes.
    beta_assignment = next(
        item for item in assignments if item.recipient_role_key == "worker_beta"
    )
    beta_generation = roles.get("worker_beta").generation
    _ack(
        restarted,
        beta_assignment.message_id,
        "worker_beta",
        beta_generation,
        "ack-beta-scope",
    )
    beta_generation = roles.get("worker_beta").generation
    beta_candidate = _digest("beta-independent-candidate")
    beta_message, beta_visibility = restarted.submit_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_beta",
        worker_generation=beta_generation,
        candidate_digest=beta_candidate,
    )
    reviewer_two = roles.get("reviewer_two")
    assert beta_message.recipient_role_key == "reviewer_two"
    assert beta_message.recipient_session_id == "codex-reviewer-two"
    assert beta_visibility.recipient_role_key == "lead_data"
    with pytest.raises(MailboxConflict):
        restarted.wake_role_session(
            role_key="reviewer_one",
            expected_generation=roles.get("reviewer_one").generation,
            expected_session_id="codex-reviewer-one",
            message_id=beta_message.message_id,
        )
    with pytest.raises(StaleRoleGeneration):
        restarted.wake_role_session(
            role_key="reviewer_two",
            expected_generation=reviewer_two.generation,
            expected_session_id="codex-reviewer-one",
            message_id=beta_message.message_id,
        )
    beta_wake = restarted.wake_role_session(
        role_key="reviewer_two",
        expected_generation=reviewer_two.generation,
        expected_session_id=reviewer_two.identity.codex_session_id,
        message_id=beta_message.message_id,
    )
    beta_wake_calls = session_boundary.calls
    assert restarted.wake_role_session(
        role_key="reviewer_two",
        expected_generation=reviewer_two.generation,
        expected_session_id=reviewer_two.identity.codex_session_id,
        message_id=beta_message.message_id,
    ) == beta_wake
    assert session_boundary.calls == beta_wake_calls
    _ack(
        restarted,
        beta_message.message_id,
        "reviewer_two",
        reviewer_two.generation,
        "ack-beta-candidate",
    )
    reviewer_two_generation = roles.get("reviewer_two").generation
    for wrong_reviewer in ("reviewer_one", "reviewer_wrong"):
        with pytest.raises(RoleRegistryError):
            restarted.review_worker_candidate(
                task_id=TASK_ID,
                queue_generation=generation_one,
                worker_role_key="worker_beta",
                reviewer_role_key=wrong_reviewer,
                reviewer_generation=roles.get(wrong_reviewer).generation,
                candidate_digest=beta_candidate,
                decision=ReviewDecision.PASS,
                reason_code="WRONG_BETA_REVIEWER",
            )
    beta_pass = restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_beta",
        reviewer_role_key="reviewer_two",
        reviewer_generation=reviewer_two_generation,
        candidate_digest=beta_candidate,
        decision=ReviewDecision.PASS,
        reason_code="BETA_ACCEPTED_IN_PARALLEL",
    )
    assert beta_pass.state == "passed_to_lead"
    beta_lead_messages = [
        item for item in restarted.mailbox("lead_data")
        if item.body.get("worker_role_key") == "worker_beta"
    ]
    assert {item.message_type for item in beta_lead_messages} == {
        MailboxMessageType.REVIEW_VISIBILITY,
        MailboxMessageType.PASS,
    }
    beta_message_count = sum(
        len(restarted.mailbox(identity.role_key)) for identity in identities
    )
    assert restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_beta",
        reviewer_role_key="reviewer_two",
        reviewer_generation=reviewer_two_generation,
        candidate_digest=beta_candidate,
        decision=ReviewDecision.PASS,
        reason_code="BETA_ACCEPTED_IN_PARALLEL",
    ) == beta_pass
    assert sum(
        len(restarted.mailbox(identity.role_key)) for identity in identities
    ) == beta_message_count

    # Process restart reuses every stored session and replays beta without effects.
    calls_before_beta_restart = session_boundary.calls
    restarted.close()
    restarted, state, roles = _build_service(
        root,
        owner="pm-process-three",
        direct_boundary=direct_boundary,
        session_boundary=session_boundary,
    )
    restarted.resume_session_hierarchy()
    assert session_boundary.calls == calls_before_beta_restart
    assert restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_beta",
        reviewer_role_key="reviewer_two",
        reviewer_generation=reviewer_two_generation,
        candidate_digest=beta_candidate,
        decision=ReviewDecision.PASS,
        reason_code="BETA_ACCEPTED_IN_PARALLEL",
    ) == beta_pass
    assert sum(
        len(restarted.mailbox(identity.role_key)) for identity in identities
    ) == beta_message_count

    # Parts 5 and 9: direct Worker↔Reviewer loop, stable wake, two FIX rounds, then replan.
    candidate_one = _digest("candidate-one")
    with pytest.raises(StaleRoleGeneration):
        restarted.submit_worker_candidate(
            task_id=TASK_ID,
            queue_generation=generation_one,
            worker_role_key="worker_alpha",
            worker_generation=worker_generation - 1,
            candidate_digest=candidate_one,
        )
    candidate_message, lead_visibility = restarted.submit_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        worker_generation=worker_generation,
        candidate_digest=candidate_one,
    )
    assert candidate_message.recipient_role_key == "reviewer_one"
    assert lead_visibility.recipient_role_key == "lead_data"
    reviewer_one = roles.get("reviewer_one")
    with pytest.raises(MailboxConflict):
        restarted.wake_role_session(
            role_key="reviewer_wrong",
            expected_generation=roles.get("reviewer_wrong").generation,
            expected_session_id="codex-reviewer-wrong",
            message_id=candidate_message.message_id,
        )
    with pytest.raises(StaleRoleGeneration):
        restarted.wake_role_session(
            role_key="reviewer_one",
            expected_generation=reviewer_one.generation,
            expected_session_id="codex-reviewer-stale",
            message_id=candidate_message.message_id,
        )
    wake_one = restarted.wake_role_session(
        role_key="reviewer_one",
        expected_generation=reviewer_one.generation,
        expected_session_id=reviewer_one.identity.codex_session_id,
        message_id=candidate_message.message_id,
    )
    wake_calls = session_boundary.calls
    assert restarted.wake_role_session(
        role_key="reviewer_one",
        expected_generation=reviewer_one.generation,
        expected_session_id=reviewer_one.identity.codex_session_id,
        message_id=candidate_message.message_id,
    ) == wake_one
    assert session_boundary.calls == wake_calls, "part 9 duplicate Reviewer wake must be zero-effect"
    # Deterministic fault injection: completed wake receipt rebinding fails closed.
    with sqlite3.connect(root / "hierarchy.sqlite3") as connection:
        wake_row = connection.execute(
            "SELECT runner_receipt_digest, outbox_digest FROM role_wake_outbox "
            "WHERE message_id = ?",
            (candidate_message.message_id,),
        ).fetchone()
        assert wake_row is not None
        connection.execute(
            "UPDATE role_wake_outbox SET runner_receipt_digest = ? "
            "WHERE message_id = ?",
            ("f" * 64, candidate_message.message_id),
        )
    with pytest.raises(MailboxConflict):
        restarted.wake_role_session(
            role_key="reviewer_one",
            expected_generation=reviewer_one.generation,
            expected_session_id=reviewer_one.identity.codex_session_id,
            message_id=candidate_message.message_id,
        )
    with sqlite3.connect(root / "hierarchy.sqlite3") as connection:
        connection.execute(
            "UPDATE role_wake_outbox SET runner_receipt_digest = ?, "
            "outbox_digest = ? WHERE message_id = ?",
            (*wake_row, candidate_message.message_id),
        )
    _ack(restarted, candidate_message.message_id, "reviewer_one", reviewer_one.generation, "ack-candidate-one")
    reviewer_generation = roles.get("reviewer_one").generation
    with pytest.raises(StaleRoleGeneration):
        restarted.review_worker_candidate(
            task_id=TASK_ID,
            queue_generation=generation_one,
            worker_role_key="worker_alpha",
            reviewer_role_key="reviewer_one",
            reviewer_generation=reviewer_generation - 1,
            candidate_digest=candidate_one,
            decision=ReviewDecision.FIX,
            reason_code="FIX_ROUND_ONE",
        )
    with pytest.raises(RoleRegistryError):
        restarted.review_worker_candidate(
            task_id=TASK_ID,
            queue_generation=generation_one,
            worker_role_key="worker_alpha",
            reviewer_role_key="reviewer_wrong",
            reviewer_generation=roles.get("reviewer_wrong").generation,
            candidate_digest=candidate_one,
            decision=ReviewDecision.FIX,
            reason_code="WRONG_REVIEWER",
        )
    fix_one = restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        reviewer_role_key="reviewer_one",
        reviewer_generation=reviewer_generation,
        candidate_digest=candidate_one,
        decision=ReviewDecision.FIX,
        reason_code="FIX_ROUND_ONE",
    )
    assert fix_one.fix_count == 1 and fix_one.state == "fix_returned"
    fix_messages = [
        item for item in restarted.mailbox("worker_alpha")
        if item.message_type is MailboxMessageType.FIX
    ]
    assert [item.recipient_role_key for item in fix_messages] == ["worker_alpha"]
    assert len([
        item for item in restarted.mailbox("lead_data")
        if item.message_type is MailboxMessageType.REVIEW_VISIBILITY
        and "fix_count" in item.body
    ]) == 1
    _ack(restarted, fix_messages[-1].message_id, "worker_alpha", worker_generation, "ack-fix-one")
    worker_generation = roles.get("worker_alpha").generation

    candidate_two = _digest("candidate-two")
    candidate_two_message, _ = restarted.submit_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        worker_generation=worker_generation,
        candidate_digest=candidate_two,
    )
    restarted.wake_role_session(
        role_key="reviewer_one",
        expected_generation=reviewer_generation,
        expected_session_id="codex-reviewer-one",
        message_id=candidate_two_message.message_id,
    )
    fix_two = restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        reviewer_role_key="reviewer_one",
        reviewer_generation=reviewer_generation,
        candidate_digest=candidate_two,
        decision=ReviewDecision.FIX,
        reason_code="FIX_ROUND_TWO",
    )
    assert fix_two.fix_count == 2 and fix_two.state == "fix_returned"
    fix_messages = [
        item for item in restarted.mailbox("worker_alpha")
        if item.message_type is MailboxMessageType.FIX
    ]
    assert len(fix_messages) == 2 and all(item.recipient_role_key == "worker_alpha" for item in fix_messages)
    assert len([
        item for item in restarted.mailbox("lead_data")
        if item.message_type is MailboxMessageType.REVIEW_VISIBILITY
        and "fix_count" in item.body
    ]) == 2
    _ack(restarted, fix_messages[-1].message_id, "worker_alpha", worker_generation, "ack-fix-two")
    worker_generation = roles.get("worker_alpha").generation

    candidate_three = _digest("candidate-three")
    candidate_three_message, _ = restarted.submit_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        worker_generation=worker_generation,
        candidate_digest=candidate_three,
    )
    restarted.wake_role_session(
        role_key="reviewer_one",
        expected_generation=reviewer_generation,
        expected_session_id="codex-reviewer-one",
        message_id=candidate_three_message.message_id,
    )
    third_fix = restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        reviewer_role_key="reviewer_one",
        reviewer_generation=reviewer_generation,
        candidate_digest=candidate_three,
        decision=ReviewDecision.FIX,
        reason_code="FIX_ROUND_THREE",
    )
    assert third_fix.fix_count == 3 and third_fix.state == "replan_required"
    assert all(
        len([
            item for item in restarted.mailbox(role)
            if item.message_type is MailboxMessageType.REPLAN_REQUIRED
        ]) == 1
        for role in ("lead_data", "project_manager")
    )
    assert len([item for item in restarted.mailbox("worker_alpha") if item.message_type is MailboxMessageType.FIX]) == 2
    message_count_after_third = sum(len(restarted.mailbox(identity.role_key)) for identity in identities)
    assert restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_one,
        worker_role_key="worker_alpha",
        reviewer_role_key="reviewer_one",
        reviewer_generation=reviewer_generation,
        candidate_digest=candidate_three,
        decision=ReviewDecision.FIX,
        reason_code="FIX_ROUND_THREE",
    ) == third_fix
    assert sum(len(restarted.mailbox(identity.role_key)) for identity in identities) == message_count_after_third
    with pytest.raises(ReviewLoopError):
        restarted.submit_worker_candidate(
            task_id=TASK_ID,
            queue_generation=generation_one,
            worker_role_key="worker_alpha",
            worker_generation=worker_generation,
            candidate_digest=_digest("forbidden-fourth-patch"),
        )

    # Fresh PM generation: PASS to Lead, Lead integration/checkpoint to PM, PM finalizes Queue.
    generation_two = _digest("queue-generation-two")
    contract_two = _contract(
        generation_two,
        "reviewer_two",
        secondary_reviewer="reviewer_one",
    )
    restarted.dispatch_task_contract(contract_two, pm_generation=pm_generation)
    restarted.dispatch_workers(
        task_id=TASK_ID,
        queue_generation=generation_two,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
    )
    with pytest.raises(StaleQueueGeneration):
        restarted.submit_worker_candidate(
            task_id=TASK_ID,
            queue_generation=generation_one,
            worker_role_key="worker_alpha",
            worker_generation=worker_generation,
            candidate_digest=_digest("stale-generation-candidate"),
        )
    passing_candidate = _digest("fresh-generation-passing-candidate")
    passing_message, _ = restarted.submit_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_two,
        worker_role_key="worker_alpha",
        worker_generation=worker_generation,
        candidate_digest=passing_candidate,
    )
    reviewer_two = roles.get("reviewer_two")
    restarted.wake_role_session(
        role_key="reviewer_two",
        expected_generation=reviewer_two.generation,
        expected_session_id=reviewer_two.identity.codex_session_id,
        message_id=passing_message.message_id,
    )
    passed = restarted.review_worker_candidate(
        task_id=TASK_ID,
        queue_generation=generation_two,
        worker_role_key="worker_alpha",
        reviewer_role_key="reviewer_two",
        reviewer_generation=reviewer_two.generation,
        candidate_digest=passing_candidate,
        decision=ReviewDecision.PASS,
        reason_code="ACCEPTED_FRESH_GENERATION",
    )
    assert passed.state == "passed_to_lead"
    assert restarted.mailbox("lead_data")[-1].message_type is MailboxMessageType.PASS
    checkpoint_digest = _digest("lead-integrated-pass")
    checkpoint_id = restarted.record_lead_checkpoint(
        task_id=TASK_ID,
        queue_generation=generation_two,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
        checkpoint_digest=checkpoint_digest,
    )
    assert restarted.record_lead_checkpoint(
        task_id=TASK_ID,
        queue_generation=generation_two,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
        checkpoint_digest=checkpoint_digest,
    ) == checkpoint_id
    pm_checkpoints = [
        item for item in restarted.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    ]
    assert len(pm_checkpoints) == 1 and pm_checkpoints[0].body["checkpoint_id"] == checkpoint_id
    # Deterministic fault injection: every immutable checkpoint field is digested.
    with sqlite3.connect(root / "hierarchy.sqlite3") as connection:
        checkpoint_row = connection.execute(
            "SELECT created_at, row_digest FROM lead_checkpoint WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        assert checkpoint_row is not None
        connection.execute(
            "UPDATE lead_checkpoint SET created_at = ? WHERE checkpoint_id = ?",
            ("2026-08-31T00:00:00Z", checkpoint_id),
        )
    with pytest.raises(MailboxConflict):
        restarted.record_lead_checkpoint(
            task_id=TASK_ID,
            queue_generation=generation_two,
            lead_role_key="lead_data",
            lead_generation=lead_generation,
            checkpoint_digest=checkpoint_digest,
        )
    with sqlite3.connect(root / "hierarchy.sqlite3") as connection:
        connection.execute(
            "UPDATE lead_checkpoint SET created_at = ?, row_digest = ? "
            "WHERE checkpoint_id = ?",
            (*checkpoint_row, checkpoint_id),
        )

    # PM lifecycle rotation redelivers the same logical checkpoint exactly once.
    pm_before_rotation = roles.get("project_manager")
    pm_after_rotation = roles.heartbeat(
        "project_manager",
        expected_generation=pm_before_rotation.generation,
        observed_at=T0 + timedelta(minutes=35),
        lease_until=T0 + timedelta(hours=5),
    )
    assert restarted.record_lead_checkpoint(
        task_id=TASK_ID,
        queue_generation=generation_two,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
        checkpoint_digest=checkpoint_digest,
    ) == checkpoint_id
    assert restarted.record_lead_checkpoint(
        task_id=TASK_ID,
        queue_generation=generation_two,
        lead_role_key="lead_data",
        lead_generation=lead_generation,
        checkpoint_digest=checkpoint_digest,
    ) == checkpoint_id
    pm_checkpoints = [
        item for item in restarted.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    ]
    assert len(pm_checkpoints) == 2
    assert {item.recipient_generation for item in pm_checkpoints} == {
        pm_before_rotation.generation,
        pm_after_rotation.generation,
    }
    current_checkpoint = next(
        item for item in pm_checkpoints
        if item.recipient_generation == pm_after_rotation.generation
    )
    old_checkpoint = next(
        item for item in pm_checkpoints
        if item.recipient_generation == pm_before_rotation.generation
    )
    _ack(
        restarted,
        current_checkpoint.message_id,
        "project_manager",
        pm_after_rotation.generation,
        "ack-current-pm-checkpoint",
        observed_at=T0 + timedelta(minutes=40),
    )
    # Deterministic fault injection: an ACK tombstone prevents resurrection.
    with sqlite3.connect(root / "hierarchy.sqlite3") as connection:
        connection.execute(
            "DELETE FROM role_mailbox WHERE message_id = ?",
            (current_checkpoint.message_id,),
        )
    with pytest.raises(MailboxConflict):
        restarted.record_lead_checkpoint(
            task_id=TASK_ID,
            queue_generation=generation_two,
            lead_role_key="lead_data",
            lead_generation=lead_generation,
            checkpoint_digest=checkpoint_digest,
        )
    remaining_checkpoint_ids = {
        item.message_id for item in restarted.mailbox("project_manager")
        if item.message_type is MailboxMessageType.LEAD_CHECKPOINT
    }
    assert remaining_checkpoint_ids == {old_checkpoint.message_id}
    assert state.event_count() == 4, "parts 5-6 review/checkpoint must not finalize Queue"

    done_event = _transition(5, TaskState.DONE, TaskState.REVIEW)
    done_receipt = restarted.run((done_event,))
    assert state.task_snapshots()[0].state is TaskState.DONE
    assert state.event_count() == 5
    assert restarted.run((done_event,)) == done_receipt
    assert state.event_count() == 5, "parts 3 and 9 PM replay must not duplicate transitions"
    workflow_text = (root / "workflow_events.jsonl").read_text(encoding="utf-8")
    assert workflow_text.startswith(workflow_prefix), "part 7 JSONL must remain append-only"

    # Part 7: SQLite current state, sanitized JSONL, Markdown human contracts.
    assert all(
        (root / name).is_file()
        for name in (
            "workflow_state.sqlite3",
            "role_registry.sqlite3",
            "hierarchy.sqlite3",
            "workflow_controller_service.sqlite3",
        )
    )
    workflow_lines = [json.loads(line) for line in workflow_text.splitlines()]
    assert len(workflow_lines) == 5
    assert all(set(line) == {
        "active_worker_count", "domain", "event_id", "from_state", "kind",
        "occurred_at", "outcome", "priority", "reason_code",
        "recurrence_fingerprint", "runnable_count", "schema_version",
        "session_fingerprint", "source", "task_id", "to_state",
    } for line in workflow_lines)
    assert "민감한 원문" not in workflow_text
    assert "민감한 원문" not in (root / "listener_events.jsonl").read_text(encoding="utf-8")
    markdown_paths = (
        REPOSITORY_ROOT / "artifacts/request_queue/README.md",
        REPOSITORY_ROOT / "artifacts/request_queue/WORKFLOW.md",
        REPOSITORY_ROOT / "artifacts/request_queue/PIPELINE.md",
        REPOSITORY_ROOT / ".agents/roles/README.md",
    )
    markdown = "\n".join(path.read_text(encoding="utf-8") for path in markdown_paths)
    assert all(term in markdown for term in ("SQLite", "JSONL", "Markdown", "TaskContract", "REPLAN_REQUIRED"))
    assert "Reviewer FIX -> Lead" not in markdown
    assert "never directly to Reviewer" not in markdown
    assert "optional Orca" not in markdown

    # Parts 9-10: tamper safety, exact monitoring projection, Korean dark read-only Qt.
    with pytest.raises(WorkflowControllerError):
        replace(passing_message, body_digest="0" * 64)
    tampered_receipt = done_receipt.to_dict()
    tampered_receipt["receipt_digest"] = "0" * 64
    with pytest.raises(ControllerServiceError):
        ServiceReceipt.from_dict(tampered_receipt)
    with pytest.raises(StaleRoleGeneration):
        restarted.wake_role_session(
            role_key="reviewer_two",
            expected_generation=reviewer_two.generation + 1,
            expected_session_id=reviewer_two.identity.codex_session_id,
            message_id=passing_message.message_id,
        )

    queue_reader = _QueueReader(QueueSnapshot(
        T0,
        (("new", 0), ("waiting", 0), ("ready", 0), ("active", 0), ("review", 0), ("blocked", 0), ("done", 1)),
        (),
        0,
        (),
    ))
    snapshot = MonitoringSnapshotAdapter(
        workflow_db=root / "workflow_state.sqlite3",
        role_db=root / "role_registry.sqlite3",
        event_log=root / "workflow_events.jsonl",
        execution_source=root / "missing-execution-source.sqlite3",
        queue_adapter=queue_reader,
        stale_after_seconds=20_000,
    ).snapshot(observed_at=T0 + timedelta(hours=1))
    projected_roles = {
        role.role_key
        for group in (snapshot.pm, snapshot.leads, snapshot.workers, snapshot.reviewers)
        for role in group
    }
    assert projected_roles == {identity.role_key for identity in identities}, "part 10 monitoring must not invent roles"
    assert {item.task_id for item in snapshot.tasks} == {TASK_ID}
    assert snapshot.queue is not None and snapshot.queue.count("done") == 1
    assert len(snapshot.events) == 5
    assert snapshot.pm_current_decision == "다음으로 맡길 일을 정리하고 있습니다."
    assert snapshot.pm_next_action == "작업 목록과 최근 활동을 다시 확인합니다."

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dashboard = OperationsDashboard(lambda: snapshot, refresh_interval_ms=None)
    try:
        dashboard.resize(1280, 720)
        dashboard.show()
        app.processEvents()
        assert dashboard.windowTitle() == "프로젝트 작업 현황"
        assert [button.text() for button in dashboard.findChildren(QtWidgets.QPushButton)] == ["정보 다시 확인"]
        assert "background:#111315" in dashboard.styleSheet()
    finally:
        dashboard.close()
    screenshot = render_dashboard_png(snapshot, tmp_path / "operations-control-plane.png")
    image = QtGui.QImage(str(screenshot))
    assert not image.isNull() and (image.width(), image.height()) == (1280, 720)
    sampled_lightness = [
        image.pixelColor(x, y).lightness()
        for x in range(0, image.width(), 80)
        for y in range(0, image.height(), 80)
    ]
    assert sum(sampled_lightness) / len(sampled_lightness) < 100, "part 10 dashboard must retain the dark theme"

    assert done_receipt.orca_used is False
    assert all(record.identity.orca_run_id == "legacy-orca-denied" for record in roles.records())
    restarted.close()
