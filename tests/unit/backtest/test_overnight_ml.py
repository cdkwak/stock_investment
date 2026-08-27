from __future__ import annotations

import numpy as np
import optuna
import pandas as pd
import pytest

from market_backtest.holdout import CoverageHoldout
from market_backtest.overnight_ml import (
    FEATURE_COLUMNS,
    OvernightMLRequest,
    evaluate_ml_trial,
    prepare_ml_development_data,
)


def _source(rows: int = 420) -> pd.DataFrame:
    dates = pd.bdate_range("2018-01-02", periods=rows)
    phase = np.arange(rows, dtype="float64")
    close = 100.0 * np.exp(
        0.18 * np.sin(phase / 17.0) + 0.05 * np.sin(phase / 5.0)
    )
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": close,
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def _holdout(source: pd.DataFrame, development_rows: int = 360) -> CoverageHoldout:
    return CoverageHoldout(
        policy_id="TEST_UNTOUCHED_HOLDOUT",
        coverage_start=str(source["date"].iloc[0]),
        coverage_end=str(source["date"].iloc[-1]),
        holdout_start=str(source["date"].iloc[development_rows]),
        development_observations=development_rows,
        holdout_observations=len(source) - development_rows,
    )


def test_prepare_ml_data_slices_source_before_features_and_labels() -> None:
    source = _source()
    holdout = _holdout(source)

    prepared = prepare_ml_development_data(
        source, holdout, minimum_train=120, test_size=20, purge=60, embargo=5,
    )

    assert prepared.holdout.results_reviewed is False
    assert prepared.frame["observation_date"].lt(holdout.holdout_start).all()
    assert prepared.frame["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()
    assert prepared.frame.loc[:, list(FEATURE_COLUMNS)].notna().all().all()
    assert not any(
        column.startswith(("forward_", "label_"))
        for column in FEATURE_COLUMNS
    )
    usable = pd.to_datetime(prepared.frame["usable_from"], utc=True)
    available = pd.to_datetime(prepared.frame["label_available_at"], utc=True)
    assert usable.lt(available).all()
    for split in prepared.splits:
        assert (
            available.iloc[split.train_start:split.train_end].max()
            < usable.iloc[split.test_start:split.test_end].min()
        )


def test_prepare_ml_data_requires_maximum_label_horizon_purge() -> None:
    source = _source()
    with pytest.raises(ValueError, match="maximum label horizon"):
        prepare_ml_development_data(
            source, _holdout(source), minimum_train=120,
            test_size=20, purge=59, embargo=5,
        )


def test_logistic_trial_is_deterministic_and_holdout_unreviewed() -> None:
    source = _source()
    prepared = prepare_ml_development_data(
        source, _holdout(source), minimum_train=120,
        test_size=20, purge=60, embargo=5,
    )
    parameters = {
        "decision_threshold": 0.5,
        "model_family": "logistic",
        "balanced_weight": True,
        "logistic_c": 1.0,
    }
    first = optuna.trial.FixedTrial(parameters, number=7)
    second = optuna.trial.FixedTrial(parameters, number=7)

    first_score = evaluate_ml_trial(first, prepared)
    second_score = evaluate_ml_trial(second, prepared)

    assert first_score == second_score
    assert first.user_attrs["prediction_sha256"] == second.user_attrs["prediction_sha256"]
    assert first.user_attrs["holdout_results_reviewed"] is False
    assert first.user_attrs["signal_pit_status"] == "PIT_SAFE_EOD_T_PLUS_1"


def test_request_rejects_more_than_eight_hours(tmp_path) -> None:
    with pytest.raises(ValueError, match="duration_seconds"):
        OvernightMLRequest(
            project_root=tmp_path,
            duration_seconds=8 * 60 * 60 + 1,
        )
