"""Explicit, pluggable buy-signal candidates for retained-price research."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Literal

import numpy as np
import pandas as pd

from .condition_backtest import compute_signals


BuySignalKind = Literal["A", "B", "C", "D", "E"]
_UNDECIDED = object()


def _reject_outcome_columns(frame: pd.DataFrame) -> None:
    forbidden = sorted(
        str(column) for column in frame.columns
        if isinstance(column, str) and column.startswith(
            ("forward_", "future_", "label_", "outcome_")
        )
    )
    if forbidden:
        raise ValueError(f"outcome namespace is forbidden in signal input: {forbidden}")


def _required(value: Any, name: str) -> Any:
    if value is _UNDECIDED or value is None:
        raise ValueError(
            f"{name} is undecided under rule ⑥; caller must pass it explicitly"
        )
    return value


def _finite(value: Any, name: str) -> float:
    value = _required(value, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


@dataclass(frozen=True, slots=True)
class BuySignalSpec:
    """One candidate definition; only parameters used by its kind are required."""

    kind: BuySignalKind
    drawdown_threshold: float | object = _UNDECIDED
    disp60_threshold: float | object = _UNDECIDED
    rsi14_threshold: float | object = _UNDECIDED
    deceleration_lookback_days: int | object = _UNDECIDED
    deceleration_tolerance: float | object = _UNDECIDED
    rebound_pct: float | object = _UNDECIDED

    def __post_init__(self) -> None:
        if self.kind not in {"A", "B", "C", "D", "E"}:
            raise ValueError("kind must be one of A, B, C, D, or E")
        drawdown = _finite(self.drawdown_threshold, "drawdown_threshold")
        if not -1.0 < drawdown < 0.0:
            raise ValueError("drawdown_threshold must be between -1 and 0")
        if self.kind == "B":
            disparity = _finite(self.disp60_threshold, "disp60_threshold")
            if not -1.0 < disparity < 0.0:
                raise ValueError("disp60_threshold must be between -1 and 0")
        elif self.kind == "C":
            rsi = _finite(self.rsi14_threshold, "rsi14_threshold")
            if not 0.0 <= rsi <= 100.0:
                raise ValueError("rsi14_threshold must be in [0, 100]")
        elif self.kind == "D":
            lookback = _required(
                self.deceleration_lookback_days, "deceleration_lookback_days"
            )
            if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
                raise ValueError("deceleration_lookback_days must be a positive integer")
            tolerance = _finite(self.deceleration_tolerance, "deceleration_tolerance")
            if tolerance < 0.0:
                raise ValueError("deceleration_tolerance must be non-negative")
        elif self.kind == "E":
            rebound = _finite(self.rebound_pct, "rebound_pct")
            if rebound < 0.0:
                raise ValueError("rebound_pct must be non-negative")


def compute_signal_features(prices: pd.DataFrame) -> pd.DataFrame:
    """Build close-T features shared by candidates A-E without future data."""

    _reject_outcome_columns(prices)
    frame = compute_signals(prices).copy()
    frame = frame.drop(columns=[
        column for column in frame.columns if str(column).startswith("event_")
    ])
    grouped = frame.groupby("series_id", sort=False)
    frame["drawdown252_change_1"] = grouped["drawdown252"].diff(1)
    frame["running_min_close"] = grouped["close"].cummin()
    return frame


def _group_positions(frame: pd.DataFrame) -> list[pd.Index]:
    if "series_id" in frame:
        return [group.index for _, group in frame.groupby("series_id", sort=False)]
    return [frame.index]


def evaluate_buy_signal(features: pd.DataFrame, spec: BuySignalSpec) -> pd.DataFrame:
    """Evaluate a candidate and expose its component score and derived columns."""

    required = {"date", "drawdown252"}
    _reject_outcome_columns(features)
    if spec.kind in {"B"}:
        required.add("disp60")
    if spec.kind in {"C"}:
        required.add("rsi14")
    if spec.kind == "E":
        required.add("close")
    missing = required.difference(features.columns)
    if missing:
        raise ValueError(f"signal feature input is missing columns: {sorted(missing)}")

    frame = features.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    key_columns = ["date"] if "series_id" not in frame else ["series_id", "date"]
    if frame[key_columns].duplicated().any():
        raise ValueError("signal feature keys must be unique")
    for indices in _group_positions(frame):
        if not frame.loc[indices, "date"].is_monotonic_increasing:
            raise ValueError("signal dates must be increasing within each series")

    drawdown = pd.to_numeric(frame["drawdown252"], errors="coerce")
    drawdown_flag = drawdown.le(float(spec.drawdown_threshold)).where(drawdown.notna())
    conditions: list[pd.Series] = [drawdown_flag.astype("boolean")]
    names = ["condition_drawdown"]

    if spec.kind == "B":
        disparity = pd.to_numeric(frame["disp60"], errors="coerce")
        conditions.append(
            disparity.le(float(spec.disp60_threshold)).where(disparity.notna()).astype("boolean")
        )
        names.append("condition_disparity")
    elif spec.kind == "C":
        rsi = pd.to_numeric(frame["rsi14"], errors="coerce")
        conditions.append(
            rsi.le(float(spec.rsi14_threshold)).where(rsi.notna()).astype("boolean")
        )
        names.append("condition_rsi")
    elif spec.kind == "D":
        lookback = int(spec.deceleration_lookback_days)
        change = pd.Series(np.nan, index=frame.index, dtype="float64")
        for indices in _group_positions(frame):
            change.loc[indices] = drawdown.loc[indices].diff(lookback)
        column = f"drawdown252_change_{lookback}"
        frame[column] = change
        conditions.append(
            change.ge(-float(spec.deceleration_tolerance)).where(change.notna()).astype("boolean")
        )
        names.append("condition_decelerating")
    elif spec.kind == "E":
        close = pd.to_numeric(frame["close"], errors="coerce")
        trough = pd.Series(np.nan, index=frame.index, dtype="float64")
        active = pd.Series(False, index=frame.index, dtype="boolean")
        for indices in _group_positions(frame):
            prior_qualifies = False
            current_trough: float | None = None
            for index in indices:
                qualifies = bool(drawdown_flag.loc[index]) if pd.notna(drawdown_flag.loc[index]) else False
                if qualifies and not prior_qualifies:
                    current_trough = float(close.loc[index]) if pd.notna(close.loc[index]) else None
                elif current_trough is not None and pd.notna(close.loc[index]):
                    current_trough = min(current_trough, float(close.loc[index]))
                if current_trough is not None:
                    trough.loc[index] = current_trough
                    active.loc[index] = True
                prior_qualifies = qualifies
        frame["trough_since_drawdown_event"] = trough
        rebound = close.ge((1.0 + float(spec.rebound_pct)) * trough)
        rebound = rebound.where(active & close.notna() & trough.notna()).astype("boolean")
        conditions = [rebound]
        names = ["condition_rebound"]

    condition_frame = pd.concat(conditions, axis=1)
    condition_frame.columns = names
    for name in names:
        frame[name] = condition_frame[name]
    valid = condition_frame.notna().all(axis=1)
    raw_score = condition_frame.fillna(False).astype("int8").sum(axis=1)
    maximum = len(conditions)
    frame["raw_score"] = raw_score.astype("Int64").where(valid, pd.NA)
    frame["max_score"] = maximum
    frame["signal"] = frame["raw_score"].eq(maximum).fillna(False).astype(bool)
    return frame


def signal_claim_ko(spec: BuySignalSpec) -> str:
    """Return a one-line Korean claim that states exactly what the signal measures."""

    drawdown = f"{float(spec.drawdown_threshold) * 100:g}%"
    if spec.kind == "A":
        return f"A: 252일 낙폭이 {drawdown} 이하인 구간"
    if spec.kind == "B":
        return (
            f"B: 252일 낙폭이 {drawdown} 이하이고 60일 이격이 "
            f"{float(spec.disp60_threshold) * 100:g}% 이하인 구간"
        )
    if spec.kind == "C":
        return (
            f"C: 252일 낙폭이 {drawdown} 이하이고 RSI14가 "
            f"{float(spec.rsi14_threshold):g} 이하인 구간"
        )
    if spec.kind == "D":
        return (
            f"D: 252일 낙폭이 {drawdown} 이하이고 {int(spec.deceleration_lookback_days)}일 "
            f"낙폭 변화가 -{float(spec.deceleration_tolerance):g} 이상인 구간"
        )
    return (
        f"E: 낙폭이 {drawdown} 이하로 진입한 뒤 저점에서 "
        f"{float(spec.rebound_pct) * 100:g}% 이상 반등한 구간"
    )


__all__ = [
    "BuySignalKind",
    "BuySignalSpec",
    "compute_signal_features",
    "evaluate_buy_signal",
    "signal_claim_ko",
]
