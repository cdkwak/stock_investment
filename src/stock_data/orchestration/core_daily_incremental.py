from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Iterable


class IncrementalReadiness(StrEnum):
    DAILY_INCREMENTAL_READY = "DAILY_INCREMENTAL_READY"
    ADAPTER_REUSE_POSSIBLE = "ADAPTER_REUSE_POSSIBLE"
    NEW_INCREMENTAL_WRAPPER_NEEDED = "NEW_INCREMENTAL_WRAPPER_NEEDED"
    SOURCE_FINALITY_BLOCKED = "SOURCE_FINALITY_BLOCKED"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"


@dataclass(frozen=True)
class IncrementalPlan:
    last_accepted: date
    latest_finalized: date
    missing_dates: tuple[date, ...]
    expected_calls: int


def plan_missing_finalized_dates(
    *,
    last_accepted: date,
    latest_finalized: date,
    accepted_trading_dates: Iterable[date],
    calls_per_date: int,
) -> IncrementalPlan:
    """Plan only explicitly accepted trading dates; never infer weekdays/holidays."""
    if calls_per_date < 1:
        raise ValueError("calls_per_date must be positive")
    if latest_finalized < last_accepted:
        raise ValueError("latest_finalized precedes last_accepted")
    dates = tuple(sorted(set(accepted_trading_dates)))
    if any(day > latest_finalized for day in dates):
        raise ValueError("trading date exceeds latest finalized date")
    missing = tuple(day for day in dates if last_accepted < day <= latest_finalized)
    return IncrementalPlan(last_accepted, latest_finalized, missing, len(missing) * calls_per_date)


def require_executable_gate(readiness: IncrementalReadiness, plan: IncrementalPlan) -> None:
    if readiness is not IncrementalReadiness.DAILY_INCREMENTAL_READY:
        raise RuntimeError(f"live incremental blocked: {readiness.value}")
    if not plan.missing_dates:
        raise RuntimeError("no missing finalized trading dates")
