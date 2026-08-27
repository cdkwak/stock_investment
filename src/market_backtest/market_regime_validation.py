"""Purged development-only validation for predefined three-axis market regimes.

The module is pure and provider-free.  It never selects a winner, inspects an
untouched holdout, or turns missing valuation/earnings evidence into a score.
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
from .walk_forward import expanding_walk_forward


MARKET_REGIME_VALIDATION_VERSION = "market-regime-validation/v1"
MARKET_REGIME_VALIDATION_STATUS = "DEVELOPMENT_ONLY_NO_WINNER_SELECTION"
MARKET_REGIME_HORIZONS = (63, 126, 252)
MARKET_REGIME_MAX_HORIZON = max(MARKET_REGIME_HORIZONS)

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_MISSING_STATES = frozenset({"UNKNOWN", "UNAVAILABLE", "MISSING", "N_A", "NA"})
_IDENTITY = ("ticker", "date_semantics")
_AXES = ("price_axis_state", "valuation_axis_state", "earnings_axis_state")
_AXIS_PIT = (
    "price_axis_pit_status", "valuation_axis_pit_status",
    "earnings_axis_pit_status",
)
_OUTCOME_PREFIXES = ("forward_", "future_", "label_", "outcome_")


def _complete_state(value: object) -> bool:
    return bool(
        type(value) is str
        and _TOKEN.fullmatch(value) is not None
        and value not in _MISSING_STATES
        and not any(
            marker in value
            for marker in (
                "BLOCKED", "LIMITED", "UNAVAILABLE", "UNSUPPORTED",
                "MISSING", "UNKNOWN",
            )
        )
    )


@dataclass(frozen=True, slots=True)
class MarketRegimeCandidate:
    candidate_id: str
    price_axis_state: str
    valuation_axis_state: str
    earnings_axis_state: str
    minimum_observations: int = 20

    def __post_init__(self) -> None:
        values = (
            self.candidate_id, self.price_axis_state,
            self.valuation_axis_state, self.earnings_axis_state,
        )
        if (
            any(type(value) is not str or _TOKEN.fullmatch(value) is None for value in values)
            or any(not _complete_state(value) for value in values[1:])
            or type(self.minimum_observations) is not int
            or self.minimum_observations < 1
        ):
            raise ValueError("market regime candidate is invalid")


@dataclass(frozen=True, slots=True)
class MarketRegimeHorizonMetrics:
    horizon_sessions: int
    signal_observations: int
    conditional_mean_return: float | None
    conditional_median_return: float | None
    conditional_positive_rate: float | None
    conditional_mean_max_drawdown: float | None
    unconditional_mean_return: float
    unconditional_median_return: float
    unconditional_positive_rate: float
    unconditional_mean_max_drawdown: float
    conditional_mean_return_difference: float | None
    conditional_mean_max_drawdown_difference: float | None


@dataclass(frozen=True, slots=True)
class MarketRegimeCandidateResult:
    candidate: MarketRegimeCandidate
    availability: str
    test_observations: int
    signal_observations: int
    signal_rate: float
    horizons: tuple[MarketRegimeHorizonMetrics, ...]


@dataclass(frozen=True, slots=True)
class MarketRegimeValidationStudy:
    contract_version: str
    status: str
    holdout_policy_id: str
    holdout_start: str
    ticker: str
    date_semantics: str
    folds: int
    test_observations: int
    purge_sessions: int
    embargo_sessions: int
    horizons: tuple[int, ...]
    winner_selected: bool
    results: tuple[MarketRegimeCandidateResult, ...]


def _canonical_date(value: object, *, artifact: str) -> str:
    if type(value) is not str or _DATE.fullmatch(value) is None:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD")
    return value


def _ordered_dates(values: pd.Series, *, artifact: str) -> tuple[str, ...]:
    dates = tuple(_canonical_date(value, artifact=artifact) for value in values)
    if not dates or len(set(dates)) != len(dates) or any(
        left >= right for left, right in zip(dates, dates[1:])
    ):
        raise ValueError(f"{artifact} dates must be nonempty, unique, and sorted")
    return dates


def _aware_timestamp(value: object, *, artifact: str) -> datetime:
    if type(value) is not str or "T" not in value:
        raise ValueError(f"{artifact} timestamp must be timezone-aware ISO text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{artifact} timestamp must be timezone-aware ISO text") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{artifact} timestamp must be timezone-aware ISO text")
    return parsed


def _frame(value: object, *, required: frozenset[str], artifact: str) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        raise ValueError(f"{artifact} schema/content is invalid")
    if (
        value.columns.has_duplicates
        or any(type(column) is not str or not column for column in value.columns)
        or not required.issubset(value.columns)
    ):
        raise ValueError(f"{artifact} schema/content is invalid")
    return value


def _identity(frame: pd.DataFrame, *, artifact: str) -> tuple[str, str]:
    output: list[str] = []
    for column in _IDENTITY:
        values = frame[column].tolist()
        if not values or any(
            type(value) is not str or not value or value != value.strip()
            for value in values
        ) or any(value != values[0] for value in values):
            raise ValueError(f"{artifact} identity is invalid")
        output.append(values[0])
    return output[0], output[1]


def _holdout(policy: CoverageHoldout) -> tuple[str, str]:
    if type(policy) is not CoverageHoldout or policy.results_reviewed is not False:
        raise ValueError("an untouched CoverageHoldout is required")
    policy_id = policy.policy_id
    if type(policy_id) is not str or not policy_id or policy_id != policy_id.strip():
        raise ValueError("holdout policy identity is invalid")
    start = _canonical_date(policy.holdout_start, artifact="holdout")
    coverage_start = _canonical_date(policy.coverage_start, artifact="holdout")
    coverage_end = _canonical_date(policy.coverage_end, artifact="holdout")
    if (
        not coverage_start < start <= coverage_end
        or type(policy.development_observations) is not int
        or type(policy.holdout_observations) is not int
        or min(policy.development_observations, policy.holdout_observations) < 1
    ):
        raise ValueError("holdout policy coverage is invalid")
    return policy_id, start


def _development_dates(
    frame: pd.DataFrame, *, artifact: str, holdout_start: str,
) -> tuple[str, ...]:
    dates = _ordered_dates(frame["observation_date"], artifact=artifact)
    if any(value >= holdout_start for value in dates):
        raise ValueError(f"{artifact} crosses the untouched holdout")
    return dates


def _finite_numeric(series: pd.Series, *, artifact: str) -> np.ndarray:
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
    return float(fsum(float(value) for value in values) / len(values))


def build_market_regime_labels(
    source: pd.DataFrame, holdout: CoverageHoldout,
) -> pd.DataFrame:
    """Build outcome-only 63/126/252-session returns and path drawdowns."""
    _policy_id, holdout_start = _holdout(holdout)
    source = _frame(
        source,
        required=frozenset({"observation_date", "close", *_IDENTITY}),
        artifact="market regime label source",
    )
    dates = _development_dates(
        source, artifact="market regime label source", holdout_start=holdout_start,
    )
    ticker, semantics = _identity(source, artifact="market regime label source")
    close = _finite_numeric(source["close"], artifact="market regime close")
    if (close <= 0.0).any() or len(close) <= MARKET_REGIME_MAX_HORIZON:
        raise ValueError("market regime close coverage is insufficient or nonpositive")

    rows = len(close) - MARKET_REGIME_MAX_HORIZON
    output = pd.DataFrame({
        "observation_date": dates[:rows],
        "ticker": ticker,
        "date_semantics": semantics,
    })
    for horizon in MARKET_REGIME_HORIZONS:
        returns: list[float] = []
        drawdowns: list[float] = []
        for index in range(rows):
            path = close[index:index + horizon + 1]
            returns.append(float(path[-1] / path[0] - 1.0))
            running_peak = np.maximum.accumulate(path)
            drawdowns.append(float(np.min(path / running_peak - 1.0)))
        output[f"forward_return_{horizon}d"] = returns
        output[f"forward_max_drawdown_{horizon}d"] = drawdowns
    availability_dates = dates[MARKET_REGIME_MAX_HORIZON:]
    output["label_available_at"] = [
        f"{value}T15:30:00+09:00" for value in availability_dates
    ]
    output["label_version"] = 1
    return output


def _validated_candidates(
    candidates: tuple[MarketRegimeCandidate, ...],
) -> tuple[MarketRegimeCandidate, ...]:
    if (
        type(candidates) is not tuple or not candidates or len(candidates) > 64
        or any(type(candidate) is not MarketRegimeCandidate for candidate in candidates)
    ):
        raise ValueError("predefined market regime candidates are invalid")
    for candidate in candidates:
        MarketRegimeCandidate.__post_init__(candidate)
    ids = tuple(candidate.candidate_id for candidate in candidates)
    states = tuple(
        (candidate.price_axis_state, candidate.valuation_axis_state, candidate.earnings_axis_state)
        for candidate in candidates
    )
    if len(set(ids)) != len(ids) or len(set(states)) != len(states):
        raise ValueError("predefined market regime candidates must be unique")
    return candidates


def _state_values(frame: pd.DataFrame) -> None:
    for column in _AXES:
        values = frame[column].tolist()
        if any(
            not _complete_state(value)
            for value in values
        ):
            raise ValueError(f"{column} must contain complete typed states")
    for column in _AXIS_PIT:
        if not frame[column].map(
            lambda value: type(value) is str and value == "PIT_SAFE_EOD_T_PLUS_1"
        ).all():
            raise ValueError(f"{column} requires exact PIT-safe status")


def _metrics(
    returns: np.ndarray, drawdowns: np.ndarray, mask: np.ndarray,
    *, horizon: int, minimum: int,
) -> MarketRegimeHorizonMetrics:
    baseline_return = _mean(returns)
    baseline_drawdown = _mean(drawdowns)
    count = int(mask.sum())
    if count < minimum:
        conditional = (None, None, None, None, None, None)
    else:
        selected_return = returns[mask]
        selected_drawdown = drawdowns[mask]
        mean_return = _mean(selected_return)
        mean_drawdown = _mean(selected_drawdown)
        conditional = (
            mean_return,
            float(np.median(selected_return)),
            float(np.mean(selected_return > 0.0)),
            mean_drawdown,
            mean_return - baseline_return,
            mean_drawdown - baseline_drawdown,
        )
    return MarketRegimeHorizonMetrics(
        horizon_sessions=horizon,
        signal_observations=count,
        conditional_mean_return=conditional[0],
        conditional_median_return=conditional[1],
        conditional_positive_rate=conditional[2],
        conditional_mean_max_drawdown=conditional[3],
        unconditional_mean_return=baseline_return,
        unconditional_median_return=float(np.median(returns)),
        unconditional_positive_rate=float(np.mean(returns > 0.0)),
        unconditional_mean_max_drawdown=baseline_drawdown,
        conditional_mean_return_difference=conditional[4],
        conditional_mean_max_drawdown_difference=conditional[5],
    )


def evaluate_predefined_market_regimes(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    candidates: tuple[MarketRegimeCandidate, ...],
    holdout: CoverageHoldout,
    *,
    minimum_train: int = 1260,
    test_size: int = 252,
    purge: int = MARKET_REGIME_MAX_HORIZON,
    embargo: int = 5,
) -> MarketRegimeValidationStudy:
    """Evaluate fixed complete three-axis states on purged development folds."""
    policy_id, holdout_start = _holdout(holdout)
    candidates = _validated_candidates(candidates)
    feature_required = frozenset({
        "observation_date", *_IDENTITY, "usable_from", "pit_status",
        *_AXES, *_AXIS_PIT,
    })
    label_required = frozenset({
        "observation_date", *_IDENTITY, "label_available_at", "label_version",
        *(f"forward_return_{horizon}d" for horizon in MARKET_REGIME_HORIZONS),
        *(f"forward_max_drawdown_{horizon}d" for horizon in MARKET_REGIME_HORIZONS),
    })
    features = _frame(features, required=feature_required, artifact="market regime features")
    labels = _frame(labels, required=label_required, artifact="market regime labels")
    feature_dates = _development_dates(
        features, artifact="market regime features", holdout_start=holdout_start,
    )
    label_dates = _development_dates(
        labels, artifact="market regime labels", holdout_start=holdout_start,
    )
    if any(column.startswith(_OUTCOME_PREFIXES) for column in features.columns):
        raise ValueError("market regime features contain an outcome namespace")
    feature_identity = _identity(features, artifact="market regime features")
    if feature_identity != _identity(labels, artifact="market regime labels"):
        raise ValueError("market regime feature/label identity differs")
    if not features["pit_status"].map(
        lambda value: type(value) is str and value == "PIT_SAFE_EOD_T_PLUS_1"
    ).all():
        raise ValueError("market regime features require exact PIT-safe status")
    usable = tuple(
        _aware_timestamp(value, artifact="feature usable_from")
        for value in features["usable_from"]
    )
    if any(
        value.date() <= calendar_date.fromisoformat(day)
        for day, value in zip(feature_dates, usable)
    ):
        raise ValueError("market regime feature clock is not T+1")
    available = tuple(
        _aware_timestamp(value, artifact="label available_at")
        for value in labels["label_available_at"]
    )
    if any(value.date().isoformat() >= holdout_start for value in available):
        raise ValueError("market regime label availability crosses the holdout")
    if not labels["label_version"].map(
        lambda value: type(value) is int and value == 1
    ).all():
        raise ValueError("market regime label version differs")

    feature_index = {value: index for index, value in enumerate(feature_dates)}
    for label_date, label_available in zip(label_dates, available):
        source_index = feature_index.get(label_date)
        if (
            source_index is None
            or source_index + MARKET_REGIME_MAX_HORIZON >= len(feature_dates)
        ):
            raise ValueError("market regime label lacks the full retained horizon")
        expected_end = feature_dates[source_index + MARKET_REGIME_MAX_HORIZON]
        expected_available = datetime.fromisoformat(
            f"{expected_end}T15:30:00+09:00"
        )
        if label_available != expected_available:
            raise ValueError(
                "market regime label availability differs from the 252nd retained session"
            )
        expected_decision_date = feature_dates[source_index + 1]
        if usable[source_index] != datetime.fromisoformat(
            f"{expected_decision_date}T09:00:00+09:00"
        ):
            raise ValueError(
                "market regime feature usability differs from the next retained session"
            )

    common = set(feature_dates).intersection(label_dates)
    aligned_features = features.loc[features["observation_date"].isin(common)].reset_index(drop=True)
    aligned_labels = labels.loc[labels["observation_date"].isin(common)].reset_index(drop=True)
    if aligned_features.empty or not aligned_features["observation_date"].equals(
        aligned_labels["observation_date"]
    ):
        raise ValueError("market regime feature/label alignment differs")
    _state_values(aligned_features)
    numeric: dict[str, np.ndarray] = {}
    for horizon in MARKET_REGIME_HORIZONS:
        for prefix in ("forward_return", "forward_max_drawdown"):
            column = f"{prefix}_{horizon}d"
            numeric[column] = _finite_numeric(aligned_labels[column], artifact=column)
        if (numeric[f"forward_max_drawdown_{horizon}d"] > 1e-12).any():
            raise ValueError("market regime max drawdown must be nonpositive")
    if (
        type(minimum_train) is not int or type(test_size) is not int
        or type(purge) is not int or type(embargo) is not int
        or purge < MARKET_REGIME_MAX_HORIZON
    ):
        raise ValueError("market regime walk-forward policy is invalid")
    splits = expanding_walk_forward(
        observations=len(aligned_features), minimum_train=minimum_train,
        test_size=test_size, purge=purge, embargo=embargo,
    )
    for split in splits:
        latest_train_available = _aware_timestamp(
            aligned_labels["label_available_at"].iloc[split.train_end - 1],
            artifact="latest train label",
        )
        earliest_test_decision = _aware_timestamp(
            aligned_features["usable_from"].iloc[split.test_start],
            artifact="earliest test decision",
        )
        if latest_train_available > earliest_test_decision:
            raise ValueError("training label is unavailable at the test decision")
    test_indices = np.concatenate([
        np.arange(split.test_start, split.test_end, dtype="int64")
        for split in splits
    ])
    test_features = aligned_features.iloc[test_indices].reset_index(drop=True)
    test_numeric = {key: value[test_indices] for key, value in numeric.items()}
    results: list[MarketRegimeCandidateResult] = []
    for candidate in candidates:
        mask = (
            test_features["price_axis_state"].eq(candidate.price_axis_state)
            & test_features["valuation_axis_state"].eq(candidate.valuation_axis_state)
            & test_features["earnings_axis_state"].eq(candidate.earnings_axis_state)
        ).to_numpy(dtype="bool")
        count = int(mask.sum())
        horizons = tuple(
            _metrics(
                test_numeric[f"forward_return_{horizon}d"],
                test_numeric[f"forward_max_drawdown_{horizon}d"],
                mask, horizon=horizon, minimum=candidate.minimum_observations,
            )
            for horizon in MARKET_REGIME_HORIZONS
        )
        results.append(MarketRegimeCandidateResult(
            candidate=candidate,
            availability=(
                "EVALUATED" if count >= candidate.minimum_observations
                else "INSUFFICIENT_SIGNAL_OBSERVATIONS"
            ),
            test_observations=len(test_features),
            signal_observations=count,
            signal_rate=count / len(test_features),
            horizons=horizons,
        ))
    return MarketRegimeValidationStudy(
        contract_version=MARKET_REGIME_VALIDATION_VERSION,
        status=MARKET_REGIME_VALIDATION_STATUS,
        holdout_policy_id=policy_id,
        holdout_start=holdout_start,
        ticker=feature_identity[0],
        date_semantics=feature_identity[1],
        folds=len(splits),
        test_observations=len(test_features),
        purge_sessions=purge,
        embargo_sessions=embargo,
        horizons=MARKET_REGIME_HORIZONS,
        winner_selected=False,
        results=tuple(results),
    )


__all__ = [
    "MARKET_REGIME_HORIZONS", "MARKET_REGIME_MAX_HORIZON",
    "MARKET_REGIME_VALIDATION_STATUS", "MARKET_REGIME_VALIDATION_VERSION",
    "MarketRegimeCandidate", "MarketRegimeCandidateResult",
    "MarketRegimeHorizonMetrics", "MarketRegimeValidationStudy",
    "build_market_regime_labels", "evaluate_predefined_market_regimes",
]
