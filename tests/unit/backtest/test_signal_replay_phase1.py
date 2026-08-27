from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_backtest.crisis import replay_crisis_windows
from market_backtest.signals import (
    PREDEFINED_SMALL_GRID, SignalThresholds, build_descriptive_signals,
    evaluate_predefined_small_grid, evaluate_predefined_walk_forward, evaluate_signals,
)


def features() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=8)
    return pd.DataFrame({
        "observation_date": dates.strftime("%Y-%m-%d"),
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
        "usable_from": (dates + pd.offsets.BDay(1)).strftime("%Y-%m-%d") + "T09:00:00+09:00",
        "source_dataset": "kr_kospi200_index_daily",
        "source_contract_version": 1,
        "realized_volatility_20d": [0.1, 0.3, 0.4, 0.1, 0.5, 0.2, 0.1, 0.4],
        "rolling_drawdown_60d": [-0.01, -0.11, -0.02, -0.2, -0.15, -0.01, -0.2, -0.12],
        "ma_distance_60d": [-0.01, -0.09, -0.1, -0.01, -0.2, -0.01, -0.09, -0.1],
        "return_20d": [0.01, -0.06, 0.02, -0.1, -0.2, 0.01, -0.1, -0.06],
        "pit_status": "PIT_SAFE_EOD_T_PLUS_1",
    })


def labels() -> pd.DataFrame:
    frame = features().loc[:, [
        "observation_date", "ticker", "date_semantics",
    ]].copy()
    frame["forward_max_drawdown_20d"] = [-0.01, -0.2, -0.03, -0.15, -0.3, -0.02, -0.2, -0.11]
    frame["forward_return_20d"] = [0.1, -0.1, 0.02, -0.08, -0.2, 0.03, -0.15, -0.05]
    frame["label_available_at"] = "2020-06-01T15:30:00+09:00"
    return frame


def walk_forward_frames(periods: int = 80) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2020-01-02", periods=periods)
    selector = np.arange(periods) % len(features())
    feature_frame = features().iloc[selector].reset_index(drop=True)
    label_frame = labels().iloc[selector].reset_index(drop=True)
    observation_dates = dates.strftime("%Y-%m-%d")
    feature_frame["observation_date"] = observation_dates
    feature_frame["usable_from"] = (
        dates + pd.offsets.BDay(1)
    ).strftime("%Y-%m-%d") + "T09:00:00+09:00"
    label_frame["observation_date"] = observation_dates
    label_frame["label_available_at"] = "2020-12-31T15:30:00+09:00"
    return feature_frame, label_frame


