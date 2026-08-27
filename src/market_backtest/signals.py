from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

import pandas as pd

from .walk_forward import expanding_walk_forward
from .labels import (
    MAX_LABEL_HORIZON_TRADING_DAYS,
    SOURCE_IDENTITY_COLUMNS,
    _validated_source_identity,
)


@dataclass(frozen=True)
class SignalThresholds:
    realized_volatility_20d: float = 0.25
    rolling_drawdown_60d: float = -0.10
    ma_distance_60d: float = -0.08
    return_20d: float = -0.05
    minimum_conditions: int = 2

    def __post_init__(self) -> None:
        values = (
            self.realized_volatility_20d, self.rolling_drawdown_60d,
            self.ma_distance_60d, self.return_20d,
        )
        if not np.isfinite(values).all() or not 1 <= self.minimum_conditions <= 4:
            raise ValueError("signal thresholds are invalid")


PREDEFINED_SMALL_GRID = (
    ("baseline_v1", SignalThresholds()),
    ("sensitive_v1", SignalThresholds(0.20, -0.08, -0.05, -0.03, 2)),
    ("strict_confirmation_v1", SignalThresholds(0.25, -0.10, -0.08, -0.05, 3)),
    ("deep_stress_v1", SignalThresholds(0.30, -0.15, -0.12, -0.08, 2)),
)

_REQUIRED_EVALUATION_OUTCOMES = (
    "forward_return_20d", "forward_max_drawdown_20d",
)
_OPTIONAL_EVALUATION_OUTCOMES = ("mae_20d", "mfe_20d")


def _average_precision(
    scores: pd.Series, adverse: pd.Series, observation_dates: pd.Series,
) -> float:
    numeric_scores = scores.to_numpy(dtype="float64")
    adverse_values = adverse.to_numpy(dtype="bool")
    date_values = observation_dates.to_numpy(dtype="str")
    if (
        len(numeric_scores) != len(adverse_values)
        or len(numeric_scores) != len(date_values)
        or not np.isfinite(numeric_scores).all()
    ):
        raise ValueError("average precision inputs are invalid")
    positives = int(adverse_values.sum())
    if positives == 0:
        return 0.0
    ordered = pd.DataFrame({
        "score": numeric_scores,
        "adverse": adverse_values,
        "observation_date": date_values,
    }).sort_values(
        ["score", "observation_date"], ascending=[False, True], kind="stable",
    )
    # The date key is the canonical secondary rank for equal discrete scores.
    # This preserves the established AP formula while removing row-order drift.
    cumulative = ordered["adverse"].cumsum()
    precision = cumulative / np.arange(1, len(ordered) + 1)
    return float(precision.loc[ordered["adverse"]].sum() / positives)


