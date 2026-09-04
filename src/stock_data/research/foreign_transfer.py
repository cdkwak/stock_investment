"""Fixed-design foreign transfer helpers for the compound ladder research.

The helpers are provider-free and operate on retained close histories.  They
reuse the compound-ladder account engine; this module owns only the predeclared
cross-market scaling, episode classification, and Japan warning diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd


FIT_END = pd.Timestamp("2015-12-31")
HOLDOUT_START = pd.Timestamp("2016-01-01")
JAPAN_START = pd.Timestamp("1990-01-01")
JAPAN_END = pd.Timestamp("2012-12-31")
TRADING_DAYS = 252
RECOVERY_SESSIONS = 252
RECOVERY_DRAWDOWN = -0.05
NEGATIVE_DISP_SESSIONS = 120

MARKET_GROUPS: dict[str, str] = {
    "TAIEX": "확인용",
    "SP500": "확인용",
    "NASDAQ100": "확인용 보조(비투표)",
    "EURO_STOXX50": "보너스",
    "DAX": "보너스",
    "NIKKEI225": "경고용",
}
CONFIDENCE_MARKETS: tuple[str, ...] = (
    "TAIEX",
    "SP500",
    "NASDAQ100",
    "EURO_STOXX50",
    "DAX",
)


@dataclass(frozen=True, slots=True)
class SynchronousWindow:
    window_id: str
    start: pd.Timestamp
    end: pd.Timestamp
    markets: frozenset[str] | None = None

    def applies(self, market: str, date: pd.Timestamp) -> bool:
        return (
            self.start <= date <= self.end
            and (self.markets is None or market in self.markets)
        )


SYNCHRONOUS_WINDOWS: tuple[SynchronousWindow, ...] = (
    SynchronousWindow(
        "asia_crisis_1997_1998",
        pd.Timestamp("1997-07-01"),
        pd.Timestamp("1998-12-31"),
        frozenset({"TAIEX", "HANG_SENG", "KOSPI"}),
    ),
    SynchronousWindow(
        "dotcom_2000_2002",
        pd.Timestamp("2000-03-01"),
        pd.Timestamp("2002-12-31"),
    ),
    SynchronousWindow(
        "gfc_2008_2009",
        pd.Timestamp("2008-06-01"),
        pd.Timestamp("2009-06-30"),
    ),
    SynchronousWindow(
        "euro_crisis_2011",
        pd.Timestamp("2011-07-01"),
        pd.Timestamp("2011-12-31"),
        frozenset({"EURO_STOXX50", "DAX"}),
    ),
    SynchronousWindow(
        "covid_2020",
        pd.Timestamp("2020-02-01"),
        pd.Timestamp("2020-05-31"),
    ),
    SynchronousWindow(
        "rate_shock_2022",
        pd.Timestamp("2022-01-01"),
        pd.Timestamp("2022-10-31"),
    ),
)


def _positive_close(close: pd.Series, *, label: str) -> pd.Series:
    values = pd.to_numeric(close, errors="raise").astype("float64")
    if len(values) < 3 or not np.isfinite(values).all() or values.le(0.0).any():
        raise ValueError(f"{label} closes must contain at least three finite positive values")
    return values


def annualized_log_volatility(close: pd.Series) -> float:
    """Annualised sample volatility of daily log returns."""

    values = _positive_close(close, label="fit")
    log_returns = np.log(values).diff().dropna()
    sigma = float(log_returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
    if not np.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("fit volatility must be finite and positive")
    return sigma


def compute_volatility_scale(market_close: pd.Series, korea_close: pd.Series) -> float:
    """Compute the one-time fit-window market/Korea volatility ratio."""

    market_sigma = annualized_log_volatility(market_close)
    korea_sigma = annualized_log_volatility(korea_close)
    scale = market_sigma / korea_sigma
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("volatility scale must be finite and positive")
    return float(scale)


def normalized_thresholds(scale: float) -> tuple[float, float]:
    """Scale the two rule thresholds and apply the fixed clamps."""

    value = float(scale)
    if not np.isfinite(value) or value <= 0.0:
        raise ValueError("volatility scale must be finite and positive")
    drawdown = float(np.clip(-0.20 * value, -0.60, -0.05))
    disp60 = float(np.clip(-0.10 * value, -0.30, -0.03))
    return drawdown, disp60


def restrict_japan_window(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only the predeclared 1990-01-01 through 2012-12-31 Japan span."""

    if "date" not in frame:
        raise ValueError("Japan frame is missing date")
    dates = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    selected = frame.loc[dates.between(JAPAN_START, JAPAN_END)].copy()
    selected["date"] = dates.loc[selected.index]
    return selected.reset_index(drop=True)


