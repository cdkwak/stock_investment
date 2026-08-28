"""Read-only adapter for the canonical ``scripts/request_queue.py`` status contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
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
    utc_text,
)


_COUNT_KEYS = ("new", "waiting", "ready", "active", "review", "blocked", "done")
_TASK_ID_IN_DIRECTORY = re.compile(r"(?:^|-)RQ-\d{8}T\d{6}-[A-Z0-9]{4}(?:-|$)")
_EXACT_TASK_ID = re.compile(r"RQ-\d{8}T\d{6}-[A-Z0-9]{4}")


class QueueAdapterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    observed_at: datetime
    state_counts: tuple[tuple[str, int], ...]
    active_task_ids: tuple[str, ...]
    compacted_count: int

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
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.script_path = self.repository_root / "scripts" / "request_queue.py"
        self.queue_root = self.repository_root / "artifacts" / "request_queue"
        self.runner = runner

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
        )
        if completed.returncode != 0:
            raise QueueAdapterError(
                f"request_queue status failed with exit code {completed.returncode}"
            )
        return parse_compact_status(completed.stdout, observed_at=observed_at)


def parse_compact_status(output: str, *, observed_at: datetime) -> QueueSnapshot:
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
