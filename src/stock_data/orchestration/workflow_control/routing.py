"""Deterministic, read-only role and execution routing.

The router consumes already-projected Queue facts.  It deliberately has no
Queue manager, Orca, subprocess, network, or SDK dependency and therefore
cannot triage, claim, dispatch, or activate work.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Iterable, Mapping


MAX_LEADS = 3
_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_QUEUE_STATES = {"new", "waiting", "ready", "active", "review", "blocked", "done"}
_WRITER_LANES = {"gui", "data", "backtest", "shared"}
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_GENERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')


class RoutingError(ValueError):
    """Raised when routing input is not a safe canonical projection."""


class WorkflowRole(StrEnum):
    PROJECT_MANAGER = "project_manager"
    LEAD = "lead"
    WORKER = "worker"
    REVIEWER = "reviewer"


class RoleAction(StrEnum):
    """State intents; actual Queue mutation remains outside this router."""

    STRUCTURAL_DECISION = "structural_decision"
    ASSIGN_LEAD = "assign_lead"
    REPLAN = "replan"
    PROGRESS_CHECKPOINT = "progress_checkpoint"
    DISPATCH_WORKER = "dispatch_worker"
    REQUEST_REVIEW = "request_review"
    COMPOSE_PASS = "compose_pass"
    SUBMIT_CANDIDATE = "submit_candidate"
    REVIEW_FIX = "review_fix"
    REVIEW_PASS = "review_pass"


_ROLE_ACTIONS: Mapping[WorkflowRole, frozenset[RoleAction]] = {
    WorkflowRole.PROJECT_MANAGER: frozenset(
        {RoleAction.STRUCTURAL_DECISION, RoleAction.ASSIGN_LEAD, RoleAction.REPLAN}
    ),
    WorkflowRole.LEAD: frozenset(
        {
            RoleAction.PROGRESS_CHECKPOINT,
            RoleAction.DISPATCH_WORKER,
            RoleAction.REQUEST_REVIEW,
            RoleAction.COMPOSE_PASS,
        }
    ),
    WorkflowRole.WORKER: frozenset({RoleAction.SUBMIT_CANDIDATE}),
    WorkflowRole.REVIEWER: frozenset({RoleAction.REVIEW_FIX, RoleAction.REVIEW_PASS}),
}


def require_role_authority(role: WorkflowRole, action: RoleAction) -> None:
    """Reject cross-role state mutation before any durable write is attempted."""

    if not isinstance(role, WorkflowRole) or not isinstance(action, RoleAction):
        raise RoutingError("role authority requires typed role and action")
    if action not in _ROLE_ACTIONS[role]:
        raise RoutingError(f"{role.value} cannot perform {action.value}")


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
    if request.requires_orca_ipc:
        return BoundaryDecision(
            ExecutionBoundary.DENIED,
            "denied: Python is the sole control plane and Orca IPC has no runtime route",
        )
    host_reasons = []
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
    review_snapshot_pinned: bool = False

    def __post_init__(self) -> None:
        if _TASK_ID.fullmatch(self.task_id) is None:
            raise RoutingError("invalid Queue task id")
        if self.state not in _QUEUE_STATES:
            raise RoutingError(f"invalid state for {self.task_id}")
        if self.priority not in {"P0", "P1", "P2"}:
            raise RoutingError(f"invalid priority for {self.task_id}")
        if self.writer_lane not in _WRITER_LANES:
            raise RoutingError(f"invalid writer lane for {self.task_id}")
        if not isinstance(self.review_snapshot_pinned, bool):
            raise RoutingError(f"invalid review snapshot flag for {self.task_id}")
        if any(_TASK_ID.fullmatch(dependency) is None for dependency in self.depends_on):
            raise RoutingError(f"invalid dependency for {self.task_id}")
        for path in self.write_scope:
            _canonical_path(path, task_id=self.task_id)


def _canonical_path(path: str, *, task_id: str) -> str:
    if not isinstance(path, str):
        raise RoutingError(f"non-canonical write scope for {task_id}")
    parsed = PurePosixPath(path)
    if (
        not path
        or parsed.parts == (".",)
        or "\\" in path
        or parsed.is_absolute()
        or ".." in parsed.parts
        or parsed.as_posix() != path
    ):
        raise RoutingError(f"non-canonical write scope for {task_id}")
    for part in parsed.parts:
        if (
            part.endswith((".", " "))
            or any(character in _WINDOWS_FORBIDDEN or ord(character) < 32 for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        ):
            raise RoutingError(f"non-canonical write scope for {task_id}")
    return path


def _canonical_parts(path: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PurePosixPath(path).parts)


def _path_is_within(path: str, allowed: str) -> bool:
    path_parts = _canonical_parts(path)
    allowed_parts = _canonical_parts(allowed)
    return path_parts[: len(allowed_parts)] == allowed_parts


def require_unique_role_sessions(
    *,
    lead_role_key: str,
    reviewer_role_key: str,
    worker_role_keys: tuple[str, ...],
    session_ids: Mapping[str, str],
) -> None:
    """Require independent durable Codex sessions for Lead/Workers/Reviewer."""

    role_keys = (lead_role_key, *worker_role_keys, reviewer_role_key)
    if set(session_ids) != set(role_keys):
        raise RoutingError("role session projection does not match the task contract")
    values = tuple(session_ids[role_key] for role_key in role_keys)
    if any(not isinstance(value, str) or not value for value in values):
        raise RoutingError("role session projection contains an invalid session")
    if len(values) != len(set(values)):
        raise RoutingError("Reviewer, Lead, and Workers require unique Codex sessions")


@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    worker_role_key: str
    write_scope: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.worker_role_key, str) or _ROLE_KEY.fullmatch(self.worker_role_key) is None:
            raise RoutingError("worker role key is invalid")
        if not isinstance(self.write_scope, tuple) or not self.write_scope:
            raise RoutingError("worker assignment requires a non-empty write scope")
        normalized = tuple(_canonical_path(path, task_id=self.worker_role_key) for path in self.write_scope)
        if len(normalized) != len(set(normalized)):
            raise RoutingError("worker assignment repeats a write scope")


@dataclass(frozen=True, slots=True)
class TaskContract:
    """Immutable PM-to-Lead contract with a preassigned independent Reviewer."""

    task_id: str
    queue_generation: str
    pm_role_key: str
    lead_role_key: str
    reviewer_role_key: str
    write_scope: tuple[str, ...]
    worker_assignments: tuple[WorkerAssignment, ...]
    worker_profile: str = "balanced"
    reviewer_profile: str = "strong"
    contract_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or _TASK_ID.fullmatch(self.task_id) is None:
            raise RoutingError("task contract id is invalid")
        if not isinstance(self.queue_generation, str) or _GENERATION.fullmatch(self.queue_generation) is None:
            raise RoutingError("task contract generation is invalid")
        for value, label in (
            (self.pm_role_key, "PM"),
            (self.lead_role_key, "Lead"),
            (self.reviewer_role_key, "Reviewer"),
        ):
            if not isinstance(value, str) or _ROLE_KEY.fullmatch(value) is None:
                raise RoutingError(f"task contract {label} role is invalid")
        if len({self.pm_role_key, self.lead_role_key, self.reviewer_role_key}) != 3:
            raise RoutingError("PM, Lead, and Reviewer identities must be distinct")
        if not isinstance(self.write_scope, tuple) or not self.write_scope:
            raise RoutingError("task contract requires a non-empty write scope")
        scope = tuple(_canonical_path(path, task_id=self.task_id) for path in self.write_scope)
        if len(scope) != len(set(scope)):
            raise RoutingError("task contract repeats a write scope")
        if not isinstance(self.worker_assignments, tuple) or any(
            not isinstance(item, WorkerAssignment) for item in self.worker_assignments
        ):
            raise RoutingError("task contract Worker assignments are invalid")
        if not self.worker_assignments:
            raise RoutingError("task contract requires a non-empty Worker assignment")
        workers = tuple(item.worker_role_key for item in self.worker_assignments)
        if len(workers) != len(set(workers)):
            raise RoutingError("task contract repeats a Worker identity")
        if self.reviewer_role_key in workers or self.lead_role_key in workers:
            raise RoutingError("Reviewer and Lead cannot also be Workers")
        assigned_paths: list[tuple[str, str]] = []
        for assignment in self.worker_assignments:
            for path in assignment.write_scope:
                if not any(_path_is_within(path, allowed) for allowed in scope):
                    raise RoutingError("worker assignment escapes the task contract scope")
                for prior_worker, prior_path in assigned_paths:
                    if _paths_overlap(path, prior_path):
                        raise RoutingError(
                            f"worker scopes overlap between {prior_worker} and "
                            f"{assignment.worker_role_key}"
                        )
                assigned_paths.append((assignment.worker_role_key, path))
        if self.worker_profile not in _QUEUE_PROFILES:
            raise RoutingError("task contract worker profile is invalid")
        if self.reviewer_profile not in _QUEUE_PROFILES:
            raise RoutingError("task contract reviewer profile is invalid")
        expected = hashlib.sha256(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "queue_generation": self.queue_generation,
                    "pm_role_key": self.pm_role_key,
                    "lead_role_key": self.lead_role_key,
                    "reviewer_role_key": self.reviewer_role_key,
                    "write_scope": list(self.write_scope),
                    "worker_assignments": [
                        {
                            "worker_role_key": item.worker_role_key,
                            "write_scope": list(item.write_scope),
                        }
                        for item in self.worker_assignments
                    ],
                    "worker_profile": self.worker_profile,
                    "reviewer_profile": self.reviewer_profile,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.contract_digest and self.contract_digest != expected:
            raise RoutingError("task contract digest does not match its content")
        object.__setattr__(self, "contract_digest", expected)


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
    left_parts = _canonical_parts(left)
    right_parts = _canonical_parts(right)
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
        (
            item for item in projected
            if item.state == "review" and not item.review_snapshot_pinned
        ),
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
