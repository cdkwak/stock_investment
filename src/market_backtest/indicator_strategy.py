"""Development-only threshold-band strategy wired to next-open execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from math import isfinite
import re

import numpy as np
import pandas as pd

from .execution import (
    NEXT_OPEN_V1,
    ExecutionAssumptions,
    ExecutionSimulation,
    simulate_next_open_execution,
)
from .holdout import CoverageHoldout
from .labels import LABEL_NAMESPACE


THRESHOLD_BAND_CONTRACT_VERSION = "predefined-threshold-band/v1"
THRESHOLD_BAND_STATUS = "DEVELOPMENT_ONLY_NO_PARAMETER_SELECTION"
MATCHED_HOLD_CONTRACT_VERSION = "threshold-band-matched-hold/v1"
MATCHED_HOLD_STATUS = "DEVELOPMENT_ONLY_NO_WINNER_SELECTION"

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, slots=True)
class ThresholdBandPolicy:
    policy_id: str
    indicator_column: str
    enter_at_or_below: float
    exit_at_or_above: float
    initial_long: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.policy_id) is not str
            or not self.policy_id
            or self.policy_id != self.policy_id.strip()
            or type(self.indicator_column) is not str
            or not self.indicator_column
            or self.indicator_column != self.indicator_column.strip()
            or self.indicator_column in LABEL_NAMESPACE
            or self.indicator_column.startswith(
                ("forward_", "future_", "label_", "outcome_")
            )
            or type(self.enter_at_or_below) is not float
            or type(self.exit_at_or_above) is not float
            or not isfinite(self.enter_at_or_below)
            or not isfinite(self.exit_at_or_above)
            or self.enter_at_or_below >= self.exit_at_or_above
            or self.initial_long is not False
        ):
            raise ValueError("threshold-band policy is invalid")


@dataclass(frozen=True, slots=True)
class ThresholdBandDecision:
    observation_date: str
    usable_from: str
    target_long: bool
    reason: str
    indicator_value: float


@dataclass(frozen=True, slots=True)
class ThresholdBandSimulation:
    contract_version: str
    status: str
    holdout_policy_id: str
    holdout_start: str
    policy: ThresholdBandPolicy
    decisions: tuple[ThresholdBandDecision, ...]
    execution: ExecutionSimulation


@dataclass(frozen=True, slots=True)
class MatchedHoldMetrics:
    strategy_ending_nav: float
    baseline_ending_nav: float
    ending_nav_difference: float
    strategy_total_return: float
    baseline_total_return: float
    total_return_difference: float
    strategy_annualized_volatility: float
    baseline_annualized_volatility: float
    annualized_volatility_difference: float
    strategy_max_drawdown: float
    baseline_max_drawdown: float
    strategy_total_turnover: float
    baseline_total_turnover: float
    strategy_transaction_cost: float
    baseline_transaction_cost: float
    incremental_transaction_cost: float


@dataclass(frozen=True, slots=True)
class MatchedHoldComparison:
    contract_version: str
    status: str
    availability: str
    entry_observation_date: str | None
    entry_usable_from: str | None
    winner_selected: bool
    strategy: ThresholdBandSimulation
    baseline: ExecutionSimulation | None
    metrics: MatchedHoldMetrics | None


def _date(value: object, *, artifact: str) -> str:
    if type(value) is not str or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{artifact} date must be canonical YYYY-MM-DD")
    return value


def _dates(series: pd.Series, *, artifact: str) -> tuple[str, ...]:
    output = tuple(_date(value, artifact=artifact) for value in series)
    if len(set(output)) != len(output) or any(
        current >= following for current, following in zip(output, output[1:])
    ):
        raise ValueError(f"{artifact} dates must be unique and sorted")
    return output


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


def _constant(series: pd.Series, *, artifact: str) -> str:
    values = series.tolist()
    if not values or any(
        type(value) is not str or not value or value != value.strip()
        for value in values
    ):
        raise ValueError(f"{artifact} identity is invalid")
    if any(value != values[0] for value in values):
        raise ValueError(f"{artifact} identity must be constant")
    return values[0]


def _indicator(series: pd.Series) -> tuple[float, ...]:
    if (
        pd.api.types.is_bool_dtype(series.dtype)
        or pd.api.types.is_complex_dtype(series.dtype)
        or not pd.api.types.is_numeric_dtype(series.dtype)
    ):
        raise ValueError("threshold-band indicator must be real numeric and finite")
    values = series.to_numpy(dtype="float64", na_value=np.nan)
    if not np.isfinite(values).all():
        raise ValueError("threshold-band indicator must be real numeric and finite")
    return tuple(float(value) for value in values)


def simulate_predefined_threshold_band(
    market: pd.DataFrame,
    features: pd.DataFrame,
    policy: ThresholdBandPolicy,
    holdout_policy: CoverageHoldout,
    assumptions: ExecutionAssumptions = NEXT_OPEN_V1,
) -> ThresholdBandSimulation:
    """Build sparse hysteresis decisions and execute them at next open."""
    if type(policy) is not ThresholdBandPolicy:
        raise ValueError("a typed threshold-band policy is required")
    ThresholdBandPolicy.__post_init__(policy)
    if type(holdout_policy) is not CoverageHoldout:
        raise ValueError("an untouched CoverageHoldout is required")
    if holdout_policy.results_reviewed is not False:
        raise ValueError("an untouched CoverageHoldout is required")
    holdout_start = _date(holdout_policy.holdout_start, artifact="holdout")
    coverage_start = _date(
        holdout_policy.coverage_start, artifact="holdout coverage",
    )
    coverage_end = _date(
        holdout_policy.coverage_end, artifact="holdout coverage",
    )
    counts = (
        holdout_policy.development_observations,
        holdout_policy.holdout_observations,
    )
    if (
        type(holdout_policy.policy_id) is not str
        or not holdout_policy.policy_id
        or holdout_policy.policy_id != holdout_policy.policy_id.strip()
        or any(type(value) is not int or value < 1 for value in counts)
        or not coverage_start < holdout_start <= coverage_end
    ):
        raise ValueError("holdout policy identity is invalid")

    _frame(
        market,
        required=frozenset({
            "session_date", "open", "close", "instrument_id", "currency",
        }),
        artifact="market",
    )
    _frame(
        features,
        required=frozenset({
            "observation_date", "instrument_id", "usable_from", "pit_status",
            policy.indicator_column,
        }),
        artifact="feature",
    )
    forbidden = {
        column for column in features.columns
        if column in LABEL_NAMESPACE
        or column.startswith(("forward_", "future_", "label_", "outcome_"))
    }
    if forbidden:
        raise ValueError(
            f"outcome namespace is forbidden in features: {sorted(forbidden)}"
        )
    market_dates = _dates(market["session_date"], artifact="market")
    feature_dates = _dates(features["observation_date"], artifact="feature")

    # Reject the sealed boundary before inspecting price or indicator values.
    scenario_dates = (*market_dates, *feature_dates)
    if any(
        value < coverage_start
        or value > coverage_end
        or value >= holdout_start
        for value in scenario_dates
    ):
        raise ValueError(
            "threshold-band inputs must stay within coverage before untouched holdout"
        )
    market_instrument = _constant(
        market["instrument_id"], artifact="market instrument_id",
    )
    feature_instrument = _constant(
        features["instrument_id"], artifact="feature instrument_id",
    )
    if market_instrument != feature_instrument:
        raise ValueError("market/feature instrument identity differs")
    if (
        not features["pit_status"].map(lambda value: type(value) is str).all()
        or not features["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()
    ):
        raise ValueError("features must be exact PIT_SAFE_EOD_T_PLUS_1")

    market_index = {value: index for index, value in enumerate(market_dates)}
    for feature_date, usable_from in zip(
        feature_dates, features["usable_from"], strict=True,
    ):
        index = market_index.get(feature_date)
        if index is None:
            raise ValueError("feature date must be an exact retained market session")
        if index == len(market_dates) - 1:
            raise ValueError("feature date has no next retained execution session")
        expected = f"{market_dates[index + 1]}T09:00:00+09:00"
        if usable_from != expected:
            raise ValueError(
                "feature usable_from must equal next retained market session"
            )

    values = _indicator(features[policy.indicator_column])
    long = policy.initial_long
    decisions: list[ThresholdBandDecision] = []
    for observation_date, usable_from, value in zip(
        feature_dates, features["usable_from"], values, strict=True,
    ):
        if not long and value <= policy.enter_at_or_below:
            long = True
            decisions.append(ThresholdBandDecision(
                observation_date=observation_date,
                usable_from=usable_from,
                target_long=True,
                reason="ENTER_AT_OR_BELOW",
                indicator_value=value,
            ))
        elif long and value >= policy.exit_at_or_above:
            long = False
            decisions.append(ThresholdBandDecision(
                observation_date=observation_date,
                usable_from=usable_from,
                target_long=False,
                reason="EXIT_AT_OR_ABOVE",
                indicator_value=value,
            ))

    decision_frame = pd.DataFrame({
        "decision_session": pd.Series(
            [row.observation_date for row in decisions], dtype="object",
        ),
        "target_long": pd.Series(
            [row.target_long for row in decisions], dtype="bool",
        ),
    })
    execution = simulate_next_open_execution(
        market, decision_frame, assumptions,
    )
    return ThresholdBandSimulation(
        contract_version=THRESHOLD_BAND_CONTRACT_VERSION,
        status=THRESHOLD_BAND_STATUS,
        holdout_policy_id=holdout_policy.policy_id,
        holdout_start=holdout_start,
        policy=policy,
        decisions=tuple(decisions),
        execution=execution,
    )


def compare_threshold_band_to_matched_hold(
    market: pd.DataFrame,
    features: pd.DataFrame,
    policy: ThresholdBandPolicy,
    holdout_policy: CoverageHoldout,
    assumptions: ExecutionAssumptions = NEXT_OPEN_V1,
) -> MatchedHoldComparison:
    """Compare with buy-and-hold beginning at the same first entry fill."""
    strategy = simulate_predefined_threshold_band(
        market, features, policy, holdout_policy, assumptions,
    )
    entry = next(
        (row for row in strategy.decisions if row.target_long), None,
    )
    if entry is None:
        return MatchedHoldComparison(
            contract_version=MATCHED_HOLD_CONTRACT_VERSION,
            status=MATCHED_HOLD_STATUS,
            availability="NO_ENTRY_OBSERVATION",
            entry_observation_date=None,
            entry_usable_from=None,
            winner_selected=False,
            strategy=strategy,
            baseline=None,
            metrics=None,
        )

    baseline_decision = pd.DataFrame({
        "decision_session": pd.Series(
            [entry.observation_date], dtype="object",
        ),
        "target_long": pd.Series([True], dtype="bool"),
    })
    baseline = simulate_next_open_execution(
        market, baseline_decision, assumptions,
    )
    if (
        baseline.instrument_id != strategy.execution.instrument_id
        or baseline.currency != strategy.execution.currency
        or baseline.assumptions != strategy.execution.assumptions
        or tuple(row.session_date for row in baseline.ledger)
        != tuple(row.session_date for row in strategy.execution.ledger)
    ):
        raise RuntimeError("matched-hold comparator identity differs")

    strategy_metrics = strategy.execution.metrics
    baseline_metrics = baseline.metrics
    metrics = MatchedHoldMetrics(
        strategy_ending_nav=strategy_metrics.ending_nav,
        baseline_ending_nav=baseline_metrics.ending_nav,
        ending_nav_difference=(
            strategy_metrics.ending_nav - baseline_metrics.ending_nav
        ),
        strategy_total_return=strategy_metrics.total_return,
        baseline_total_return=baseline_metrics.total_return,
        total_return_difference=(
            strategy_metrics.total_return - baseline_metrics.total_return
        ),
        strategy_annualized_volatility=(
            strategy_metrics.annualized_volatility
        ),
        baseline_annualized_volatility=(
            baseline_metrics.annualized_volatility
        ),
        annualized_volatility_difference=(
            strategy_metrics.annualized_volatility
            - baseline_metrics.annualized_volatility
        ),
        strategy_max_drawdown=strategy_metrics.max_drawdown,
        baseline_max_drawdown=baseline_metrics.max_drawdown,
        strategy_total_turnover=strategy_metrics.total_turnover,
        baseline_total_turnover=baseline_metrics.total_turnover,
        strategy_transaction_cost=strategy_metrics.transaction_cost_paid,
        baseline_transaction_cost=baseline_metrics.transaction_cost_paid,
        incremental_transaction_cost=(
            strategy_metrics.transaction_cost_paid
            - baseline_metrics.transaction_cost_paid
        ),
    )
    if any(
        not isfinite(value)
        for value in (
            metrics.strategy_ending_nav,
            metrics.baseline_ending_nav,
            metrics.ending_nav_difference,
            metrics.strategy_total_return,
            metrics.baseline_total_return,
            metrics.total_return_difference,
            metrics.strategy_annualized_volatility,
            metrics.baseline_annualized_volatility,
            metrics.annualized_volatility_difference,
            metrics.strategy_max_drawdown,
            metrics.baseline_max_drawdown,
            metrics.strategy_total_turnover,
            metrics.baseline_total_turnover,
            metrics.strategy_transaction_cost,
            metrics.baseline_transaction_cost,
            metrics.incremental_transaction_cost,
        )
    ):
        raise ValueError("matched-hold metrics must remain finite")
    return MatchedHoldComparison(
        contract_version=MATCHED_HOLD_CONTRACT_VERSION,
        status=MATCHED_HOLD_STATUS,
        availability="EVALUATED",
        entry_observation_date=entry.observation_date,
        entry_usable_from=entry.usable_from,
        winner_selected=False,
        strategy=strategy,
        baseline=baseline,
        metrics=metrics,
    )


__all__ = [
    "MATCHED_HOLD_CONTRACT_VERSION",
    "MATCHED_HOLD_STATUS",
    "MatchedHoldComparison",
    "MatchedHoldMetrics",
    "THRESHOLD_BAND_CONTRACT_VERSION",
    "THRESHOLD_BAND_STATUS",
    "ThresholdBandDecision",
    "ThresholdBandPolicy",
    "ThresholdBandSimulation",
    "compare_threshold_band_to_matched_hold",
    "simulate_predefined_threshold_band",
]
