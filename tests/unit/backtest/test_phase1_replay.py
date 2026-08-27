from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading

import numpy as np
import pandas as pd
import pytest

import market_backtest
import market_backtest.phase1_replay as replay
import market_features.frozen as frozen_source
from market_features.types import FrozenInputManifest
from scripts import run_phase1_signal_replay as replay_cli
import stock_data.storage.contract_parquet as contract_storage


def _bundle(tag: str) -> replay._ReplayBundle:
    return replay._bind_bundle(
        {
            "signals.csv": f"date,signal\n{tag},0\n".encode(),
            "result.json": (json.dumps({"tag": tag}, sort_keys=True) + "\n").encode(),
            "experiments.json": (
                json.dumps({"experiments": [tag]}, sort_keys=True) + "\n"
            ).encode(),
            "portfolio_ledger.json": (
                json.dumps({"ledger": [tag]}, sort_keys=True) + "\n"
            ).encode(),
        },
        frozen_input_digest=replay.EXPECTED_FROZEN_DIGEST,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(root.iterdir(), key=lambda item: item.name)
    }


def test_phase1_code_dependency_manifest_is_explicit_and_fail_closed(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).resolve().parents[3]
    project_root = tmp_path / "explicit-phase1-dependencies"
    paths = replay.PHASE1_DEPENDENCY_PATHS
    names = tuple(path.as_posix() for path in paths)

    assert replay.PHASE1_DEPENDENCY_MANIFEST_SCHEMA == (
        "phase1-code-dependencies/v1"
    )
    assert names == tuple(sorted(names))
    assert len(names) == len(set(names)) == 12
    assert all(not path.is_absolute() and ".." not in path.parts for path in paths)
    assert not any(
        name.endswith((
            "/__init__.py", "/overnight_ml.py", "/execution.py",
            "/indicator_study.py", "/indicator_strategy.py",
        ))
        for name in names
    )

    for relative in paths:
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)

    accepted = replay.phase1_code_digest(project_root)
    unrelated = project_root / "src/market_backtest/unrelated_future_module.py"
    unrelated.write_text("UNRELATED = True\n", encoding="utf-8")
    assert replay.phase1_code_digest(project_root) == accepted

    representative = project_root / "src/market_backtest/signals.py"
    representative.write_bytes(representative.read_bytes() + b"\n# changed\n")
    assert replay.phase1_code_digest(project_root) != accepted

    representative.unlink()
    with pytest.raises(replay.Phase1ReplayError, match="cannot be verified"):
        replay.phase1_code_digest(project_root)


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
    output_root: Path,
    bundle: replay._ReplayBundle,
    *,
    hook=None,
) -> replay.Phase1ReplayReceipt:
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda _root, _output: bundle,
    )
    return replay.run_phase1_replay(
        replay.Phase1ReplayRequest(project_root, output_root),
        _promotion_hook=hook,
    )


def _assert_no_transaction_paths(output: Path) -> None:
    assert all(not path.exists() for path in replay._transaction_paths(output))


def _make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "mklink",
                "/J",
                str(link),
                str(target),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("test volume does not permit a directory junction")
    else:
        link.symlink_to(target, target_is_directory=True)


def _write_valid_legacy(output: Path) -> None:
    output.mkdir()
    signals = (
        "observation_date,usable_from,risk_score,risk_off_signal,signal_version\n"
        "2020-01-02,2020-01-03T09:00:00+09:00,0,False,1\n"
    ).encode()
    result_payload = {
        "status": "DESCRIPTIVE_SIGNAL_REPLAY_NOT_PORTFOLIO_BACKTEST",
        "frozen_manifest": {
            "root_manifest_sha256": replay.EXPECTED_FROZEN_DIGEST,
        },
        "untouched_holdout_policy": asdict(replay.KOSPI200_FROZEN_HOLDOUT_V1),
    }
    result = (json.dumps(result_payload, sort_keys=True) + "\n").encode()
    experiments = (json.dumps({
        "version": 1,
        "experiments": [{
            "frozen_input_digest": replay.EXPECTED_FROZEN_DIGEST,
            "holdout_results_reviewed": False,
            "signals_artifact_digest": hashlib.sha256(signals).hexdigest(),
            "result_artifact_digest": hashlib.sha256(result).hexdigest(),
        }],
    }, sort_keys=True) + "\n").encode()
    (output / "signals.csv").write_bytes(signals)
    (output / "result.json").write_bytes(result)
    (output / "experiments.json").write_bytes(experiments)


