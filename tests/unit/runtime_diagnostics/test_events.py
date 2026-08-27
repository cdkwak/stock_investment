from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime_diagnostics.events import (
    RuntimeDiagnosticEvent,
    artifact_identity,
    failure_event,
)


def test_failure_event_retains_only_owned_frames_and_exception_classes(tmp_path: Path) -> None:
    source = tmp_path / "owned.py"
    source.write_text("def fail():\n    raise ValueError('secret account=123')\n", encoding="utf-8")
    namespace: dict[str, object] = {}
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    try:
        namespace["fail"]()  # type: ignore[operator]
    except ValueError as error:
        event = failure_event(
            project_root=tmp_path, domain="BACKTEST", kind="TERMINAL_FAILURE",
            session_id="a" * 32, run_id="b" * 32, code="REPLAY_FAILED",
            stage="RUNNER", error=error,
            now=datetime(2026, 8, 25, tzinfo=timezone.utc),
        )

    rendered = str(event.to_dict())
    assert event.exception_classes == ("ValueError",)
    assert event.frames == ("owned.py:2",)
    assert "secret" not in rendered and "account" not in rendered and "123" not in rendered


def test_event_rejects_extra_private_or_unsafe_shape() -> None:
    base = dict(
        schema="runtime-diagnostic/v1", event_id="a" * 32,
        occurred_at="2026-08-25T00:00:00+00:00", domain="GUI",
        kind="TERMINAL_FAILURE", session_id="b" * 32, run_id=None,
        code="WORKER_FAILED", stage="WORKER", exception_classes=("ValueError",),
        frames=("src/app.py:10",),
    )
    with pytest.raises(TypeError):
        RuntimeDiagnosticEvent(**base, message="private")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        RuntimeDiagnosticEvent(**{**base, "frames": ("../secret.py:1",)})
    with pytest.raises(ValueError):
        RuntimeDiagnosticEvent(**{**base, "code": "https://private"})


def test_artifact_identity_is_owned_relative_and_content_bound(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts/backtest/result.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"{}\n")
    identity = artifact_identity(tmp_path, artifact)
    assert identity is not None
    assert identity.startswith("artifacts/backtest/result.json@sha256:")
    assert artifact_identity(tmp_path, tmp_path.parent / "outside.json") is None
