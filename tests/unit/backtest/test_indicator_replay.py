from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import market_backtest.indicator_replay as replay
from market_backtest.execution import simulate_next_open_execution
from market_backtest.portfolio import KOSPI200_FROZEN_HOLDOUT_V1


def source(*, include_holdout: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=22).strftime("%Y-%m-%d").tolist()
    close: list[object] = [100.0 + index for index in range(len(dates))]
    opens: list[object] = [99.0 + index for index in range(len(dates))]
    if include_holdout:
        dates.append(KOSPI200_FROZEN_HOLDOUT_V1.holdout_start)
        close.append("DO_NOT_INSPECT")
        opens.append("DO_NOT_INSPECT")
    return pd.DataFrame({
        "date": dates, "open": opens, "close": close,
        "ticker": "1028", "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def write_code_identity(project: Path) -> str:
    for index, relative in enumerate(replay._CODE_PATHS):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# semantic dependency {index}\n", encoding="utf-8")
    return replay._code_digest(project)


def minimal_bodies(code_digest: str, *, variant: float = 1.0) -> dict[str, bytes]:
    candidates = [
        {
            "candidate_id": "RSI14_LOW_30", "indicator_column": "rsi_14",
            "direction": "LOW", "threshold": 30.0, "horizon_sessions": 20,
            "minimum_signal_observations": 20,
        },
        {
            "candidate_id": "RSI14_HIGH_70", "indicator_column": "rsi_14",
            "direction": "HIGH", "threshold": 70.0, "horizon_sessions": 20,
            "minimum_signal_observations": 20,
        },
    ]
    policy = {
        "policy_id": "RSI14_30_70", "indicator_column": "rsi_14",
        "enter_at_or_below": 30.0, "exit_at_or_above": 70.0,
        "initial_long": False,
    }
    result = replay._json_bytes({
        "schema": replay.INDICATOR_REPLAY_SCHEMA,
        "status": replay.INDICATOR_REPLAY_STATUS,
        "frozen_input_digest": replay.EXPECTED_FROZEN_DIGEST,
        "code_digest": code_digest,
        "feature": {
            "name": "WILDER_RSI14", "version": 1,
            "pit_status": "PIT_SAFE_EOD_T_PLUS_1", "observations": 1,
        },
        "execution_proxy": {
            "instrument_id": "KRX:1028",
            "claim": "INDEX_OPEN_PROXY_NOT_OBTAINABLE_INSTRUMENT",
            "coverage_start": "2020-01-02", "coverage_end": "2020-01-03",
            "observations": 2,
        },
        "study_candidates": candidates,
        "execution_policy": policy,
        "holdout": asdict(KOSPI200_FROZEN_HOLDOUT_V1),
        "winner_selected": False,
        "recommendation_made": False,
        "results_scope": "DEVELOPMENT_ONLY_HOLDOUT_UNTOUCHED",
    })
    execution = asdict(simulate_next_open_execution(
        pd.DataFrame({
            "session_date": ["2020-01-02", "2020-01-03"],
            "open": [100.0, 100.0], "close": [100.0, 100.0],
            "instrument_id": ["KRX:1028", "KRX:1028"],
            "currency": ["KRW", "KRW"],
        }),
        pd.DataFrame({
            "decision_session": pd.Series(["2020-01-02"], dtype="object"),
            "target_long": pd.Series([True], dtype="bool"),
        }),
    ))
    execution_metrics = execution["metrics"]
    matched_metrics = {
        "strategy_ending_nav": execution_metrics["ending_nav"],
        "baseline_ending_nav": execution_metrics["ending_nav"],
        "ending_nav_difference": 0.0,
        "strategy_total_return": execution_metrics["total_return"],
        "baseline_total_return": execution_metrics["total_return"],
        "total_return_difference": 0.0,
        "strategy_annualized_volatility": execution_metrics["annualized_volatility"],
        "baseline_annualized_volatility": execution_metrics["annualized_volatility"],
        "annualized_volatility_difference": 0.0,
        "strategy_max_drawdown": execution_metrics["max_drawdown"],
        "baseline_max_drawdown": execution_metrics["max_drawdown"],
        "strategy_total_turnover": execution_metrics["total_turnover"],
        "baseline_total_turnover": execution_metrics["total_turnover"],
        "strategy_transaction_cost": execution_metrics["transaction_cost_paid"],
        "baseline_transaction_cost": execution_metrics["transaction_cost_paid"],
        "incremental_transaction_cost": 0.0,
    }
    study_metrics = {
        "aligned_observations": 20, "signal_observations": 20,
        "signal_rate": 1.0,
        "conditional_mean_return": 0.1,
        "conditional_median_return": 0.1,
        "conditional_positive_rate": 1.0,
        "conditional_mean_max_drawdown": -0.05,
        "unconditional_mean_return": 0.05,
        "unconditional_median_return": 0.05,
        "unconditional_positive_rate": 0.75,
        "unconditional_mean_max_drawdown": -0.1,
        "conditional_mean_return_difference": 0.05,
    }
    bodies = {
        "result.json": result,
        "rsi14.csv": (
            "observation_date,ticker,date_semantics,instrument_id,observation_time,"
            "available_at,usable_from,rsi_14,source_dataset,source_contract_version,"
            "feature_version,pit_status\n"
            "2020-01-02,1028,KRX_TRADING_DATE_DAILY_FINAL,KRX:1028,"
            "2020-01-02T15:30:00+09:00,2020-01-02T15:30:00+09:00,"
            f"2020-01-03T09:00:00+09:00,{variant},kr_kospi200_index_daily,1,1,"
            "PIT_SAFE_EOD_T_PLUS_1\n"
        ).encode("utf-8"),
        "scenario.json": replay._json_bytes({"comparison": {
            "availability": "EVALUATED",
            "contract_version": "threshold-band-matched-hold/v1",
            "status": "DEVELOPMENT_ONLY_NO_WINNER_SELECTION",
            "winner_selected": False,
            "entry_observation_date": "2020-01-02",
            "entry_usable_from": "2020-01-03T09:00:00+09:00",
            "strategy": {
                "contract_version": "predefined-threshold-band/v1",
                "status": "DEVELOPMENT_ONLY_NO_PARAMETER_SELECTION",
                "holdout_policy_id": KOSPI200_FROZEN_HOLDOUT_V1.policy_id,
                "holdout_start": KOSPI200_FROZEN_HOLDOUT_V1.holdout_start,
                "policy": policy,
                "decisions": [{
                    "observation_date": "2020-01-02",
                    "usable_from": "2020-01-03T09:00:00+09:00",
                    "target_long": True, "reason": "ENTER_AT_OR_BELOW",
                    "indicator_value": variant,
                }],
                "execution": execution,
            },
            "baseline": execution,
            "metrics": matched_metrics,
        }}),
        "study.json": replay._json_bytes({"study": {
            "contract_version": "predefined-indicator-study/v1",
            "status": "DEVELOPMENT_ONLY_NO_WINNER_SELECTION",
            "ticker": "1028", "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
            "holdout_policy_id": KOSPI200_FROZEN_HOLDOUT_V1.policy_id,
            "holdout_start": KOSPI200_FROZEN_HOLDOUT_V1.holdout_start,
            "winner_selected": False,
            "results": [
                {"candidate": candidate, "availability": "EVALUATED",
                 "metrics": study_metrics}
                for candidate in candidates
            ],
        }}),
    }
    receipts = tuple(
        replay._receipt(name, bodies[name]) for name in replay._ARTIFACT_NAMES
    )
    bodies["bundle.json"] = replay._json_bytes({
        "schema": replay.INDICATOR_REPLAY_SCHEMA,
        "frozen_input_digest": replay.EXPECTED_FROZEN_DIGEST,
        "code_digest": code_digest,
        "thresholds": {"enter_at_or_below": 30.0, "exit_at_or_above": 70.0},
        "holdout_policy_id": KOSPI200_FROZEN_HOLDOUT_V1.policy_id,
        "artifact_set_sha256": replay._receipt_digest(receipts),
        "artifacts": [asdict(item) for item in receipts],
    })
    return bodies


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(root.iterdir())}


def rebind_bundle(bodies: dict[str, bytes]) -> None:
    bundle = json.loads(bodies["bundle.json"])
    receipts = tuple(
        replay._receipt(name, bodies[name]) for name in replay._ARTIFACT_NAMES
    )
    bundle["artifacts"] = [asdict(item) for item in receipts]
    bundle["artifact_set_sha256"] = replay._receipt_digest(receipts)
    bodies["bundle.json"] = replay._json_bytes(bundle)


def test_scenario_rejects_holdout_date_before_price_or_indicator_inspection():
    with pytest.raises(replay.IndicatorReplayError, match="untouched holdout"):
        replay._scenario_inputs(source(include_holdout=True))


def test_scenario_builds_t_plus_one_rsi_labels_and_contiguous_positive_open_proxy():
    frame = source()
    frame.loc[2, "open"] = 0.0

    features, labels, market = replay._scenario_inputs(frame)

    assert features["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()
    assert set(labels["observation_date"]).issubset(features["observation_date"])
    assert market["session_date"].iloc[0] == frame["date"].iloc[3]
    assert pd.to_numeric(market["open"]).gt(0.0).all()


def test_content_binding_rejects_extra_file_and_changed_threshold(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = tmp_path / "bundle"
    output.mkdir()
    for name, body in minimal_bodies(code_digest).items():
        (output / name).write_bytes(body)
    replay._verify_directory(output, project_root=project)

    (output / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(replay.IndicatorReplayError, match="artifact set"):
        replay._verify_directory(output, project_root=project)
    (output / "extra.json").unlink()

    payload = json.loads((output / "bundle.json").read_text(encoding="utf-8"))
    payload["thresholds"]["enter_at_or_below"] = 31.0
    (output / "bundle.json").write_bytes(replay._json_bytes(payload))
    with pytest.raises(replay.IndicatorReplayError, match="content binding"):
        replay._verify_directory(output, project_root=project)


def test_atomic_publish_rolls_back_exact_prior_bundle_after_backup_failure(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = project / "artifacts/backtest/indicator_scenario_replay"
    first = minimal_bodies(code_digest, variant=1.0)
    replay._publish(project, output, first)
    prior = tree_bytes(output)

    def fail(phase: str) -> None:
        if phase == "after_live_backup":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        replay._publish(
            project, output, minimal_bodies(code_digest, variant=2.0),
            promotion_hook=fail,
        )

    assert tree_bytes(output) == prior
    replay._verify_directory(output, project_root=project)
    assert not (project / ".tmp/agents/root/indicator_scenario_replay_transaction").exists()


def test_atomic_publish_restores_absence_when_first_publish_fails_after_promotion(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = project / "artifacts/backtest/indicator_scenario_replay"

    def fail(phase: str) -> None:
        if phase == "after_live_publish":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        replay._publish(project, output, minimal_bodies(code_digest), promotion_hook=fail)

    assert not output.exists()
    assert not (project / ".tmp/agents/root/indicator_scenario_replay_transaction").exists()


class SimulatedProcessCrash(BaseException):
    pass


def test_interrupted_valid_live_is_discarded_and_prior_restored_before_retry(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = project / "artifacts/backtest/indicator_scenario_replay"
    prior_bodies = minimal_bodies(code_digest, variant=1.0)
    replay._publish(project, output, prior_bodies)
    prior = tree_bytes(output)

    def crash(phase: str) -> None:
        if phase == "after_live_publish":
            raise SimulatedProcessCrash()

    with pytest.raises(SimulatedProcessCrash):
        replay._publish(
            project, output, minimal_bodies(code_digest, variant=2.0),
            promotion_hook=crash,
        )

    def fail_before_promotion(phase: str) -> None:
        if phase == "after_stage_readback":
            raise RuntimeError("retry injected")

    with pytest.raises(RuntimeError, match="retry injected"):
        replay._publish(
            project, output, minimal_bodies(code_digest, variant=3.0),
            promotion_hook=fail_before_promotion,
        )

    assert tree_bytes(output) == prior
    replay._verify_directory(output, project_root=project)
    assert not (project / ".tmp/agents/root/indicator_scenario_replay_transaction").exists()


def test_interrupted_first_live_is_removed_to_restore_prior_absence_before_retry(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = project / "artifacts/backtest/indicator_scenario_replay"

    def crash(phase: str) -> None:
        if phase == "after_live_publish":
            raise SimulatedProcessCrash()

    with pytest.raises(SimulatedProcessCrash):
        replay._publish(
            project, output, minimal_bodies(code_digest, variant=1.0),
            promotion_hook=crash,
        )
    assert output.is_dir()

    def fail_before_promotion(phase: str) -> None:
        if phase == "after_stage_readback":
            raise RuntimeError("retry injected")

    with pytest.raises(RuntimeError, match="retry injected"):
        replay._publish(
            project, output, minimal_bodies(code_digest, variant=2.0),
            promotion_hook=fail_before_promotion,
        )

    assert not output.exists()
    assert not (project / ".tmp/agents/root/indicator_scenario_replay_transaction").exists()


def test_readback_recomputes_current_code_digest_and_rejects_malformed_payloads(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = tmp_path / "bundle"
    output.mkdir()

    forged = minimal_bodies("a" * 64)
    for name, body in forged.items():
        (output / name).write_bytes(body)
    with pytest.raises(replay.IndicatorReplayError, match="semantics"):
        replay._verify_directory(output, project_root=project)

    valid = minimal_bodies(code_digest)
    malformed = {
        "rsi14.csv": b"observation_date,rsi_14\n2020-01-02,30.0\n",
        "study.json": b'{"study":{}}\n',
        "scenario.json": b'{"comparison":{}}\n',
    }
    for target, replacement in malformed.items():
        bodies = dict(valid)
        bodies[target] = replacement
        rebind_bundle(bodies)
        for name, body in bodies.items():
            (output / name).write_bytes(body)
        with pytest.raises(replay.IndicatorReplayError, match="schema|semantics"):
            replay._verify_directory(output, project_root=project)


def test_readback_rejects_nested_timestamp_schema_and_numeric_forgery(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    code_digest = write_code_identity(project)
    output = tmp_path / "bundle"
    output.mkdir()

    cases: list[tuple[str, object]] = []
    invalid_rsi = minimal_bodies(code_digest)
    invalid_rsi["rsi14.csv"] = invalid_rsi["rsi14.csv"].replace(
        b"2020-01-02T15:30:00+09:00",
        b"NOT_A_TIMESTAMP",
        1,
    )
    cases.append(("RSI observation timestamp", invalid_rsi))

    invalid_last_usable = minimal_bodies(code_digest)
    invalid_last_usable["rsi14.csv"] = invalid_last_usable["rsi14.csv"].replace(
        b"2020-01-03T09:00:00+09:00",
        b"2020-01-06T09:00:00+09:00",
        1,
    )
    cases.append(("RSI last usable/proxy boundary", invalid_last_usable))

    invalid_study = minimal_bodies(code_digest)
    study = json.loads(invalid_study["study.json"])
    study["study"]["results"][0]["metrics"]["unexpected_metric"] = 1.0
    invalid_study["study.json"] = replay._json_bytes(study)
    cases.append(("study metric schema", invalid_study))

    scenario_mutations = (
        lambda payload: payload["comparison"].__setitem__(
            "entry_usable_from", "NOT_A_TIMESTAMP",
        ),
        lambda payload: payload["comparison"]["metrics"].__setitem__(
            "ending_nav_difference", "NOT_NUMERIC",
        ),
        lambda payload: payload["comparison"]["strategy"]["execution"]["metrics"].__setitem__(
            "ending_nav", "NOT_NUMERIC",
        ),
        lambda payload: payload["comparison"]["baseline"]["metrics"].__setitem__(
            "ending_nav", "NOT_NUMERIC",
        ),
        lambda payload: payload["comparison"]["strategy"]["execution"]["ledger"][1].__setitem__(
            "trade_side", "SELL",
        ),
        lambda payload: payload["comparison"]["strategy"]["execution"]["ledger"][1].__setitem__(
            "position_before", 1,
        ),
        lambda payload: payload["comparison"]["strategy"]["execution"]["metrics"].__setitem__(
            "annualized_return", 999.0,
        ),
        lambda payload: (
            payload["comparison"].__setitem__(
                "entry_usable_from", "2020-01-06T09:00:00+09:00",
            ),
            payload["comparison"]["strategy"]["decisions"][0].__setitem__(
                "usable_from", "2020-01-06T09:00:00+09:00",
            ),
        ),
        lambda payload: payload["comparison"]["strategy"]["decisions"][0].__setitem__(
            "indicator_value", 2.0,
        ),
    )
    for index, mutate in enumerate(scenario_mutations):
        bodies = minimal_bodies(code_digest)
        scenario = json.loads(bodies["scenario.json"])
        mutate(scenario)
        bodies["scenario.json"] = replay._json_bytes(scenario)
        cases.append((f"scenario nested mutation {index}", bodies))

    for label, bodies in cases:
        rebind_bundle(bodies)
        for name, body in bodies.items():
            (output / name).write_bytes(body)
        with pytest.raises(
            replay.IndicatorReplayError, match="schema|semantics|differs|invalid",
        ):
            replay._verify_directory(output, project_root=project)


def test_bundle_serialization_and_receipts_are_deterministic():
    first = minimal_bodies("a" * 64)
    second = minimal_bodies("a" * 64)

    assert first == second
    assert {
        name: hashlib.sha256(body).hexdigest() for name, body in first.items()
    } == {
        name: hashlib.sha256(body).hexdigest() for name, body in second.items()
    }