def test_typed_receipt_binds_exact_bundle_and_double_run_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    bundle = _bundle("same")

    first = _fake_run(monkeypatch, project, output, bundle)
    before = _snapshot(output)
    second = _fake_run(monkeypatch, project, output, bundle)

    assert type(first) is replay.Phase1ReplayReceipt
    assert first == second
    assert first.schema == replay.BUNDLE_SCHEMA
    assert first.status == "READY"
    assert first.output_root == output.resolve()
    assert first.frozen_input_digest == replay.EXPECTED_FROZEN_DIGEST
    assert [item.name for item in first.artifacts] == sorted(replay._OWNED_FILES)
    assert first.bundle_digest == replay._verify_bundle(output.resolve())[1]
    assert _snapshot(output) == before
    manifest = json.loads(before["bundle.json"])
    assert manifest["artifact_set_sha256"] == replay._records_digest(
        tuple(item for item in first.artifacts if item.name != "bundle.json")
    )
    with pytest.raises(ValueError, match="receipt is invalid"):
        replace(first, bundle_digest="0" * 64)
    with pytest.raises(ValueError, match="receipt is invalid"):
        replace(first, output_root=Path("relative-output"))
    _assert_no_transaction_paths(output.resolve())


def test_known_legacy_three_file_output_migrates_as_one_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "legacy"
    _write_valid_legacy(output)

    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert set(_snapshot(output)) == replay._OWNED_FILES
    replay._verify_bundle(output)
    _assert_no_transaction_paths(output)


@pytest.mark.parametrize("alias_kind", ["project", "output"])
def test_redirected_request_root_is_refused_before_computation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
) -> None:
    real_project = tmp_path / "real-project"
    real_project.mkdir()
    real_output = tmp_path / "real-output"
    _write_valid_legacy(real_output)
    project = real_project
    output = real_output
    alias = tmp_path / f"{alias_kind}-alias"
    if alias_kind == "project":
        _make_directory_link(alias, real_project)
        project = alias
    else:
        _make_directory_link(alias, real_output)
        output = alias
    before = _snapshot(real_output)
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="redirected"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert _snapshot(real_output) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows 8.3 alias boundary")
def test_short_path_alias_is_canonicalized_before_protected_scope_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    project = tmp_path / "phase1-long-project-name"
    protected = project / "data"
    protected.mkdir(parents=True)
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(  # type: ignore[attr-defined]
        str(project), buffer, len(buffer)
    )
    if length == 0 or os.path.normcase(buffer.value) == os.path.normcase(str(project)):
        pytest.skip("test volume does not expose a distinct 8.3 path alias")
    output = Path(buffer.value) / "data" / "phase1-output"
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="protected data root"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert list(protected.iterdir()) == []


@pytest.mark.parametrize(
    "relative_output",
    [
        Path("data/derived/injected-phase1"),
        Path("artifacts/backtest/frozen_inputs/injected-phase1"),
    ],
)
def test_protected_data_output_is_refused_before_creation_or_computation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_output: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = project / relative_output
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="protected data root"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert not output.exists()


@pytest.mark.parametrize(
    "relative_output",
    [
        Path("src/generated-phase1"),
        Path("artifacts/backtest"),
        Path("artifacts/test_tmp"),
    ],
)
def test_project_local_output_is_confined_below_backtest_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_output: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = project / relative_output
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="below artifacts/backtest"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert not output.exists()


