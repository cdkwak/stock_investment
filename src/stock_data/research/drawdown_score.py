"""Deterministic composite oversold score and threshold-grid evaluation.

This module is deliberately provider-free.  It consumes signal columns and
outcome labels that have already been separated by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, order=True)
class ScoreThresholds:
    """Three increasingly severe one-point cutoffs for each score component."""

    drawdown252: tuple[float, float, float]
    disp60: tuple[float, float, float]
    rsi14: tuple[float, float, float]
    trigger_score: int

    def __post_init__(self) -> None:
        for name, values in (
            ("drawdown252", self.drawdown252),
            ("disp60", self.disp60),
            ("rsi14", self.rsi14),
        ):
            if len(values) != 3 or not all(np.isfinite(values)):
                raise ValueError(f"{name} must contain three finite cutoffs")
            if not values[0] > values[1] > values[2]:
                raise ValueError(f"{name} cutoffs must be strictly decreasing")
        if not 1 <= self.trigger_score <= 9:
            raise ValueError("trigger_score must be between 1 and 9")

    @property
    def threshold_id(self) -> str:
        def joined(values: tuple[float, float, float]) -> str:
            return "/".join(f"{value:.3f}" for value in values)

        return (
            f"dd={joined(self.drawdown252)};disp={joined(self.disp60)};"
            f"rsi={joined(self.rsi14)};score>={self.trigger_score}"
        )


DEFAULT_DRAWDOWN_GRIDS: tuple[tuple[float, float, float], ...] = (
    (-0.10, -0.20, -0.30),
    (-0.15, -0.25, -0.35),
    (-0.20, -0.30, -0.40),
)
DEFAULT_DISP60_GRIDS: tuple[tuple[float, float, float], ...] = (
    (-0.05, -0.10, -0.15),
    (-0.08, -0.12, -0.18),
    (-0.10, -0.15, -0.20),
)
DEFAULT_RSI14_GRIDS: tuple[tuple[float, float, float], ...] = (
    (40.0, 30.0, 20.0),
    (35.0, 30.0, 25.0),
    (30.0, 25.0, 20.0),
)
DEFAULT_TRIGGER_SCORES: tuple[int, ...] = (3, 4, 5, 6)


def default_threshold_grid() -> tuple[ScoreThresholds, ...]:
    """Return the small, predeclared and deterministically ordered search grid."""

    return tuple(
        ScoreThresholds(drawdown, disp60, rsi14, trigger)
        for drawdown, disp60, rsi14, trigger in product(
            DEFAULT_DRAWDOWN_GRIDS,
            DEFAULT_DISP60_GRIDS,
            DEFAULT_RSI14_GRIDS,
            DEFAULT_TRIGGER_SCORES,
        )
    )


def compute_drawdown_score(
    frame: pd.DataFrame, thresholds: ScoreThresholds,
) -> pd.Series:
    """Score each row from contemporaneous signal values only (range 0..9)."""

    required = {"drawdown252", "disp60", "rsi14"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"score input is missing columns: {sorted(missing)}")
    score = pd.Series(0, index=frame.index, dtype="int8")
    for column, cutoffs in (
        ("drawdown252", thresholds.drawdown252),
        ("disp60", thresholds.disp60),
        ("rsi14", thresholds.rsi14),
    ):
        values = pd.to_numeric(frame[column], errors="coerce")
        for cutoff in cutoffs:
            score = score + values.le(cutoff).fillna(False).astype("int8")
    return score.astype("int8")


def score_event_mask(
    frame: pd.DataFrame,
    thresholds: ScoreThresholds,
    *,
    series_column: str = "series_id",
) -> pd.Series:
    """Mark crossings from below the configured score into its trigger zone."""

    if series_column not in frame.columns:
        raise ValueError(f"{series_column} is required")
    score = compute_drawdown_score(frame, thresholds)
    previous = score.groupby(frame[series_column], sort=False).shift(1)
    valid = frame[["drawdown252", "disp60", "rsi14"]].notna().all(axis=1)
    previous_valid = valid.groupby(frame[series_column], sort=False).shift(
        1, fill_value=False
    )
    return (
        valid
        & previous_valid
        & score.ge(thresholds.trigger_score)
        & previous.lt(thresholds.trigger_score)
    ).astype(bool)


def _period_stats(values: pd.Series) -> dict[str, float | int]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {"n": 0, "mean": np.nan, "median": np.nan, "hit_rate": np.nan}
    return {
        "n": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "hit_rate": float(clean.gt(0).mean()),
    }


def evaluate_threshold(
    frame: pd.DataFrame,
    thresholds: ScoreThresholds,
    *,
    fit_end: str,
    holdout_start: str,
) -> dict[str, float | int | str]:
    """Evaluate one configuration with strict outcome availability in fitting.

    New callers provide ``outcome_end_date_90`` so every fitted 20/60/90-day
    comparison shares the same conservative boundary.  The 60-day fallback is
    retained for the original public API and its historical tests.
    """

    required = {
        "observation_date",
        "outcome_end_date_60",
        "forward_return_60",
        "series_id",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"grid input is missing columns: {sorted(missing)}")
    dates = pd.to_datetime(frame["observation_date"], errors="raise")
    split_column = (
        "outcome_end_date_90"
        if "outcome_end_date_90" in frame.columns
        else "outcome_end_date_60"
    )
    outcome_dates = pd.to_datetime(frame[split_column], errors="coerce")
    event = score_event_mask(frame, thresholds)
    fit_cutoff = pd.Timestamp(fit_end)
    holdout_cutoff = pd.Timestamp(holdout_start)
    fit = event & dates.le(fit_cutoff) & outcome_dates.le(fit_cutoff)
    holdout = event & dates.ge(holdout_cutoff) & outcome_dates.notna()
    fit_stats = _period_stats(frame.loc[fit, "forward_return_60"])
    holdout_stats = _period_stats(frame.loc[holdout, "forward_return_60"])
    return {
        "threshold_id": thresholds.threshold_id,
        "drawdown_l1": thresholds.drawdown252[0],
        "drawdown_l2": thresholds.drawdown252[1],
        "drawdown_l3": thresholds.drawdown252[2],
        "disp60_l1": thresholds.disp60[0],
        "disp60_l2": thresholds.disp60[1],
        "disp60_l3": thresholds.disp60[2],
        "rsi14_l1": thresholds.rsi14[0],
        "rsi14_l2": thresholds.rsi14[1],
        "rsi14_l3": thresholds.rsi14[2],
        "trigger_score": thresholds.trigger_score,
        **{f"fit_{key}": value for key, value in fit_stats.items()},
        **{f"holdout_{key}": value for key, value in holdout_stats.items()},
    }


def search_score_grid(
    frame: pd.DataFrame,
    *,
    fit_end: str,
    holdout_start: str,
    min_events: int,
    candidates: Sequence[ScoreThresholds] | None = None,
) -> tuple[pd.DataFrame, ScoreThresholds | None]:
    """Rank a grid solely by fit-window 60-session outcomes.

    Ties are broken by fit median, hit rate, event count, then the stable
    threshold identifier.  Hold-out columns are carried for reporting only and
    never participate in the ranking.
    """

    if min_events < 1:
        raise ValueError("min_events must be positive")
    grid = tuple(candidates) if candidates is not None else default_threshold_grid()
    if not grid:
        raise ValueError("at least one score threshold candidate is required")
    rows = [
        evaluate_threshold(
            frame,
            candidate,
            fit_end=fit_end,
            holdout_start=holdout_start,
        )
        for candidate in grid
    ]
    result = pd.DataFrame(rows)
    result["eligible"] = result["fit_n"].ge(min_events)
    result["rank"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    eligible = result.loc[result["eligible"]].sort_values(
        ["fit_mean", "fit_median", "fit_hit_rate", "fit_n", "threshold_id"],
        ascending=[False, False, False, False, True],
        kind="mergesort",
    )
    if eligible.empty:
        return result.sort_values("threshold_id", kind="mergesort").reset_index(drop=True), None
    result.loc[eligible.index, "rank"] = np.arange(1, len(eligible) + 1)
    winner_row = eligible.iloc[0]
    winner = ScoreThresholds(
        drawdown252=(
            float(winner_row["drawdown_l1"]),
            float(winner_row["drawdown_l2"]),
            float(winner_row["drawdown_l3"]),
        ),
        disp60=(
            float(winner_row["disp60_l1"]),
            float(winner_row["disp60_l2"]),
            float(winner_row["disp60_l3"]),
        ),
        rsi14=(
            float(winner_row["rsi14_l1"]),
            float(winner_row["rsi14_l2"]),
            float(winner_row["rsi14_l3"]),
        ),
        trigger_score=int(winner_row["trigger_score"]),
    )
    return result.sort_values(
        ["eligible", "rank", "threshold_id"],
        ascending=[False, True, True],
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True), winner


def threshold_from_row(row: pd.Series) -> ScoreThresholds:
    return ScoreThresholds(
        drawdown252=(row.drawdown_l1, row.drawdown_l2, row.drawdown_l3),
        disp60=(row.disp60_l1, row.disp60_l2, row.disp60_l3),
        rsi14=(row.rsi14_l1, row.rsi14_l2, row.rsi14_l3),
        trigger_score=int(row.trigger_score),
    )


__all__ = [
    "DEFAULT_DRAWDOWN_GRIDS",
    "DEFAULT_DISP60_GRIDS",
    "DEFAULT_RSI14_GRIDS",
    "DEFAULT_TRIGGER_SCORES",
    "ScoreThresholds",
    "compute_drawdown_score",
    "default_threshold_grid",
    "evaluate_threshold",
    "score_event_mask",
    "search_score_grid",
    "threshold_from_row",
]
