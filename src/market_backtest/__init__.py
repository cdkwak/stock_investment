"""Deterministic offline backtest research contracts."""

from .labels import build_forward_labels
from .holdout import CoverageHoldout, define_untouched_holdout, development_only
from .walk_forward import WalkForwardSplit, expanding_walk_forward
from .ablation import FeatureFamilyStatus, build_ablation_plan
from .experiments import ExperimentRecord, serialize_experiment_registry
from .phase1_replay import (
    Phase1ArtifactReceipt,
    Phase1ReplayReceipt,
    Phase1ReplayRequest,
    run_phase1_replay,
)
from .portfolio import (
    CLOSE_PROXY_V1,
    INSTRUMENT_CLAIM,
    KOSPI200_FROZEN_HOLDOUT_V1,
    PORTFOLIO_STATUS,
    PortfolioAssumptions,
    PortfolioLedgerRow,
    PortfolioMetrics,
    PortfolioSimulation,
    simulate_kospi200_risk_off_portfolio,
)
from .execution import (
    EXECUTION_CLAIM,
    EXECUTION_CONTRACT_VERSION,
    EXECUTION_STATUS,
    NEXT_OPEN_V1,
    ExecutionAssumptions,
    ExecutionLedgerRow,
    ExecutionMetrics,
    ExecutionSimulation,
    simulate_next_open_execution,
)
from .indicator_study import (
    INDICATOR_STUDY_CONTRACT_VERSION,
    INDICATOR_STUDY_STATUS,
    IndicatorCandidate,
    IndicatorStudy,
    IndicatorStudyMetrics,
    IndicatorStudyResult,
    evaluate_predefined_indicators,
)
from .indicator_strategy import (
    MATCHED_HOLD_CONTRACT_VERSION,
    MATCHED_HOLD_STATUS,
    THRESHOLD_BAND_CONTRACT_VERSION,
    THRESHOLD_BAND_STATUS,
    MatchedHoldComparison,
    MatchedHoldMetrics,
    ThresholdBandDecision,
    ThresholdBandPolicy,
    ThresholdBandSimulation,
    compare_threshold_band_to_matched_hold,
    simulate_predefined_threshold_band,
)
from .indicator_replay import (
    INDICATOR_REPLAY_SCHEMA,
    INDICATOR_REPLAY_STATUS,
    IndicatorArtifactReceipt,
    IndicatorReplayError,
    IndicatorReplayReceipt,
    IndicatorReplayRequest,
    run_indicator_scenario_replay,
)
from .market_regime_validation import (
    MARKET_REGIME_HORIZONS,
    MARKET_REGIME_MAX_HORIZON,
    MARKET_REGIME_VALIDATION_STATUS,
    MARKET_REGIME_VALIDATION_VERSION,
    MarketRegimeCandidate,
    MarketRegimeCandidateResult,
    MarketRegimeHorizonMetrics,
    MarketRegimeValidationStudy,
    build_market_regime_labels,
    evaluate_predefined_market_regimes,
)

__all__ = [
    "CoverageHoldout", "WalkForwardSplit", "build_forward_labels",
    "define_untouched_holdout", "development_only", "expanding_walk_forward",
    "ExperimentRecord", "FeatureFamilyStatus", "build_ablation_plan",
    "serialize_experiment_registry", "CLOSE_PROXY_V1", "INSTRUMENT_CLAIM",
    "KOSPI200_FROZEN_HOLDOUT_V1", "PORTFOLIO_STATUS", "PortfolioAssumptions",
    "PortfolioLedgerRow", "PortfolioMetrics", "PortfolioSimulation",
    "simulate_kospi200_risk_off_portfolio", "Phase1ArtifactReceipt",
    "Phase1ReplayReceipt", "Phase1ReplayRequest", "run_phase1_replay",
    "EXECUTION_CLAIM", "EXECUTION_CONTRACT_VERSION", "EXECUTION_STATUS",
    "NEXT_OPEN_V1", "ExecutionAssumptions", "ExecutionLedgerRow",
    "ExecutionMetrics", "ExecutionSimulation", "simulate_next_open_execution",
    "INDICATOR_STUDY_CONTRACT_VERSION", "INDICATOR_STUDY_STATUS",
    "IndicatorCandidate", "IndicatorStudy", "IndicatorStudyMetrics",
    "IndicatorStudyResult", "evaluate_predefined_indicators",
    "THRESHOLD_BAND_CONTRACT_VERSION", "THRESHOLD_BAND_STATUS",
    "MATCHED_HOLD_CONTRACT_VERSION", "MATCHED_HOLD_STATUS",
    "MatchedHoldComparison", "MatchedHoldMetrics",
    "ThresholdBandDecision", "ThresholdBandPolicy",
    "ThresholdBandSimulation", "compare_threshold_band_to_matched_hold",
    "simulate_predefined_threshold_band",
    "INDICATOR_REPLAY_SCHEMA", "INDICATOR_REPLAY_STATUS",
    "IndicatorArtifactReceipt", "IndicatorReplayError",
    "IndicatorReplayReceipt", "IndicatorReplayRequest",
    "run_indicator_scenario_replay",
    "MARKET_REGIME_HORIZONS", "MARKET_REGIME_MAX_HORIZON",
    "MARKET_REGIME_VALIDATION_STATUS", "MARKET_REGIME_VALIDATION_VERSION",
    "MarketRegimeCandidate", "MarketRegimeCandidateResult",
    "MarketRegimeHorizonMetrics", "MarketRegimeValidationStudy",
    "build_market_regime_labels", "evaluate_predefined_market_regimes",
]