def test_project_local_output_may_use_an_ignored_test_tmp_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = project / "artifacts/test_tmp/phase1-replay"

    receipt = _fake_run(monkeypatch, project, output, _bundle("test-output"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("test-output").bodies)


def test_output_root_cannot_contain_the_project_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="contain the project"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, tmp_path))

    assert calls == []
    assert list(tmp_path.iterdir()) == [project]


@pytest.mark.parametrize(
    "reserved_name",
    [
        ".output.phase1-replay.lock",
        ".output.phase1-replay.stage",
        ".output.phase1-replay.backup",
        ".output.phase1-replay.journal.json",
        ".output.phase1-replay.journal.tmp",
        ".OUTPUT.PHASE1-REPLAY.STAGE",
    ],
)
def test_transaction_namespace_cannot_be_used_as_an_output_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reserved_name: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / reserved_name
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="reserved transaction"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert not output.exists()


def test_output_cannot_be_nested_below_a_reserved_transaction_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    reserved_parent = tmp_path / ".other.phase1-replay.stage"
    output = reserved_parent / "nested-output"
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="reserved transaction"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert not reserved_parent.exists()


@pytest.mark.parametrize("prior_kind", ["bound", "legacy"])
def test_output_cannot_be_nested_inside_an_existing_replay_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_kind: str,
) -> None:
    project = tmp_path / "project"
    parent_output = tmp_path / "parent-output"
    if prior_kind == "bound":
        _fake_run(monkeypatch, project, parent_output, _bundle("parent"))
    else:
        project.mkdir()
        _write_valid_legacy(parent_output)
    before = _snapshot(parent_output)
    child_output = parent_output / "child-output"
    child_lock = replay._lock_path(child_output.absolute())
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="replay namespace"):
        replay.run_phase1_replay(
            replay.Phase1ReplayRequest(project, child_output)
        )

    assert calls == []
    assert _snapshot(parent_output) == before
    assert not child_lock.exists()
    assert not child_output.exists()


def test_hardlinked_empty_lock_is_refused_without_mutating_its_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "output"
    victim = tmp_path / "unowned-empty-file"
    victim.write_bytes(b"")
    lock = replay._lock_path(output.absolute())
    try:
        os.link(victim, lock)
    except OSError:
        pytest.skip("test volume does not permit hard links")
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="lock topology changed"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert victim.read_bytes() == b""
    assert lock.read_bytes() == b""
    assert calls == []


def test_single_link_empty_orphan_lock_is_reused_without_mutation_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    lock = replay._lock_path(output.absolute())
    lock.touch()

    receipt = _fake_run(monkeypatch, project, output, _bundle("recovered"))

    assert receipt.status == "READY"
    assert lock.read_bytes() == b""
    assert _snapshot(output) == dict(_bundle("recovered").bodies)


