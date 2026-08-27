"""Fail-closed policies for BOK ECOS Korean Treasury observation and daily use.

Publication and revision timing for ECOS table 817Y002 remain unverified.  The
bounded finality-observation policy may collect immutable diagnostic Landing;
the daily dataset policy still requires explicit review and permits no automatic
expected date, promotion, scheduler, or Dashboard route.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from stock_data.contracts.bok_ecos_treasury import BOK_ECOS_TREASURY_TENORS
from stock_data.orchestration.expected_latest import (
    ExpectedLagPolicy,
    ObservationCalendar,
    ProviderAvailabilityPolicy,
    ProviderFinality,
)


DATASET_ID = "bok_ecos_kr_treasury_yield_source_observation"
SOURCE_OPERATION = "bok_ecos:StatisticSearch:817Y002"
CANONICAL_TENORS = BOK_ECOS_TREASURY_TENORS


class DailyPlanAction(StrEnum):
    NOOP_ALREADY_SUCCEEDED = "NOOP_ALREADY_SUCCEEDED"
    NOOP_ALREADY_RETAINED = "NOOP_ALREADY_RETAINED"
    COLLECT_EXACT_DATE = "COLLECT_EXACT_DATE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CHECKPOINT_CONFLICT = "CHECKPOINT_CONFLICT"


class ExactDateReview(StrEnum):
    NOT_CONFIRMED = "NOT_CONFIRMED"
    OPERATOR_REVIEWED = "OPERATOR_REVIEWED"


class FinalityObservationAction(StrEnum):
    OBSERVE_OR_REPLAY = "OBSERVE_OR_REPLAY"
    NOOP_OUTSIDE_WINDOW = "NOOP_OUTSIDE_WINDOW"
    NOOP_REVIEW_GATE_REACHED = "NOOP_REVIEW_GATE_REACHED"


@dataclass(frozen=True)
class BokEcosTreasuryDailyPolicy:
    dataset_id: str = DATASET_ID
    source_operation: str = SOURCE_OPERATION
    observation_calendar: ObservationCalendar = ObservationCalendar.PROVIDER_PUBLICATION
    provider_availability_policy: ProviderAvailabilityPolicy = (
        ProviderAvailabilityPolicy.MANUAL_OBSERVATION
    )
    expected_lag_policy: ExpectedLagPolicy = ExpectedLagPolicy.MANUAL
    finality_policy: ProviderFinality = ProviderFinality.UNKNOWN
    scopes: tuple[str, ...] = CANONICAL_TENORS
    max_data_calls: int = len(CANONICAL_TENORS)
    retry_budget: int = 0
    landing_first: bool = True
    atomic_promotion_scope: str = "ALL_SIX_TENORS_AND_CHECKPOINT"
    pre_network_replay_required: bool = True


@dataclass(frozen=True)
class BokEcosTreasuryFinalityObservationPolicy:
    source_operation: str = SOURCE_OPERATION
    scopes: tuple[str, ...] = CANONICAL_TENORS
    observation_calendar: ObservationCalendar = ObservationCalendar.PROVIDER_PUBLICATION
    max_statistic_search_calls_per_batch: int = len(CANONICAL_TENORS)
    retry_budget: int = 0
    landing_first: bool = True
    normalized_writes: int = 0
    official_ui_marker_separate: bool = True
    observation_window_kst: str = "17:00-18:00"
    required_batches_before_review: int = 3
    automatic_expected_latest: bool = False
    automatic_finality_claim: bool = False


@dataclass(frozen=True)
class BokEcosTreasuryDailyPlan:
    target_date: date
    action: DailyPlanAction
    max_data_calls: int
    retry_budget: int
    reason: str

    @property
    def pre_network_noop(self) -> bool:
        return self.max_data_calls == 0


@dataclass(frozen=True)
class BokEcosTreasuryFinalityOccurrencePlan:
    action: FinalityObservationAction
    observation_date_kst: str
    max_statistic_search_calls: int
    max_official_ui_calls: int
    reason: str

    @property
    def pre_network_noop(self) -> bool:
        return self.max_statistic_search_calls == self.max_official_ui_calls == 0


POLICY = BokEcosTreasuryDailyPolicy()
FINALITY_OBSERVATION_POLICY = BokEcosTreasuryFinalityObservationPolicy()


def plan_finality_observation_occurrence(
    *, observation_time: datetime, retained_batch_count: int,
) -> BokEcosTreasuryFinalityOccurrencePlan:
    """Plan one unattended evidence occurrence before credentials or network.

    The schedule is deliberately observation-only.  Reaching three batches
    stops further calls and leaves the evidence for semantic/finality review.
    """

    if observation_time.tzinfo is None or observation_time.utcoffset() is None:
        raise ValueError("observation_time must be timezone-aware")
    if (
        not isinstance(retained_batch_count, int)
        or isinstance(retained_batch_count, bool)
        or retained_batch_count < 0
    ):
        raise ValueError("retained_batch_count must be a non-negative integer")
    observed = observation_time.astimezone(ZoneInfo("Asia/Seoul"))
    observed_date = observed.strftime("%Y%m%d")
    if retained_batch_count >= FINALITY_OBSERVATION_POLICY.required_batches_before_review:
        return BokEcosTreasuryFinalityOccurrencePlan(
            FinalityObservationAction.NOOP_REVIEW_GATE_REACHED,
            observed_date, 0, 0, "THREE_BATCH_REVIEW_GATE_REACHED",
        )
    if not 17 <= observed.hour < 18:
        return BokEcosTreasuryFinalityOccurrencePlan(
            FinalityObservationAction.NOOP_OUTSIDE_WINDOW,
            observed_date, 0, 0, "OUTSIDE_1700_1800_KST_OBSERVATION_WINDOW",
        )
    return BokEcosTreasuryFinalityOccurrencePlan(
        FinalityObservationAction.OBSERVE_OR_REPLAY,
        observed_date,
        FINALITY_OBSERVATION_POLICY.max_statistic_search_calls_per_batch,
        1,
        "BOUNDED_FINALITY_OBSERVATION_OR_SAME_DATE_REPLAY",
    )


def plan_daily_operation(
    *,
    target_date: date,
    retained_latest: date | None,
    checkpoint: Mapping[str, object] | None = None,
    exact_date_review: ExactDateReview = ExactDateReview.NOT_CONFIRMED,
) -> BokEcosTreasuryDailyPlan:
    """Return a deterministic, pre-network decision for one exact date.

    A successful checkpoint is not trusted by itself: the retained dataset
    must also contain the target.  This prevents a missing/rolled-back target
    from being hidden by stale state.
    """
    checkpoint = checkpoint or {}
    accepted_scopes = checkpoint.get("accepted_scopes", ())
    accepted_scopes = accepted_scopes if isinstance(accepted_scopes, (list, tuple)) else ()
    checkpoint_succeeded = (
        checkpoint.get("dataset") == DATASET_ID
        and checkpoint.get("target_date") == target_date.isoformat()
        and checkpoint.get("status") == "SUCCEEDED"
        and tuple(accepted_scopes) == CANONICAL_TENORS
    )
    retained = retained_latest is not None and retained_latest >= target_date
    if checkpoint_succeeded and not retained:
        return BokEcosTreasuryDailyPlan(
            target_date, DailyPlanAction.CHECKPOINT_CONFLICT, 0, 0,
            "SUCCEEDED_CHECKPOINT_WITHOUT_RETAINED_TARGET",
        )
    if checkpoint_succeeded and retained:
        return BokEcosTreasuryDailyPlan(
            target_date, DailyPlanAction.NOOP_ALREADY_SUCCEEDED, 0, 0,
            "RETAINED_TARGET_AND_COMPLETE_CHECKPOINT_MATCH",
        )
    if retained:
        return BokEcosTreasuryDailyPlan(
            target_date, DailyPlanAction.NOOP_ALREADY_RETAINED, 0, 0,
            "TARGET_ALREADY_PRESENT_WITHOUT_COMPLETE_DAILY_CHECKPOINT",
        )
    if exact_date_review is not ExactDateReview.OPERATOR_REVIEWED:
        return BokEcosTreasuryDailyPlan(
            target_date, DailyPlanAction.REVIEW_REQUIRED, 0, 0,
            "PUBLICATION_REVISION_AND_EXPECTED_LATEST_UNKNOWN",
        )
    return BokEcosTreasuryDailyPlan(
        target_date, DailyPlanAction.COLLECT_EXACT_DATE,
        POLICY.max_data_calls, POLICY.retry_budget,
        "EXPLICIT_REVIEWED_DATE_ONLY",
    )


def validate_atomic_scope_dates(
    scope_dates: Mapping[str, Iterable[date]], *, target_date: date,
) -> None:
    """Require all canonical tenors and only the exact target before commit."""
    if tuple(scope_dates) != CANONICAL_TENORS:
        raise ValueError("candidate must contain the six canonical tenors in contract order")
    for tenor, values in scope_dates.items():
        observed = tuple(values)
        if observed != (target_date,):
            raise ValueError(f"{tenor} candidate must contain exactly the target date")


__all__ = [
    "BokEcosTreasuryDailyPlan", "BokEcosTreasuryDailyPolicy",
    "BokEcosTreasuryFinalityOccurrencePlan",
    "BokEcosTreasuryFinalityObservationPolicy", "CANONICAL_TENORS",
    "DATASET_ID", "DailyPlanAction", "ExactDateReview",
    "FinalityObservationAction", "POLICY",
    "FINALITY_OBSERVATION_POLICY",
    "plan_daily_operation", "plan_finality_observation_occurrence",
    "validate_atomic_scope_dates",
]