def test_rule_signal_and_metrics_are_deterministic():
    first = build_descriptive_signals(features(), SignalThresholds())
    second = build_descriptive_signals(features(), SignalThresholds())
    pd.testing.assert_frame_equal(first, second)
    assert first["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()
    assert first["ticker"].eq("1028").all()
    assert first["date_semantics"].eq("KRX_TRADING_DATE_DAILY_FINAL").all()
    assert first["source_dataset"].eq("kr_kospi200_index_daily").all()
    assert first["source_contract_version"].eq(1).all()
    assert first.risk_off_signal.tolist() == [False, True, True, True, True, False, True, True]
    metrics = evaluate_signals(first, labels())
    assert metrics["observations"] == 8
    assert metrics["true_positive"] == 5
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 0
    assert metrics["true_negative"] == 2
    assert 0 <= metrics["precision"] <= 1
    assert 0 <= metrics["recall"] <= 1
    assert metrics["pr_auc_average_precision"] == pytest.approx(29 / 30)


@pytest.mark.parametrize("column", ["risk_off_signal", "risk_score"])
def test_signal_evaluation_requires_each_decision_field(column):
    with pytest.raises(
        ValueError, match="signal evaluation decision schema/content is invalid",
    ):
        evaluate_signals(
            build_descriptive_signals(features()).drop(columns=column), labels(),
        )


@pytest.mark.parametrize("column", ["risk_off_signal", "risk_score"])
def test_signal_evaluation_rejects_duplicate_decision_fields_cleanly(column):
    signal_frame = build_descriptive_signals(features())
    signal_frame = pd.concat(
        [signal_frame, signal_frame.loc[:, [column]]], axis=1,
    )

    with pytest.raises(
        ValueError, match="signal evaluation decision schema/content is invalid",
    ):
        evaluate_signals(signal_frame, labels())


@pytest.mark.parametrize(
    ("values", "dtype"),
    [
        pytest.param(["False"] * 8, "object", id="text"),
        pytest.param(([False, True] * 4), "object", id="object-bools"),
        pytest.param(([0, 1] * 4), "int64", id="integers"),
        pytest.param(([0.0, 1.0] * 4), "float64", id="floats"),
        pytest.param(([False] * 7 + [np.nan]), "object", id="nan"),
        pytest.param(([False] * 7 + [None]), "object", id="none"),
        pytest.param(([False] * 7 + [pd.NA]), "boolean", id="nullable-missing"),
    ],
)
def test_signal_evaluation_rejects_non_boolean_decisions(values, dtype):
    signal_frame = build_descriptive_signals(features())
    signal_frame["risk_off_signal"] = pd.Series(values, dtype=dtype)

    with pytest.raises(
        ValueError, match="signal risk_off_signal must be non-null boolean",
    ):
        evaluate_signals(signal_frame, labels())


@pytest.mark.parametrize(
    ("values", "dtype"),
    [
        pytest.param(["1"] * 8, "object", id="text"),
        pytest.param([1] * 8, "object", id="object-integers"),
        pytest.param([1.0] * 8, "float64", id="whole-floats"),
        pytest.param([1.5] * 8, "float64", id="fractional"),
        pytest.param([1] * 7 + [np.nan], "float64", id="nan"),
        pytest.param([1] * 7 + [None], "object", id="none"),
        pytest.param([-1] + [1] * 7, "int64", id="negative"),
        pytest.param([5] + [1] * 7, "int64", id="above-four"),
        pytest.param([1] * 7 + [pd.NA], "Int64", id="nullable-missing"),
        pytest.param([True] * 8, "bool", id="booleans"),
    ],
)
def test_signal_evaluation_rejects_invalid_risk_scores(values, dtype):
    signal_frame = build_descriptive_signals(features())
    signal_frame["risk_score"] = pd.Series(values, dtype=dtype)

    with pytest.raises(
        ValueError, match=r"signal risk_score must be non-null integer in \[0, 4\]",
    ):
        evaluate_signals(signal_frame, labels())


def test_signal_evaluation_accepts_null_free_nullable_decision_dtypes():
    signal_frame = build_descriptive_signals(features())
    expected = evaluate_signals(signal_frame, labels())
    signal_frame["risk_off_signal"] = signal_frame["risk_off_signal"].astype("boolean")
    signal_frame["risk_score"] = signal_frame["risk_score"].astype("Int64")

    assert evaluate_signals(signal_frame, labels()) == expected


def test_signal_evaluation_validates_decisions_before_inner_merge():
    signal_frame = build_descriptive_signals(features())
    signal_frame.loc[signal_frame.index[0], "risk_score"] = 5
    later_labels = labels().iloc[1:].reset_index(drop=True)

    with pytest.raises(
        ValueError, match=r"signal risk_score must be non-null integer in \[0, 4\]",
    ):
        evaluate_signals(signal_frame, later_labels)


@pytest.mark.parametrize(
    "column",
    ["forward_return_20d", "forward_max_drawdown_20d", "label_available_at"],
)
def test_signal_evaluation_requires_each_used_label_field(column):
    with pytest.raises(ValueError, match="label evaluation schema/content is invalid"):
        evaluate_signals(
            build_descriptive_signals(features()), labels().drop(columns=column),
        )


@pytest.mark.parametrize(
    "column",
    ["forward_return_20d", "forward_max_drawdown_20d", "mae_20d", "mfe_20d"],
)
@pytest.mark.parametrize(
    "value", ["not-numeric", None, np.nan, np.inf, -np.inf, -1.0001, True],
)
def test_signal_evaluation_rejects_invalid_used_label_outcomes(column, value):
    outcome_labels = labels()
    if column not in outcome_labels:
        outcome_labels[column] = 0.0
    outcome_labels[column] = outcome_labels[column].astype(object)
    outcome_labels.loc[outcome_labels.index[0], column] = value

    with pytest.raises(ValueError, match=rf"label {column} must be real numeric"):
        evaluate_signals(build_descriptive_signals(features()), outcome_labels)


@pytest.mark.parametrize(
    "column",
    ["forward_return_20d", "forward_max_drawdown_20d", "mae_20d", "mfe_20d"],
)
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, -1.0001])
def test_signal_evaluation_rejects_invalid_values_in_numeric_outcome_columns(
    column, value,
):
    outcome_labels = labels()
    if column not in outcome_labels:
        outcome_labels[column] = 0.0
    outcome_labels.loc[outcome_labels.index[0], column] = value

    assert pd.api.types.is_float_dtype(outcome_labels[column].dtype)
    with pytest.raises(ValueError, match=rf"label {column} must be real numeric"):
        evaluate_signals(build_descriptive_signals(features()), outcome_labels)


