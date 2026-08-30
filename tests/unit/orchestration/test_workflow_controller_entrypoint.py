from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from stock_data.orchestration.workflow_control.production import (
    build_production_service,
    canonical_control_root,
)
from stock_data.orchestration.workflow_control.service import ServiceMode


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "maintenance"
    / "workflow_controller.py"
)


def _repository(root: Path) -> Path:
    (root / "src" / "stock_data").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# test repository\n", encoding="utf-8")
    return root


def _entrypoint_module():
    spec = importlib.util.spec_from_file_location("workflow_controller_entrypoint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_composition_has_one_repository_owned_root_and_no_fake_default(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path / "repo")
    expected = repository.resolve() / "data" / "runtime" / "python_pm"

    assert canonical_control_root(repository) == expected
    assert "control_root" not in inspect.signature(build_production_service).parameters
    canary = build_production_service(
        repository, "pm-canary", ServiceMode.CANARY, command=("codex-stub",)
    )
    run = build_production_service(
        repository, "pm-run", ServiceMode.RUN, command=("codex-stub",)
    )
    assert canary.control_root == run.control_root == expected
    assert canary.execution_metadata.profile_name == "codex_read_only"
    assert canary.execution_metadata.workspace_write_enabled is False
    assert run.execution_metadata.profile_name == "codex_workspace_write"
    assert run.execution_metadata.workspace_write_enabled is True
    assert run.execution_metadata.mutation_observed is None
    source = inspect.getsource(
        __import__(
            "stock_data.orchestration.workflow_control.production",
            fromlist=["production"],
        )
    )
    assert "LocalFake" not in source
    assert "orca" not in source.casefold()


def test_cli_uses_supported_production_default_without_factory_or_control_root(
    tmp_path: Path,
) -> None:
    module = _entrypoint_module()
    parser = module._parser()
    repository = _repository(tmp_path / "repo")

    canary = parser.parse_args(
        [
            "--repository-root",
            str(repository),
            "canary",
            "--owner-id",
            "pm-canary",
            "--events",
            str(tmp_path / "events.json"),
        ]
    )
    assert not hasattr(canary, "factory")
    assert not hasattr(canary, "control_root")


def test_status_and_rollback_never_construct_an_execution_boundary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _entrypoint_module()
    repository = _repository(tmp_path / "repo")

    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("status/rollback constructed production boundary")

    monkeypatch.setattr(module, "build_production_service", forbidden)
    assert module.main(["--repository-root", str(repository), "status"]) == 0
    assert '"writer_state": "idle"' in capsys.readouterr().out
    assert module.main(
        [
            "--repository-root",
            str(repository),
            "rollback",
            "--owner-id",
            "pm-stale",
            "--generation-sequence",
            "1",
            "--generation-digest",
            "a" * 64,
        ]
    ) == 0
    assert '"writer_state": "idle"' in capsys.readouterr().out