def _validate_observation_key(frame: pd.DataFrame, *, artifact: str) -> None:
    if "observation_date" not in frame.columns or frame.empty:
        raise ValueError(f"{artifact} observation_date key is missing or empty")
    raw_dates = frame["observation_date"]
    try:
        dates = pd.to_datetime(
            raw_dates, format="%Y-%m-%d", errors="raise",
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{artifact} observation_date key is invalid") from error
    canonical_dates = dates.dt.strftime("%Y-%m-%d")
    if (
        dates.isna().any()
        or not raw_dates.map(lambda value: isinstance(value, str)).all()
        or not raw_dates.eq(canonical_dates).all()
    ):
        raise ValueError(f"{artifact} observation_date key is invalid")
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError(
            f"{artifact} observation_date key must be non-empty, unique, and sorted"
        )


def _validate_signal_lineage(frame: pd.DataFrame, *, artifact: str) -> None:
    required = {"source_dataset", "source_contract_version"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError(f"{artifact} source lineage schema/content is invalid")
    datasets = frame["source_dataset"]
    contracts = frame["source_contract_version"]
    if (
        not datasets.map(
            lambda value: isinstance(value, str) and bool(value.strip())
        ).all()
        or datasets.nunique(dropna=False) != 1
        or not pd.api.types.is_integer_dtype(contracts.dtype)
        or contracts.isna().any()
        or contracts.nunique(dropna=False) != 1
        or int(contracts.iloc[0]) < 1
    ):
        raise ValueError(f"{artifact} source lineage must be exact and constant")


def _require_matching_source_identity(
    left: pd.DataFrame, labels: pd.DataFrame, *, left_artifact: str,
) -> None:
    left_identity = _validated_source_identity(left, artifact=left_artifact)
    label_identity = _validated_source_identity(labels, artifact="label")
    if left_identity != label_identity:
        raise ValueError(f"{left_artifact}/label source identity differs")


def _reject_label_namespace(frame: pd.DataFrame, *, artifact: str) -> None:
    forbidden = [
        column for column in frame.columns
        if isinstance(column, str) and (
            column.startswith(("forward_", "label_"))
            or column in _OPTIONAL_EVALUATION_OUTCOMES
        )
    ]
    if forbidden:
        raise ValueError(
            f"label namespace is forbidden in {artifact}: {sorted(forbidden)}"
        )


def _validate_signal_evaluation_artifact(signals: pd.DataFrame) -> None:
    required = {"risk_off_signal", "risk_score"}
    column_names = signals.columns.tolist()
    if (
        signals.empty
        or any(column_names.count(column) != 1 for column in required)
    ):
        raise ValueError("signal evaluation decision schema/content is invalid")
    decisions = signals["risk_off_signal"]
    if not pd.api.types.is_bool_dtype(decisions.dtype) or decisions.isna().any():
        raise ValueError("signal risk_off_signal must be non-null boolean")
    scores = signals["risk_score"]
    if (
        pd.api.types.is_bool_dtype(scores.dtype)
        or not pd.api.types.is_integer_dtype(scores.dtype)
        or scores.isna().any()
        or scores.lt(0).any()
        or scores.gt(4).any()
    ):
        raise ValueError("signal risk_score must be non-null integer in [0, 4]")


def _validated_aware_iso_clock(
    frame: pd.DataFrame, column: str, *, artifact: str,
) -> pd.Series:
    if frame.empty or column not in frame.columns:
        raise ValueError(f"{artifact} {column} clock schema/content is invalid")
    raw_values = frame[column]
    if raw_values.isna().any() or not raw_values.map(
        lambda value: isinstance(value, str) and bool(value.strip())
    ).all():
        raise ValueError(f"{artifact} {column} clock must be an aware ISO string")
    parsed: list[datetime] = []
    for value in raw_values:
        if len(value) <= 10 or value[10] != "T":
            raise ValueError(
                f"{artifact} {column} clock must be an aware ISO string"
            )
        try:
            clock = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{artifact} {column} clock must be an aware ISO string"
            ) from error
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError(f"{artifact} {column} clock must be an aware ISO string")
        parsed.append(clock)
    return pd.Series(pd.to_datetime(parsed, utc=True), index=frame.index)


def _validate_label_evaluation_artifact(labels: pd.DataFrame) -> None:
    required = {*_REQUIRED_EVALUATION_OUTCOMES, "label_available_at"}
    column_names = labels.columns.tolist()
    if (
        labels.empty
        or not required.issubset(labels.columns)
        or any(column_names.count(column) != 1 for column in required)
        or any(
            column_names.count(column) > 1
            for column in _OPTIONAL_EVALUATION_OUTCOMES
        )
    ):
        raise ValueError("label evaluation schema/content is invalid")
    outcomes = [
        *_REQUIRED_EVALUATION_OUTCOMES,
        *(column for column in _OPTIONAL_EVALUATION_OUTCOMES if column in labels),
    ]
    for column in outcomes:
        values = labels[column]
        if (
            pd.api.types.is_bool_dtype(values.dtype)
            or pd.api.types.is_complex_dtype(values.dtype)
            or not pd.api.types.is_numeric_dtype(values.dtype)
        ):
            raise ValueError(
                f"label {column} must be real numeric, finite, non-null, and at least -1"
            )
        numeric = values.to_numpy(dtype="float64", na_value=np.nan)
        if not np.isfinite(numeric).all() or (numeric < -1.0).any():
            raise ValueError(
                f"label {column} must be real numeric, finite, non-null, and at least -1"
            )
    _validated_aware_iso_clock(
        labels, "label_available_at", artifact="label",
    )


def _validate_decision_label_clock(
    decisions: pd.DataFrame, labels: pd.DataFrame, *, decision_artifact: str,
) -> None:
    decision_clock = _validated_aware_iso_clock(
        decisions, column="usable_from", artifact=decision_artifact,
    ).reset_index(drop=True)
    label_clock = _validated_aware_iso_clock(
        labels, column="label_available_at", artifact="label",
    ).reset_index(drop=True)
    if len(decision_clock) != len(label_clock) or not decision_clock.le(label_clock).all():
        raise ValueError("label availability precedes decision clock")


def build_descriptive_signals(features: pd.DataFrame,
                              thresholds: SignalThresholds = SignalThresholds()) -> pd.DataFrame:
    required = {
        "observation_date", "usable_from", "realized_volatility_20d",
        "rolling_drawdown_60d", "ma_distance_60d", "return_20d", "pit_status",
        "source_dataset", "source_contract_version", *SOURCE_IDENTITY_COLUMNS,
    }
    if not required.issubset(features.columns) or features.empty:
        raise ValueError("signal feature schema/content is invalid")
    _validate_observation_key(features, artifact="feature")
    _validated_source_identity(features, artifact="feature")
    _validate_signal_lineage(features, artifact="feature")
    _reject_label_namespace(features, artifact="signal input")
    if not features["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all():
        raise ValueError("signals require PIT-safe T+1 features")
    numeric = features[[
        "realized_volatility_20d", "rolling_drawdown_60d",
        "ma_distance_60d", "return_20d",
    ]].to_numpy(dtype="float64")
    if not np.isfinite(numeric).all():
        raise ValueError("signal features must be finite")
    output = features.loc[:, [
        "observation_date", *SOURCE_IDENTITY_COLUMNS, "usable_from",
        "source_dataset", "source_contract_version", "pit_status",
    ]].copy()
    output["high_realized_volatility"] = features["realized_volatility_20d"].ge(thresholds.realized_volatility_20d)
    output["large_drawdown"] = features["rolling_drawdown_60d"].le(thresholds.rolling_drawdown_60d)
    output["below_moving_average"] = features["ma_distance_60d"].le(thresholds.ma_distance_60d)
    output["negative_momentum"] = features["return_20d"].le(thresholds.return_20d)
    flags = ["high_realized_volatility", "large_drawdown", "below_moving_average", "negative_momentum"]
    output["risk_score"] = output[flags].sum(axis=1).astype("int64")
    output["risk_off_signal"] = output["risk_score"].ge(thresholds.minimum_conditions)
    output["signal_version"] = 1
    return output


def evaluate_signals(signals: pd.DataFrame, labels: pd.DataFrame) -> dict[str, float | int]:
    _validate_observation_key(signals, artifact="signal")
    _validate_observation_key(labels, artifact="label")
    _validate_signal_lineage(signals, artifact="signal")
    _require_matching_source_identity(signals, labels, left_artifact="signal")
    _reject_label_namespace(signals, artifact="signal artifact")
    _validated_aware_iso_clock(signals, "usable_from", artifact="signal")
    _validate_signal_evaluation_artifact(signals)
    _validate_label_evaluation_artifact(labels)
    if "pit_status" not in signals or not signals["pit_status"].eq(
        "PIT_SAFE_EOD_T_PLUS_1"
    ).all():
        raise ValueError("signal artifact requires exact PIT-safe T+1 metadata")
    joined = signals.merge(
        labels,
        on=["observation_date", *SOURCE_IDENTITY_COLUMNS],
        how="inner",
        validate="one_to_one",
    )
    if joined.empty:
        raise ValueError("signal/label overlap is empty")
    _validate_decision_label_clock(joined, joined, decision_artifact="signal")
    predicted = joined["risk_off_signal"]
    adverse = joined["forward_max_drawdown_20d"].le(-0.10)
    tp = int((predicted & adverse).sum()); fp = int((predicted & ~adverse).sum())
    fn = int((~predicted & adverse).sum()); tn = int((~predicted & ~adverse).sum())
    result = {
        "observations": len(joined), "true_positive": tp, "false_positive": fp,
        "false_negative": fn, "true_negative": tn,
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "event_prevalence": int(adverse.sum()) / len(joined),
        "pr_auc_average_precision": _average_precision(
            joined["risk_score"], adverse, joined["observation_date"],
        ),
        "mean_forward_return_20d": float(joined.loc[predicted, "forward_return_20d"].mean()) if predicted.any() else 0.0,
        "mean_forward_max_drawdown_20d": float(joined.loc[predicted, "forward_max_drawdown_20d"].mean()) if predicted.any() else 0.0,
    }
    for metric in ("mae_20d", "mfe_20d"):
        if metric in joined.columns:
            result[f"mean_{metric}"] = (
                float(joined.loc[predicted, metric].mean()) if predicted.any() else 0.0
            )
    return result


def evaluate_predefined_small_grid(
    features: pd.DataFrame, labels: pd.DataFrame,
) -> tuple[dict[str, object], ...]:
    """Evaluate a frozen comparison grid; never selects a winner or touches holdout data."""
    results = []
    for baseline_id, thresholds in PREDEFINED_SMALL_GRID:
        signals = build_descriptive_signals(features, thresholds)
        results.append({
            "baseline_id": baseline_id,
            "thresholds": thresholds,
            "metrics": evaluate_signals(signals, labels),
        })
    return tuple(results)


def evaluate_predefined_walk_forward(
    features: pd.DataFrame, labels: pd.DataFrame, *, minimum_train: int = 2520,
    test_size: int = 252, purge: int = 60, embargo: int = 5,
) -> tuple[dict[str, object], ...]:
    """Evaluate only purged/embargoed development test slices of the frozen grid."""
    if purge < MAX_LABEL_HORIZON_TRADING_DAYS:
        raise ValueError("purge must cover the maximum label horizon")
    _validate_observation_key(features, artifact="feature")
    _validate_observation_key(labels, artifact="label")
    _validate_signal_lineage(features, artifact="feature")
    _require_matching_source_identity(features, labels, left_artifact="feature")
    _reject_label_namespace(features, artifact="signal input")
    _validated_aware_iso_clock(features, "usable_from", artifact="feature")
    _validate_label_evaluation_artifact(labels)
    label_dates = set(labels["observation_date"])
    aligned_features = features.loc[
        features["observation_date"].isin(label_dates)
    ].reset_index(drop=True)
    aligned_labels = labels.loc[
        labels["observation_date"].isin(set(aligned_features["observation_date"]))
    ].reset_index(drop=True)
    if not aligned_features["observation_date"].equals(aligned_labels["observation_date"]):
        raise ValueError("walk-forward feature/label alignment differs")
    _validate_decision_label_clock(
        aligned_features, aligned_labels, decision_artifact="feature",
    )
    splits = expanding_walk_forward(
        observations=len(aligned_features), minimum_train=minimum_train,
        test_size=test_size, purge=purge, embargo=embargo,
    )
    results = []
    for baseline_id, thresholds in PREDEFINED_SMALL_GRID:
        signals = build_descriptive_signals(aligned_features, thresholds)
        test_signals = pd.concat(
            [signals.iloc[split.test_start:split.test_end] for split in splits],
            ignore_index=True,
        )
        test_labels = pd.concat(
            [aligned_labels.iloc[split.test_start:split.test_end] for split in splits],
            ignore_index=True,
        )
        results.append({
            "baseline_id": baseline_id, "thresholds": thresholds,
            "folds": len(splits), "test_observations": len(test_signals),
            "metrics": evaluate_signals(test_signals, test_labels),
        })
    return tuple(results)


__all__ = [
    "PREDEFINED_SMALL_GRID", "SignalThresholds", "build_descriptive_signals",
    "evaluate_predefined_small_grid", "evaluate_predefined_walk_forward",
    "evaluate_signals",
]