def test_optional_label_outcomes_are_reported_without_mutating_inputs():
    signal_frame = build_descriptive_signals(features())
    outcome_labels = labels()
    outcome_labels["mae_20d"] = np.linspace(-0.20, -0.01, len(outcome_labels))
    outcome_labels["mfe_20d"] = np.linspace(0.01, 0.20, len(outcome_labels))
    signal_before = signal_frame.copy(deep=True)
    labels_before = outcome_labels.copy(deep=True)

    metrics = evaluate_signals(signal_frame, outcome_labels)
    selected = signal_frame["risk_off_signal"].to_numpy(dtype="bool")

    assert metrics["mean_mae_20d"] == pytest.approx(
        outcome_labels.loc[selected, "mae_20d"].mean()
    )
    assert metrics["mean_mfe_20d"] == pytest.approx(
        outcome_labels.loc[selected, "mfe_20d"].mean()
    )
    pd.testing.assert_frame_equal(signal_frame, signal_before)
    pd.testing.assert_frame_equal(outcome_labels, labels_before)


@pytest.mark.parametrize(
    ("artifact", "column"),
    [("signal", "usable_from"), ("label", "label_available_at")],
)
@pytest.mark.parametrize(
    "value", [None, 123, "2020-06-01T15:30:00", "not-an-iso-clock"],
)
def test_signal_evaluation_requires_aware_iso_clocks(artifact, column, value):
    signal_frame = build_descriptive_signals(features())
    label_frame = labels()
    selected = signal_frame if artifact == "signal" else label_frame
    selected[column] = value

    with pytest.raises(ValueError, match=rf"{artifact} {column} clock"):
        evaluate_signals(signal_frame, label_frame)


@pytest.mark.parametrize(
    ("artifact", "column"),
    [("signal", "usable_from"), ("label", "label_available_at")],
)
@pytest.mark.parametrize(
    "value",
    ["2020-06-01Q06:30:00+00:00", "2020-06-01\n06:30:00+00:00"],
)
def test_signal_evaluation_rejects_noncanonical_datetime_separators(
    artifact, column, value,
):
    signal_frame = build_descriptive_signals(features())
    label_frame = labels()
    selected = signal_frame if artifact == "signal" else label_frame
    selected[column] = value

    with pytest.raises(ValueError, match=rf"{artifact} {column} clock"):
        evaluate_signals(signal_frame, label_frame)


def test_signal_evaluation_compares_aware_offset_clocks_in_utc():
    signal_frame = build_descriptive_signals(features())
    label_frame = labels()
    expected = evaluate_signals(signal_frame, label_frame)
    signal_frame["usable_from"] = "2020-06-01T06:30:00Z"
    label_frame["label_available_at"] = "2020-06-01T15:30:00+09:00"

    assert evaluate_signals(signal_frame, label_frame) == expected


@pytest.mark.parametrize(
    "column", ["mae_20d", "mfe_20d", "forward_return_5d", "label_source"],
)
def test_signal_evaluation_rejects_supplied_signal_outcome_namespaces(column):
    signal_frame = build_descriptive_signals(features())
    signal_frame[column] = 0.25

    with pytest.raises(ValueError, match="label namespace is forbidden"):
        evaluate_signals(signal_frame, labels())


@pytest.mark.parametrize("column", ["mae_20d", "mfe_20d"])
def test_signal_evaluation_rejects_optional_outcome_merge_collisions(column):
    signal_frame = build_descriptive_signals(features())
    label_frame = labels()
    signal_frame[column] = -0.75
    label_frame[column] = 0.25

    with pytest.raises(ValueError, match="label namespace is forbidden"):
        evaluate_signals(signal_frame, label_frame)


