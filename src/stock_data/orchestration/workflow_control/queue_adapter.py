"""Read-only adapter for the canonical ``scripts/request_queue.py`` status contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable

from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    EventSource,
    WorkflowContractError,
    WorkflowEvent,
    parse_utc,
    utc_text,
)


_COUNT_KEYS = ("new", "waiting", "ready", "active", "review", "blocked", "done")
_TASK_ID_IN_DIRECTORY = re.compile(r"(?:^|-)RQ-\d{8}T\d{6}-[A-Z0-9]{4}(?:-|$)")
_EXACT_TASK_ID = re.compile(r"RQ-\d{8}T\d{6}-[A-Z0-9]{4}")
_OWNER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_MAX_CURRENT_TASKS = 64
_MAX_META_BYTES = 64 * 1024
_MAX_STATUS_CHARS = 16 * 1024


class QueueAdapterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QueueTaskOwnership:
    """Sanitized ownership fields from one current canonical Queue task."""

    task_id: str
    state: str
    owner: str
    lead_owner: str
    reviewer: str | None
    domain: str | None
    updated_at: datetime
    title: str | None = None

    def __post_init__(self) -> None:
        if _EXACT_TASK_ID.fullmatch(self.task_id) is None:
            raise WorkflowContractError("queue ownership task id is invalid")
        if self.state not in {"active", "review"}:
            raise WorkflowContractError("queue ownership state is not current")
        for value in (self.owner, self.lead_owner):
            if _OWNER_ID.fullmatch(value) is None:
                raise WorkflowContractError("queue ownership identity is invalid")
        if self.reviewer is not None and _OWNER_ID.fullmatch(self.reviewer) is None:
            raise WorkflowContractError("queue reviewer identity is invalid")
        if self.domain is not None and _OWNER_ID.fullmatch(self.domain) is None:
            raise WorkflowContractError("queue ownership domain is invalid")
        if self.title is not None:
            if not isinstance(self.title, str):
                raise WorkflowContractError("queue task title is invalid")
            normalized_title = self.title.strip()
            if (
                not normalized_title
                or len(normalized_title) > 160
                or any(ord(character) < 32 for character in normalized_title)
            ):
                raise WorkflowContractError("queue task title is invalid")
        utc_text(self.updated_at)


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    observed_at: datetime
    state_counts: tuple[tuple[str, int], ...]
    active_task_ids: tuple[str, ...]
    compacted_count: int
    current_tasks: tuple[QueueTaskOwnership, ...] = ()

    def __post_init__(self) -> None:
        utc_text(self.observed_at)
        if tuple(key for key, _value in self.state_counts) != _COUNT_KEYS:
            raise WorkflowContractError("queue snapshot state keys are not canonical")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for _key, value in self.state_counts
        ):
            raise WorkflowContractError("queue snapshot counts must be non-negative integers")
        if self.compacted_count < 0:
            raise WorkflowContractError("compacted_count must be non-negative")
        if any(_EXACT_TASK_ID.fullmatch(task_id) is None for task_id in self.active_task_ids):
            raise WorkflowContractError("active_task_ids must contain exact Queue task ids")
        if tuple(sorted(set(self.active_task_ids))) != self.active_task_ids:
            raise WorkflowContractError("active_task_ids must be unique and sorted")
        if self.count("active") != len(self.active_task_ids):
            raise WorkflowContractError("active task list does not match active count")
        current_ids = tuple(item.task_id for item in self.current_tasks)
        if current_ids != tuple(sorted(set(current_ids))):
            raise WorkflowContractError("current Queue ownership tasks must be unique and sorted")
        projected_active_ids = tuple(
            item.task_id for item in self.current_tasks if item.state == "active"
        )
        if self.current_tasks and projected_active_ids != self.active_task_ids:
            raise WorkflowContractError("active ownership tasks do not match Queue status")

    def count(self, state: str) -> int:
        return dict(self.state_counts)[state]

    def to_event(self) -> WorkflowEvent:
        material = "|".join(
            (
                utc_text(self.observed_at),
                *(f"{key}={value}" for key, value in self.state_counts),
                ",".join(self.active_task_ids),
                f"compacted={self.compacted_count}",
            )
        )
        event_id = "queue-snapshot-" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:24]
        return WorkflowEvent(
            event_id=event_id,
            occurred_at=self.observed_at,
            kind=EventKind.QUEUE_SNAPSHOT,
            source=EventSource.QUEUE,
            runnable_count=self.count("ready"),
            active_worker_count=self.count("active"),
            reason_code="QUEUE_STATUS_COMPACT",
        )


Runner = Callable[..., subprocess.CompletedProcess[str]]


class RequestQueueStatusAdapter:
    """Execute only ``status --compact`` and parse its stable two-line result."""

    def __init__(
        self,
        repository_root: Path,
        *,
        runner: Runner = subprocess.run,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.script_path = self.repository_root / "scripts" / "request_queue.py"
        self.queue_root = self.repository_root / "artifacts" / "request_queue"
        self.runner = runner
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be greater than zero and at most 30")
        self.timeout_seconds = float(timeout_seconds)

    def read_snapshot(self, *, observed_at: datetime) -> QueueSnapshot:
        utc_text(observed_at)
        if self.script_path.is_symlink() or not self.script_path.is_file():
            raise QueueAdapterError("canonical request_queue.py was not found")
        command = [
            sys.executable,
            str(self.script_path),
            "--root",
            str(self.queue_root),
            "status",
            "--compact",
        ]
        completed = self.runner(
            command,
            cwd=self.repository_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            raise QueueAdapterError(
                f"request_queue status failed with exit code {completed.returncode}"
            )
        snapshot = parse_compact_status(completed.stdout, observed_at=observed_at)
        current_tasks = self._read_current_ownership(snapshot)
        return QueueSnapshot(
            snapshot.observed_at,
            snapshot.state_counts,
            snapshot.active_task_ids,
            snapshot.compacted_count,
            current_tasks,
        )

    def _read_current_ownership(
        self, snapshot: QueueSnapshot,
    ) -> tuple[QueueTaskOwnership, ...]:
        """Read only bounded, explicitly allow-listed fields from active/review META.json."""
        state_roots = {
            "active": self.queue_root / "active",
            "review": self.queue_root / "review",
        }
        # Compact-status-only fixtures and older Queue roots remain supported.
        # A real canonical root that has either current-state directory is
        # validated as a complete snapshot to avoid displaying mixed epochs.
        if not any(path.is_dir() for path in state_roots.values()):
            return ()
        result: list[QueueTaskOwnership] = []
        for state, state_root in state_roots.items():
            if not state_root.exists():
                if snapshot.count(state):
                    raise QueueAdapterError(f"Queue {state} directory is missing")
                continue
            if state_root.is_symlink() or not state_root.is_dir():
                raise QueueAdapterError(f"Queue {state} directory is invalid")
            directories: list[Path] = []
            for index, directory in enumerate(state_root.iterdir()):
                if index >= _MAX_CURRENT_TASKS:
                    raise QueueAdapterError("Queue current task count exceeds display limit")
                directories.append(directory)
            directories.sort(key=lambda path: path.name)
            for directory in directories:
                if directory.is_symlink() or not directory.is_dir():
                    raise QueueAdapterError("Queue current task entry is invalid")
                match = _EXACT_TASK_ID.search(directory.name)
                if match is None:
                    raise QueueAdapterError("Queue current task directory is malformed")
                meta_path = directory / "META.json"
                if meta_path.is_symlink() or not meta_path.is_file():
                    raise QueueAdapterError("Queue current task metadata is missing")
                if meta_path.stat().st_size > _MAX_META_BYTES:
                    raise QueueAdapterError("Queue current task metadata exceeds size limit")
                try:
                    with meta_path.open("r", encoding="utf-8") as handle:
                        raw_meta = handle.read(_MAX_META_BYTES + 1)
                    if len(raw_meta) > _MAX_META_BYTES:
                        raise QueueAdapterError("Queue current task metadata exceeds size limit")
                    item = json.loads(raw_meta)
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise QueueAdapterError("Queue current task metadata is unreadable") from error
                if not isinstance(item, dict):
                    raise QueueAdapterError("Queue current task metadata is invalid")
                task_id = item.get("id")
                item_state = item.get("state")
                if task_id != match.group(0) or item_state != state:
                    raise QueueAdapterError("Queue current task metadata does not match its directory")
                owner = item.get("owner") or item.get("assigned_agent")
                lead_owner = item.get("lead_owner") or owner
                reviewer = item.get("reviewer") if state == "review" else None
                domain = item.get("domain")
                title = item.get("title")
                updated_at = item.get("updated_at")
                if not isinstance(owner, str) or not isinstance(lead_owner, str):
                    raise QueueAdapterError("Queue current task owner is missing")
                if reviewer is not None and not isinstance(reviewer, str):
                    raise QueueAdapterError("Queue current task reviewer is invalid")
                if state == "review" and reviewer is None:
                    raise QueueAdapterError("Queue review task reviewer is missing")
                if domain is not None and not isinstance(domain, str):
                    raise QueueAdapterError("Queue current task domain is invalid")
                if title is not None and not isinstance(title, str):
                    raise QueueAdapterError("Queue current task title is invalid")
                if not isinstance(updated_at, str):
                    raise QueueAdapterError("Queue current task update time is missing")
                try:
                    result.append(QueueTaskOwnership(
                        task_id, state, owner, lead_owner, reviewer, domain,
                        parse_utc(updated_at), title.strip() if title is not None else None,
                    ))
                except (ValueError, WorkflowContractError) as error:
                    raise QueueAdapterError("Queue current task metadata failed validation") from error
                if len(result) > _MAX_CURRENT_TASKS:
                    raise QueueAdapterError("Queue current task count exceeds display limit")
        result.sort(key=lambda item: item.task_id)
        if sum(item.state == "active" for item in result) != snapshot.count("active"):
            raise QueueAdapterError("Queue active metadata count does not match status")
        if sum(item.state == "review" for item in result) != snapshot.count("review"):
            raise QueueAdapterError("Queue review metadata count does not match status")
        return tuple(result)


def parse_compact_status(output: str, *, observed_at: datetime) -> QueueSnapshot:
    if len(output) > _MAX_STATUS_CHARS:
        raise QueueAdapterError("request_queue compact status exceeds size limit")
    lines = output.splitlines()
    if len(lines) != 2:
        raise QueueAdapterError("request_queue compact status must contain exactly two lines")
    values: dict[str, int] = {}
    for token in lines[0].split():
        if "=" not in token:
            raise QueueAdapterError("request_queue compact status contains a malformed count")
        key, raw_value = token.split("=", 1)
        if not raw_value.isascii() or not raw_value.isdecimal():
            raise QueueAdapterError(
                "request_queue compact status contains a non-integer count"
            )
        try:
            value = int(raw_value)
        except ValueError as error:
            raise QueueAdapterError(
                "request_queue compact status contains a non-integer count"
            ) from error
        if value < 0 or key in values:
            raise QueueAdapterError("request_queue compact status contains invalid counts")
        values[key] = value
    expected = {*_COUNT_KEYS, "compacted"}
    if set(values) != expected:
        raise QueueAdapterError("request_queue compact status count keys changed")
    if not lines[1].startswith("active="):
        raise QueueAdapterError("request_queue compact status active line changed")
    active_text = lines[1].removeprefix("active=")
    active_ids: list[str] = []
    if not active_text:
        raise QueueAdapterError("request_queue compact status active list is empty")
    if active_text != "-":
        for directory in active_text.split(","):
            if _TASK_ID_IN_DIRECTORY.search(directory) is None:
                raise QueueAdapterError("request_queue active task directory is malformed")
            match = _EXACT_TASK_ID.search(directory)
            assert match is not None
            active_ids.append(match.group(0))
    if len(active_ids) != len(set(active_ids)):
        raise QueueAdapterError("request_queue compact status repeats an active task")
    return QueueSnapshot(
        observed_at=observed_at,
        state_counts=tuple((key, values[key]) for key in _COUNT_KEYS),
        active_task_ids=tuple(sorted(active_ids)),
        compacted_count=values["compacted"],
    )
