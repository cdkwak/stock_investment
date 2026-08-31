from __future__ import annotations

import io
import hashlib
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
    CodexProcessEventPins,
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


def _failure_event(code: str, *, secret: str | None = None) -> bytes:
    event: dict[str, object] = {
        "type": "turn.failed",
        "error": {"code": code},
    }
    if secret is not None:
        event["private_payload"] = secret
    return json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"


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


def _record_session_failure(
    tmp_path: Path,
    *,
    stdout: bytes,
    stderr: bytes = b"",
    max_output_bytes: int = 2 * 1024 * 1024,
    reconciliation_binding: str | None = None,
):
    factory = FakeProcessFactory(
        [FakeProcess(stdout=stdout, stderr=stderr, returncode=7)]
    )
    boundary = _boundary(
        tmp_path, factory, max_output_bytes=max_output_bytes
    )
    captured: dict[str, str] = {}

    class Capture:
        execution_metadata = boundary.execution_metadata

        def execute(self, request: dict[str, str]) -> dict[str, str]:
            captured.update(request)
            return dict(boundary.execute(request))

    try:
        InjectedSessionRunner(Capture()).run(
            SessionAction.RESUME,
            role_key="reviewer",
            role_generation=7,
            session_id=SESSION_ID,
            provenance=GENERATION,
            reconciliation_binding=reconciliation_binding,
        )
    except CodexBoundaryProcessError as error:
        caught = error
    else:  # pragma: no cover - the helper is only for terminal fixtures
        raise AssertionError("expected a terminal process failure")
    request_digest = hashlib.sha256(
        json.dumps(captured, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    pins = CodexProcessEventPins(
        operation_id=captured["operation_id"],
        request_digest=request_digest,
        generation_sequence=7,
        generation_digest=GENERATION,
        execution_profile_digest=boundary.execution_metadata.profile_digest,
    )
    receipt = CodexCliBoundary.inspect_process_event(
        tmp_path / "codex-boundary.sqlite3", pins=pins
    )
    return boundary, factory, captured, pins, receipt, caught


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


@pytest.mark.parametrize("event", ["runtime_bootstrap_v1", "runtime_bootstrap_v2"])
def test_allowed_bootstrap_attempts_use_initialization_only_prompt(
    tmp_path: Path, event: str,
) -> None:
    factory = FakeProcessFactory([FakeProcess()])
    boundary = _boundary(tmp_path, factory, sandbox_mode="workspace-write")

    _launch(InjectedDirectRunner(boundary), event=event)

    prompt = factory.calls[0][0][-1]
    assert "Initialize this Python-CLI-owned persistent runtime session" in prompt
    assert "Do not edit files, mutate Queue state, create another role" in prompt
    assert "Complete only that assigned task" not in prompt


@pytest.mark.parametrize(
    "event",
    ["runtime_bootstrap_v0", "runtime_bootstrap_v10", "runtime_bootstrap_vx"],
)
def test_malformed_bootstrap_event_is_rejected_before_process_spawn(
    tmp_path: Path, event: str,
) -> None:
    factory = FakeProcessFactory([])
    with pytest.raises(CodexBoundaryRequestError, match="bounded attempt"):
        _launch(
            InjectedDirectRunner(
                _boundary(tmp_path, factory, sandbox_mode="workspace-write")
            ),
            event=event,
        )
    assert factory.calls == []


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
    status = CodexCliBoundary.inspect(tmp_path / "codex-boundary.sqlite3")
    assert status.pending_operations == 0
    assert status.failed_operations == 1
    assert status.pending_operation_pins == ()


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


def test_cli_launch_ownership_is_exact_and_workspace_write_resumable(
    tmp_path: Path,
) -> None:
    factory = FakeProcessFactory([FakeProcess()])
    boundary = _boundary(
        tmp_path, factory, sandbox_mode="workspace-write"
    )

    launch = _launch(
        InjectedDirectRunner(boundary), event="runtime_bootstrap_v1"
    )
    first = boundary.assert_cli_owned_session(
        role_key=ROLE_KEY, session_id=launch.agent_id
    )
    replay = boundary.assert_cli_owned_session(
        role_key=ROLE_KEY, session_id=launch.agent_id
    )
    assert replay == first and len(first) == 64

    factory.processes.append(FakeProcess())
    receipt = InjectedSessionRunner(boundary).run(
        SessionAction.RESUME,
        role_key=ROLE_KEY,
        role_generation=1,
        session_id=SESSION_ID,
        provenance="c" * 64,
    )
    assert receipt.status == "resumed"
    assert factory.calls[1][0][:5] == [
        "codex", "exec", "resume", SESSION_ID, "--json",
    ]


def test_app_coordination_session_cannot_be_adopted_as_cli_owned(
    tmp_path: Path,
) -> None:
    boundary = _boundary(
        tmp_path, FakeProcessFactory([]), sandbox_mode="workspace-write"
    )

    with pytest.raises(CodexBoundaryStateError, match="not owned"):
        boundary.assert_cli_owned_session(
            role_key=ROLE_KEY, session_id=SESSION_ID
        )


def test_exact_coordination_receipt_is_replaced_only_by_fresh_cli_launch(
    tmp_path: Path,
) -> None:
    factory = FakeProcessFactory([
        FakeProcess(stdout=_events("019cafe0-1234-7000-8000-cliowned0001")),
    ])
    boundary = _boundary(tmp_path, factory, sandbox_mode="workspace-write")
    operation_id = "session-bind-" + ("1" * 64)
    profile = boundary.execution_metadata.profile_digest
    with sqlite3.connect(tmp_path / "codex-boundary.sqlite3") as connection:
        connection.execute(
            "INSERT INTO codex_boundary_operations(operation_id, request_digest, "
            "request_kind, state, response_json, error_code, execution_profile_digest) "
            "VALUES (?, ?, 'session', 'completed', ?, NULL, ?)",
            (
                operation_id,
                "2" * 64,
                json.dumps(
                    {"binding_digest": "3" * 64, "status": "bound"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                profile,
            ),
        )
        connection.execute(
            "INSERT INTO codex_boundary_sessions(task_id, role_key, session_id, "
            "lifecycle, source_operation_id, execution_profile_digest) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (TASK_ID, ROLE_KEY, SESSION_ID, operation_id, profile),
        )

    migration_proof = boundary.assert_coordination_session(
        task_id=TASK_ID, role_key=ROLE_KEY, session_id=SESSION_ID
    )
    assert len(migration_proof) == 64
    launch = _launch(
        InjectedDirectRunner(boundary), event="runtime_bootstrap_v1"
    )
    assert boundary.assert_cli_owned_session(
        role_key=ROLE_KEY, session_id=launch.agent_id
    )
    with pytest.raises(CodexBoundaryStateError, match="migration receipt"):
        boundary.assert_coordination_session(
            task_id=TASK_ID, role_key=ROLE_KEY, session_id=SESSION_ID
        )
    assert factory.calls[0][0][1:3] == ["exec", "--json"]


def test_process_event_classifier_persists_only_explicit_model_capacity(
    tmp_path: Path,
    caplog,
) -> None:
    secret = "CAPACITY_EVENT_PRIVATE_PAYLOAD"
    _boundary_, _factory, _request, pins, receipt, error = _record_session_failure(
        tmp_path,
        stdout=_failure_event("model_capacity", secret=secret),
    )

    assert receipt.reason == "model_capacity"
    assert receipt.schema == "codex-process-event/v1"
    assert receipt.classifier_version == 1
    assert receipt.operation_id == pins.operation_id
    assert receipt.generation_sequence == 7
    assert receipt.generation_digest == GENERATION
    assert receipt.parser_error is False
    assert receipt.truncated is False
    assert receipt.full_stream_byte_length == len(
        _failure_event("model_capacity", secret=secret)
    )
    assert len(receipt.full_stream_sha256) == len(receipt.receipt_digest) == 64
    assert secret not in str(error)
    assert secret not in caplog.text
    assert secret.encode() not in (tmp_path / "codex-boundary.sqlite3").read_bytes()
    assert secret not in json.dumps(receipt.to_dict(), sort_keys=True)


@pytest.mark.parametrize(
    ("stdout", "stderr", "parser_error"),
    [
        (_failure_event("network_failure"), b"", False),
        (b"not-json\n", b"", True),
        (b"", b"", True),
        (
            _failure_event("model_capacity") + _failure_event("network_failure"),
            b"",
            False,
        ),
        (_failure_event("model_capacity"), b"timeout after 600 seconds\n", True),
    ],
)
def test_process_event_classifier_maps_unsupported_malformed_mixed_and_missing_unknown(
    tmp_path: Path,
    stdout: bytes,
    stderr: bytes,
    parser_error: bool,
) -> None:
    _boundary_, _factory, _request, _pins, receipt, _error = _record_session_failure(
        tmp_path, stdout=stdout, stderr=stderr
    )

    assert receipt.reason == "unknown_failure"
    assert receipt.parser_error is parser_error
    assert "timeout" not in receipt.reason


def test_oversized_process_event_hashes_full_stream_and_persists_no_prefix(
    tmp_path: Path,
) -> None:
    secret = b"OVERSIZED_PRIVATE_PAYLOAD" * 8_000
    stdout = _failure_event("model_capacity") + secret
    stderr = b"stderr-private" * 7_000
    _boundary_, _factory, _request, _pins, receipt, error = _record_session_failure(
        tmp_path,
        stdout=stdout,
        stderr=stderr,
        max_output_bytes=1_024,
    )

    assert receipt.reason == "unknown_failure"
    assert receipt.truncated is True
    assert receipt.full_stream_byte_length == len(stdout) + len(stderr)
    assert len(receipt.full_stream_sha256) == 64
    persisted = (tmp_path / "codex-boundary.sqlite3").read_bytes()
    assert secret[:512] not in persisted
    assert b"stderr-private" not in persisted
    assert "OVERSIZED_PRIVATE_PAYLOAD" not in str(error)


def test_process_event_receipt_replay_is_exact_and_changed_pins_or_bytes_do_not_respawn(
    tmp_path: Path,
) -> None:
    boundary, factory, request, pins, first, _error = _record_session_failure(
        tmp_path, stdout=_failure_event("model_capacity"), reconciliation_binding="a" * 64
    )
    replay = CodexCliBoundary.inspect_process_event(
        tmp_path / "codex-boundary.sqlite3",
        pins=pins,
        expected_receipt_digest=first.receipt_digest,
    )
    assert replay == first

    factory.processes.append(
        FakeProcess(stdout=_failure_event("network_failure"), returncode=7)
    )
    with pytest.raises(CodexBoundaryProcessError):
        boundary.execute(request)
    assert len(factory.calls) == 1
    assert CodexCliBoundary.inspect_process_event(
        tmp_path / "codex-boundary.sqlite3", pins=pins
    ) == first

    for changed in (
        CodexProcessEventPins(
            pins.operation_id,
            "b" * 64,
            pins.generation_sequence,
            pins.generation_digest,
            pins.execution_profile_digest,
        ),
        CodexProcessEventPins(
            pins.operation_id,
            pins.request_digest,
            pins.generation_sequence + 1,
            pins.generation_digest,
            pins.execution_profile_digest,
        ),
        CodexProcessEventPins(
            pins.operation_id,
            pins.request_digest,
            pins.generation_sequence,
            "c" * 64,
            pins.execution_profile_digest,
        ),
        CodexProcessEventPins(
            pins.operation_id,
            pins.request_digest,
            pins.generation_sequence,
            pins.generation_digest,
            "d" * 64,
        ),
    ):
        with pytest.raises(CodexBoundaryConflictError):
            CodexCliBoundary.inspect_process_event(
                tmp_path / "codex-boundary.sqlite3", pins=changed
            )
    with pytest.raises(CodexBoundaryConflictError, match="replay digest"):
        CodexCliBoundary.inspect_process_event(
            tmp_path / "codex-boundary.sqlite3",
            pins=pins,
            expected_receipt_digest="e" * 64,
        )
    mapping = CodexCliBoundary.lookup_terminal_operation_mapping(
        tmp_path / "codex-boundary.sqlite3", reconciliation_binding="a" * 64
    )
    assert mapping.operation_id == pins.operation_id
    assert mapping.request_digest == pins.request_digest
    assert mapping.process_event_receipt_digest == first.receipt_digest
    with pytest.raises(CodexBoundaryStateError, match="absent or ambiguous"):
        CodexCliBoundary.lookup_terminal_operation_mapping(
            tmp_path / "codex-boundary.sqlite3", reconciliation_binding="b" * 64
        )


def test_historical_failed_operation_without_receipt_remains_unclassified(
    tmp_path: Path,
) -> None:
    boundary = _boundary(tmp_path, FakeProcessFactory([]))
    operation_id = "session-op-" + ("1" * 64)
    with sqlite3.connect(tmp_path / "codex-boundary.sqlite3") as connection:
        connection.execute(
            "INSERT INTO codex_boundary_operations(operation_id, request_digest, "
            "request_kind, state, response_json, error_code, execution_profile_digest, "
            "process_event_json) VALUES (?, ?, 'session', 'failed', NULL, "
            "'process_failed', ?, NULL)",
            (operation_id, "2" * 64, boundary.execution_metadata.profile_digest),
        )
    pins = CodexProcessEventPins(
        operation_id,
        "2" * 64,
        1,
        "3" * 64,
        boundary.execution_metadata.profile_digest,
    )
    with pytest.raises(CodexBoundaryStateError, match="unavailable"):
        CodexCliBoundary.inspect_process_event(
            tmp_path / "codex-boundary.sqlite3", pins=pins
        )
    terminal = CodexCliBoundary.inspect_terminal_operation(
        tmp_path / "codex-boundary.sqlite3", operation_id=operation_id
    )
    assert terminal.error_code == "process_failed"


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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert "execution_profile_digest" in operation_columns
        assert "process_event_json" in operation_columns
        assert "reconciliation_binding" in operation_columns
    assert "execution_profile_digest" in session_columns