def test_signal_evaluation_rejects_duplicate_optional_label_columns_cleanly():
    label_frame = labels()
    label_frame["mae_20d"] = -0.10
    label_frame = pd.concat(
        [label_frame, label_frame.loc[:, ["mae_20d"]]], axis=1,
    )

    with pytest.raises(ValueError, match="label evaluation schema/content is invalid"):
        evaluate_signals(build_descriptive_signals(features()), label_frame)


def test_signal_evaluation_rejects_label_clock_before_decision_in_utc():
    signal_frame = build_descriptive_signals(features())
    label_frame = labels()
    signal_frame["usable_from"] = "2020-06-01T06:30:00Z"
    label_frame["label_available_at"] = "2020-06-01T15:29:59.999999+09:00"

    with pytest.raises(ValueError, match="label availability precedes decision clock"):
        evaluate_signals(signal_frame, label_frame)


@pytest.mark.parametrize(
    ("artifact", "column", "value"),
    [
        ("label", "forward_return_20d", np.nan),
        ("label", "forward_max_drawdown_20d", np.inf),
        ("label", "mae_20d", -1.0001),
        ("label", "mfe_20d", "not-numeric"),
        ("label", "label_available_at", "2020-12-31T15:30:00"),
        ("feature", "usable_from", "2020-01-03T09:00:00"),
    ],
)
def test_walk_forward_rejects_corruption_outside_test_folds(
    artifact, column, value, monkeypatch,
):
    feature_frame, label_frame = walk_forward_frames()
    selected = label_frame if artifact == "label" else feature_frame
    if column not in selected:
        selected[column] = 0.0
    if isinstance(value, str) and column in {
        "forward_return_20d", "forward_max_drawdown_20d", "mae_20d", "mfe_20d",
    }:
        selected[column] = selected[column].astype(object)
    selected.loc[selected.index[0], column] = value
    monkeypatch.setattr(
        "market_backtest.signals.expanding_walk_forward",
        lambda **_: pytest.fail("split construction ran before artifact validation"),
    )
    expected_error = (
        rf"label {column} must be real numeric"
        if column not in {"label_available_at", "usable_from"}
        else rf"{artifact} {column} clock"
    )

    with pytest.raises(ValueError, match=expected_error):
        evaluate_predefined_walk_forward(
            feature_frame, label_frame,
            minimum_train=5, test_size=5, purge=60, embargo=1,
        )


@pytest.mark.parametrize("row", [0, 20, 64])
def test_walk_forward_validates_numeric_labels_in_training_and_purge_rows(row):
    feature_frame, label_frame = walk_forward_frames()
    label_frame.loc[row, "forward_return_20d"] = np.inf

    with pytest.raises(
        ValueError, match="label forward_return_20d must be real numeric",
    ):
        evaluate_predefined_walk_forward(
            feature_frame, label_frame,
            minimum_train=5, test_size=5, purge=60, embargo=1,
        )


def test_valid_walk_forward_contract_preserves_inputs_and_fold_accounting():
    feature_frame, label_frame = walk_forward_frames()
    feature_before = feature_frame.copy(deep=True)
    labels_before = label_frame.copy(deep=True)

    results = evaluate_predefined_walk_forward(
        feature_frame, label_frame,
        minimum_train=5, test_size=5, purge=60, embargo=1,
    )

    assert len(results) == len(PREDEFINED_SMALL_GRID)
    assert all(row["folds"] == 3 for row in results)
    assert all(row["test_observations"] == 13 for row in results)
    pd.testing.assert_frame_equal(feature_frame, feature_before)
    pd.testing.assert_frame_equal(label_frame, labels_before)


@pytest.mark.parametrize("artifact", ["signal", "label"])
@pytest.mark.parametrize("mutation", ["reversed", "duplicate"])
def test_signal_evaluation_rejects_noncanonical_observation_keys(
    artifact, mutation,
):
    signal_frame = build_descriptive_signals(features())
    label_frame = labels()
    selected = signal_frame if artifact == "signal" else label_frame
    if mutation == "reversed":
        selected = selected.iloc[::-1].reset_index(drop=True)
    else:
        selected = pd.concat([selected, selected.iloc[[0]]], ignore_index=True)
    if artifact == "signal":
        signal_frame = selected
    else:
        label_frame = selected

    with pytest.raises(ValueError, match=rf"{artifact} observation_date key.*unique, and sorted"):
        evaluate_signals(signal_frame, label_frame)


