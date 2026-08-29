"""Deterministic, read-only role and execution routing.

The router consumes already-projected Queue facts.  It deliberately has no
Queue manager, Orca, subprocess, network, or SDK dependency and therefore
cannot triage, claim, dispatch, or activate work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Iterable, Mapping


MAX_LEADS = 3
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_QUEUE_STATES = {"new", "waiting", "ready", "active", "review", "blocked", "done"}
_WRITER_LANES = {"gui", "data", "backtest", "shared"}


class RoutingError(ValueError):
    """Raised when routing input is not a safe canonical projection."""


class WorkflowRole(StrEnum):
    PROJECT_MANAGER = "project_manager"
    LEAD = "lead"
    WORKER = "worker"
    REVIEWER = "reviewer"


class ExecutionBoundary(StrEnum):
    SANDBOX = "sandbox"
    HOST = "host"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    model: str
    effort: str
    source: str


@dataclass(frozen=True, slots=True)
class RoleProfiles:
    project_manager: LaunchProfile
    lead: LaunchProfile
    worker: LaunchProfile
    reviewer: LaunchProfile


_QUEUE_PROFILES: Mapping[str, tuple[str, str]] = {
    "fast": ("gpt-5.6-luna", "medium"),
    "balanced": ("gpt-5.6-terra", "medium"),
    "strong": ("gpt-5.6-sol", "high"),
    "critical": ("gpt-5.6-sol", "xhigh"),
}


def role_profiles(
    *,
    worker_profile: str,
    reviewer_profile: str,
) -> RoleProfiles:
    """Resolve role launch profiles without re-deriving Queue-owned tiers."""

    def queue_profile(name: str, role: str) -> LaunchProfile:
        try:
            model, effort = _QUEUE_PROFILES[name]
        except KeyError as error:
            raise RoutingError(f"unknown Queue {role} profile: {name}") from error
        return LaunchProfile(model, effort, f"queue_{role}_profile:{name}")

    return RoleProfiles(
        project_manager=LaunchProfile(
            "gpt-5.6-sol", "medium", "project_goal_default"
        ),
        lead=LaunchProfile("gpt-5.6-sol", "medium", "role_default:lead"),
        worker=queue_profile(worker_profile, "worker"),
        reviewer=queue_profile(reviewer_profile, "reviewer"),
    )


@dataclass(frozen=True, slots=True)
class BoundaryRequest:
    requires_orca_ipc: bool = False
    requires_external_network: bool = False
    requires_host_resource: bool = False
    requests_mutation: bool = False
    requests_privilege: bool = False


@dataclass(frozen=True, slots=True)
class BoundaryDecision:
    boundary: ExecutionBoundary
    reason: str


def route_execution_boundary(request: BoundaryRequest) -> BoundaryDecision:
    """Choose a boundary and explain it; prohibited capabilities fail closed."""

    if request.requests_mutation:
        return BoundaryDecision(
            ExecutionBoundary.DENIED,
            "denied: routing is read-only and cannot authorize mutations",
        )
    if request.requests_privilege:
        return BoundaryDecision(
            ExecutionBoundary.DENIED,
            "denied: routing cannot request or elevate privileges",
        )
    host_reasons = []
    if request.requires_orca_ipc:
        host_reasons.append("Orca IPC is host-bound")
    if request.requires_external_network:
        host_reasons.append("external network access is host-bound")
    if request.requires_host_resource:
        host_reasons.append("the requested resource is host-bound")
    if host_reasons:
        return BoundaryDecision(ExecutionBoundary.HOST, "; ".join(host_reasons))
    return BoundaryDecision(
        ExecutionBoundary.SANDBOX,
        "sandbox: deterministic local read-only computation needs no host capability",
    )


@dataclass(frozen=True, slots=True)
class QueueWorkItem:
    task_id: str
    state: str
    priority: str
    lead_owner: str | None
    depends_on: tuple[str, ...] = ()
    write_scope: tuple[str, ...] = ()
    resource_locks: tuple[str, ...] = ()
    writer_lane: str = "shared"
    worker_profile: str = "balanced"
    reviewer_profile: str = "strong"

    def __post_init__(self) -> None:
        if _TASK_ID.fullmatch(self.task_id) is None:
            raise RoutingError("invalid Queue task id")
        if self.state not in _QUEUE_STATES:
            raise RoutingError(f"invalid state for {self.task_id}")
        if self.priority not in {"P0", "P1", "P2"}:
            raise RoutingError(f"invalid priority for {self.task_id}")
        if self.writer_lane not in _WRITER_LANES:
            raise RoutingError(f"invalid writer lane for {self.task_id}")
        if any(_TASK_ID.fullmatch(dependency) is None for dependency in self.depends_on):
            raise RoutingError(f"invalid dependency for {self.task_id}")
        for path in self.write_scope:
            parsed = PurePosixPath(path)
            if (
                not path
                or parsed.parts == (".",)
                or "\\" in path
                or parsed.is_absolute()
                or ".." in parsed.parts
                or parsed.as_posix() != path
            ):
                raise RoutingError(f"non-canonical write scope for {self.task_id}")


@dataclass(frozen=True, slots=True)
class LeadDecision:
    task_id: str
    lead_owner: str | None
    selected: bool
    reason: str


@dataclass(frozen=True, slots=True)
class LeadPlan:
    selected: tuple[QueueWorkItem, ...]
    decisions: tuple[LeadDecision, ...]
    capacity: int


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _conflict(
    left: QueueWorkItem,
    right: QueueWorkItem,
    *,
    enforce_writer_lane: bool,
) -> str | None:
    if enforce_writer_lane and "shared" in {left.writer_lane, right.writer_lane}:
        return "shared writer lane is exclusive"
    common_locks = sorted(set(left.resource_locks) & set(right.resource_locks))
    if common_locks:
        return f"resource lock overlaps {common_locks[0]}"
    for left_path in sorted(left.write_scope):
        for right_path in sorted(right.write_scope):
            if _paths_overlap(left_path, right_path):
                return f"write scope overlaps {left_path} / {right_path}"
    return None


def select_dependency_ready_leads(
    items: Iterable[QueueWorkItem],
    *,
    writer_limit: int = MAX_LEADS,
    completed_task_ids: Iterable[str] = (),
) -> LeadPlan:
    """Select up to three dependency-ready, pairwise-disjoint routed Leads.

    Active tasks consume capacity.  Active and Review tasks reserve their exact
    scopes and locks.  Selection is stable by priority then task id, and one
    task per Lead is selected in a projection.
    """

    if (
        not isinstance(writer_limit, int)
        or isinstance(writer_limit, bool)
        or not 0 <= writer_limit <= MAX_LEADS
    ):
        raise RoutingError("writer_limit must be between zero and three")
    projected = tuple(items)
    task_ids = [item.task_id for item in projected]
    if len(task_ids) != len(set(task_ids)):
        raise RoutingError("Queue projection repeats a task id")

    completed_from_index = frozenset(completed_task_ids)
    if any(_TASK_ID.fullmatch(task_id) is None for task_id in completed_from_index):
        raise RoutingError("completed task ids must be exact Queue task ids")
    completed = completed_from_index | {
        item.task_id for item in projected if item.state == "done"
    }
    active = tuple(sorted(
        (item for item in projected if item.state == "active"),
        key=lambda item: item.task_id,
    ))
    review_reservations = tuple(sorted(
        (item for item in projected if item.state == "review"),
        key=lambda item: item.task_id,
    ))
    capacity = max(0, min(MAX_LEADS, writer_limit) - len(active))
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}
    candidates = sorted(
        (item for item in projected if item.state == "ready"),
        key=lambda item: (priority_rank[item.priority], item.task_id),
    )
    selected: list[QueueWorkItem] = []
    decisions: list[LeadDecision] = []

    for item in candidates:
        reason: str | None = None
        if not item.lead_owner:
            reason = "not selected: no routed Lead owner"
        else:
            incomplete = sorted(
                dependency
                for dependency in item.depends_on
                if dependency not in completed
            )
            if incomplete:
                reason = f"not selected: dependencies not done: {','.join(incomplete)}"
        if reason is None and any(
            chosen.lead_owner == item.lead_owner for chosen in selected
        ):
            reason = "not selected: Lead already has a selected task"
        if reason is None:
            for reserved in active:
                conflict = _conflict(item, reserved, enforce_writer_lane=True)
                if conflict:
                    reason = f"not selected: {conflict} with {reserved.task_id}"
                    break
        if reason is None:
            for reserved in review_reservations:
                conflict = _conflict(item, reserved, enforce_writer_lane=False)
                if conflict:
                    reason = f"not selected: {conflict} with {reserved.task_id}"
                    break
        if reason is None:
            for reserved in selected:
                conflict = _conflict(item, reserved, enforce_writer_lane=True)
                if conflict:
                    reason = f"not selected: {conflict} with {reserved.task_id}"
                    break
        if reason is None and len(selected) >= capacity:
            reason = "not selected: three-writer capacity is exhausted"

        if reason is None:
            selected.append(item)
            decisions.append(
                LeadDecision(
                    item.task_id,
                    item.lead_owner,
                    True,
                    f"selected: {item.priority} dependency-ready and pairwise disjoint",
                )
            )
        else:
            decisions.append(LeadDecision(item.task_id, item.lead_owner, False, reason))

    return LeadPlan(tuple(selected), tuple(decisions), capacity)
