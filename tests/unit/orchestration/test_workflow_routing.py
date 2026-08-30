from __future__ import annotations

import pytest

from stock_data.orchestration.workflow_control.codex_adapter import (
    CodexAdapterError,
    CodexSdkAdapter,
    LocalFakeCodexBoundary,
)
from stock_data.orchestration.workflow_control.routing import (
    BoundaryRequest,
    ExecutionBoundary,
    QueueWorkItem,
    RoutingError,
    role_profiles,
    route_execution_boundary,
    select_dependency_ready_leads,
)


def item(
    suffix: str,
    *,
    state: str = "ready",
    priority: str = "P1",
    lead: str | None = None,
    depends_on: tuple[str, ...] = (),
    scope: tuple[str, ...] = (),
    locks: tuple[str, ...] = (),
    writer_lane: str = "data",
    review_snapshot_pinned: bool = False,
) -> QueueWorkItem:
    return QueueWorkItem(
        task_id=f"RQ-20260829T0100{suffix}-ABCD",
        state=state,
        priority=priority,
        lead_owner=lead or f"lead_{suffix}",
        depends_on=depends_on,
        write_scope=scope or (f"src/{suffix}.py",),
        resource_locks=locks,
        writer_lane=writer_lane,
        review_snapshot_pinned=review_snapshot_pinned,
    )


def test_role_profiles_keep_queue_worker_and_reviewer_tiers_authoritative() -> None:
    profiles = role_profiles(worker_profile="fast", reviewer_profile="critical")

    assert (profiles.project_manager.model, profiles.project_manager.effort) == (
        "gpt-5.6-sol",
        "medium",
    )
    assert profiles.project_manager.source == "project_goal_default"
    assert (profiles.lead.model, profiles.lead.effort) == (
        "gpt-5.6-sol",
        "medium",
    )
    assert profiles.lead.source == "role_default:lead"
    assert (profiles.worker.model, profiles.worker.effort) == (
        "gpt-5.6-luna",
        "medium",
    )
    assert profiles.worker.source == "queue_worker_profile:fast"
    assert (profiles.reviewer.model, profiles.reviewer.effort) == (
        "gpt-5.6-sol",
        "xhigh",
    )
    assert profiles.reviewer.source == "queue_reviewer_profile:critical"

    with pytest.raises(RoutingError, match="unknown Queue worker"):
        role_profiles(worker_profile="invented", reviewer_profile="critical")


def test_boundary_routing_is_explanatory_and_fails_closed() -> None:
    sandbox = route_execution_boundary(BoundaryRequest())
    host = route_execution_boundary(
        BoundaryRequest(requires_orca_ipc=True, requires_host_resource=True)
    )
    denied = route_execution_boundary(BoundaryRequest(requests_mutation=True))

    assert sandbox.boundary is ExecutionBoundary.SANDBOX
    assert "deterministic local read-only" in sandbox.reason
    assert host.boundary is ExecutionBoundary.HOST
    assert "Orca IPC" in host.reason and "resource" in host.reason
    assert denied.boundary is ExecutionBoundary.DENIED
    assert "cannot authorize mutations" in denied.reason


def test_selection_honors_priority_dependencies_and_three_lead_capacity() -> None:
    done = item("00", state="done")
    p2 = item("01", priority="P2", depends_on=(done.task_id,))
    p0 = item("02", priority="P0")
    p1 = item("03", priority="P1")
    fourth = item("04", priority="P1")

    plan = select_dependency_ready_leads((p2, fourth, done, p1, p0))

    assert [selected.task_id for selected in plan.selected] == [
        p0.task_id,
        p1.task_id,
        fourth.task_id,
    ]
    assert plan.capacity == 3
    assert plan.decisions[0].reason.startswith("selected: P0")
    assert plan.decisions[-1].task_id == p2.task_id
    assert "capacity" in plan.decisions[-1].reason


def test_selection_respects_active_capacity_review_reservations_scopes_and_locks() -> None:
    active = item("10", state="active", scope=("src/active",))
    review = item("11", state="review", scope=("src/review.py",), locks=("held",))
    active_overlap = item("12", scope=("src/active/child.py",))
    review_lock = item("13", locks=("held",))
    first = item("14", lead="lead_same")
    same_lead = item("15", lead="lead_same")
    second = item("16")
    excess = item("17")

    plan = select_dependency_ready_leads(
        (active, review, active_overlap, review_lock, first, same_lead, second, excess)
    )

    assert plan.capacity == 2
    assert [entry.task_id for entry in plan.selected] == [first.task_id, second.task_id]
    reasons = {decision.task_id: decision.reason for decision in plan.decisions}
    assert "write scope overlaps" in reasons[active_overlap.task_id]
    assert "resource lock overlaps" in reasons[review_lock.task_id]
    assert "Lead already" in reasons[same_lead.task_id]
    assert "capacity" in reasons[excess.task_id]


