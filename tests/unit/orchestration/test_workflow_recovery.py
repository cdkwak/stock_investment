from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from stock_data.orchestration.workflow_control.registry import (
    RoleClaimConflict,
    RoleIdentity,
    RoleKind,
    RoleRegistry,
    RoleRegistryError,
    RoleRegistrySchemaError,
    RoleState,
    StaleRoleGeneration,
)
from stock_data.orchestration.workflow_control.simulator import FakeAgentSimulator
from stock_data.orchestration.workflow_control.watchdog import RecoveryReason


T0 = datetime(2026, 8, 29, tzinfo=timezone.utc)
TASK = "RQ-20260829T003912-025B"


def identity(
    *,
    session: str = "session-a",
    terminal: str = "term-a",
    dispatch: str = "dispatch-a",
) -> RoleIdentity:
    return RoleIdentity(
        role_key="lead_infra",
        role_kind=RoleKind.DOMAIN_LEAD,
        codex_session_id=session,
        orca_run_id="run-control-plane",
        worktree_id="repo::C:/workspace",
        terminal_handle=terminal,
        runtime_id="runtime-a",
        active_task_id=TASK,
        active_dispatch_id=dispatch,
    )


def test_atomic_double_claim_allows_exactly_one_distinct_owner(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    RoleRegistry(path)

    def claim(candidate: RoleIdentity) -> str:
        try:
            RoleRegistry(path).claim(
                candidate, observed_at=T0, lease_until=T0 + timedelta(minutes=10)
            )
            return candidate.codex_session_id
        except RoleClaimConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                claim,
                (
                    identity(session="session-a", terminal="term-a", dispatch="dispatch-a"),
                    identity(session="session-b", terminal="term-b", dispatch="dispatch-b"),
                ),
            )
        )

    assert results.count("conflict") == 1
    assert RoleRegistry(path).get("lead_infra").identity.codex_session_id in results


def test_project_manager_heartbeat_renews_lease_with_generation_fence(tmp_path: Path) -> None:
    registry = RoleRegistry(tmp_path / "registry.sqlite3")
    pm = RoleIdentity(
        role_key="project_manager",
        role_kind=RoleKind.PROJECT_MANAGER,
        codex_session_id="session-pm",
        orca_run_id="run-pm",
        worktree_id="repo::C:/workspace",
        terminal_handle="term-pm",
        runtime_id="runtime-a",
    )
    claimed = registry.claim(
        pm, observed_at=T0, lease_until=T0 + timedelta(minutes=5)
    )
    renewed = registry.heartbeat(
        "project_manager",
        expected_generation=claimed.generation,
        observed_at=T0 + timedelta(minutes=1),
        lease_until=T0 + timedelta(minutes=6),
    )

    assert renewed.identity == pm
    assert renewed.generation == claimed.generation + 1
    assert renewed.heartbeat_at == T0 + timedelta(minutes=1)
    with pytest.raises(StaleRoleGeneration):
        registry.heartbeat(
            "project_manager",
            expected_generation=claimed.generation,
            observed_at=T0 + timedelta(minutes=2),
            lease_until=T0 + timedelta(minutes=7),
        )


def test_domain_lead_settles_to_idle_and_accepts_a_fresh_assignment(
    tmp_path: Path,
) -> None:
    registry = RoleRegistry(tmp_path / "registry.sqlite3")
    first = registry.claim(
        identity(), observed_at=T0, lease_until=T0 + timedelta(minutes=10)
    )

    idle = registry.settle(
        "lead_infra",
        expected_generation=first.generation,
        observed_at=T0 + timedelta(minutes=1),
        lease_until=T0 + timedelta(minutes=31),
    )
    assigned = registry.assign(
        "lead_infra",
        expected_generation=idle.generation,
        task_id="RQ-20260829T003930-0BD9",
        dispatch_id="dispatch-next",
        observed_at=T0 + timedelta(minutes=2),
        lease_until=T0 + timedelta(minutes=32),
    )

    assert idle.state is RoleState.IDLE
    assert idle.identity.active_task_id is None
    assert idle.identity.active_dispatch_id is None
    assert assigned.state is RoleState.ACTIVE
    assert assigned.identity.codex_session_id == first.identity.codex_session_id
    assert assigned.identity.active_task_id == "RQ-20260829T003930-0BD9"
    assert assigned.identity.active_dispatch_id == "dispatch-next"

    with pytest.raises(StaleRoleGeneration):
        registry.assign(
            "lead_infra",
            expected_generation=idle.generation,
            task_id="RQ-20260829T003946-70A9",
            dispatch_id="dispatch-stale",
            observed_at=T0 + timedelta(minutes=3),
            lease_until=T0 + timedelta(minutes=33),
        )


