from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .labels import LABEL_NAMESPACE


def _validated_canonical_dates(values: pd.Series, *, artifact: str) -> pd.Series:
    message = f"{artifact} dates must be canonical YYYY-MM-DD strings"
    if not isinstance(values, pd.Series):
        raise ValueError(message)
    parsed: list[datetime] = []
    for value in values:
        if type(value) is not str:
            raise ValueError(message)
        try:
            date_value = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(message) from None
        if date_value.strftime("%Y-%m-%d") != value:
            raise ValueError(message)
        parsed.append(date_value)
    try:
        return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")
    except (OverflowError, TypeError, ValueError):
        raise ValueError(message) from None


@dataclass(frozen=True)
class CoverageHoldout:
    policy_id: str
    coverage_start: str
    coverage_end: str
    holdout_start: str
    development_observations: int
    holdout_observations: int
    results_reviewed: bool = False

    def __post_init__(self) -> None:
        if self.results_reviewed:
            raise ValueError("holdout results must remain untouched")
        if self.development_observations < 1 or self.holdout_observations < 1:
            raise ValueError("both development and holdout coverage are required")


def define_untouched_holdout(
    observation_dates: pd.Series, *, final_calendar_years: int = 5,
) -> CoverageHoldout:
    """Freeze a date-only holdout without inspecting labels, signals, or outcomes."""
    if final_calendar_years < 1:
        raise ValueError("final_calendar_years must be positive")
    dates = _validated_canonical_dates(
        observation_dates, artifact="holdout coverage",
    )
    if dates.empty or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("holdout coverage dates must be non-empty, unique, and sorted")
    boundary = dates.iloc[-1] - pd.DateOffset(years=final_calendar_years)
    holdout = dates.ge(boundary)
    if not holdout.any() or holdout.all():
        raise ValueError("coverage cannot support the requested untouched holdout")
    first = dates.loc[holdout].iloc[0]
    return CoverageHoldout(
        policy_id=f"UNTOUCHED_FINAL_{final_calendar_years}_CALENDAR_YEARS",
        coverage_start=dates.iloc[0].strftime("%Y-%m-%d"),
        coverage_end=dates.iloc[-1].strftime("%Y-%m-%d"),
        holdout_start=first.strftime("%Y-%m-%d"),
        development_observations=int((~holdout).sum()),
        holdout_observations=int(holdout.sum()),
    )


def development_only(frame: pd.DataFrame, policy: CoverageHoldout) -> pd.DataFrame:
    if "observation_date" not in frame.columns:
        raise ValueError("observation_date is required")
    observation_dates = _validated_canonical_dates(
        frame["observation_date"], artifact="development observation",
    )
    holdout_start = _validated_canonical_dates(
        pd.Series([policy.holdout_start], dtype="object"),
        artifact="holdout policy",
    ).iloc[0]
    development = observation_dates.lt(holdout_start)
    label_columns = LABEL_NAMESPACE.intersection(frame.columns)
    if label_columns and "label_available_at" not in frame.columns:
        raise ValueError("label_available_at is required for label frames")
    if "label_available_at" in frame.columns:
        raw_availability = frame["label_available_at"]
        try:
            parsed = [pd.Timestamp(value) for value in raw_availability]
            if any(pd.isna(value) or value.tzinfo is None for value in parsed):
                raise ValueError
            available_at = pd.to_datetime(
                raw_availability, format="ISO8601", errors="raise", utc=True,
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("label_available_at must contain aware ISO timestamps") from error
        holdout_boundary = holdout_start.tz_localize("Asia/Seoul")
        development &= available_at.lt(holdout_boundary)
    return frame.loc[development].reset_index(drop=True)


__all__ = ["CoverageHoldout", "define_untouched_holdout", "development_only"]