@pytest.mark.parametrize("artifact", ["feature", "label"])
@pytest.mark.parametrize("mutation", ["reversed", "duplicate"])
def test_signal_sources_fail_before_walk_forward_can_reorder_invalid_keys(
    artifact, mutation,
):
    feature_frame = features()
    label_frame = labels()
    selected = feature_frame if artifact == "feature" else label_frame
    if mutation == "reversed":
        selected = selected.iloc[::-1].reset_index(drop=True)
    else:
        selected = pd.concat([selected, selected.iloc[[0]]], ignore_index=True)
    if artifact == "feature":
        feature_frame = selected
        with pytest.raises(
            ValueError, match=r"feature observation_date key.*unique, and sorted",
        ):
            build_descriptive_signals(feature_frame)
    else:
        label_frame = selected

    with pytest.raises(
        ValueError, match=rf"{artifact} observation_date key.*unique, and sorted",
    ):
        evaluate_predefined_walk_forward(feature_frame, label_frame)


@pytest.mark.parametrize("artifact", ["feature", "signal", "label"])
def test_signal_boundaries_reject_noncanonical_date_text_before_tie_ranking(artifact):
    feature_frame = features()
    signal_frame = build_descriptive_signals(feature_frame)
    label_frame = labels()
    selected = {
        "feature": feature_frame,
        "signal": signal_frame,
        "label": label_frame,
    }[artifact]
    selected.loc[selected.index[0], "observation_date"] = "2020-1-2"

    with pytest.raises(ValueError, match=rf"{artifact} observation_date key is invalid"):
        if artifact == "feature":
            build_descriptive_signals(feature_frame)
        else:
            evaluate_signals(signal_frame, label_frame)


def test_predefined_small_grid_is_deterministic_and_does_not_select_a_winner():
    first = evaluate_predefined_small_grid(features(), labels())
    second = evaluate_predefined_small_grid(features(), labels())
    assert first == second and len(first) == len(PREDEFINED_SMALL_GRID) == 4
    assert [row["baseline_id"] for row in first] == [name for name, _ in PREDEFINED_SMALL_GRID]
    assert all("selected" not in row and "rank" not in row for row in first)
    with pytest.raises(ValueError, match="maximum label horizon"):
        evaluate_predefined_walk_forward(
            features(), labels(), minimum_train=2, test_size=2, purge=1, embargo=0,
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("ticker", "9999"),
        ("date_semantics", "UNVERIFIED_INTRADAY"),
    ],
)
def test_walk_forward_rejects_source_mismatch_before_split_construction(
    column, value,
):
    outcome_labels = labels()
    outcome_labels[column] = value

    with pytest.raises(ValueError, match="feature/label source identity differs"):
        evaluate_predefined_walk_forward(features(), outcome_labels)


def test_signal_rejects_non_pit_safe_input_and_early_label():
    unsafe = features(); unsafe["pit_status"] = "PIT_LIMITED"
    with pytest.raises(ValueError, match="PIT-safe"):
        build_descriptive_signals(unsafe)
    early = labels(); early["label_available_at"] = "2019-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="availability"):
        evaluate_signals(build_descriptive_signals(features()), early)
    contaminated = features(); contaminated["forward_return_20d"] = 0.0
    with pytest.raises(ValueError, match="label namespace"):
        build_descriptive_signals(contaminated)
    tampered = build_descriptive_signals(features()).drop(columns="pit_status")
    with pytest.raises(ValueError, match="PIT-safe"):
        evaluate_signals(tampered, labels())


def test_crisis_replay_is_diagnostic_and_does_not_change_thresholds():
    thresholds = SignalThresholds()
    result = replay_crisis_windows(
        build_descriptive_signals(features(), thresholds), labels(),
        {"fixture": ("2020-01-01", "2020-12-31"), "missing": ("1990-01-01", "1990-02-01")},
        holdout_start="2021-01-01",
    )
    assert result[0]["status"] == "DIAGNOSTIC_ONLY"
    assert result[0]["observations"] == len(features())
    assert result[1]["status"] == "NO_COVERAGE"
    assert thresholds == SignalThresholds()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("ticker", "9999"),
        ("date_semantics", "UNVERIFIED_INTRADAY"),
    ],
)
def test_crisis_replay_rejects_source_identity_mismatch(column, value):
    outcome_labels = labels()
    outcome_labels[column] = value

    with pytest.raises(ValueError, match="signal/label source identity differs"):
        replay_crisis_windows(
            build_descriptive_signals(features()), outcome_labels,
            {"development": ("2020-01-01", "2020-12-31")},
            holdout_start="2021-01-01",
        )