def test_taskless_project_manager_terminal_loss_proposes_session_recovery(
    tmp_path: Path,
) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    pm = RoleIdentity(
        role_key="project_manager",
        role_kind=RoleKind.PROJECT_MANAGER,
        codex_session_id="session-pm",
        orca_run_id="run-pm",
        worktree_id="repo::C:/workspace",
        terminal_handle="term-pm",
        runtime_id="runtime-a",
    )
    before = simulator.start(pm, observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.lose_terminal("term-pm")

    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]

    assert proposal.reason is RecoveryReason.TERMINAL_MISSING
    assert proposal.action == "RESUME_ROLE_SESSION"
    assert proposal.task_id is None
    assert proposal.retry_of_dispatch_id is None
    assert proposal.retry_attempt is None
    assert simulator.registry.get("project_manager") == before


def test_registry_rejects_free_text_and_non_identifier_schema(tmp_path: Path) -> None:
    with pytest.raises(RoleRegistryError, match="bounded identifier"):
        identity(session="not an identifier with spaces")

    database = tmp_path / "partial.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE role_registry(role_key TEXT PRIMARY KEY, transcript TEXT)")
    before = database.read_bytes()

    with pytest.raises(RoleRegistrySchemaError, match="schema shape"):
        RoleRegistry(database)

    assert database.read_bytes() == before


def test_fake_kill_restart_preserves_task_and_records_retry_provenance(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.kill_fake_agent("lead_infra")

    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]
    assert proposal.reason is RecoveryReason.STALE_DISPATCH
    assert proposal.task_id == TASK
    assert proposal.retry_of_dispatch_id == "dispatch-a"
    recovered = simulator.recover(
        proposal,
        new_terminal_handle="term-restarted",
        new_dispatch_id="dispatch-b",
        observed_at=T0 + timedelta(minutes=2),
        lease_for=timedelta(minutes=10),
    )

    assert recovered.state is RoleState.ACTIVE
    assert recovered.identity.active_task_id == TASK
    assert recovered.identity.active_dispatch_id == "dispatch-b"
    assert recovered.retry_of_dispatch_id == "dispatch-a"
    assert recovered.retry_attempt == 1
    assert recovered.retry_provenance == proposal.provenance
    assert simulator.observe(observed_at=T0 + timedelta(minutes=3)) == ()


def test_stale_heartbeat_proposes_recovery_without_mutating_registry(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(
        tmp_path / "registry.sqlite3",
        runtime_id="runtime-a",
        heartbeat_timeout=timedelta(minutes=5),
    )
    before = simulator.start(
        identity(), observed_at=T0, lease_for=timedelta(minutes=30)
    )

    proposals = simulator.observe(observed_at=T0 + timedelta(minutes=6))

    assert proposals[0].reason is RecoveryReason.STALE_HEARTBEAT
    assert proposals[0].state is RoleState.RECOVERY_REQUIRED
    assert simulator.registry.get("lead_infra") == before


def test_terminal_loss_has_precedence_and_never_implies_completion(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    before = simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.lose_terminal("term-a")

    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]

    assert proposal.reason is RecoveryReason.TERMINAL_MISSING
    assert proposal.action == "SETTLE_THEN_RETRY_SAME_TASK"
    assert simulator.registry.get("lead_infra") == before


def test_stale_generation_rejects_recovery_and_retry(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    original = simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.kill_fake_agent("lead_infra")
    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]
    simulator.registry.heartbeat(
        "lead_infra",
        expected_generation=original.generation,
        observed_at=T0 + timedelta(minutes=1),
        lease_until=T0 + timedelta(minutes=11),
    )

    with pytest.raises(StaleRoleGeneration):
        simulator.recover(
            proposal,
            new_terminal_handle="term-b",
            new_dispatch_id="dispatch-b",
            observed_at=T0 + timedelta(minutes=2),
            lease_for=timedelta(minutes=10),
        )


def test_retry_application_is_idempotent_for_exact_provenance(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.kill_fake_agent("lead_infra")
    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]
    first = simulator.recover(
        proposal,
        new_terminal_handle="term-b",
        new_dispatch_id="dispatch-b",
        observed_at=T0 + timedelta(minutes=2),
        lease_for=timedelta(minutes=10),
    )

    replay = simulator.recover(
        proposal,
        new_dispatch_id="dispatch-b",
        new_terminal_handle="term-b",
        observed_at=T0 + timedelta(minutes=2),
        lease_for=timedelta(minutes=10),
    )

    assert replay == first
    assert replay.generation == first.generation


def test_recovery_replay_resumes_after_crash_between_mark_and_retry(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.kill_fake_agent("lead_infra")
    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]
    marked = simulator.registry.mark_recovery_required(
        proposal.role_key, expected_generation=proposal.role_generation
    )

    recovered = simulator.recover(
        proposal,
        new_terminal_handle="term-b",
        new_dispatch_id="dispatch-b",
        observed_at=T0 + timedelta(minutes=2),
        lease_for=timedelta(minutes=10),
    )

    assert recovered.generation == marked.generation + 1
    assert recovered.identity.active_task_id == TASK
    assert recovered.retry_provenance == proposal.provenance


def test_retry_rejects_any_previously_used_dispatch_identifier(tmp_path: Path) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.kill_fake_agent("lead_infra")
    first_proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]
    simulator.recover(
        first_proposal,
        new_terminal_handle="term-b",
        new_dispatch_id="dispatch-b",
        observed_at=T0 + timedelta(minutes=2),
        lease_for=timedelta(minutes=10),
    )
    simulator.kill_fake_agent("lead_infra")
    second_proposal = simulator.observe(observed_at=T0 + timedelta(minutes=3))[0]

    with pytest.raises(RoleRegistryError, match="never-used dispatch"):
        simulator.recover(
            second_proposal,
            new_terminal_handle="term-a-reused",
            new_dispatch_id="dispatch-a",
            observed_at=T0 + timedelta(minutes=4),
            lease_for=timedelta(minutes=10),
        )


