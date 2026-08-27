from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket

import pytest

from market_backtest.indicator_replay import (
    EXPECTED_FROZEN_DIGEST,
    IndicatorReplayRequest,
    run_indicator_scenario_replay,
)


def identity(path: Path) -> tuple[int, str]:
    body = path.read_bytes()
    return len(body), hashlib.sha256(body).hexdigest()


def protected_identities(root: Path) -> dict[str, tuple[int, str]]:
    paths = [root / "artifacts/backtest/kospi200_frozen_manifest.json"]
    frozen = root / "artifacts/backtest/frozen_inputs/kr_kospi200_index_daily" / EXPECTED_FROZEN_DIGEST
    paths.extend(sorted(frozen.rglob("data.parquet")))
    accepted = root / "artifacts/backtest/phase1_signal_replay"
    if accepted.is_dir():
        paths.extend(sorted(path for path in accepted.iterdir() if path.is_file()))
    return {
        path.relative_to(root).as_posix(): identity(path)
        for path in paths
    }


def output_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes() for path in sorted(root.iterdir())
        if path.is_file()
    }


def test_two_real_frozen_development_replays_are_byte_identical_and_offline(
    tmp_path, monkeypatch,
):
    project = Path(__file__).resolve().parents[3]
    before = protected_identities(project)
    network_attempts: list[tuple[object, ...]] = []

    def forbidden_network(*args, **kwargs):
        network_attempts.append(args)
        raise AssertionError("network is unreachable in indicator replay")

    monkeypatch.setattr(socket, "socket", forbidden_network)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first = run_indicator_scenario_replay(IndicatorReplayRequest(
        project_root=project, output_root=first_root,
    ))
    second = run_indicator_scenario_replay(IndicatorReplayRequest(
        project_root=project, output_root=second_root,
    ))

    assert first.status == second.status == "READY"
    assert first.frozen_input_digest == second.frozen_input_digest == EXPECTED_FROZEN_DIGEST
    assert first.bundle_digest == second.bundle_digest
    assert output_bytes(first_root) == output_bytes(second_root)
    assert set(output_bytes(first_root)) == {
        "bundle.json", "result.json", "rsi14.csv", "scenario.json", "study.json",
    }
    result = json.loads((first_root / "result.json").read_text(encoding="utf-8"))
    assert result["winner_selected"] is False
    assert result["recommendation_made"] is False
    assert result["holdout"]["results_reviewed"] is False
    assert result["execution_policy"] == {
        "enter_at_or_below": 30.0,
        "exit_at_or_above": 70.0,
        "indicator_column": "rsi_14",
        "initial_long": False,
        "policy_id": "RSI14_30_70",
    }
    assert result["execution_proxy"]["claim"] == (
        "INDEX_OPEN_PROXY_NOT_OBTAINABLE_INSTRUMENT"
    )
    assert network_attempts == []
    assert protected_identities(project) == before
    assert not (project / ".tmp/agents/root/indicator_scenario_replay_transaction").exists()
    assert all(not path.is_symlink() for path in first_root.iterdir())