def test_output_parent_identity_is_rechecked_after_computation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    parent = tmp_path / "publish-parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-publish-parent"
    output = parent / "output"

    def replace_parent(_root: Path, _output: Path) -> replay._ReplayBundle:
        try:
            parent.rename(moved_parent)
            parent.mkdir()
        except OSError:
            pytest.skip("platform prevents replacing a directory containing an open lock")
        return _bundle("never-published")

    monkeypatch.setattr(replay, "_build_replay_bundle", replace_parent)

    with pytest.raises(replay.Phase1ReplayError, match="parent topology changed"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert list(parent.iterdir()) == []
    assert not output.exists()


def test_concurrent_run_is_refused_without_recovering_active_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    reached_stage = threading.Event()
    release_first = threading.Event()
    first_errors: list[BaseException] = []

    def pause_after_stage(phase: str) -> None:
        if phase == "after_stage_readback":
            reached_stage.set()
            if not release_first.wait(timeout=10):
                raise RuntimeError("test did not release the first replay")

    def first_run() -> None:
        try:
            _fake_run(
                monkeypatch,
                project,
                output,
                _bundle("first"),
                hook=pause_after_stage,
            )
        except BaseException as error:
            first_errors.append(error)

    worker = threading.Thread(target=first_run)
    worker.start()
    assert reached_stage.wait(timeout=10)
    stage, _backup, marker, _temporary = replay._transaction_paths(
        output.absolute()
    )
    assert stage.is_dir() and marker.is_file()

    try:
        with pytest.raises(replay.Phase1ReplayError, match="already active"):
            _fake_run(monkeypatch, project, output, _bundle("second"))
        assert stage.is_dir() and marker.is_file()
    finally:
        release_first.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert first_errors == []
    assert _snapshot(output) == dict(_bundle("first").bodies)
    _assert_no_transaction_paths(output)


@pytest.mark.parametrize("unexpected", ["notes.txt", "nested"])
def test_unowned_file_or_directory_is_refused_before_computation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unexpected: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    for name in replay._LEGACY_FILES:
        (output / name).write_bytes(name.encode())
    target = output / unexpected
    target.mkdir() if unexpected == "nested" else target.write_text("mine")
    before = {
        path.name: (path.read_bytes() if path.is_file() else b"DIRECTORY")
        for path in output.iterdir()
    }
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="unowned|non-regular"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert {
        path.name: (path.read_bytes() if path.is_file() else b"DIRECTORY")
        for path in output.iterdir()
    } == before
    assert not replay._lock_path(output.absolute()).exists()


@pytest.mark.parametrize(
    "failure_phase",
    [
        "before_stage_write",
        "after_stage_write",
        "after_stage_readback",
        "before_live_backup",
        "after_live_backup",
        "before_live_publish",
        "after_live_publish",
        "before_live_readback",
        "after_live_readback",
        "after_verified",
    ],
)
def test_each_promotion_failure_leaves_one_complete_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    old_bundle = _bundle("old")
    new_bundle = _bundle("new")
    _fake_run(monkeypatch, project, output, old_bundle)
    old = _snapshot(output)

    def fail(phase: str) -> None:
        if phase == failure_phase:
            raise RuntimeError(f"injected {phase}")

    with pytest.raises(RuntimeError, match="injected"):
        _fake_run(monkeypatch, project, output, new_bundle, hook=fail)

    current = _snapshot(output)
    expected_new = dict(new_bundle.bodies)
    assert current in (old, expected_new)
    replay._verify_bundle(output)
    _assert_no_transaction_paths(output)


@pytest.mark.parametrize(
    "crash_phase", ["after_live_backup", "after_live_publish", "after_verified"],
)
def test_restart_recovers_a_hard_crash_after_each_directory_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_phase: str,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise KeyboardInterrupt(f"hard crash {phase}")

    with pytest.raises(KeyboardInterrupt, match="hard crash"):
        _fake_run(monkeypatch, project, output, _bundle("new"), hook=crash)
    assert any(path.exists() for path in replay._transaction_paths(output))

    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("new").bodies)
    replay._verify_bundle(output)
    _assert_no_transaction_paths(output)


@pytest.mark.parametrize(
    "tamper_phase",
    [
        "after_stage_readback",
        "after_live_publish",
        "after_live_readback",
        "after_verified",
    ],
)
def test_readback_tamper_is_rejected_and_prior_bundle_is_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_phase: str,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))
    old = _snapshot(output)
    stage = replay._transaction_paths(output.resolve())[0]

    def tamper(phase: str) -> None:
        if phase == tamper_phase:
            target = stage if phase == "after_stage_readback" else output.resolve()
            (target / "signals.csv").write_bytes(b"tampered\n")

    with pytest.raises(
        replay.Phase1ReplayError,
        match="changed|digest|readback|rolled back",
    ):
        _fake_run(monkeypatch, project, output, _bundle("new"), hook=tamper)

    assert _snapshot(output) == old
    replay._verify_bundle(output)
    _assert_no_transaction_paths(output)


def test_after_verified_tamper_without_prior_never_returns_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"

    def tamper(phase: str) -> None:
        if phase == "after_verified":
            (output / "signals.csv").write_bytes(b"tampered\n")

    with pytest.raises(replay.Phase1ReplayError, match="rolled back"):
        _fake_run(
            monkeypatch,
            project,
            output,
            _bundle("new"),
            hook=tamper,
        )

    assert not output.exists()
    _assert_no_transaction_paths(output)


