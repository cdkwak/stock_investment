"""Append-only, canonical JSONL storage for sanitized workflow events."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterator

from stock_data.orchestration.workflow_control.contracts import WorkflowEvent


class EventLedgerError(RuntimeError):
    pass


class EventLedgerConflictError(EventLedgerError):
    pass


def canonical_event_json(event: WorkflowEvent) -> str:
    return json.dumps(
        event.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class LedgerAppendResult:
    appended: bool
    duplicate: bool


class SanitizedJsonlLedger:
    """One append-only JSONL ledger.

    Cross-process callers serialize appends through the owning SQLite
    ``BEGIN IMMEDIATE`` transaction.  The local lock also prevents interleaving
    between threads sharing one process.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def iter_events(self) -> Iterator[WorkflowEvent]:
        if not self.path.exists():
            return
        if self.path.is_symlink() or not self.path.is_file():
            raise EventLedgerError("event ledger must be a regular file")
        try:
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.endswith("\n"):
                        raise EventLedgerError(
                            f"event ledger line {line_number} is incomplete"
                        )
                    body = line[:-1]
                    if not body:
                        raise EventLedgerError(f"event ledger line {line_number} is empty")
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError as error:
                        raise EventLedgerError(
                            f"event ledger line {line_number} is invalid JSON"
                        ) from error
                    if not isinstance(payload, dict):
                        raise EventLedgerError(
                            f"event ledger line {line_number} is not an object"
                        )
                    event = WorkflowEvent.from_dict(payload)
                    if canonical_event_json(event) != body:
                        raise EventLedgerError(
                            f"event ledger line {line_number} is not canonical"
                        )
                    yield event
        except UnicodeDecodeError as error:
            raise EventLedgerError("event ledger is not valid UTF-8") from error

    def append(self, event: WorkflowEvent) -> LedgerAppendResult:
        canonical = canonical_event_json(event)
        with self._lock:
            for existing in self.iter_events():
                if existing.event_id != event.event_id:
                    continue
                if canonical_event_json(existing) != canonical:
                    raise EventLedgerConflictError(
                        f"event_id {event.event_id} has conflicting ledger content"
                    )
                return LedgerAppendResult(appended=False, duplicate=True)

            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
                raise EventLedgerError("event ledger must be a regular file")
            encoded = (canonical + "\n").encode("utf-8")
            descriptor = os.open(
                self.path,
                os.O_APPEND
                | os.O_CREAT
                | os.O_WRONLY
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise EventLedgerError("event ledger append was incomplete")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return LedgerAppendResult(appended=True, duplicate=False)
