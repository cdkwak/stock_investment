"""Deterministic Markdown state and overnight bottleneck projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from stock_data.orchestration.workflow_control.contracts import (
    DIGEST_SCHEMA_VERSION,
    STATE_PROJECTION_SCHEMA_VERSION,
    EventKind,
    ReviewOutcome,
    TaskSnapshot,
    TaskState,
    WorkflowContractError,
    WorkflowEvent,
    parse_utc,
    utc_text,
)


@dataclass(frozen=True, slots=True)
class DigestMetrics:
    throughput: int
    wait_count: int
    mean_wait_ms: int
    max_wait_ms: int
    rework_count: int
    review_failure_count: int
    repeated_escalation_groups: int
    repeated_escalation_count: int
    runnable_idle_observations: int
    session_start_count: int


@dataclass(frozen=True, slots=True)
class BottleneckMetric:
    code: str
    observations: int
    detail: str


@dataclass(frozen=True, slots=True)
class WorkflowDigest:
    window_start: datetime
    window_end: datetime
    metrics: DigestMetrics
    bottlenecks: tuple[BottleneckMetric, ...]
    schema_version: str = DIGEST_SCHEMA_VERSION


def build_digest(
    events: Iterable[WorkflowEvent],
    *,
    window_start: datetime,
    window_end: datetime,
) -> WorkflowDigest:
    start_text = utc_text(window_start)
    end_text = utc_text(window_end)
    if start_text >= end_text:
        raise WorkflowContractError("digest window_start must precede window_end")

    ordered = sorted(events, key=lambda event: event.sort_key)
    in_window = [
        event
        for event in ordered
        if start_text <= utc_text(event.occurred_at) < end_text
    ]

    ready_at: dict[str, str] = {}
    waits: list[int] = []
    throughput_tasks: set[str] = set()
    for event in ordered:
        event_text = utc_text(event.occurred_at)
        if event_text >= end_text:
            break
        if event.kind is not EventKind.TASK_TRANSITION or event.task_id is None:
            continue
        if event.to_state is TaskState.READY:
            ready_at[event.task_id] = event_text
        elif event.to_state is TaskState.ACTIVE:
            ready = ready_at.pop(event.task_id, None)
            if ready is not None and start_text <= event_text:
                elapsed = parse_utc(event_text) - parse_utc(ready)
                wait_ms = (
                    elapsed.days * 86_400_000
                    + elapsed.seconds * 1_000
                    + elapsed.microseconds // 1_000
                )
                if wait_ms >= 0:
                    waits.append(wait_ms)
        elif event.from_state is TaskState.READY:
            ready_at.pop(event.task_id, None)
        if (
            start_text <= event_text
            and event.to_state is TaskState.DONE
        ):
            throughput_tasks.add(event.task_id)

    rework_count = sum(event.kind is EventKind.REWORK_REQUESTED for event in in_window)
    review_failure_count = sum(
        event.kind is EventKind.REVIEW_RESULT
        and event.outcome is ReviewOutcome.FAILED
        for event in in_window
    )
    escalation_counts: dict[str, int] = {}
    for event in in_window:
        if event.kind is EventKind.ESCALATION:
            assert event.recurrence_fingerprint is not None
            escalation_counts[event.recurrence_fingerprint] = (
                escalation_counts.get(event.recurrence_fingerprint, 0) + 1
            )
    repeated_groups = sum(count > 1 for count in escalation_counts.values())
    repeated_count = sum(max(0, count - 1) for count in escalation_counts.values())
    runnable_idle = sum(
        event.kind is EventKind.QUEUE_SNAPSHOT
        and (event.runnable_count or 0) > 0
        and event.active_worker_count == 0
        for event in in_window
    )
    sessions = {
        event.session_fingerprint
        for event in in_window
        if event.kind is EventKind.SESSION_STARTED
    }
    mean_wait = sum(waits) // len(waits) if waits else 0
    max_wait = max(waits, default=0)
    metrics = DigestMetrics(
        throughput=len(throughput_tasks),
        wait_count=len(waits),
        mean_wait_ms=mean_wait,
        max_wait_ms=max_wait,
        rework_count=rework_count,
        review_failure_count=review_failure_count,
        repeated_escalation_groups=repeated_groups,
        repeated_escalation_count=repeated_count,
        runnable_idle_observations=runnable_idle,
        session_start_count=len(sessions),
    )
    bottlenecks = (
        BottleneckMetric(
            "WAIT_TIME",
            metrics.wait_count,
            f"mean_ms={metrics.mean_wait_ms}; max_ms={metrics.max_wait_ms}",
        ),
        BottleneckMetric("REWORK", metrics.rework_count, "rework requests"),
        BottleneckMetric(
            "REVIEW_FAILURE",
            metrics.review_failure_count,
            "failed review results",
        ),
        BottleneckMetric(
            "REPEATED_ESCALATION",
            metrics.repeated_escalation_count,
            f"groups={metrics.repeated_escalation_groups}",
        ),
        BottleneckMetric(
            "RUNNABLE_IDLE",
            metrics.runnable_idle_observations,
            "snapshots with runnable work and no active worker",
        ),
    )
    return WorkflowDigest(
        window_start=window_start,
        window_end=window_end,
        metrics=metrics,
        bottlenecks=tuple(
            sorted(bottlenecks, key=lambda item: (-item.observations, item.code))
        ),
    )


def render_digest(digest: WorkflowDigest) -> str:
    metrics = digest.metrics
    lines = [
        "# Workflow Overnight Digest",
        "",
        f"schema: `{digest.schema_version}`",
        f"window: `{utc_text(digest.window_start)}` to `{utc_text(digest.window_end)}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Throughput | {metrics.throughput} |",
        f"| Activations measured for wait | {metrics.wait_count} |",
        f"| Mean ready-to-active wait (ms) | {metrics.mean_wait_ms} |",
        f"| Maximum ready-to-active wait (ms) | {metrics.max_wait_ms} |",
        f"| Rework requests | {metrics.rework_count} |",
        f"| Review failures | {metrics.review_failure_count} |",
        f"| Repeated escalation groups | {metrics.repeated_escalation_groups} |",
        f"| Repeated escalations beyond first | {metrics.repeated_escalation_count} |",
        f"| Runnable-idle observations | {metrics.runnable_idle_observations} |",
        f"| Unique session starts | {metrics.session_start_count} |",
        "",
        "## Bottleneck Signals",
        "",
        "| Signal | Observations | Detail |",
        "|---|---:|---|",
    ]
    lines.extend(
        f"| {metric.code} | {metric.observations} | {metric.detail} |"
        for metric in digest.bottlenecks
    )
    return "\n".join(lines) + "\n"


_STATE_ORDER = {state: index for index, state in enumerate(TaskState)}


def render_state_projection(
    snapshots: Iterable[TaskSnapshot],
    *,
    as_of: datetime,
) -> str:
    ordered = sorted(
        snapshots,
        key=lambda item: (_STATE_ORDER[item.state], item.task_id),
    )
    lines = [
        "# Workflow State",
        "",
        f"schema: `{STATE_PROJECTION_SCHEMA_VERSION}`",
        f"as_of: `{utc_text(as_of)}`",
        "",
        "| Task | State | Priority | Domain | Updated |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                snapshot.task_id,
                snapshot.state.value,
                snapshot.priority.value if snapshot.priority else "-",
                snapshot.domain or "-",
                utc_text(snapshot.updated_at),
            )
        )
        + " |"
        for snapshot in ordered
    )
    return "\n".join(lines) + "\n"
