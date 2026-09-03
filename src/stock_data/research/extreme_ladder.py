"""Two-sided, equal-weight extreme-score ladder for offline research.

The functions in this module are provider-free and deterministic.  Indicator
flags use only values observed through close T; outcome columns are accepted
only by the evaluation helpers and never participate in flag or score creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


FIT_END = "2015-12-31"
HOLDOUT_START = "2016-01-01"
MIN_EVENTS = 15
MIN_LADDER_INDICATORS = 2
MAX_LADDER_INDICATORS = 4
LADDER_BASKETS: tuple[str, ...] = ("KR", "US_TECH", "SEMIS")
LADDER_HORIZONS: tuple[int, ...] = (20, 60, 90)
DEFAULT_TARGET_VOL = 0.15


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    side: str
    indicator: str
    flag_column: str


INDICATOR_SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("DRAWDOWN", "drawdown252 하단", "flag_drawdown252_low"),
    IndicatorSpec("DRAWDOWN", "disp60 하단", "flag_disp60_low"),
    IndicatorSpec("DRAWDOWN", "RSI14 하단", "flag_rsi14_low"),
    IndicatorSpec("DRAWDOWN", "공포지수 상위 20%", "flag_fear_high"),
    IndicatorSpec("OVERHEAT", "disp60 상단", "flag_disp60_high"),
    IndicatorSpec("OVERHEAT", "RSI14 상단", "flag_rsi14_high"),
    IndicatorSpec("OVERHEAT", "공포지수 하위 20%", "flag_greed_low"),
)


def rolling_percentile_rank(values: pd.Series, *, window: int = 252) -> pd.Series:
    """Return the causal midrank percentile of the current value in its window."""

    if window < 2:
        raise ValueError("percentile window must be at least two sessions")
    numeric = pd.to_numeric(values, errors="coerce").astype("float64")

    def current_midrank(sample: np.ndarray) -> float:
        current = sample[-1]
        below = np.count_nonzero(sample < current)
        equal = np.count_nonzero(sample == current)
        return float((below + 0.5 * equal) / sample.size)

    return numeric.rolling(window, min_periods=window).apply(current_midrank, raw=True)


def _nullable_flag(values: pd.Series, predicate: object) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(pd.NA, index=values.index, dtype="boolean")
    valid = numeric.notna()
    result.loc[valid] = predicate(numeric.loc[valid]).astype(bool)
    return result


def add_extreme_flags(signals: pd.DataFrame) -> pd.DataFrame:
    """Add fixed lower/upper extreme flags without reading any future value."""

    required = {"drawdown252", "disp60", "rsi14", "vol_index_percentile252"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"extreme flag input is missing columns: {sorted(missing)}")
    frame = signals.copy()
    frame["flag_drawdown252_low"] = _nullable_flag(
        frame["drawdown252"], lambda value: value.le(-0.30)
    )
    frame["flag_disp60_low"] = _nullable_flag(
        frame["disp60"], lambda value: value.le(-0.10)
    )
    frame["flag_rsi14_low"] = _nullable_flag(
        frame["rsi14"], lambda value: value.le(30.0)
    )
    frame["flag_fear_high"] = _nullable_flag(
        frame["vol_index_percentile252"], lambda value: value.ge(0.80)
    )
    frame["flag_disp60_high"] = _nullable_flag(
        frame["disp60"], lambda value: value.ge(0.10)
    )
    frame["flag_rsi14_high"] = _nullable_flag(
        frame["rsi14"], lambda value: value.ge(70.0)
    )
    frame["flag_greed_low"] = _nullable_flag(
        frame["vol_index_percentile252"], lambda value: value.le(0.20)
    )
    return frame


def indicator_survives(
    fit_difference: float,
    holdout_difference: float,
    fit_n: int,
    holdout_n: int,
    *,
    min_events: int = MIN_EVENTS,
) -> bool:
    """Apply the predeclared same-sign and minimum-event survivor rule."""

    if min_events < 1:
        raise ValueError("min_events must be positive")
    if fit_n < min_events or holdout_n < min_events:
        return False
    if not np.isfinite(fit_difference) or not np.isfinite(holdout_difference):
        return False
    return bool(
        np.sign(fit_difference) != 0
        and np.sign(fit_difference) == np.sign(holdout_difference)
    )


def _period_masks(
    frame: pd.DataFrame,
    *,
    fit_end: str = FIT_END,
    holdout_start: str = HOLDOUT_START,
) -> dict[str, pd.Series]:
    required = {"observation_date", "outcome_end_date_90"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"evaluation input is missing columns: {sorted(missing)}")
    observation = pd.to_datetime(frame["observation_date"], errors="raise")
    outcome_end_90 = pd.to_datetime(frame["outcome_end_date_90"], errors="coerce")
    return {
        "FIT": outcome_end_90.le(pd.Timestamp(fit_end)),
        "HOLDOUT": observation.ge(pd.Timestamp(holdout_start)) & outcome_end_90.notna(),
    }


def _standalone_stats(
    frame: pd.DataFrame,
    flag_column: str,
    period_mask: pd.Series,
) -> dict[str, float | int]:
    flag = frame[flag_column].astype("boolean")
    returns = pd.to_numeric(frame["forward_return_60"], errors="coerce")
    eligible = period_mask & flag.notna() & returns.notna()
    baseline_by_series = (
        frame.loc[eligible]
        .assign(_return=returns.loc[eligible])
        .groupby("series_id", sort=True)["_return"]
        .mean()
    )
    selected = eligible & flag.fillna(False)
    event_returns = returns.loc[selected]
    if event_returns.empty:
        return {"n": 0, "mean": np.nan, "baseline": np.nan, "difference": np.nan}
    event_baselines = frame.loc[selected, "series_id"].map(baseline_by_series)
    event_mean = float(event_returns.mean())
    baseline_mean = float(event_baselines.mean())
    return {
        "n": int(event_returns.size),
        "mean": event_mean,
        "baseline": baseline_mean,
        "difference": event_mean - baseline_mean,
    }


def evaluate_standalone_indicators(
    frame: pd.DataFrame,
    *,
    baskets: Sequence[str] = LADDER_BASKETS,
    specs: Sequence[IndicatorSpec] = INDICATOR_SPECS,
    fit_end: str = FIT_END,
    holdout_start: str = HOLDOUT_START,
    min_events: int = MIN_EVENTS,
) -> pd.DataFrame:
    """Evaluate each indicator alone before constructing any combined score."""

    required = {"basket", "series_id", "forward_return_60"}
    required.update(spec.flag_column for spec in specs)
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"standalone input is missing columns: {sorted(missing)}")
    masks = _period_masks(frame, fit_end=fit_end, holdout_start=holdout_start)
    rows: list[dict[str, object]] = []
    for basket in baskets:
        basket_mask = frame["basket"].eq(basket)
        scoped = frame.loc[basket_mask].copy()
        scoped_masks = {name: mask.loc[basket_mask] for name, mask in masks.items()}
        for spec in specs:
            fit = _standalone_stats(scoped, spec.flag_column, scoped_masks["FIT"])
            holdout = _standalone_stats(scoped, spec.flag_column, scoped_masks["HOLDOUT"])
            survives = indicator_survives(
                float(fit["difference"]),
                float(holdout["difference"]),
                int(fit["n"]),
                int(holdout["n"]),
                min_events=min_events,
            )
            rows.append(
                {
                    "basket": basket,
                    "side": spec.side,
                    "indicator": spec.indicator,
                    "flag_column": spec.flag_column,
                    **{f"fit_{key}": value for key, value in fit.items()},
                    **{f"holdout_{key}": value for key, value in holdout.items()},
                    "survives": survives,
                    "fit_low_sample": int(fit["n"]) < min_events,
                    "holdout_low_sample": int(holdout["n"]) < min_events,
                }
            )
    return pd.DataFrame(rows)


def survivor_map(validation: pd.DataFrame) -> dict[tuple[str, str], tuple[str, ...]]:
    """Return stable, ordered flag columns that pass the standalone screen."""

    required = {"basket", "side", "flag_column", "survives"}
    missing = required.difference(validation.columns)
    if missing:
        raise ValueError(f"validation table is missing columns: {sorted(missing)}")
    order = {spec.flag_column: position for position, spec in enumerate(INDICATOR_SPECS)}
    selected = validation.loc[validation["survives"].astype(bool)].copy()
    result: dict[tuple[str, str], tuple[str, ...]] = {}
    for (basket, side), group in selected.groupby(["basket", "side"], sort=True):
        columns = sorted(group["flag_column"].astype(str), key=lambda value: order[value])
        result[(str(basket), str(side))] = tuple(columns)
    return result


def build_equal_weight_scores(
    frame: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    baskets: Sequence[str] = LADDER_BASKETS,
) -> pd.DataFrame:
    """Build one daily score point per active surviving indicator."""

    selected = survivor_map(validation)
    pieces: list[pd.DataFrame] = []
    for basket in baskets:
        scoped = frame.loc[frame["basket"].eq(basket)].copy()
        for side in ("DRAWDOWN", "OVERHEAT"):
            columns = selected.get((basket, side), ())
            part = scoped.copy()
            part["side"] = side
            part["indicator_count"] = len(columns)
            part["survivor_flags"] = ";".join(columns)
            if not MIN_LADDER_INDICATORS <= len(columns) <= MAX_LADDER_INDICATORS:
                part["score_level"] = pd.Series(pd.NA, index=part.index, dtype="Int64")
            else:
                flags = part.loc[:, columns].astype("boolean")
                valid = flags.notna().all(axis=1)
                score = flags.fillna(False).astype("int8").sum(axis=1).astype("Int64")
                part["score_level"] = score.where(valid, pd.NA)
            pieces.append(part)
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def monotonicity_check(
    levels: pd.DataFrame,
    *,
    side: str,
    value_column: str = "mean_return_60",
    min_events: int = MIN_EVENTS,
    tolerance: float = 1e-12,
) -> str:
    """Check the expected return ordering across adequately sampled levels."""

    required = {"score_level", "n", value_column}
    missing = required.difference(levels.columns)
    if missing:
        raise ValueError(f"monotonicity input is missing columns: {sorted(missing)}")
    usable = levels.loc[
        pd.to_numeric(levels["n"], errors="coerce").ge(min_events)
        & pd.to_numeric(levels[value_column], errors="coerce").notna()
    ].sort_values("score_level", kind="mergesort")
    if len(usable) < 2:
        return "INSUFFICIENT_LEVELS"
    differences = np.diff(usable[value_column].to_numpy(dtype="float64"))
    if side == "DRAWDOWN":
        passed = bool(np.all(differences >= -tolerance))
    elif side == "OVERHEAT":
        passed = bool(np.all(differences <= tolerance))
    else:
        raise ValueError("side must be DRAWDOWN or OVERHEAT")
    return "PASS" if passed else "FAIL"


def aggregate_ladder_levels(
    scored: pd.DataFrame,
    *,
    fit_end: str = FIT_END,
    holdout_start: str = HOLDOUT_START,
    min_events: int = MIN_EVENTS,
) -> pd.DataFrame:
    """Aggregate forward distributions at every attainable equal-weight level."""

    required = {
        "basket",
        "side",
        "indicator_count",
        "score_level",
        "forward_return_20",
        "forward_return_60",
        "forward_return_90",
        "forward_realized_volatility_60",
        "forward_max_drawdown_60",
    }
    missing = required.difference(scored.columns)
    if missing:
        raise ValueError(f"ladder input is missing columns: {sorted(missing)}")
    masks = _period_masks(scored, fit_end=fit_end, holdout_start=holdout_start)
    rows: list[dict[str, object]] = []
    groups = scored.groupby(["basket", "side"], sort=True)
    for (basket, side), group in groups:
        indicator_count = int(group["indicator_count"].iloc[0])
        if not MIN_LADDER_INDICATORS <= indicator_count <= MAX_LADDER_INDICATORS:
            continue
        group_masks = {name: mask.loc[group.index] for name, mask in masks.items()}
        for period, period_mask in group_masks.items():
            eligible = group.loc[
                period_mask
                & group["score_level"].notna()
                & group["forward_return_90"].notna()
            ]
            for level in range(indicator_count + 1):
                selected = eligible.loc[eligible["score_level"].eq(level)]
                rows.append(
                    {
                        "basket": basket,
                        "side": side,
                        "period": period,
                        "indicator_count": indicator_count,
                        "score_level": level,
                        "n": int(len(selected)),
                        "mean_return_20": selected["forward_return_20"].mean(),
                        "mean_return_60": selected["forward_return_60"].mean(),
                        "mean_return_90": selected["forward_return_90"].mean(),
                        "mean_forward_realized_volatility_60": selected[
                            "forward_realized_volatility_60"
                        ].mean(),
                        "mean_forward_max_drawdown_60": selected[
                            "forward_max_drawdown_60"
                        ].mean(),
                        "low_sample": len(selected) < min_events,
                    }
                )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["monotonicity"] = ""
    for (basket, side, period), group in result.groupby(
        ["basket", "side", "period"], sort=True
    ):
        verdict = monotonicity_check(group, side=side, min_events=min_events)
        result.loc[group.index, "monotonicity"] = verdict
    return result


def volatility_target_exposure(
    realized_volatility_20d: pd.Series,
    *,
    target_vol: float = DEFAULT_TARGET_VOL,
) -> pd.Series:
    """Return min(1, target_vol / realised_vol_20d), preserving missing rows."""

    if not np.isfinite(target_vol) or target_vol <= 0:
        raise ValueError("target_vol must be finite and positive")
    volatility = pd.to_numeric(realized_volatility_20d, errors="coerce")
    if volatility.dropna().lt(0).any():
        raise ValueError("realized volatility must be non-negative")
    exposure = pd.Series(np.nan, index=volatility.index, dtype="float64")
    positive = volatility.gt(0)
    exposure.loc[positive] = np.minimum(1.0, target_vol / volatility.loc[positive])
    exposure.loc[volatility.eq(0)] = 1.0
    return exposure


def aggregate_vol_targeting(
    frame: pd.DataFrame,
    *,
    target_vol: float = DEFAULT_TARGET_VOL,
    baskets: Sequence[str] = LADDER_BASKETS,
    fit_end: str = FIT_END,
    holdout_start: str = HOLDOUT_START,
    min_events: int = MIN_EVENTS,
) -> pd.DataFrame:
    """Compare constant full exposure with causal 20-day volatility targeting."""

    required = {"basket", "realized_volatility_20d"}
    required.update(f"forward_return_{horizon}" for horizon in LADDER_HORIZONS)
    required.update({"forward_realized_volatility_60", "forward_max_drawdown_60"})
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"vol-target input is missing columns: {sorted(missing)}")
    masks = _period_masks(frame, fit_end=fit_end, holdout_start=holdout_start)
    exposure = volatility_target_exposure(
        frame["realized_volatility_20d"], target_vol=target_vol
    )
    rows: list[dict[str, object]] = []
    for basket in baskets:
        basket_mask = frame["basket"].eq(basket)
        for period, period_mask in masks.items():
            selected = frame.loc[
                basket_mask
                & period_mask
                & exposure.notna()
                & frame["forward_return_90"].notna()
            ].copy()
            selected_exposure = exposure.loc[selected.index]
            row: dict[str, object] = {
                "basket": basket,
                "period": period,
                "target_vol": target_vol,
                "n": int(len(selected)),
                "mean_exposure": selected_exposure.mean(),
                "low_sample": len(selected) < min_events,
            }
            for horizon in LADDER_HORIZONS:
                returns = selected[f"forward_return_{horizon}"]
                row[f"mean_return_{horizon}_full"] = returns.mean()
                row[f"mean_return_{horizon}_scaled"] = (
                    selected_exposure * returns
                ).mean()
            row["mean_forward_realized_volatility_60_full"] = selected[
                "forward_realized_volatility_60"
            ].mean()
            row["mean_forward_realized_volatility_60_scaled"] = (
                selected_exposure * selected["forward_realized_volatility_60"]
            ).mean()
            row["mean_forward_max_drawdown_60_full"] = selected[
                "forward_max_drawdown_60"
            ].mean()
            row["mean_forward_max_drawdown_60_scaled"] = (
                selected_exposure * selected["forward_max_drawdown_60"]
            ).mean()
            rows.append(row)
    return pd.DataFrame(rows)


def aggregate_exploratory_indicators(
    frame: pd.DataFrame,
    *,
    indicators: Mapping[str, str] | None = None,
    baskets: Sequence[str] = LADDER_BASKETS,
    fit_end: str = FIT_END,
    holdout_start: str = HOLDOUT_START,
    min_events: int = MIN_EVENTS,
) -> pd.DataFrame:
    """Report optional diagnostics that never enter either extreme score."""

    indicators = indicators or {
        "20일 거래량 비율": "volume_ratio20",
        "Bollinger %b(20,2)": "bollinger_percent_b20",
    }
    required = {"basket", "forward_return_60", *indicators.values()}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"exploratory input is missing columns: {sorted(missing)}")
    masks = _period_masks(frame, fit_end=fit_end, holdout_start=holdout_start)
    rows: list[dict[str, object]] = []
    for basket in baskets:
        basket_mask = frame["basket"].eq(basket)
        for period, period_mask in masks.items():
            for label, column in indicators.items():
                values = pd.to_numeric(frame[column], errors="coerce")
                returns = pd.to_numeric(frame["forward_return_60"], errors="coerce")
                selected = basket_mask & period_mask & values.notna() & returns.notna()
                rows.append(
                    {
                        "basket": basket,
                        "period": period,
                        "indicator": label,
                        "n": int(selected.sum()),
                        "mean": values.loc[selected].mean(),
                        "median": values.loc[selected].median(),
                        "correlation_with_forward_return_60": values.loc[selected].corr(
                            returns.loc[selected]
                        ),
                        "included_in_score": False,
                        "low_sample": int(selected.sum()) < min_events,
                    }
                )
    return pd.DataFrame(rows)


__all__ = [
    "DEFAULT_TARGET_VOL",
    "FIT_END",
    "HOLDOUT_START",
    "INDICATOR_SPECS",
    "IndicatorSpec",
    "LADDER_BASKETS",
    "LADDER_HORIZONS",
    "MAX_LADDER_INDICATORS",
    "MIN_EVENTS",
    "MIN_LADDER_INDICATORS",
    "add_extreme_flags",
    "aggregate_exploratory_indicators",
    "aggregate_ladder_levels",
    "aggregate_vol_targeting",
    "build_equal_weight_scores",
    "evaluate_standalone_indicators",
    "indicator_survives",
    "monotonicity_check",
    "rolling_percentile_rank",
    "survivor_map",
    "volatility_target_exposure",
]