def test_restart_rolls_back_a_stale_verified_marker_when_live_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))
    old = _snapshot(output)

    def crash(phase: str) -> None:
        if phase == "after_verified":
            raise KeyboardInterrupt("hard crash after verified marker")

    with pytest.raises(KeyboardInterrupt, match="verified marker"):
        _fake_run(monkeypatch, project, output, _bundle("new"), hook=crash)
    (output / "signals.csv").write_bytes(b"changed after verified\n")

    assert replay._recover(output) == "ROLLED_BACK"
    assert _snapshot(output) == old
    replay._verify_bundle(output)
    _assert_no_transaction_paths(output)


@pytest.mark.parametrize("temporary_body", [b"{", b"{}\n"])
def test_restart_discards_partial_marker_temporary_and_keeps_valid_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temporary_body: bytes,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))

    def crash(phase: str) -> None:
        if phase == "after_live_backup":
            raise KeyboardInterrupt("hard crash before next marker")

    with pytest.raises(KeyboardInterrupt, match="next marker"):
        _fake_run(monkeypatch, project, output, _bundle("new"), hook=crash)
    _stage, _backup, marker, temporary = replay._transaction_paths(output)
    assert marker.is_file()
    temporary.write_bytes(temporary_body)

    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("new").bodies)
    _assert_no_transaction_paths(output)


def test_restart_promotes_only_a_complete_successor_marker_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))

    def crash(phase: str) -> None:
        if phase == "after_live_backup":
            raise KeyboardInterrupt("hard crash during marker advance")

    with pytest.raises(KeyboardInterrupt, match="marker advance"):
        _fake_run(monkeypatch, project, output, _bundle("new"), hook=crash)
    _stage, _backup, marker, temporary = replay._transaction_paths(output)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["phase"] == "BACKUP_PENDING"
    payload["phase"] = "PUBLISH_PENDING"
    temporary.write_bytes(replay._marker_bytes(payload))

    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("new").bodies)
    _assert_no_transaction_paths(output)


def test_restart_discards_a_partial_initial_marker_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    original_write_marker = replay._write_marker
    crashed = False

    def crash_first_marker(
        marker: Path, temporary: Path, payload: dict[str, object],
    ) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            temporary.write_bytes(b"{")
            raise KeyboardInterrupt("hard crash during initial marker write")
        original_write_marker(marker, temporary, payload)

    monkeypatch.setattr(replay, "_write_marker", crash_first_marker)
    with pytest.raises(KeyboardInterrupt, match="initial marker"):
        _fake_run(monkeypatch, project, output, _bundle("new"))
    stage, backup, marker, temporary = replay._transaction_paths(output)
    assert temporary.read_bytes() == b"{"
    assert not any(path.exists() for path in (stage, backup, marker))

    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("new").bodies)
    _assert_no_transaction_paths(output)


def test_invalid_committed_marker_and_temporary_are_preserved_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "output"
    _stage, _backup, marker, temporary = replay._transaction_paths(output)
    marker.write_bytes(b"invalid committed marker")
    temporary.write_bytes(b"invalid pending marker")
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError, match="marker temporary"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert marker.read_bytes() == b"invalid committed marker"
    assert temporary.read_bytes() == b"invalid pending marker"
    assert calls == []


def test_partial_stage_write_failure_rolls_back_without_mixing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))
    old = _snapshot(output)

    def partial(
        stage: Path,
        _bundle_value: replay._ReplayBundle,
        *,
        promotion_hook=None,
        scope_assertion=None,
    ) -> str:
        del promotion_hook, scope_assertion
        stage.mkdir()
        (stage / "signals.csv").write_bytes(b"")
        raise OSError("injected stage write failure")

    monkeypatch.setattr(replay, "_write_stage", partial)
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda _root, _output: _bundle("new"),
    )
    with pytest.raises(OSError, match="stage write"):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert _snapshot(output) == old
    _assert_no_transaction_paths(output)