def test_waiting_blocked_missing_dependencies_and_invalid_scope_do_not_route() -> None:
    waiting = item("20", state="waiting")
    blocked = item("21", state="blocked")
    missing = item("22", depends_on=("RQ-20260829T999999-ZZZZ",))
    no_lead = item("23", lead="lead_temp")
    no_lead = QueueWorkItem(
        task_id=no_lead.task_id,
        state=no_lead.state,
        priority=no_lead.priority,
        lead_owner=None,
        write_scope=no_lead.write_scope,
        writer_lane=no_lead.writer_lane,
    )

    plan = select_dependency_ready_leads((waiting, blocked, missing, no_lead))

    assert plan.selected == ()
    reasons = {decision.task_id: decision.reason for decision in plan.decisions}
    assert set(reasons) == {missing.task_id, no_lead.task_id}
    assert "dependencies not done" in reasons[missing.task_id]
    assert "no routed Lead" in reasons[no_lead.task_id]
    with pytest.raises(RoutingError, match="non-canonical write scope"):
        item("24", scope=("../escape.py",))
    with pytest.raises(RoutingError, match="invalid state"):
        item("24", state="invented")

    compacted_dependency = item(
        "25", depends_on=("RQ-20260820T000000-DONE",)
    )
    compacted_plan = select_dependency_ready_leads(
        (compacted_dependency,),
        completed_task_ids=("RQ-20260820T000000-DONE",),
    )
    assert compacted_plan.selected == (compacted_dependency,)


def test_shared_writer_lane_is_exclusive_but_review_only_reserves_exact_scope() -> None:
    active_shared = item("30", state="active", writer_lane="shared")
    disjoint = item("31", writer_lane="data")
    assert select_dependency_ready_leads((disjoint, active_shared)).selected == ()

    candidate_shared = item("32", writer_lane="shared")
    later = item("33", writer_lane="gui")
    plan = select_dependency_ready_leads((later, candidate_shared))
    assert plan.selected == (candidate_shared,)
    assert "shared writer lane is exclusive" in plan.decisions[1].reason

    review_shared = item("34", state="review", writer_lane="shared")
    allowed = item("35", writer_lane="backtest")
    assert select_dependency_ready_leads((review_shared, allowed)).selected == (allowed,)


def test_commit_pinned_review_snapshot_does_not_reserve_writer_scope() -> None:
    review = item(
        "36",
        state="review",
        scope=("src/reusable.py",),
        review_snapshot_pinned=True,
    )
    later = item("37", scope=("src/reusable.py",))

    plan = select_dependency_ready_leads((review, later))

    assert plan.selected == (later,)


def test_conflict_reasons_are_permutation_invariant_and_limit_requires_integer() -> None:
    active_later = item("41", state="active", scope=("src/owned",))
    active_first = item("40", state="active", scope=("src/owned",))
    candidate = item("42", scope=("src/owned/child.py",))

    forward = select_dependency_ready_leads((active_later, candidate, active_first))
    reverse = select_dependency_ready_leads((active_first, candidate, active_later))
    assert forward.decisions == reverse.decisions
    assert active_first.task_id in forward.decisions[0].reason

    with pytest.raises(RoutingError, match="between zero and three"):
        select_dependency_ready_leads((candidate,), writer_limit=1.5)  # type: ignore[arg-type]


def test_codex_sdk_adapter_is_disabled_by_default_behind_local_fake() -> None:
    fake = LocalFakeCodexBoundary({"status": "local-fake"})
    adapter = CodexSdkAdapter(fake)

    with pytest.raises(CodexAdapterError, match="disabled"):
        adapter.invoke({"role": "worker"})
    assert fake.calls == 0

    enabled_for_local_test = CodexSdkAdapter(fake, enabled=True)
    assert enabled_for_local_test.invoke({"role": "worker"}) == {
        "status": "local-fake"
    }
    assert fake.calls == 1
    with pytest.raises(CodexAdapterError, match="no local"):
        CodexSdkAdapter(enabled=True).invoke({"role": "worker"})
