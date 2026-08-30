from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryConflictError,
    CodexBoundaryProcessError,
    CodexBoundaryRequestError,
    CodexBoundaryStateError,
    CodexBoundaryUnsupportedActionError,
    CodexBoundaryUncertainOperationError,
    CodexCliBoundary,
    background_creationflags,
)
from stock_data.orchestration.workflow_control.runner import (
    InjectedDirectRunner,
    RunnerAction,
)
from stock_data.orchestration.workflow_control.session_runner import (
    InjectedSessionRunner,
    SessionAction,
)


TASK_ID = "RQ-20260830T120000-A1B2"
ROLE_KEY = "lead_gui"
GENERATION = "a" * 64
SESSION_ID = "019cafe0-1234-7000-8000-abcdef123456"


def _events(session_id: str = SESSION_ID, *, transcript: str = "done") -> bytes:
    values = (
        {"type": "thread.started", "thread_id": session_id},
        {"type": "item.completed", "item": {"type": "agent_message", "text": transcript}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    )
    return b"".join(
        json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        for value in values
    )


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = _events(),
        stderr: bytes = b"",
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.timeout = timeout
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("codex", timeout)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode if self.killed or not self.timeout else None

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeProcessFactory:
    def __init__(self, processes: list[FakeProcess]) -> None:
        self.processes = list(processes)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> FakeProcess:
        self.calls.append((argv, kwargs))
        if not self.processes:
            raise AssertionError("unexpected Codex process spawn")
        return self.processes.pop(0)


def _boundary(tmp_path: Path, factory: FakeProcessFactory, **kwargs: object) -> CodexCliBoundary:
    return CodexCliBoundary(
        tmp_path / "codex-boundary.sqlite3",
        cwd=tmp_path,
        process_factory=factory,
        **kwargs,
    )


def _launch(runner: InjectedDirectRunner, *, event: str = "evt-launch"):
    return runner.run(
        RunnerAction.LAUNCH,
        task_id=TASK_ID,
        role_key=ROLE_KEY,
        generation=GENERATION,
        source_event_id=event,
    )


def test_direct_boundary_launch_resume_and_settle_use_codex_argv(tmp_path: Path) -> None:
    factory = FakeProcessFactory([FakeProcess(), FakeProcess(), FakeProcess()])
    boundary = _boundary(tmp_path, factory, sandbox_mode="read-only")
    runner = InjectedDirectRunner(boundary)

    launch = _launch(runner)
    resume = runner.run(
        RunnerAction.RESUME,
        task_id=TASK_ID,
        role_key=ROLE_KEY,
        generation=GENERATION,
        source_event_id="evt-retry",
        attempt=1,
        retry_of="dispatch-1",
        retry_provenance="b" * 64,
    )
    settle = runner.run(
        RunnerAction.SETTLE,
        task_id=TASK_ID,
        role_key=ROLE_KEY,
        generation=GENERATION,
        source_event_id="evt-settle",
    )

    assert (launch.status, resume.status, settle.status) == (
        "launched",
        "resumed",
        "settled",
    )
    assert launch.agent_id == resume.agent_id == settle.agent_id == SESSION_ID
    launch_argv, launch_kwargs = factory.calls[0]
    assert isinstance(launch_argv, list)
    assert launch_argv[:3] == ["codex", "exec", "--json"]
    assert "--approve-for-me" not in launch_argv
    assert launch_argv[launch_argv.index("--sandbox") + 1] == "read-only"
    assert launch_kwargs["shell"] is False
    assert launch_kwargs["stdin"] is subprocess.DEVNULL
    if os.name == "nt":
        assert int(launch_kwargs["creationflags"]) & subprocess.CREATE_NO_WINDOW
    assert factory.calls[1][0][:5] == ["codex", "exec", "resume", SESSION_ID, "--json"]
    assert factory.calls[2][0][:5] == ["codex", "exec", "resume", SESSION_ID, "--json"]
    assert all("orca" not in part.casefold() for argv, _ in factory.calls for part in argv)


def test_windows_background_flags_include_no_console() -> None:
    if os.name == "nt":
        assert background_creationflags() & subprocess.CREATE_NO_WINDOW
        assert background_creationflags() & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert background_creationflags() == 0


def test_workspace_write_launch_uses_reviewed_mode_without_conflicting_sandbox(
    tmp_path: Path,
) -> None:
    factory = FakeProcessFactory([FakeProcess()])
    boundary = _boundary(tmp_path, factory, sandbox_mode="workspace-write")

    receipt = _launch(InjectedDirectRunner(boundary))
    argv = factory.calls[0][0]

    assert receipt.workspace_write_enabled is True
    assert receipt.mutation_observed is None
    assert "--approve-for-me" in argv
    assert "--sandbox" not in argv


def test_session_boundary_resume_uses_exact_session_and_interrupt_fails_closed(
    tmp_path: Path,
) -> None:
    factory = FakeProcessFactory([FakeProcess()])
    boundary = _boundary(tmp_path, factory)
    runner = InjectedSessionRunner(boundary)

    receipt = runner.run(
        SessionAction.RESUME,
        role_key="project_manager",
        role_generation=2,
        session_id=SESSION_ID,
        provenance="c" * 64,
    )
    assert receipt.status == "resumed"
    assert factory.calls[0][0][:5] == [
        "codex",
        "exec",
        "resume",
        SESSION_ID,
        "--json",
    ]

    with pytest.raises(CodexBoundaryUnsupportedActionError, match="unsupported"):
        runner.run(
            SessionAction.INTERRUPT,
            role_key="project_manager",
            role_generation=2,
            session_id=SESSION_ID,
            provenance="d" * 64,
        )
    assert len(factory.calls) == 1


def test_operation_id_is_idempotent_across_boundary_reinstantiation(tmp_path: Path) -> None:
    first_factory = FakeProcessFactory([FakeProcess()])
    first_runner = InjectedDirectRunner(_boundary(tmp_path, first_factory))
    first = _launch(first_runner)

    second_factory = FakeProcessFactory([])
    second_runner = InjectedDirectRunner(_boundary(tmp_path, second_factory))
    replay = _launch(second_runner)

    assert replay == first
    assert len(first_factory.calls) == 1
    assert second_factory.calls == []


def test_operation_id_conflicting_reuse_is_rejected_without_spawn(tmp_path: Path) -> None:
    factory = FakeProcessFactory([FakeProcess()])
    boundary = _boundary(tmp_path, factory)
    captured: dict[str, str] = {}

    class Capture:
        execution_metadata = boundary.execution_metadata

        def execute(self, request: dict[str, str]) -> dict[str, str]:
            captured.update(request)
            return dict(boundary.execute(request))

    _launch(InjectedDirectRunner(Capture()))
    conflict = dict(captured)
    conflict["source_event_id"] = "evt-conflict"
    with pytest.raises(CodexBoundaryConflictError, match="different content"):
        boundary.execute(conflict)
    assert len(factory.calls) == 1


def test_mismatched_operation_id_is_stably_rejected_without_spawn(tmp_path: Path) -> None:
    factory = FakeProcessFactory([])
    boundary = _boundary(tmp_path, factory)
    request = {
        "action": "settle",
        "attempt": "0",
        "retry_of": "",
        "retry_provenance": "",
        "role_key": ROLE_KEY,
        "source_event_id": "evt-settle",
        "task_id": TASK_ID,
        "execution_profile_digest": boundary.execution_metadata.profile_digest,
        "operation_id": "op-" + "f" * 64,
    }
    for _ in range(2):
        with pytest.raises(CodexBoundaryRequestError, match="does not match"):
            boundary.execute(request)
    assert factory.calls == []


def test_settle_without_durable_session_fails_closed_without_spawn(tmp_path: Path) -> None:
    factory = FakeProcessFactory([])
    runner = InjectedDirectRunner(_boundary(tmp_path, factory))
    with pytest.raises(CodexBoundaryStateError, match="no durable Codex session"):
        runner.run(
            RunnerAction.SETTLE,
            task_id=TASK_ID,
            role_key=ROLE_KEY,
            generation=GENERATION,
            source_event_id="evt-settle",
        )
    assert factory.calls == []


def test_session_mapping_is_durable_for_settle_after_restart(tmp_path: Path) -> None:
    launch_factory = FakeProcessFactory([FakeProcess()])
    _launch(InjectedDirectRunner(_boundary(tmp_path, launch_factory)))

    settle_factory = FakeProcessFactory([FakeProcess()])
    settle_runner = InjectedDirectRunner(_boundary(tmp_path, settle_factory))
    receipt = settle_runner.run(
        RunnerAction.SETTLE,
        task_id=TASK_ID,
        role_key=ROLE_KEY,
        generation=GENERATION,
        source_event_id="evt-after-restart",
    )
    assert receipt.status == "settled"
    assert settle_factory.calls[0][0][:5] == [
        "codex",
        "exec",
        "resume",
        SESSION_ID,
        "--json",
    ]


def test_transcript_and_process_errors_are_not_persisted_or_disclosed(tmp_path: Path) -> None:
    transcript_secret = "TRANSCRIPT_SECRET_DO_NOT_STORE"
    stderr_secret = "STDERR_SECRET_DO_NOT_STORE"
    factory = FakeProcessFactory(
        [
            FakeProcess(stdout=_events(transcript=transcript_secret)),
            FakeProcess(stdout=b"", stderr=stderr_secret.encode(), returncode=7),
        ]
    )
    boundary = _boundary(tmp_path, factory)
    _launch(InjectedDirectRunner(boundary))

    session_runner = InjectedSessionRunner(boundary)
    with pytest.raises(CodexBoundaryProcessError) as caught:
        session_runner.run(
            SessionAction.RESUME,
            role_key="reviewer",
            role_generation=1,
            session_id="review-session-1",
            provenance="e" * 64,
        )
    persisted = (tmp_path / "codex-boundary.sqlite3").read_bytes()
    assert transcript_secret.encode() not in persisted
    assert stderr_secret.encode() not in persisted
    assert transcript_secret not in str(caught.value)
    assert stderr_secret not in str(caught.value)


def test_output_limit_is_combined_and_fails_sanitized(tmp_path: Path) -> None:
    secret = "X" * 4_096
    factory = FakeProcessFactory([FakeProcess(stdout=secret.encode(), stderr=secret.encode())])
    runner = InjectedDirectRunner(
        _boundary(tmp_path, factory, max_output_bytes=1_024)
    )
    with pytest.raises(CodexBoundaryProcessError, match="output limit") as caught:
        _launch(runner)
    assert secret not in str(caught.value)
    assert secret.encode() not in (tmp_path / "codex-boundary.sqlite3").read_bytes()


def test_timeout_kills_process_and_replay_does_not_respawn(tmp_path: Path) -> None:
    process = FakeProcess(timeout=True)
    factory = FakeProcessFactory([process])
    boundary = _boundary(tmp_path, factory, timeout_seconds=1)
    runner = InjectedDirectRunner(boundary)

    with pytest.raises(CodexBoundaryProcessError, match="timeout"):
        _launch(runner)
    assert process.killed is True
    with pytest.raises(CodexBoundaryProcessError, match="timeout"):
        _launch(InjectedDirectRunner(boundary))
    assert len(factory.calls) == 1


def test_default_profile_is_read_only_and_receipts_report_no_mutation(
    tmp_path: Path,
) -> None:
    factory = FakeProcessFactory([FakeProcess()])
    boundary = _boundary(tmp_path, factory)
    receipt = _launch(InjectedDirectRunner(boundary))

    assert boundary.sandbox_mode == "read-only"
    assert receipt.workspace_write_enabled is False
    assert receipt.mutation_observed is False
    assert receipt.production_mutated is False
    assert receipt.orca_used is False
    assert receipt.execution_profile == "codex_read_only"


def test_pending_operation_is_visible_and_exact_replay_never_respawns(
    tmp_path: Path,
) -> None:
    class InterruptedFactory:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, argv: list[str], **kwargs: object) -> FakeProcess:
            del argv, kwargs
            self.calls += 1
            raise KeyboardInterrupt("simulated host interruption")

    factory = InterruptedFactory()
    boundary = _boundary(tmp_path, factory)  # type: ignore[arg-type]
    with pytest.raises(KeyboardInterrupt):
        _launch(InjectedDirectRunner(boundary))

    status = CodexCliBoundary.inspect(tmp_path / "codex-boundary.sqlite3")
    assert status.pending_operations == 1
    assert status.completed_operations == 0
    with pytest.raises(CodexBoundaryUncertainOperationError, match="uncertain"):
        _launch(InjectedDirectRunner(_boundary(tmp_path, FakeProcessFactory([]))))
    assert factory.calls == 1