def test_restart_finishes_committed_backup_cleanup_after_partial_rmtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))
    original_rmtree = replay.shutil.rmtree
    crashed = False

    def partial_cleanup(path: Path) -> None:
        nonlocal crashed
        target = Path(path)
        if target.name.endswith(".backup") and not crashed:
            crashed = True
            first = next(target.iterdir())
            first.unlink()
            raise KeyboardInterrupt("hard crash during committed cleanup")
        original_rmtree(target)

    monkeypatch.setattr(replay.shutil, "rmtree", partial_cleanup)
    with pytest.raises(KeyboardInterrupt, match="committed cleanup"):
        _fake_run(monkeypatch, project, output, _bundle("new"))
    assert replay._verify_bundle(output)[1] == replay._directory_digest(
        tuple(replay._artifact_receipt(name, body) for name, body in _bundle("new").bodies)
    )

    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("new").bodies)
    _assert_no_transaction_paths(output)


def test_backup_retiring_without_prior_live_refuses_an_injected_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    original_phase = replay._phase

    def crash_after_marker(
        payload: dict[str, object],
        value: str,
        marker: Path,
        temporary: Path,
    ) -> None:
        original_phase(payload, value, marker, temporary)
        if value == "BACKUP_RETIRING":
            raise KeyboardInterrupt("hard crash after backup-retiring marker")

    monkeypatch.setattr(replay, "_phase", crash_after_marker)
    with pytest.raises(KeyboardInterrupt, match="backup-retiring marker"):
        _fake_run(monkeypatch, project, output, _bundle("new"))
    _stage, backup, marker, _temporary = replay._transaction_paths(output)
    assert marker.is_file()
    backup.mkdir()

    with pytest.raises(replay.Phase1ReplayError, match="unexpected.*backup"):
        replay._recover(output)

    assert backup.is_dir()
    assert marker.is_file()
    assert _snapshot(output) == dict(_bundle("new").bodies)


def test_verified_publication_refuses_an_injected_stage_without_deleting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    stage = replay._transaction_paths(output)[0]

    def inject_stage(phase: str) -> None:
        if phase == "after_verified":
            stage.mkdir()
            (stage / "signals.csv").write_bytes(b"unowned user bytes")

    with pytest.raises(
        replay.Phase1ReplayError,
        match="recovery could not complete|unexpected.*stage",
    ):
        _fake_run(
            monkeypatch,
            project,
            output,
            _bundle("new"),
            hook=inject_stage,
        )

    assert (stage / "signals.csv").read_bytes() == b"unowned user bytes"
    assert _snapshot(output) == dict(_bundle("new").bodies)


def test_rollback_restores_prior_before_partial_candidate_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    output = tmp_path / "output"
    _fake_run(monkeypatch, project, output, _bundle("old"))
    old = _snapshot(output)
    original_rmtree = replay.shutil.rmtree
    failed = False

    def partial_cleanup(path: Path) -> None:
        nonlocal failed
        target = Path(path)
        if target.name.endswith(".stage") and not failed:
            failed = True
            next(target.iterdir()).unlink()
            raise OSError("injected rollback cleanup failure")
        original_rmtree(target)

    def fail_after_publish(phase: str) -> None:
        if phase == "after_live_publish":
            raise RuntimeError("injected publication failure")

    monkeypatch.setattr(replay.shutil, "rmtree", partial_cleanup)
    with pytest.raises(
        replay.Phase1ReplayError,
        match="recovery could not complete",
    ):
        _fake_run(
            monkeypatch,
            project,
            output,
            _bundle("new"),
            hook=fail_after_publish,
        )

    assert _snapshot(output) == old
    replay._verify_bundle(output)
    assert any(path.exists() for path in replay._transaction_paths(output))

    monkeypatch.setattr(replay.shutil, "rmtree", original_rmtree)
    receipt = _fake_run(monkeypatch, project, output, _bundle("new"))

    assert receipt.status == "READY"
    assert _snapshot(output) == dict(_bundle("new").bodies)
    _assert_no_transaction_paths(output)