@pytest.mark.parametrize(
    ("artifact", "column"),
    [("signal", "ticker"), ("label", "date_semantics")],
)
def test_crisis_replay_requires_explicit_source_identity(artifact, column):
    signal_rows = build_descriptive_signals(features())
    outcome_labels = labels()
    if artifact == "signal":
        signal_rows = signal_rows.drop(columns=column)
    else:
        outcome_labels = outcome_labels.drop(columns=column)

    with pytest.raises(
        ValueError, match=rf"{artifact} source identity schema/content is invalid",
    ):
        replay_crisis_windows(
            signal_rows, outcome_labels,
            {"development": ("2020-01-01", "2020-12-31")},
            holdout_start="2021-01-01",
        )


def test_crisis_diagnostics_never_inspect_untouched_holdout_rows():
    boundary = "2020-01-08"
    signals = build_descriptive_signals(features())
    outcome_labels = labels()
    eligible = outcome_labels["observation_date"].lt("2020-01-07")
    outcome_labels.loc[eligible, "label_available_at"] = (
        "2020-01-07T15:30:00+09:00"
    )
    unavailable = ~eligible
    outcome_labels.loc[unavailable, "forward_return_20d"] = np.inf
    outcome_labels.loc[unavailable, "forward_max_drawdown_20d"] = np.nan
    signals = pd.concat([
        signals,
        signals.loc[signals["observation_date"].ge(boundary)].iloc[[0]],
    ], ignore_index=True)
    outcome_labels = pd.concat([
        outcome_labels,
        outcome_labels.loc[
            outcome_labels["observation_date"].ge(boundary)
        ].iloc[[0]],
    ], ignore_index=True)
    result = replay_crisis_windows(
        signals, outcome_labels,
        {"development": ("2020-01-01", "2020-01-07"), "holdout": (boundary, "2020-12-31")},
        holdout_start=boundary,
    )
    assert result[0]["status"] == "DIAGNOSTIC_ONLY"
    assert result[0]["observations"] == 3
    assert np.isfinite(result[0]["worst_forward_20d_drawdown"])
    assert np.isfinite(result[0]["mean_forward_20d_return"])
    assert result[1] == {
        "event": "holdout", "start": boundary, "end": "2020-12-31",
        "status": "UNTOUCHED_HOLDOUT", "holdout_observations_excluded": "NOT_INSPECTED",
    }


def test_crisis_replay_uses_only_labels_available_before_holdout():
    boundary = "2020-02-01"
    signal_rows = build_descriptive_signals(features())
    outcome_labels = labels()
    outcome_labels["label_available_at"] = "2020-02-01T00:00:00+09:00"
    outcome_labels.loc[outcome_labels.index[:2], "label_available_at"] = (
        "2020-01-31T15:30:00+09:00"
    )
    outcome_labels.loc[outcome_labels.index[2:], "forward_return_20d"] = np.inf
    outcome_labels.loc[
        outcome_labels.index[2:], "forward_max_drawdown_20d"
    ] = np.nan
    signals_before = signal_rows.copy(deep=True)
    labels_before = outcome_labels.copy(deep=True)

    result = replay_crisis_windows(
        signal_rows, outcome_labels,
        {"development": ("2020-01-01", "2020-01-31")},
        holdout_start=boundary,
    )[0]

    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["observations"] == 2
    assert result["risk_off_observations"] == 1
    assert result["adverse_observations"] == 1
    assert result["event_precision"] == 1.0
    assert result["event_recall"] == 1.0
    assert result["worst_forward_20d_drawdown"] == pytest.approx(-0.20)
    assert result["mean_forward_20d_return"] == pytest.approx(0.0)
    pd.testing.assert_frame_equal(signal_rows, signals_before)
    pd.testing.assert_frame_equal(outcome_labels, labels_before)


def test_crisis_replay_reports_no_coverage_when_all_labels_cross_holdout():
    outcome_labels = labels()
    outcome_labels["label_available_at"] = "2020-02-01T00:00:00+09:00"
    outcome_labels["forward_return_20d"] = np.inf
    outcome_labels["forward_max_drawdown_20d"] = np.nan

    result = replay_crisis_windows(
        build_descriptive_signals(features()), outcome_labels,
        {"development": ("2020-01-01", "2020-01-31")},
        holdout_start="2020-02-01",
    )[0]

    assert result["status"] == "NO_COVERAGE"