def classify_episode(entry_date: Any, market: str) -> tuple[str, str | None]:
    """Classify one ladder entry using only the fixed calendar table."""

    date = pd.Timestamp(entry_date).normalize()
    for window in SYNCHRONOUS_WINDOWS:
        if window.applies(str(market), date):
            return "synchronous", window.window_id
    return "idiosyncratic", None


def annotate_episodes(cycles: pd.DataFrame, market: str) -> pd.DataFrame:
    frame = cycles.copy()
    if frame.empty:
        frame["episode_class"] = pd.Series(dtype="object")
        frame["window_id"] = pd.Series(dtype="object")
        return frame
    labels = frame["entry_date"].map(lambda value: classify_episode(value, market))
    frame["episode_class"] = labels.map(lambda value: value[0])
    frame["window_id"] = labels.map(lambda value: value[1])
    return frame


def period_episode_counts(cycles: pd.DataFrame, period: str) -> dict[str, int]:
    if cycles.empty:
        selected = cycles
    else:
        dates = pd.to_datetime(cycles["entry_date"], errors="raise")
        if period == "fit":
            selected = cycles.loc[dates.le(FIT_END)]
        elif period == "holdout":
            selected = cycles.loc[dates.ge(HOLDOUT_START)]
        elif period == "full":
            selected = cycles
        else:
            raise ValueError(f"unsupported period: {period}")
    counts = selected.get("episode_class", pd.Series(dtype="object")).value_counts()
    synchronous = int(counts.get("synchronous", 0))
    idiosyncratic = int(counts.get("idiosyncratic", 0))
    return {
        "total": int(len(selected)),
        "synchronous": synchronous,
        "idiosyncratic": idiosyncratic,
    }