@pytest.mark.parametrize("damage", ["missing", "result", "experiments"])
def test_invalid_legacy_output_is_preserved_and_refused_before_computation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "legacy"
    _write_valid_legacy(output)
    if damage == "missing":
        (output / "signals.csv").unlink()
    elif damage == "result":
        (output / "result.json").write_bytes(b"{}\n")
    else:
        payload = json.loads((output / "experiments.json").read_text())
        payload["experiments"][0]["signals_artifact_digest"] = "0" * 64
        (output / "experiments.json").write_text(json.dumps(payload) + "\n")
    before = _snapshot(output)
    calls: list[Path] = []
    monkeypatch.setattr(
        replay,
        "_build_replay_bundle",
        lambda root, _output: calls.append(root) or _bundle("never"),
    )

    with pytest.raises(replay.Phase1ReplayError):
        replay.run_phase1_replay(replay.Phase1ReplayRequest(project, output))

    assert calls == []
    assert _snapshot(output) == before


def _exact_dates(start: str, end: str, count: int) -> pd.Series:
    candidates = pd.date_range(start, end, freq="D")
    indexes = np.linspace(0, len(candidates) - 1, count, dtype="int64")
    selected = candidates[indexes]
    assert len(selected.unique()) == count
    assert selected[0].strftime("%Y-%m-%d") == start
    assert selected[-1].strftime("%Y-%m-%d") == end
    return pd.Series(selected.strftime("%Y-%m-%d"), dtype="object")