def test_post_commit_replay_rebuilds_fake_terminal_and_dispatch_readback(
    tmp_path: Path,
) -> None:
    simulator = FakeAgentSimulator(tmp_path / "registry.sqlite3", runtime_id="runtime-a")
    simulator.start(identity(), observed_at=T0, lease_for=timedelta(minutes=10))
    simulator.kill_fake_agent("lead_infra")
    simulator.lose_terminal("term-a")
    proposal = simulator.observe(observed_at=T0 + timedelta(minutes=1))[0]
    marked = simulator.registry.mark_recovery_required(
        proposal.role_key, expected_generation=proposal.role_generation
    )
    committed = simulator.registry.register_retry(
        proposal.role_key,
        expected_generation=marked.generation,
        task_id=proposal.task_id or "",
        retry_of_dispatch_id=proposal.retry_of_dispatch_id or "",
        new_dispatch_id="dispatch-b",
        terminal_handle="term-b",
        runtime_id="runtime-a",
        retry_attempt=proposal.retry_attempt or 0,
        retry_provenance=proposal.provenance,
        observed_at=T0 + timedelta(minutes=2),
        lease_until=T0 + timedelta(minutes=12),
    )

    replayed = simulator.recover(
        proposal,
        new_terminal_handle="term-b",
        new_dispatch_id="dispatch-b",
        observed_at=T0 + timedelta(minutes=2),
        lease_for=timedelta(minutes=10),
    )

    assert replayed == committed
    assert simulator.observe(observed_at=T0 + timedelta(minutes=3)) == ()


def test_v1_registry_migrates_without_losing_existing_session_or_dispatch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry-v1.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE role_registry("
            "role_key TEXT PRIMARY KEY, role_kind TEXT NOT NULL CHECK(role_kind IN ('project_manager', 'domain_lead')), "
            "codex_session_id TEXT NOT NULL, orca_run_id TEXT NOT NULL, worktree_id TEXT NOT NULL, "
            "terminal_handle TEXT, runtime_id TEXT NOT NULL, active_task_id TEXT, active_dispatch_id TEXT, "
            "state TEXT NOT NULL CHECK(state IN ('active', 'idle', 'stopped', 'recovery_required')), "
            "generation INTEGER NOT NULL CHECK(generation >= 1), heartbeat_at TEXT NOT NULL, lease_until TEXT NOT NULL, "
            "retry_of_dispatch_id TEXT, retry_attempt INTEGER NOT NULL DEFAULT 0 CHECK(retry_attempt >= 0), "
            "retry_provenance TEXT, CHECK((active_task_id IS NULL) = (active_dispatch_id IS NULL)))"
        )
        connection.execute(
            "CREATE TABLE role_dispatch_history("
            "role_key TEXT NOT NULL, task_id TEXT NOT NULL, dispatch_id TEXT NOT NULL, "
            "attempt INTEGER NOT NULL CHECK(attempt >= 0), provenance TEXT, "
            "PRIMARY KEY(role_key, dispatch_id), FOREIGN KEY(role_key) REFERENCES role_registry(role_key))"
        )
        connection.execute(
            "INSERT INTO role_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "lead_infra", "domain_lead", "session-a", "python-control",
                "repo::C:/workspace", "term-a", "runtime-a", TASK, "dispatch-a",
                "active", 1, T0.isoformat().replace("+00:00", "Z"),
                (T0 + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
                None, 0, None,
            ),
        )
        connection.execute(
            "INSERT INTO role_dispatch_history VALUES (?,?,?,?,?)",
            ("lead_infra", TASK, "dispatch-a", 0, None),
        )
        connection.execute("PRAGMA user_version = 1")

    registry = RoleRegistry(database)
    migrated = registry.get("lead_infra")
    assert migrated.identity.codex_session_id == "session-a"
    assert migrated.identity.parent_role_key is None
    assert migrated.last_message_id is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT dispatch_id FROM role_dispatch_history"
        ).fetchone()[0] == "dispatch-a"
