import json

import pytest

from market_backtest.ablation import FeatureFamilyStatus, build_ablation_plan
from market_backtest.experiments import (
    ExperimentRecord, artifact_bytes_digest, canonical_json_digest,
    code_tree_digest, serialize_experiment_registry,
)


def test_ablation_plan_stops_at_first_blocked_family_without_substitution():
    plan = build_ablation_plan({
        "PRICE": FeatureFamilyStatus.AVAILABLE,
        "VOLATILITY": FeatureFamilyStatus.AVAILABLE,
        "FX": FeatureFamilyStatus.BLOCKED,
        "BREADTH": FeatureFamilyStatus.AVAILABLE,
    })
    assert plan[1].included_families == ("PRICE", "VOLATILITY")
    assert plan[2].reason == "PIT_OR_INPUT_GATE_OPEN"
    assert plan[3].included_families == ("PRICE", "VOLATILITY")


def test_ablation_requires_price_and_rejects_unknown_family():
    with pytest.raises(ValueError, match="PRICE baseline"):
        build_ablation_plan({"PRICE": FeatureFamilyStatus.BLOCKED})
    with pytest.raises(ValueError, match="unknown"):
        build_ablation_plan({
            "PRICE": FeatureFamilyStatus.AVAILABLE,
            "OTHER": FeatureFamilyStatus.AVAILABLE,
        })


def _record(experiment_id: str = "price_baseline_v1") -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        frozen_input_digest="a" * 64,
        feature_set=("PRICE",),
        feature_versions=("return_20d:v1",),
        label_version="forward_outcomes:v1",
        split_policy="PURGED_EXPANDING_WALK_FORWARD",
        purge=60,
        embargo=5,
        threshold_rule="baseline_v1",
        result_artifact="artifacts/backtest/phase1_signal_replay/result.json",
        code_version="WORKTREE",
        code_tree_digest="b" * 64,
        threshold_values_digest="c" * 64,
        signals_artifact_digest="d" * 64,
        result_artifact_digest="e" * 64,
        label_horizon_trading_days=60,
        signal_pit_status="PIT_SAFE_EOD_T_PLUS_1",
    )


def test_experiment_registry_is_deterministic_and_holdout_sealed():
    first = serialize_experiment_registry((_record("b"), _record("a")))
    second = serialize_experiment_registry((_record("a"), _record("b")))
    assert first == second
    assert [row["experiment_id"] for row in json.loads(first)["experiments"]] == ["a", "b"]
    with pytest.raises(ValueError, match="holdout"):
        ExperimentRecord(**{**_record().__dict__, "holdout_results_reviewed": True})


def test_experiment_registry_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="duplicate"):
        serialize_experiment_registry((_record(), _record()))


def test_experiment_identity_digests_exact_bytes_values_and_owned_code(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_bytes(b"a\n")
    (tmp_path / "src/b.py").write_bytes(b"b\n")
    first = code_tree_digest(tmp_path, ("src/b.py", "src/a.py"))
    assert first == code_tree_digest(tmp_path, ("src/a.py", "src/b.py"))
    (tmp_path / "src/a.py").write_bytes(b"changed\n")
    assert first != code_tree_digest(tmp_path, ("src/a.py", "src/b.py"))
    assert artifact_bytes_digest(b"x") != artifact_bytes_digest(b"x\n")
    assert canonical_json_digest({"value": 1}) != canonical_json_digest({"value": 2})
    with pytest.raises(ValueError, match="escapes"):
        code_tree_digest(tmp_path, ("../outside.py",))


def test_experiment_identity_rejects_weak_purge_pit_and_digests():
    with pytest.raises(ValueError, match="label_horizon"):
        ExperimentRecord(**{**_record().__dict__, "purge": 59})
    with pytest.raises(ValueError, match="PIT status"):
        ExperimentRecord(**{**_record().__dict__, "signal_pit_status": "PIT_LIMITED"})
    with pytest.raises(ValueError, match="SHA-256"):
        ExperimentRecord(**{**_record().__dict__, "code_tree_digest": "invalid"})