def _synthetic_frozen_source() -> pd.DataFrame:
    dates = pd.concat([
        _exact_dates("1990-01-03", "2021-08-13", 8225),
        _exact_dates("2021-08-17", "2026-08-14", 1222),
    ], ignore_index=True)
    steps = np.linspace(0.0, 1.0, len(dates))
    close = 100.0 * np.exp(0.25 * steps + 0.01 * np.sin(steps * 80.0))
    return pd.DataFrame({
        "date": dates,
        "close": close,
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def _manifest() -> FrozenInputManifest:
    return FrozenInputManifest(
        dataset="kr_kospi200_index_daily",
        contract_version=1,
        coverage_start="1990-01-03",
        coverage_end="2026-08-14",
        rows=9447,
        files=37,
        bytes=738068,
        root_manifest_sha256=replay.EXPECTED_FROZEN_DIGEST,
        decision_rule="T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
    )


def _synthetic_project(project: Path, manifest: FrozenInputManifest) -> None:
    path = project / "artifacts/backtest/kospi200_frozen_manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(asdict(manifest)), encoding="utf-8")


def test_synthetic_exact_coverage_composition_is_holdout_sealed_and_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    manifest = _manifest()
    _synthetic_project(project, manifest)
    source = _synthetic_frozen_source()
    current = [source.copy(deep=True)]
    label_inputs: list[pd.DataFrame] = []
    original_labels = replay.build_forward_labels

    monkeypatch.setattr(
        frozen_source, "verify_frozen_kospi200", lambda *_args: manifest,
    )
    monkeypatch.setattr(
        contract_storage,
        "read_dataset",
        lambda *_args: current[0].copy(deep=True),
    )
    monkeypatch.setattr(replay, "code_tree_digest", lambda *_args: "c" * 64)
    monkeypatch.setattr(
        replay,
        "build_forward_labels",
        lambda frame: label_inputs.append(frame.copy(deep=True)) or original_labels(frame),
    )
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )

    first = replay._build_replay_bundle(project)
    custom_output = project / "artifacts/backtest/custom-phase1"
    custom = replay._build_replay_bundle(project, custom_output)
    changed = source.copy(deep=True)
    changed.loc[changed["date"].ge("2021-08-17"), "close"] *= 10_000.0
    current[0] = changed
    second = replay._build_replay_bundle(project)

    assert first == second
    assert len(label_inputs) == 3
    assert all(len(frame) == 8225 for frame in label_inputs)
    assert all(frame["date"].max() == "2021-08-13" for frame in label_inputs)
    bodies = first.body_map()
    custom_registry = json.loads(custom.body_map()["experiments.json"])
    assert custom_registry["experiments"][0]["result_artifact"] == (
        "artifacts/backtest/custom-phase1/result.json"
    )
    result = json.loads(bodies["result.json"])
    ledger = json.loads(bodies["portfolio_ledger.json"])["simulation"]
    rows = ledger["ledger"]
    assert result["untouched_holdout_policy"] == asdict(
        replay.KOSPI200_FROZEN_HOLDOUT_V1
    )
    assert result["portfolio_foundation"]["instrument_claim"] == (
        "NOT_EXECUTABLE_INSTRUMENT"
    )
    assert hashlib.sha256(bodies["portfolio_ledger.json"]).hexdigest() == (
        result["portfolio_foundation"]["ledger_artifact_digest"]
    )
    assert rows[0]["signal_observation_date"] is None
    assert rows[1]["signal_observation_date"] == rows[0]["date"]
    assert rows[1]["usable_from"] == rows[1]["date"] + "T09:00:00+09:00"
    assert rows[-1]["date"] == "2021-08-13"
    assert rows[1]["transaction_cost"] / rows[1]["trade_notional"] == pytest.approx(
        0.001
    )
    registry = json.loads(bodies["experiments.json"])
    assert registry["experiments"][0]["holdout_results_reviewed"] is False


def test_production_digest_is_fixed_and_cannot_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    manifest = _manifest()
    _synthetic_project(project, manifest)
    wrong = replace(manifest, root_manifest_sha256="0" * 64)
    monkeypatch.setattr(
        frozen_source, "verify_frozen_kospi200", lambda *_args: wrong,
    )
    monkeypatch.setattr(
        contract_storage,
        "read_dataset",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not read")),
    )

    assert "expected_frozen_digest" not in replay.Phase1ReplayRequest.__dataclass_fields__
    with pytest.raises(replay.Phase1ReplayError, match="digest differs"):
        replay._build_replay_bundle(project)


def test_frozen_input_is_reverified_after_local_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    manifest = _manifest()
    _synthetic_project(project, manifest)
    verify_calls = 0

    def verify(*_args):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise ValueError("persisted bytes changed")
        return manifest

    monkeypatch.setattr(frozen_source, "verify_frozen_kospi200", verify)
    monkeypatch.setattr(
        contract_storage,
        "read_dataset",
        lambda *_args: _synthetic_frozen_source(),
    )

    with pytest.raises(replay.Phase1ReplayError, match="changed or failed"):
        replay._load_verified_source(project)

    assert verify_calls == 2


def test_cli_keeps_paths_thin_wrapper_and_returns_nonzero_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[replay.Phase1ReplayRequest] = []
    monkeypatch.setattr(
        replay_cli,
        "run_phase1_replay",
        lambda request: calls.append(request),
    )
    output = tmp_path / "custom"

    assert replay_cli.main([
        "--project-root", str(tmp_path), "--output-root", str(output),
    ]) == 0
    assert calls == [replay.Phase1ReplayRequest(tmp_path, output)]
    assert replay_cli._parser().parse_args([]).output_root is None
    assert replay.DEFAULT_OUTPUT_RELATIVE == Path(
        "artifacts/backtest/phase1_signal_replay"
    )

    def fail(_request):
        raise RuntimeError("bounded failure")

    monkeypatch.setattr(replay_cli, "run_phase1_replay", fail)
    assert replay_cli.main(["--project-root", str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert error == "Phase-1 replay failed safely.\n"
    assert "bounded failure" not in error
    assert "Traceback" not in error


def test_package_root_exports_typed_runner_contract() -> None:
    assert market_backtest.Phase1ReplayRequest is replay.Phase1ReplayRequest
    assert market_backtest.Phase1ArtifactReceipt is replay.Phase1ArtifactReceipt
    assert market_backtest.Phase1ReplayReceipt is replay.Phase1ReplayReceipt
    assert market_backtest.run_phase1_replay is replay.run_phase1_replay
