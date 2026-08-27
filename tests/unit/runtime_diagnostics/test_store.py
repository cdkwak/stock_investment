from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from runtime_diagnostics import (
    RuntimeDiagnosticStore,
    failure_event,
    safe_append,
    safe_record_failure,
)
import runtime_diagnostics.store as store_module


def _event(root: Path, index: int):
    try:
        raise RuntimeError("never retained")
    except RuntimeError as error:
        return failure_event(
            project_root=root, domain="GUI", kind="TERMINAL_FAILURE",
            session_id="a" * 32, run_id=None, code="APP_FAILED", stage="MAIN",
            error=error,
            now=datetime(2026, 8, 25, tzinfo=timezone.utc) + timedelta(seconds=index),
        )


def test_store_atomically_rotates_and_reads_bounded_latest(tmp_path: Path) -> None:
    store = RuntimeDiagnosticStore(tmp_path / "logs", max_events=2)
    for index in range(3):
        store.append(_event(tmp_path, index))
    files = list((tmp_path / "logs").glob("*.json"))
    assert len(files) == 2
    assert not list((tmp_path / "logs").glob("*.tmp"))
    latest = store.latest(limit=2)
    assert len(latest) == 2
    assert all(set(item) == {
        "schema", "event_id", "occurred_at", "domain", "kind", "session_id",
        "run_id", "code", "stage", "exception_classes", "frames",
        "artifacts",
    } for item in latest)


def test_read_skips_interrupted_or_corrupt_files(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    (root / ".runtime-interrupted.tmp").write_text("private", encoding="utf-8")
    (root / "corrupt.json").write_text("{", encoding="utf-8")
    (root / "private.json").write_text(
        json.dumps({"message": "private holding"}), encoding="utf-8",
    )
    assert RuntimeDiagnosticStore(root).latest() == ()
    RuntimeDiagnosticStore(root).append(_event(tmp_path, 0))
    assert not list(root.glob(".runtime-*.tmp"))


def test_safe_append_never_changes_caller_outcome(tmp_path: Path, monkeypatch) -> None:
    store = RuntimeDiagnosticStore(tmp_path / "logs")
    monkeypatch.setattr(store, "append", lambda _event: (_ for _ in ()).throw(OSError()))
    assert safe_append(store, _event(tmp_path, 0)) is None
    monkeypatch.setattr(
        store_module, "failure_event",
        lambda **_details: (_ for _ in ()).throw(ValueError("schema failure")),
    )
    assert safe_record_failure(store, project_root=tmp_path) is None