def test_session_route_cannot_cross_read_only_and_workspace_write_profiles(
    tmp_path: Path,
) -> None:
    _launch(InjectedDirectRunner(_boundary(tmp_path, FakeProcessFactory([FakeProcess()]))))
    workspace_factory = FakeProcessFactory([])
    workspace = _boundary(
        tmp_path, workspace_factory, sandbox_mode="workspace-write"
    )

    with pytest.raises(CodexBoundaryStateError, match="profile"):
        InjectedDirectRunner(workspace).run(
            RunnerAction.SETTLE,
            task_id=TASK_ID,
            role_key=ROLE_KEY,
            generation=GENERATION,
            source_event_id="workspace-settle",
        )
    assert workspace_factory.calls == []


def test_v1_boundary_database_migrates_profile_columns_without_rebinding(
    tmp_path: Path,
) -> None:
    database = tmp_path / "codex-boundary.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE codex_boundary_operations("
            "operation_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, "
            "request_kind TEXT NOT NULL, state TEXT NOT NULL, response_json TEXT, "
            "error_code TEXT)"
        )
        connection.execute(
            "CREATE TABLE codex_boundary_sessions("
            "task_id TEXT NOT NULL, role_key TEXT NOT NULL, session_id TEXT NOT NULL, "
            "lifecycle TEXT NOT NULL, source_operation_id TEXT NOT NULL, "
            "PRIMARY KEY(task_id, role_key))"
        )
        connection.execute("PRAGMA user_version = 1")

    _boundary(tmp_path, FakeProcessFactory([]))
    with sqlite3.connect(database) as connection:
        operation_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(codex_boundary_operations)")
        }
        session_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(codex_boundary_sessions)")
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "execution_profile_digest" in operation_columns
    assert "execution_profile_digest" in session_columns
