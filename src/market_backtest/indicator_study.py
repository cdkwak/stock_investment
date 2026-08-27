"""PIT-safe descriptive studies for predefined indicator candidates.

This module does not tune thresholds, rank candidates, or inspect a final
holdout.  It only summarizes already-defined indicator conditions against
outcome-only development labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from datetime import datetime
from math import fsum, isfinite
import re

import numpy as np
import pandas as pd

from .holdout import CoverageHoldout
from .labels import LABEL_HORIZONS_TRADING_DAYS, LABEL_NAMESPACE


INDICATOR_STUDY_CONTRACT_VERSION = "predefined-indicator-study/v1"
INDICATOR_STUDY_STATUS = "DEVELOPMENT_ONLY_NO_WINNER_SELECTION"

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_DIRECTIONS = frozenset({"LOW", "HIGH"})


@dataclass(frozen=True, slots=True)
class IndicatorCandidate:
    candidate_id: str
    indicator_column: str
    direction: str
    threshold: float
    horizon_sessions: int
    minimum_signal_observations: int = 20

    def __post_init__(self) -> None:
        if (
            type(self.candidate_id) is not str
            or not self.candidate_id
            or self.candidate_id != self.candidate_id.strip()
            or type(self.indicator_column) is not str
            or not self.indicator_column
            or self.indicator_column != self.indicator_column.strip()
            or self.indicator_column in LABEL_NAMESPACE
            or self.indicator_column.startswith(
                ("forward_", "future_", "label_", "outcome_")
            )
            or self.direction not in _DIRECTIONS
            or type(self.threshold) is not float
            or not isfinite(self.threshold)
            or type(self.horizon_sessions) is not int
            or self.horizon_sessions not in LABEL_HORIZONS_TRADING_DAYS
            or type(self.minimum_signal_observations) is not int
            or self.minimum_signal_observations < 1
        ):
            raise ValueError("indicator candidate is invalid")


@dataclass(frozen=True, slots=True)
class IndicatorStudyMetrics:
    aligned_observations: int
    signal_observations: int
    signal_rate: float
    conditional_mean_return: float | None
    conditional_median_return: float | None
    conditional_positive_rate: float | None
    conditional_mean_max_drawdown: float | None
    unconditional_mean_return: float
    unconditional_median_return: float
    unconditional_positive_rate: float
    unconditional_mean_max_drawdown: float
    conditional_mean_return_difference: float | None


@dataclass(frozen=True, slots=True)
class IndicatorStudyResult:
    candidate: IndicatorCandidate
    availability: str
    metrics: IndicatorStudyMetrics


@dataclass(frozen=True, slots=True)
class IndicatorStudy:
    contract_version: str
    status: str
    holdout_policy_id: str
    holdout_start: str
    ticker: str
    date_semantics: str
    winner_selected: bool
    results: tuple[IndicatorStudyResult, ...]


def _canonical_date(value: object, *, artifact: str) -> str:
    if type(value) is not str or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD")
    return value


def _ordered_dates(series: pd.Series, *, artifact: str) -> tuple[str, ...]:
    dates = tuple(_canonical_date(value, artifact=artifact) for value in series)
    if len(set(dates)) != len(dates) or any(
        current >= following for current, following in zip(dates, dates[1:])
    ):
        raise ValueError(f"{artifact} dates must be unique and sorted")
    return dates


def _aware_timestamp(value: object, *, artifact: str) -> datetime:
    if type(value) is not str or "T" not in value:
        raise ValueError(f"{artifact} timestamp must be timezone-aware ISO text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"{artifact} timestamp must be timezone-aware ISO text"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{artifact} timestamp must be timezone-aware ISO text")
    return parsed


def _frame(
    value: pd.DataFrame, *, required: frozenset[str], artifact: str,
) -> None:
    if not isinstance(value, pd.DataFrame) or value.empty:
        raise ValueError(f"{artifact} schema/content is invalid")
    columns = value.columns.tolist()
    if (
        value.columns.has_duplicates
        or any(type(column) is not str or not column for column in columns)
        or not required.issubset(columns)
    ):
        raise ValueError(f"{artifact} schema/content is invalid")


def _identity(frame: pd.DataFrame, *, artifact: str) -> tuple[str, str]:
    output: list[str] = []
    for column in ("ticker", "date_semantics"):
        values = frame[column].tolist()
        if not values or any(
            type(value) is not str or not value or value != value.strip()
            for value in values
        ):
            raise ValueError(f"{artifact} {column} identity is invalid")
        if any(value != values[0] for value in values):
            raise ValueError(f"{artifact} {column} identity must be constant")
        output.append(values[0])
    return output[0], output[1]


def _holdout(policy: CoverageHoldout) -> tuple[str, str, str, str]:
    if type(policy) is not CoverageHoldout or policy.results_reviewed is not False:
        raise ValueError("an untouched CoverageHoldout is required")
    if (
        type(policy.policy_id) is not str
        or not policy.policy_id
        or policy.policy_id != policy.policy_id.strip()
    ):
        raise ValueError("holdout policy identity is invalid")
    start = _canonical_date(policy.holdout_start, artifact="holdout")
    coverage_start = _canonical_date(policy.coverage_start, artifact="holdout")
    coverage_end = _canonical_date(policy.coverage_end, artifact="holdout")
    counts = (policy.development_observations, policy.holdout_observations)
    if (
        any(type(value) is not int or value < 1 for value in counts)
        or not coverage_start < start <= coverage_end
    ):
        raise ValueError("holdout policy coverage is invalid")
    return policy.policy_id, start, coverage_start, coverage_end


def _candidate_set(
    candidates: tuple[IndicatorCandidate, ...], features: pd.DataFrame,
) -> tuple[IndicatorCandidate, ...]:
    if (
        type(candidates) is not tuple
        or not candidates
        or len(candidates) > 64
        or any(type(candidate) is not IndicatorCandidate for candidate in candidates)
    ):
        raise ValueError("predefined candidate set is invalid")
    for candidate in candidates:
        IndicatorCandidate.__post_init__(candidate)
    ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(set(ids)) != len(ids):
        raise ValueError("predefined candidate ids must be unique")
    missing = {
        candidate.indicator_column for candidate in candidates
        if candidate.indicator_column not in features.columns
    }
    if missing:
        raise ValueError(f"indicator columns are missing: {sorted(missing)}")
    return candidates


def _numeric(series: pd.Series, *, artifact: str) -> np.ndarray:
    if (
        pd.api.types.is_bool_dtype(series.dtype)
        or pd.api.types.is_complex_dtype(series.dtype)
        or not pd.api.types.is_numeric_dtype(series.dtype)
    ):
        raise ValueError(f"{artifact} must be real numeric and finite")
    values = series.to_numpy(dtype="float64", na_value=np.nan)
    if not np.isfinite(values).all():
        raise ValueError(f"{artifact} must be real numeric and finite")
    return values


def _mean(values: np.ndarray) -> float:
    try:
        value = fsum(float(item) for item in values) / len(values)
    except (OverflowError, ZeroDivisionError):
        raise ValueError("indicator study metrics must remain finite") from None
    if not isfinite(value):
        raise ValueError("indicator study metrics must remain finite")
    return value


def _median(values: np.ndarray) -> float:
    value = float(np.median(values))
    if not isfinite(value):
        raise ValueError("indicator study metrics must remain finite")
    return value


def _validated_metrics(metrics: IndicatorStudyMetrics) -> IndicatorStudyMetrics:
    values = (
        metrics.signal_rate,
        metrics.conditional_mean_return,
        metrics.conditional_median_return,
        metrics.conditional_positive_rate,
        metrics.conditional_mean_max_drawdown,
        metrics.unconditional_mean_return,
        metrics.unconditional_median_return,
        metrics.unconditional_positive_rate,
        metrics.unconditional_mean_max_drawdown,
        metrics.conditional_mean_return_difference,
    )
    if any(value is not None and not isfinite(value) for value in values):
        raise ValueError("indicator study metrics must remain finite")
    return metrics


def evaluate_predefined_indicators(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    candidates: tuple[IndicatorCandidate, ...],
    holdout_policy: CoverageHoldout,
) -> IndicatorStudy:
    """Return ordered descriptive studies without candidate selection."""
    _frame(
        features,
        required=frozenset({
            "observation_date", "ticker", "date_semantics", "usable_from",
            "pit_status",
        }),
        artifact="feature",
    )
    _frame(
        labels,
        required=frozenset({
            "observation_date", "ticker", "date_semantics",
            "label_available_at", "label_version",
        }),
        artifact="label",
    )
    forbidden_features = {
        column for column in features.columns
        if column in LABEL_NAMESPACE
        or column.startswith(("forward_", "future_", "label_", "outcome_"))
    }
    if forbidden_features:
        raise ValueError(
            f"outcome namespace is forbidden in features: {sorted(forbidden_features)}"
        )
    policy_id, holdout_start, coverage_start, coverage_end = _holdout(
        holdout_policy
    )
    feature_dates = _ordered_dates(
        features["observation_date"], artifact="feature",
    )
    label_dates = _ordered_dates(labels["observation_date"], artifact="label")

    # The holdout boundary is checked before any indicator or outcome numeric
    # value is inspected.
    study_dates = (*feature_dates, *label_dates)
    if any(
        value < coverage_start
        or value > coverage_end
        or value >= holdout_start
        for value in study_dates
    ):
        raise ValueError(
            "indicator study inputs must stay within coverage before untouched holdout"
        )
    feature_identity = _identity(features, artifact="feature")
    label_identity = _identity(labels, artifact="label")
    if feature_identity != label_identity:
        raise ValueError("feature/label source identity differs")
    if not set(label_dates).issubset(feature_dates):
        raise ValueError("each label must align to an exact retained feature date")

    usable_times = tuple(
        _aware_timestamp(value, artifact="feature usable_from")
        for value in features["usable_from"]
    )
    for observation_date, usable_at in zip(feature_dates, usable_times, strict=True):
        if usable_at.date().isoformat() <= observation_date:
            raise ValueError("feature usable_from must be after observation date")
    if (
        not features["pit_status"].map(lambda value: type(value) is str).all()
        or not features["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()
    ):
        raise ValueError("features must be exact PIT_SAFE_EOD_T_PLUS_1")
    feature_index = {value: index for index, value in enumerate(feature_dates)}
    for label_date in label_dates:
        index = feature_index[label_date]
        if index == len(feature_dates) - 1:
            raise ValueError("labelled feature has no next retained usable session")
        expected = f"{feature_dates[index + 1]}T09:00:00+09:00"
        if features["usable_from"].iloc[index] != expected:
            raise ValueError(
                "labelled feature usable_from must equal next retained session"
            )

    label_times = tuple(
        _aware_timestamp(value, artifact="label label_available_at")
        for value in labels["label_available_at"]
    )
    for observation_date, available_at in zip(label_dates, label_times, strict=True):
        if (
            available_at.date().isoformat() <= observation_date
            or available_at.date().isoformat() >= holdout_start
        ):
            raise ValueError(
                "label availability must be after observation and before holdout"
            )
    versions = labels["label_version"]
    if (
        pd.api.types.is_bool_dtype(versions.dtype)
        or not pd.api.types.is_integer_dtype(versions.dtype)
        or versions.isna().any()
        or not versions.eq(1).all()
    ):
        raise ValueError("label_version must equal integer 1")

    candidate_set = _candidate_set(candidates, features)
    aligned_indexes = np.asarray(
        [feature_index[value] for value in label_dates], dtype="int64",
    )
    results: list[IndicatorStudyResult] = []

    for candidate in candidate_set:
        indicator_all = _numeric(
            features[candidate.indicator_column],
            artifact=f"feature {candidate.indicator_column}",
        )
        indicator = indicator_all[aligned_indexes]
        return_column = f"forward_return_{candidate.horizon_sessions}d"
        drawdown_column = (
            f"forward_max_drawdown_{candidate.horizon_sessions}d"
        )
        if return_column not in labels.columns or drawdown_column not in labels.columns:
            raise ValueError("candidate outcome columns are missing from labels")
        returns = _numeric(labels[return_column], artifact=f"label {return_column}")
        drawdowns = _numeric(
            labels[drawdown_column], artifact=f"label {drawdown_column}",
        )
        mask = (
            indicator <= candidate.threshold
            if candidate.direction == "LOW"
            else indicator >= candidate.threshold
        )
        signal_count = int(mask.sum())
        enough = signal_count >= candidate.minimum_signal_observations
        conditional_returns = returns[mask]
        conditional_drawdowns = drawdowns[mask]
        unconditional_mean = _mean(returns)
        conditional_mean = _mean(conditional_returns) if enough else None
        metrics = _validated_metrics(IndicatorStudyMetrics(
            aligned_observations=len(labels),
            signal_observations=signal_count,
            signal_rate=float(signal_count / len(labels)),
            conditional_mean_return=conditional_mean,
            conditional_median_return=(
                _median(conditional_returns) if enough else None
            ),
            conditional_positive_rate=(
                float((conditional_returns > 0.0).mean()) if enough else None
            ),
            conditional_mean_max_drawdown=(
                _mean(conditional_drawdowns) if enough else None
            ),
            unconditional_mean_return=unconditional_mean,
            unconditional_median_return=_median(returns),
            unconditional_positive_rate=float((returns > 0.0).mean()),
            unconditional_mean_max_drawdown=_mean(drawdowns),
            conditional_mean_return_difference=(
                conditional_mean - unconditional_mean if enough else None
            ),
        ))
        results.append(IndicatorStudyResult(
            candidate=candidate,
            availability="EVALUATED" if enough else "INSUFFICIENT_SIGNAL_OBSERVATIONS",
            metrics=metrics,
        ))

    return IndicatorStudy(
        contract_version=INDICATOR_STUDY_CONTRACT_VERSION,
        status=INDICATOR_STUDY_STATUS,
        holdout_policy_id=policy_id,
        holdout_start=holdout_start,
        ticker=feature_identity[0],
        date_semantics=feature_identity[1],
        winner_selected=False,
        results=tuple(results),
    )


__all__ = [
    "INDICATOR_STUDY_CONTRACT_VERSION",
    "INDICATOR_STUDY_STATUS",
    "IndicatorCandidate",
    "IndicatorStudy",
    "IndicatorStudyMetrics",
    "IndicatorStudyResult",
    "evaluate_predefined_indicators",
]
