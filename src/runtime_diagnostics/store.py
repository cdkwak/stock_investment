from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from contextlib import contextmanager
from typing import Any

from .events import RuntimeDiagnosticEvent, failure_event


@contextmanager
def _store_lock(root: Path) -> Any:
    path = root / ".store.lock"
    deadline = time.monotonic() + 2.0
    with path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("runtime diagnostic store lock timeout") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class RuntimeDiagnosticStore:
    def __init__(self, root: Path, *, max_events: int = 200) -> None:
        if max_events < 1 or max_events > 10_000:
            raise ValueError("runtime diagnostic bound is invalid")
        self.root = Path(root)
        self.max_events = max_events

    def append(self, event: RuntimeDiagnosticEvent) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        with _store_lock(self.root):
            for orphan in self.root.glob(".runtime-*.tmp"):
                orphan.unlink(missing_ok=True)
            body = (json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()
            temporary: Path | None = None
            target = self.root / f"{event.occurred_at.replace(':', '')}-{event.event_id}.json"
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.root, prefix=".runtime-", suffix=".tmp", delete=False,
                ) as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                os.replace(temporary, target)
                temporary = None
                retained = sorted(self.root.glob("*.json"))
                for expired in retained[:-self.max_events]:
                    expired.unlink(missing_ok=True)
                return target
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def latest(self, *, limit: int = 20) -> tuple[dict[str, object], ...]:
        if limit < 1 or limit > self.max_events:
            raise ValueError("runtime diagnostic read limit is invalid")
        result: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*.json"), reverse=True)[:limit]:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            try:
                event = RuntimeDiagnosticEvent.from_dict(value)
            except (TypeError, ValueError):
                continue
            result.append(event.to_dict())
        return tuple(result)


def safe_append(store: RuntimeDiagnosticStore, event: RuntimeDiagnosticEvent) -> None:
    try:
        store.append(event)
    except Exception:
        pass


def safe_record_failure(store: RuntimeDiagnosticStore, **details: object) -> None:
    try:
        event = failure_event(**details)  # type: ignore[arg-type]
        store.append(event)
    except Exception:
        pass
