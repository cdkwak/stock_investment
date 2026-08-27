"""Read-only GUI adapter for one accepted development-only RSI14 scenario."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date

import pandas as pd

from market_backtest.indicator_strategy import (
    MATCHED_HOLD_CONTRACT_VERSION,
    THRESHOLD_BAND_CONTRACT_VERSION,
    ThresholdBandPolicy,
    compare_threshold_band_to_matched_hold,
)
from market_backtest.indicator_study import (
    INDICATOR_STUDY_CONTRACT_VERSION,
    IndicatorCandidate,
    evaluate_predefined_indicators,
)
from market_backtest.holdout import CoverageHoldout
from market_backtest.portfolio import KOSPI200_FROZEN_HOLDOUT_V1


SCENARIO_ADAPTER_VERSION = "backtest-gui-scenario-adapter/v1"
SCENARIO_INPUT_VERSION = "backtest-gui-scenario-input/v1"
SCENARIO_ID = "RSI14_30_70"
SCENARIO_STATUS = "DEVELOPMENT_ONLY_FIXED_SCENARIO"

_MARKET_COLUMNS = (
    "session_date", "open", "close", "instrument_id", "currency",
)
_FEATURE_COLUMNS = (
    "observation_date", "ticker", "date_semantics", "instrument_id",
    "usable_from", "pit_status", "rsi_14",
)
_LABEL_COLUMNS = (
    "observation_date", "ticker", "date_semantics", "label_available_at",
    "label_version", "forward_return_20d", "forward_max_drawdown_20d",
)

_CANDIDATES = (
    IndicatorCandidate(
        "RSI14_LOW_30", "rsi_14", "LOW", 30.0, 20, 20,
    ),
    IndicatorCandidate(
        "RSI14_HIGH_70", "rsi_14", "HIGH", 70.0, 20, 20,
    ),
)
_POLICY = ThresholdBandPolicy(
    "RSI14_30_70", "rsi_14", 30.0, 70.0,
)


class BacktestScenarioError(ValueError):
    """Raised when the fixed GUI scenario boundary fails closed."""


@dataclass(frozen=True, slots=True)
class BacktestScenarioInputs:
    """Exact typed development inputs; frames are copied before evaluation."""

    contract_version: str
    scenario_id: str
    market: pd.DataFrame
    features: pd.DataFrame
    labels: pd.DataFrame
    holdout_policy: CoverageHoldout = KOSPI200_FROZEN_HOLDOUT_V1

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or type(self.scenario_id) is not str
            or type(self.market) is not pd.DataFrame
            or type(self.features) is not pd.DataFrame
            or type(self.labels) is not pd.DataFrame
            or type(self.holdout_policy) is not CoverageHoldout
        ):
            raise BacktestScenarioError("exact typed scenario input fields are required")


@dataclass(frozen=True, slots=True)
class ScenarioConditionalView:
    candidate_id: str
    direction: str
    threshold: float
    availability: str
    aligned_observations: int
    signal_observations: int
    signal_rate: float
    conditional_mean_return: float | None
    unconditional_mean_return: float
    mean_return_difference: float | None
    conditional_positive_rate: float | None
    conditional_mean_max_drawdown: float | None


@dataclass(frozen=True, slots=True)
class ScenarioExecutionView:
    contract_version: str
    execution_claim: str
    instrument_id: str
    currency: str
    observations: int
    trade_count: int
    ending_nav: float
    total_return: float
    annualized_volatility: float
    max_drawdown: float
    total_turnover: float
    average_long_exposure: float
    transaction_cost_paid: float


@dataclass(frozen=True, slots=True)
class ScenarioMatchedHoldView:
    contract_version: str
    availability: str
    entry_observation_date: str | None
    entry_usable_from: str | None
    ending_nav_difference: float | None
    total_return_difference: float | None
    annualized_volatility_difference: float | None
    max_drawdown_difference: float | None
    incremental_transaction_cost: float | None


@dataclass(frozen=True, slots=True)
class BacktestScenarioView:
    contract_version: str
    input_contract_version: str
    scenario_id: str
    status: str
    study_contract_version: str
    strategy_contract_version: str
    matched_hold_contract_version: str
    holdout_policy_id: str
    holdout_start: str
    results_reviewed: bool
    winner_selected: bool
    recommendation_provided: bool
    market_start: str
    market_end: str
    conditional: tuple[ScenarioConditionalView, ...]
    execution: ScenarioExecutionView
    matched_hold: ScenarioMatchedHoldView


def _exact_frame(
    value: object, *, columns: tuple[str, ...], artifact: str,
) -> pd.DataFrame:
    if type(value) is not pd.DataFrame or value.empty:
        raise BacktestScenarioError(f"{artifact} must be a non-empty exact DataFrame")
    if value.columns.has_duplicates or tuple(value.columns) != columns:
        raise BacktestScenarioError(f"{artifact} schema differs from the fixed input")
    return value


def _canonical_dates(
    frame: pd.DataFrame, column: str, *, artifact: str,
) -> tuple[str, ...]:
    output: list[str] = []
    for value in frame[column]:
        if type(value) is not str:
            raise BacktestScenarioError(f"{artifact} date key is invalid")
        try:
            parsed = calendar_date.fromisoformat(value)
        except ValueError as error:
            raise BacktestScenarioError(f"{artifact} date key is invalid") from error
        if parsed.isoformat() != value:
            raise BacktestScenarioError(f"{artifact} date key is invalid")
        output.append(value)
    dates = tuple(output)
    if len(set(dates)) != len(dates) or any(
        left >= right for left, right in zip(dates, dates[1:])
    ):
        raise BacktestScenarioError(
            f"{artifact} date keys must be unique and increasing"
        )
    return dates


class BacktestScenarioService:
    """Evaluate exactly one fixed scenario without I/O or parameter search."""

    def evaluate(self, inputs: BacktestScenarioInputs) -> BacktestScenarioView:
        if type(inputs) is not BacktestScenarioInputs:
            raise BacktestScenarioError("exact typed scenario inputs are required")
        if (
            inputs.contract_version != SCENARIO_INPUT_VERSION
            or inputs.scenario_id != SCENARIO_ID
            or inputs.holdout_policy != KOSPI200_FROZEN_HOLDOUT_V1
        ):
            raise BacktestScenarioError("scenario input identity differs")

        market = _exact_frame(
            inputs.market, columns=_MARKET_COLUMNS, artifact="market",
        )
        features = _exact_frame(
            inputs.features, columns=_FEATURE_COLUMNS, artifact="feature",
        )
        labels = _exact_frame(
            inputs.labels, columns=_LABEL_COLUMNS, artifact="label",
        )

        # Date keys are the only row values touched before the sealed boundary.
        # A holdout row therefore fails before identity, clocks, prices,
        # indicators, labels, outcomes, or metrics can be inspected.
        market_dates = _canonical_dates(
            market, "session_date", artifact="market",
        )
        feature_dates = _canonical_dates(
            features, "observation_date", artifact="feature",
        )
        label_dates = _canonical_dates(
            labels, "observation_date", artifact="label",
        )
        policy = KOSPI200_FROZEN_HOLDOUT_V1
        all_dates = (*market_dates, *feature_dates, *label_dates)
        if any(
            value < policy.coverage_start
            or value > policy.coverage_end
            or value >= policy.holdout_start
            for value in all_dates
        ):
            raise BacktestScenarioError(
                "scenario inputs cross the untouched holdout boundary"
            )

        # Accepted engines own identity, usable-clock, numeric, accounting and
        # matched-clock/cost validation. Copies prevent caller frame mutation.
        market_copy = market.copy(deep=True)
        feature_copy = features.copy(deep=True)
        label_copy = labels.copy(deep=True)
        try:
            study = evaluate_predefined_indicators(
                feature_copy, label_copy, _CANDIDATES, policy,
            )
            comparison = compare_threshold_band_to_matched_hold(
                market_copy, feature_copy, _POLICY, policy,
            )
        except (TypeError, ValueError, RuntimeError) as error:
            raise BacktestScenarioError(str(error)) from error

        if (
            study.contract_version != INDICATOR_STUDY_CONTRACT_VERSION
            or study.winner_selected is not False
            or comparison.contract_version != MATCHED_HOLD_CONTRACT_VERSION
            or comparison.winner_selected is not False
            or comparison.strategy.contract_version
            != THRESHOLD_BAND_CONTRACT_VERSION
        ):
            raise BacktestScenarioError("accepted scenario engine identity differs")

        conditional = tuple(
            ScenarioConditionalView(
                candidate_id=result.candidate.candidate_id,
                direction=result.candidate.direction,
                threshold=result.candidate.threshold,
                availability=result.availability,
                aligned_observations=result.metrics.aligned_observations,
                signal_observations=result.metrics.signal_observations,
                signal_rate=result.metrics.signal_rate,
                conditional_mean_return=result.metrics.conditional_mean_return,
                unconditional_mean_return=result.metrics.unconditional_mean_return,
                mean_return_difference=(
                    result.metrics.conditional_mean_return_difference
                ),
                conditional_positive_rate=(
                    result.metrics.conditional_positive_rate
                ),
                conditional_mean_max_drawdown=(
                    result.metrics.conditional_mean_max_drawdown
                ),
            )
            for result in study.results
        )
        execution = comparison.strategy.execution
        execution_metrics = execution.metrics
        execution_view = ScenarioExecutionView(
            contract_version=execution.contract_version,
            execution_claim=execution.execution_claim,
            instrument_id=execution.instrument_id,
            currency=execution.currency,
            observations=execution_metrics.observations,
            trade_count=execution_metrics.trade_count,
            ending_nav=execution_metrics.ending_nav,
            total_return=execution_metrics.total_return,
            annualized_volatility=execution_metrics.annualized_volatility,
            max_drawdown=execution_metrics.max_drawdown,
            total_turnover=execution_metrics.total_turnover,
            average_long_exposure=execution_metrics.average_long_exposure,
            transaction_cost_paid=execution_metrics.transaction_cost_paid,
        )
        matched_metrics = comparison.metrics
        matched_view = ScenarioMatchedHoldView(
            contract_version=comparison.contract_version,
            availability=comparison.availability,
            entry_observation_date=comparison.entry_observation_date,
            entry_usable_from=comparison.entry_usable_from,
            ending_nav_difference=(
                matched_metrics.ending_nav_difference if matched_metrics else None
            ),
            total_return_difference=(
                matched_metrics.total_return_difference if matched_metrics else None
            ),
            annualized_volatility_difference=(
                matched_metrics.annualized_volatility_difference
                if matched_metrics else None
            ),
            max_drawdown_difference=(
                matched_metrics.strategy_max_drawdown
                - matched_metrics.baseline_max_drawdown
                if matched_metrics else None
            ),
            incremental_transaction_cost=(
                matched_metrics.incremental_transaction_cost
                if matched_metrics else None
            ),
        )
        return BacktestScenarioView(
            contract_version=SCENARIO_ADAPTER_VERSION,
            input_contract_version=SCENARIO_INPUT_VERSION,
            scenario_id=SCENARIO_ID,
            status=SCENARIO_STATUS,
            study_contract_version=study.contract_version,
            strategy_contract_version=comparison.strategy.contract_version,
            matched_hold_contract_version=comparison.contract_version,
            holdout_policy_id=policy.policy_id,
            holdout_start=policy.holdout_start,
            results_reviewed=False,
            winner_selected=False,
            recommendation_provided=False,
            market_start=market_dates[0],
            market_end=market_dates[-1],
            conditional=conditional,
            execution=execution_view,
            matched_hold=matched_view,
        )


__all__ = [
    "SCENARIO_ADAPTER_VERSION",
    "SCENARIO_ID",
    "SCENARIO_INPUT_VERSION",
    "SCENARIO_STATUS",
    "BacktestScenarioError",
    "BacktestScenarioInputs",
    "BacktestScenarioService",
    "BacktestScenarioView",
    "ScenarioConditionalView",
    "ScenarioExecutionView",
    "ScenarioMatchedHoldView",
]