def summarize_episode_classes(cycles: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    """Summarize wins and compounded episode contributions by class.

    A hit means the strategy episode contribution exceeded the same-date 1x
    baseline contribution.  Contribution multiples compound the episode-level
    account changes within each class; they are not standalone portfolios.
    """

    output: dict[str, dict[str, float | int | None]] = {}
    for label in ("all", "synchronous", "idiosyncratic"):
        selected = cycles if label == "all" else cycles.loc[cycles["episode_class"].eq(label)]
        count = int(len(selected))
        if count:
            strategy = pd.to_numeric(selected["contribution_to_wealth"], errors="raise")
            baseline = pd.to_numeric(selected["baseline_contribution"], errors="raise")
            hits = int(strategy.gt(baseline).sum())
            strategy_multiple = float(np.prod(1.0 + strategy.to_numpy()))
            baseline_multiple = float(np.prod(1.0 + baseline.to_numpy()))
            relative = strategy_multiple / baseline_multiple if baseline_multiple != 0 else np.nan
            hit_rate: float | None = hits / count
        else:
            hits = 0
            strategy_multiple = 1.0
            baseline_multiple = 1.0
            relative = 1.0
            hit_rate = None
        output[label] = {
            "episodes": count,
            "hits_vs_baseline": hits,
            "hit_rate_vs_baseline": hit_rate,
            "strategy_contribution_multiple": strategy_multiple,
            "baseline_contribution_multiple": baseline_multiple,
            "relative_contribution_multiple": float(relative),
        }
    return output


def independent_observation_proxy(
    cycles_by_market: dict[str, pd.DataFrame], markets: Iterable[str]
) -> dict[str, int]:
    """Count idiosyncratic episodes plus each predefined shared window once."""

    idiosyncratic = 0
    windows: set[str] = set()
    total = 0
    for market in markets:
        cycles = cycles_by_market[market]
        total += int(len(cycles))
        idiosyncratic += int(cycles["episode_class"].eq("idiosyncratic").sum())
        windows.update(
            str(value)
            for value in cycles.loc[cycles["episode_class"].eq("synchronous"), "window_id"].dropna()
        )
    return {
        "episode_rows": total,
        "idiosyncratic_episodes": idiosyncratic,
        "distinct_synchronous_windows": len(windows),
        "independent_proxy": idiosyncratic + len(windows),
    }


def _longest_true_run(mask: pd.Series, dates: pd.Series) -> dict[str, int | str | None]:
    values = mask.fillna(False).to_numpy(dtype=bool)
    calendar = pd.to_datetime(dates, errors="raise").reset_index(drop=True)
    best_start: int | None = None
    best_end: int | None = None
    current_start: int | None = None
    for index, active in enumerate(values):
        if active and current_start is None:
            current_start = index
        if current_start is not None and (not active or index == len(values) - 1):
            end = index if active else index - 1
            if best_start is None or end - current_start > best_end - best_start:  # type: ignore[operator]
                best_start, best_end = current_start, end
            current_start = None
    if best_start is None or best_end is None:
        return {"sessions": 0, "calendar_days": 0, "start": None, "end": None}
    start_date = pd.Timestamp(calendar.iloc[best_start])
    end_date = pd.Timestamp(calendar.iloc[best_end])
    return {
        "sessions": best_end - best_start + 1,
        "calendar_days": (end_date - start_date).days + 1,
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
    }


def underperformance_summary(
    strategy_curve: pd.DataFrame,
    baseline_curve: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """Measure Japan's deficit magnitude and longest deficit spell."""

    strategy = pd.to_numeric(strategy_curve["wealth"], errors="raise")
    baseline = pd.to_numeric(baseline_curve["wealth"], errors="raise")
    if len(strategy) != len(baseline) or len(strategy) != len(strategy_curve["date"]):
        raise ValueError("strategy and baseline curves must align")
    comparisons = {"baseline": baseline, "cash": pd.Series(1.0, index=strategy.index)}
    output: dict[str, dict[str, Any]] = {}
    for name, comparator in comparisons.items():
        ratio = strategy / comparator
        output[name] = {
            "ending_shortfall": float(ratio.iloc[-1] - 1.0),
            "worst_shortfall": float(ratio.min() - 1.0),
            "longest_below": _longest_true_run(
                strategy.lt(comparator), strategy_curve["date"]
            ),
        }
    return output


def diagnostic_flags(signals: pd.DataFrame, cycles: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Evaluate the three predeclared long-run-premise warning candidates."""

    required = {"date", "high252", "drawdown252", "disp60"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signal input is missing columns: {sorted(missing)}")
    frame = signals.loc[:, sorted(required)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame = frame.sort_values("date", kind="mergesort").reset_index(drop=True)
    positions = {pd.Timestamp(date): index for index, date in enumerate(frame["date"])}

    lower_high_dates: list[str] = []
    prior_high: float | None = None
    for entry in pd.to_datetime(cycles.get("entry_date", pd.Series(dtype="object")), errors="raise"):
        index = positions.get(pd.Timestamp(entry).normalize())
        if index is None:
            continue
        current_high = float(frame.loc[index, "high252"])
        if np.isfinite(current_high) and prior_high is not None and current_high < prior_high:
            lower_high_dates.append(pd.Timestamp(entry).strftime("%Y-%m-%d"))
        if np.isfinite(current_high):
            prior_high = current_high

    unrecovered_dates: list[str] = []
    drawdown = pd.to_numeric(frame["drawdown252"], errors="coerce")
    for entry in pd.to_datetime(cycles.get("entry_date", pd.Series(dtype="object")), errors="raise"):
        index = positions.get(pd.Timestamp(entry).normalize())
        flag_index = None if index is None else index + RECOVERY_SESSIONS
        if flag_index is None or flag_index >= len(frame):
            continue
        window = drawdown.iloc[index : flag_index + 1]
        if not window.ge(RECOVERY_DRAWDOWN).any():
            unrecovered_dates.append(pd.Timestamp(frame.loc[flag_index, "date"]).strftime("%Y-%m-%d"))

    negative_disp_dates: list[str] = []
    negative = pd.to_numeric(frame["disp60"], errors="coerce").lt(0.0)
    run_start: int | None = None
    for index, active in enumerate(negative.to_numpy(dtype=bool)):
        if active and run_start is None:
            run_start = index
        elif not active:
            run_start = None
        if run_start is not None and index - run_start + 1 == NEGATIVE_DISP_SESSIONS + 1:
            negative_disp_dates.append(pd.Timestamp(frame.loc[index, "date"]).strftime("%Y-%m-%d"))

    def result(dates: list[str]) -> dict[str, Any]:
        return {"count": len(dates), "first_flag_date": dates[0] if dates else None, "dates": dates}

    return {
        "successive_entries_at_lower_252d_highs": result(lower_high_dates),
        "drawdown_not_recovered_within_252_sessions": result(unrecovered_dates),
        "disp60_negative_more_than_120_sessions": result(negative_disp_dates),
    }


__all__ = [
    "CONFIDENCE_MARKETS",
    "FIT_END",
    "HOLDOUT_START",
    "JAPAN_END",
    "JAPAN_START",
    "MARKET_GROUPS",
    "NEGATIVE_DISP_SESSIONS",
    "RECOVERY_DRAWDOWN",
    "RECOVERY_SESSIONS",
    "SYNCHRONOUS_WINDOWS",
    "annualized_log_volatility",
    "annotate_episodes",
    "classify_episode",
    "compute_volatility_scale",
    "diagnostic_flags",
    "independent_observation_proxy",
    "normalized_thresholds",
    "period_episode_counts",
    "restrict_japan_window",
    "summarize_episode_classes",
    "underperformance_summary",
]
