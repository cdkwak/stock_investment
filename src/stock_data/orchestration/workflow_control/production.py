"""Canonical Python-PM production composition.

This is the only supported composition root for the operational CLI.  It
deliberately has no configurable control directory and imports no local fake
boundary.  Every repository therefore has exactly one writer authority root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from stock_data.orchestration.workflow_control.codex_boundary import CodexCliBoundary
from stock_data.orchestration.workflow_control.controller import WorkflowController
from stock_data.orchestration.workflow_control.runner import InjectedDirectRunner
from stock_data.orchestration.workflow_control.service import (
    ControllerServiceError,
    ServiceMode,
    WorkflowControllerService,
)
from stock_data.orchestration.workflow_control.session_runner import InjectedSessionRunner
from stock_data.orchestration.workflow_control.state import WorkflowStateStore


def canonical_repository_root(repository_root: Path) -> Path:
    """Resolve and validate a repository that can own Python-PM authority."""

    root = Path(repository_root).resolve()
    if not (root / "AGENTS.md").is_file() or not (
        root / "src" / "stock_data"
    ).is_dir():
        raise ControllerServiceError(
            "repository root must contain AGENTS.md and src/stock_data"
        )
    return root


def canonical_control_root(repository_root: Path) -> Path:
    """Return the sole repository-owned controller root.

    ``data`` is already the repository's ignored runtime-data boundary.  No
    caller-supplied second root can become a live writer authority.
    """

    return canonical_repository_root(repository_root) / "data" / "runtime" / "python_pm"


def build_production_service(
    repository_root: Path,
    owner_id: str,
    mode: ServiceMode,
    *,
    command: Sequence[str] = ("codex",),
    timeout_seconds: float = 1_800.0,
    max_output_bytes: int = 1_048_576,
) -> WorkflowControllerService:
    """Compose the real direct Codex boundary for canary or production run.

    Canary is always read-only.  Run enables workspace-write capability, while
    receipts still report mutation observation as unknown unless a future
    scoped observer proves it.  Status and rollback intentionally cannot call
    this function and therefore never construct an execution boundary.
    """

    if mode not in {ServiceMode.CANARY, ServiceMode.RUN}:
        raise ControllerServiceError(
            "production composition is available only for canary or run"
        )
    root = canonical_repository_root(repository_root)
    control_root = canonical_control_root(root)
    sandbox_mode = (
        "read-only" if mode is ServiceMode.CANARY else "workspace-write"
    )
    boundary = CodexCliBoundary(
        control_root / "codex_boundary.sqlite3",
        command=command,
        cwd=root,
        sandbox_mode=sandbox_mode,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )
    direct_runner = InjectedDirectRunner(boundary)
    session_runner = InjectedSessionRunner(boundary)
    state_store = WorkflowStateStore(
        control_root / "workflow_state.sqlite3",
        control_root / "workflow_events.jsonl",
    )
    controller = WorkflowController(
        state_store,
        direct_runner,
        control_root / "workflow_controller.sqlite3",
        session_runner=session_runner,
    )
    return WorkflowControllerService(controller, control_root, owner_id=owner_id)


__all__ = [
    "build_production_service",
    "canonical_control_root",
    "canonical_repository_root",
]