@pytest.mark.parametrize(
    "invalid", [None, "2020-01-31T15:30:00", "2020-01-31Q15:30:00+09:00"],
)
def test_crisis_replay_rejects_invalid_development_availability_clock(invalid):
    outcome_labels = labels()
    outcome_labels.loc[outcome_labels.index[0], "label_available_at"] = invalid

    with pytest.raises(ValueError, match="label label_available_at clock"):
        replay_crisis_windows(
            build_descriptive_signals(features()), outcome_labels,
            {"development": ("2020-01-01", "2020-01-31")},
            holdout_start="2020-02-01",
        )


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_crisis_replay_requires_exactly_one_development_availability_field(
    mutation,
):
    outcome_labels = labels()
    if mutation == "missing":
        outcome_labels = outcome_labels.drop(columns="label_available_at")
    else:
        outcome_labels = pd.concat(
            [outcome_labels, outcome_labels.loc[:, ["label_available_at"]]],
            axis=1,
        )

    with pytest.raises(
        ValueError, match="label availability schema/content is invalid",
    ):
        replay_crisis_windows(
            build_descriptive_signals(features()), outcome_labels,
            {"development": ("2020-01-01", "2020-01-31")},
            holdout_start="2020-02-01",
        )


def test_crisis_replay_rejects_invalid_eligible_outcome_without_dropping_row():
    outcome_labels = labels()
    outcome_labels["label_available_at"] = "2020-02-01T00:00:00+09:00"
    outcome_labels.loc[outcome_labels.index[0], "label_available_at"] = (
        "2020-01-31T15:30:00+09:00"
    )
    outcome_labels.loc[outcome_labels.index[0], "forward_return_20d"] = np.inf

    with pytest.raises(
        ValueError, match="label forward_return_20d must be real numeric",
    ):
        replay_crisis_windows(
            build_descriptive_signals(features()), outcome_labels,
            {"development": ("2020-01-01", "2020-01-31")},
            holdout_start="2020-02-01",
        )


@pytest.mark.parametrize("artifact", ["signal", "label"])
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("ticker", "9999"),
        ("date_semantics", "UNVERIFIED_INTRADAY"),
    ],
)
def test_crisis_replay_never_validates_holdout_only_source_identity(
    artifact, column, value,
):
    boundary = "2020-01-08"
    windows = {
        "development": ("2020-01-01", "2020-01-07"),
        "holdout": (boundary, "2020-12-31"),
    }
    signal_rows = build_descriptive_signals(features())
    outcome_labels = labels()
    outcome_labels.loc[
        outcome_labels["observation_date"].lt("2020-01-07"),
        "label_available_at",
    ] = "2020-01-07T15:30:00+09:00"
    expected = replay_crisis_windows(
        signal_rows, outcome_labels, windows, holdout_start=boundary,
    )
    selected = signal_rows if artifact == "signal" else outcome_labels
    selected.loc[selected["observation_date"].ge(boundary), column] = value

    actual = replay_crisis_windows(
        signal_rows, outcome_labels, windows, holdout_start=boundary,
    )

    assert actual == expected
    assert actual[0]["status"] == "DIAGNOSTIC_ONLY"
    assert actual[0]["observations"] == 3
    assert actual[1] == {
        "event": "holdout", "start": boundary, "end": "2020-12-31",
        "status": "UNTOUCHED_HOLDOUT", "holdout_observations_excluded": "NOT_INSPECTED",
    }


def test_crisis_replay_requires_holdout_boundary_before_evaluation():
    with pytest.raises(TypeError, match="holdout_start"):
        replay_crisis_windows(pd.DataFrame(), pd.DataFrame())


@pytest.mark.parametrize(
    "holdout_start", [None, "", "2020-02-30", "2020-01-08T00:00:00+09:00"],
)
def test_crisis_replay_validates_holdout_boundary_before_frame_access(
    holdout_start,
):
    with pytest.raises(ValueError, match="holdout_start must be an ISO date"):
        replay_crisis_windows(
            pd.DataFrame(), pd.DataFrame(), holdout_start=holdout_start,
        )
