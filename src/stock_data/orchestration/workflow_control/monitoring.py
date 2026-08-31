"""Immutable, read-only projection of Python-only workflow state for the GUI.

The adapter keeps three different facts separate: Queue document ownership,
registered role sessions, and observed execution activity.  A Queue document
never becomes evidence that a PM, Lead, Worker, or Reviewer is running.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import parse_utc
from .queue_adapter import QueueSnapshot, RequestQueueStatusAdapter


_MAX_CURRENT_EXECUTION_ROLES = 48


@dataclass(frozen=True, slots=True)
class MonitoringWarning:
    code: str
    message: str
    severity: str = "warning"
    human_title: str | None = None
    operator_action: str | None = None


@dataclass(frozen=True, slots=True)
class RoleView:
    role_key: str
    role_kind: str
    state: str
    generation: int
    heartbeat_at: datetime | None
    lease_until: datetime | None
    active_task_id: str | None = None
    fresh: bool = True
    active: bool = True


@dataclass(frozen=True, slots=True)
class TaskView:
    task_id: str
    state: str
    priority: str | None = None
    domain: str | None = None
    updated_at: datetime | None = None
    summary: str | None = None
    owner: str | None = None
    reviewer: str | None = None
    # These display fields are deliberately optional: old SQLite and Queue
    # projections have no prose title, while the GUI must never invent a
    # worker or expose an opaque task ID as the primary label.
    human_title: str | None = None
    lead: str | None = None
    fix_count: int = 0
    last_activity: datetime | None = None


@dataclass(frozen=True, slots=True)
class EventView:
    event_id: str
    occurred_at: datetime
    kind: str
    source: str
    task_id: str | None = None
    reason_code: str | None = None
    human_message: str | None = None


@dataclass(frozen=True, slots=True)
class MonitoringSnapshot:
    observed_at: datetime
    pm: tuple[RoleView, ...] = ()
    leads: tuple[RoleView, ...] = ()
    workers: tuple[RoleView, ...] = ()
    reviewers: tuple[RoleView, ...] = ()
    queue: QueueSnapshot | None = None
    tasks: tuple[TaskView, ...] = ()
    events: tuple[EventView, ...] = ()
    warnings: tuple[MonitoringWarning, ...] = ()
    source_freshness: Mapping[str, datetime | None] = MappingProxyType({})
    pm_current_decision: str | None = None
    pm_next_action: str | None = None
    goal_summary: str | None = None
    queue_action: str | None = None
    proposal_state: str | None = None

    @property
    def task_summaries(self) -> tuple[TaskView, ...]:
        return self.tasks

    @property
    def recent_events(self) -> tuple[EventView, ...]:
        return self.events


class MonitoringSnapshotAdapter:
    """Build a bounded snapshot without mutating any source."""

    def __init__(self, repository_root: Path | None = None, *, workflow_db: Path | None = None,
                 role_db: Path | None = None, event_log: Path | None = None,
                 execution_source: Path | None = None,
                 service_db: Path | None = None,
                 control_root: Path | None = None,
                 queue_adapter: Any | None = None, stale_after_seconds: float = 120.0,
        max_events: int = 25) -> None:
        root = Path(repository_root or Path.cwd())
        if control_root is not None:
            state_root = Path(control_root)
        elif workflow_db is not None:
            # An explicitly injected workflow DB defines one coherent fixture
            # or alternate read-only state root.  Never mix it with the live
            # repository role registry merely because ``role_db`` was omitted.
            state_root = Path(workflow_db).parent
        else:
            state_root = root / "data" / "runtime" / "python_pm"
        self.workflow_db = Path(workflow_db or state_root / "workflow_state.sqlite3")
        self.role_db = Path(role_db or state_root / "role_registry.sqlite3")
        self.event_log = Path(event_log or state_root / "workflow_events.jsonl")
        self.execution_source = Path(execution_source or service_db or state_root / "workflow_controller_service.sqlite3")
        self.queue = queue_adapter or RequestQueueStatusAdapter(root)
        self.stale_after_seconds = stale_after_seconds
        self.max_events = max(1, int(max_events))

    def snapshot(self, *, observed_at: datetime | None = None) -> MonitoringSnapshot:
        now = observed_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        warnings: list[MonitoringWarning] = []
        queue = None
        try:
            queue = self.queue.read_snapshot(observed_at=now)
        except Exception:  # source failures are data, not fatal GUI errors
            warnings.append(MonitoringWarning("QUEUE_UNREADABLE", "Queue 상태를 읽을 수 없습니다."))
        roles: list[RoleView] = []
        if self.role_db.exists():
            try:
                roles = self._read_roles()
            except Exception:
                warnings.append(MonitoringWarning("ROLE_SOURCE_UNREADABLE", "역할 상태 저장소를 읽을 수 없습니다."))
        else:
            warnings.append(MonitoringWarning("ROLE_SOURCE_MISSING", "역할 상태 저장소가 없습니다."))
        fresh_keys: set[str] = set()
        for role in roles:
            if role.heartbeat_at is not None and (now - role.heartbeat_at).total_seconds() <= self.stale_after_seconds:
                fresh_keys.add(role.role_key)
            else:
                warnings.append(MonitoringWarning("STALE_HEARTBEAT", f"{role.role_key} heartbeat가 오래되었습니다."))
        roles = [RoleView(r.role_key, r.role_kind, r.state, r.generation, r.heartbeat_at,
                          r.lease_until, r.active_task_id, r.role_key in fresh_keys, r.active) for r in roles]
        tasks = self._read_tasks(warnings)
        execution_roles = self._read_execution_roles(warnings, now)
        # Operation activity is the freshest view for a role kind.  Fall back
        # to the persistent session registry only when that kind has no
        # execution record, so the same PM is not rendered twice.
        def current_roles(role_kind: str) -> tuple[RoleView, ...]:
            observed = tuple(r for r in execution_roles if r.role_kind == role_kind)
            connected_observed = tuple(r for r in observed if self._is_connected(r))
            registered = tuple(r for r in roles if r.role_kind == role_kind)
            return connected_observed or registered or observed

        pm = current_roles("project_manager")
        leads = current_roles("domain_lead")
        workers = current_roles("worker")
        reviewers = current_roles("reviewer")
        if queue is not None and queue.current_tasks:
            queue_tasks = {
                item.task_id: TaskView(
                    item.task_id, item.state, domain=item.domain,
                    updated_at=item.updated_at, owner=item.owner,
                    reviewer=item.reviewer, human_title=item.title,
                )
                for item in queue.current_tasks
            }
            tasks = tuple(
                sorted(
                    (*(
                        task for task in tasks if task.task_id not in queue_tasks
                    ), *queue_tasks.values()),
                    key=lambda task: task.task_id,
                )
            )
        # A stopped or stale row is history, not a connected session.
        active_pm = [
            r for r in pm
            if self._is_connected(r)
        ]
        if len(active_pm) > 1:
            warnings.append(MonitoringWarning("DUPLICATE_PM_GENERATION", "활성 PM 세대가 둘 이상입니다.", "error"))
        if queue and queue.current_tasks and not active_pm:
            warnings.append(MonitoringWarning(
                "PM_SESSION_MISSING",
                "작업 문건은 있지만 연결된 Python PM 세션이 없습니다.",
                "error",
                "PM 세션이 등록되지 않았습니다.",
                "Python PM을 시작하고 역할 저장소 등록 상태를 확인하세요.",
            ))
        connected_lead_tasks = {
            role.active_task_id for role in leads
            if self._is_connected(role) and role.active_task_id
        }
        connected_reviewer_tasks = {
            role.active_task_id for role in reviewers
            if self._is_connected(role) and role.active_task_id
        }
        if queue:
            for item in queue.current_tasks:
                if item.task_id not in connected_lead_tasks:
                    warnings.append(MonitoringWarning(
                        "LEAD_SESSION_MISSING",
                        f"{item.task_id} 문건에 연결된 Lead 세션이 없습니다.",
                        "error",
                        "문서상 담당자와 실제 Lead 세션이 연결되지 않았습니다.",
                        "PM이 문건을 실제 Lead 세션에 배정했는지 확인하세요.",
                    ))
                if item.state == "review" and item.task_id not in connected_reviewer_tasks:
                    warnings.append(MonitoringWarning(
                        "REVIEWER_SESSION_MISSING",
                        f"{item.task_id} 검토 문건에 연결된 Reviewer 세션이 없습니다.",
                        "error",
                        "검토 문건에 실제 Reviewer 세션이 없습니다.",
                        "Lead가 Reviewer 세션을 깨워 검토를 요청했는지 확인하세요.",
                    ))
        by_task: dict[str, list[RoleView]] = {}
        for role in (*leads, *workers, *reviewers):
            if role.active_task_id:
                by_task.setdefault(role.active_task_id, []).append(role)
        for task_id, owners in by_task.items():
            # A Lead coordinates its Workers and Reviewers for the same task.
            # Only exclusive Lead ownership is conflicting; membership roles
            # are normal and intentionally do not generate an alert.
            exclusive_leads = [owner for owner in owners if owner.role_kind == "domain_lead" and owner.active]
            if len(exclusive_leads) > 1:
                warnings.append(MonitoringWarning("OWNERSHIP_CONFLICT", f"{task_id} 소유자가 중복되었습니다.", "error"))
            if not exclusive_leads and any(owner.role_kind in {"worker", "reviewer"} for owner in owners):
                warnings.append(MonitoringWarning(
                    "OWNERSHIP_CONFLICT", f"{task_id} 실행 구성에 Lead 세션이 없습니다.", "error",
                ))
        events = self._read_events(warnings)
        tasks = self._enrich_task_display(tasks, events)
        events = self._enrich_event_display(events)
        freshness_data = {
            "queue_document": queue.observed_at if queue else None,
            "role_registry": max((r.heartbeat_at for r in roles if r.heartbeat_at), default=None),
            "execution": max((r.heartbeat_at for r in execution_roles if r.heartbeat_at), default=None),
            "events": events[-1].occurred_at if events else None,
        }
        freshness = MappingProxyType(freshness_data)
        return MonitoringSnapshot(
            now, pm, leads, workers, reviewers, queue, tasks, events,
            tuple(warnings), freshness,
            queue_action="작업 문서 상태를 읽었습니다." if queue is not None else None,
            proposal_state="문서 상태" if queue is not None else None,
        )

    @staticmethod
    def _is_connected(role: RoleView) -> bool:
        return (
            role.active
            and role.fresh
            and role.state.casefold() not in {"stopped", "recovery_required"}
        )

    @staticmethod
    def _enrich_task_display(
        tasks: tuple[TaskView, ...], events: tuple[EventView, ...],
    ) -> tuple[TaskView, ...]:
        """Add conservative, human-facing display defaults without writes."""
        domain_names = {
            "gui": "화면 개선 작업", "data": "데이터 확인 작업",
            "research": "조사 작업", "backtest": "검증 작업",
        }
        event_by_task: dict[str, list[EventView]] = {}
        for event in events:
            if event.task_id:
                event_by_task.setdefault(event.task_id, []).append(event)
        result: list[TaskView] = []
        for task in tasks:
            task_events = event_by_task.get(task.task_id, [])
            rework = sum(event.kind == "REWORK_REQUESTED" for event in task_events)
            last_activity = max(
                (event.occurred_at for event in task_events),
                default=None,
            )
            title = task.human_title or domain_names.get(
                str(task.domain or "").casefold(), "제목이 없는 Queue 문건",
            )
            state_names = {"active": "진행 문서", "review": "검토 문서"}
            summary = task.summary or f"작업 문서 상태: {state_names.get(task.state, task.state)}"
            result.append(replace(
                task,
                human_title=title,
                summary=summary,
                fix_count=max(0, task.fix_count, rework),
                last_activity=task.last_activity or last_activity,
            ))
        return tuple(result)

    @staticmethod
    def _enrich_event_display(
        events: tuple[EventView, ...],
    ) -> tuple[EventView, ...]:
        messages = {
            "TASK_TRANSITION": "작업 상태가 바뀌었습니다.",
            "REVIEW_RESULT": "검토 결과가 도착했습니다.",
            "REWORK_REQUESTED": "수정 요청이 전달되었습니다.",
            "ESCALATION": "확인이 필요한 내용이 올라왔습니다.",
            "SESSION_STARTED": "작업 확인을 시작했습니다.",
            "QUEUE_SNAPSHOT": "작업 목록을 다시 확인했습니다.",
        }
        return tuple(replace(
            event,
            human_message=event.human_message or messages.get(
                event.kind, "최근 활동을 확인했습니다.",
            ),
        ) for event in events)

    read_snapshot = snapshot

    def _read_roles(self) -> list[RoleView]:
        uri = "file:" + str(self.role_db.resolve()).replace("\\", "/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT role_key, role_kind, state, generation, heartbeat_at, lease_until, active_task_id FROM role_registry ORDER BY role_key").fetchall()
        result = []
        for row in rows:
            hb = parse_utc(row["heartbeat_at"]) if row["heartbeat_at"] else None
            lease = parse_utc(row["lease_until"]) if row["lease_until"] else None
            state = str(row["state"])
            result.append(RoleView(
                row["role_key"], row["role_kind"], state, int(row["generation"]), hb,
                lease, row["active_task_id"],
                active=state.casefold() not in {"stopped", "recovery_required"},
            ))
        return result

    def _read_tasks(self, warnings: list[MonitoringWarning]) -> tuple[TaskView, ...]:
        if not self.workflow_db.exists():
            warnings.append(MonitoringWarning("WORKFLOW_SOURCE_MISSING", "워크플로 상태 저장소가 없습니다."))
            return ()
        uri = "file:" + str(self.workflow_db.resolve()).replace("\\", "/") + "?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT task_id, state, priority, domain, updated_at FROM tasks ORDER BY task_id").fetchall()
            return tuple(TaskView(r["task_id"], r["state"], r["priority"], r["domain"], parse_utc(r["updated_at"])) for r in rows)
        except Exception:
            warnings.append(MonitoringWarning("WORKFLOW_SOURCE_UNREADABLE", "워크플로 상태 저장소를 읽을 수 없습니다."))
            return ()

    def _read_events(self, warnings: list[MonitoringWarning]) -> tuple[EventView, ...]:
        if not self.event_log.exists():
            warnings.append(MonitoringWarning("EVENT_SOURCE_MISSING", "이벤트 기록이 없습니다."))
            return ()
        result: list[EventView] = []
        try:
            lines: list[str] = []
            with self.event_log.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if len(line) > 16384:
                        raise ValueError("event line exceeds limit")
                    lines.append(line)
                    if len(lines) > self.max_events:
                        lines.pop(0)
            for line in lines:
                item = json.loads(line)
                result.append(EventView(str(item["event_id"]), parse_utc(item["occurred_at"]), str(item["kind"]), str(item["source"]), item.get("task_id"), item.get("reason_code")))
            return tuple(result)
        except Exception:
            warnings.append(MonitoringWarning("EVENT_SOURCE_UNREADABLE", "이벤트 기록을 읽을 수 없습니다."))
            return ()

    def _read_execution_roles(self, warnings: list[MonitoringWarning], now: datetime) -> tuple[RoleView, ...]:
        """Read the service's sanitized operation_activity contract, if supplied."""
        if self.execution_source is None or not self.execution_source.exists():
            warnings.append(MonitoringWarning("EXECUTION_SOURCE_MISSING", "Worker/Reviewer 실행 상태 소스가 없습니다."))
            return ()
        try:
            if self.execution_source.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                uri = "file:" + str(self.execution_source.resolve()).replace("\\", "/") + "?mode=ro"
                with sqlite3.connect(uri, uri=True) as db:
                    db.row_factory = sqlite3.Row
                    columns = {
                        str(column[1]) for column in db.execute("PRAGMA table_info(operation_activity)")
                    }
                    writer_columns = {
                        str(column[1]) for column in db.execute("PRAGMA table_info(writer_lease)")
                    }
                    has_generation = {"generation_sequence", "generation_digest"} <= columns
                    select_generation = (
                        ", generation_sequence, generation_digest" if has_generation else ""
                    )
                    rows = db.execute(
                        "SELECT operation_id, role_kind, task_id, state, heartbeat_at, active"
                        + select_generation + " FROM operation_activity ORDER BY operation_id"
                    ).fetchall()
                    lease = (
                        db.execute(
                            "SELECT generation_sequence, generation_digest FROM writer_lease "
                            "WHERE service_key = 'python_pm'"
                        ).fetchone()
                        if {"service_key", "generation_sequence", "generation_digest"} <= writer_columns
                        else None
                    )
                raw = [dict(row) for row in rows]
                # A canonical service database has a writer lease table.  Its
                # activity log is audit history, not a roster: project active
                # cards for the live generation only.  Once no writer owns a
                # lease, retain one newest PM settlement and no old Lead or
                # execution cards.  Older external sources remain compatible.
                if writer_columns:
                    if lease is not None:
                        raw = [
                            item for item in raw
                            if bool(item.get("active"))
                            and int(item.get("generation_sequence", 0)) == int(lease["generation_sequence"])
                            and str(item.get("generation_digest", "")) == str(lease["generation_digest"])
                        ]
                    else:
                        candidates = [item for item in raw if item.get("role_kind") == "project_manager"]
                        if candidates:
                            latest = max(candidates, key=lambda item: (
                                int(item.get("generation_sequence", 0)),
                                str(item.get("heartbeat_at", "")), str(item.get("operation_id", "")),
                            ))
                            raw = [{**latest, "state": "stopped", "active": False}]
                        else:
                            raw = []
            else:
                raw = []
                with self.execution_source.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if len(line) > 16384:
                            raise ValueError("execution line exceeds limit")
                        if line.strip():
                            raw.append(json.loads(line))
                        if len(raw) > 1000:
                            raise ValueError("execution source exceeds record limit")
            result = []
            for index, item in enumerate(raw):
                if item.get("role_kind") not in {"worker", "reviewer", "project_manager", "domain_lead"}:
                    continue
                if not isinstance(item.get("operation_id", index), (str, int)):
                    raise ValueError("invalid operation id")
                heartbeat = parse_utc(item["heartbeat_at"])
                operation_id = str(item.get("operation_id", index))
                role_key = "activity-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:16]
                result.append(RoleView(
                    role_key, item["role_kind"], str(item["state"]),
                    int(item.get("generation_sequence", item.get("generation", 0))), heartbeat, None,
                    item.get("task_id"), (now - heartbeat).total_seconds() <= self.stale_after_seconds,
                    bool(item.get("active")),
                ))
            # The mini dashboard needs a bounded glanceable roster.  An
            # operation has no display identity beyond its role and Queue task,
            # so retain its newest heartbeat deterministically and cap output.
            projected = result
            if writer_columns:
                newest: dict[tuple[str, str | None], RoleView] = {}
                for role in result:
                    # Keep all PM rows visible to duplicate-writer detection;
                    # execution cards otherwise have one intended role/task.
                    key = (role.role_kind, role.role_key if role.role_kind == "project_manager" else role.active_task_id)
                    previous = newest.get(key)
                    if previous is None or (
                        role.generation,
                        role.heartbeat_at or datetime.min.replace(tzinfo=timezone.utc),
                        role.role_key,
                    ) > (
                        previous.generation,
                        previous.heartbeat_at or datetime.min.replace(tzinfo=timezone.utc),
                        previous.role_key,
                    ):
                        newest[key] = role
                projected = list(newest.values())
            # Preserve the one decision-critical PM card even when a damaged
            # or unusually busy source reaches the defensive display cap.
            # Alphabetic role ordering would put domain Leads before the PM
            # and could otherwise trim the authoritative writer from view.
            role_order = {
                "project_manager": 0,
                "domain_lead": 1,
                "reviewer": 2,
                "worker": 3,
            }
            return tuple(sorted(
                projected,
                key=lambda role: (
                    role_order[role.role_kind], role.active_task_id or "",
                    -role.generation, role.role_key,
                ),
            )[:_MAX_CURRENT_EXECUTION_ROLES])
        except Exception:
            warnings.append(MonitoringWarning("EXECUTION_SOURCE_UNREADABLE", "Worker/Reviewer 실행 상태 소스를 읽을 수 없습니다."))
            return ()


def read_monitoring_snapshot(repository_root: Path, *, observed_at: datetime | None = None) -> MonitoringSnapshot:
    return MonitoringSnapshotAdapter(repository_root).snapshot(observed_at=observed_at)
