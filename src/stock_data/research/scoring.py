"""Side-specific event scoring and compact research result cards."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .rule_leaderboard import independent_episodes
from .signals import (
    BuySignalSpec,
    compute_signal_features,
    evaluate_buy_signal,
    signal_claim_ko,
)


BUY_HORIZONS: tuple[int, ...] = (21, 63, 126, 252)
MONTH_LABELS: dict[int, str] = {21: "1개월", 63: "3개월", 126: "6개월", 252: "12개월"}


def _prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "close"}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"price input is missing columns: {sorted(missing)}")
    frame = prices.loc[:, [column for column in prices.columns if column in {
        "date", "close", "series_id", "basket", "volume"
    }]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="raise").astype("float64")
    if frame.empty or not np.isfinite(frame["close"]).all() or frame["close"].le(0).any():
        raise ValueError("price close values must be non-empty, finite, and positive")
    if "series_id" in frame and frame["series_id"].astype(str).nunique() != 1:
        raise ValueError("event scoring requires exactly one price series")
    if frame["date"].duplicated().any():
        raise ValueError("price dates must be unique")
    return frame.sort_values("date", kind="mergesort").reset_index(drop=True)


def _horizons(values: Sequence[int]) -> tuple[int, ...]:
    result = tuple(int(value) for value in values)
    if not result or len(result) != len(set(result)) or any(value < 1 for value in result):
        raise ValueError("horizons must be unique positive session counts")
    return result


def _event_rows(frame: pd.DataFrame, event_dates: Iterable[Any]) -> tuple[pd.Series, list[int]]:
    dates = pd.to_datetime(pd.Series(list(event_dates), dtype="object"), errors="raise").dt.normalize()
    dates = dates.drop_duplicates().sort_values().reset_index(drop=True)
    by_date = {date: index for index, date in enumerate(frame["date"])}
    missing = [date.strftime("%Y-%m-%d") for date in dates if date not in by_date]
    if missing:
        raise ValueError(f"event dates are absent from prices: {missing}")
    return dates, [by_date[date] for date in dates]


def _number(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def score_buy_events(
    prices: pd.DataFrame,
    event_dates: Iterable[Any],
    horizons: Sequence[int] = BUY_HORIZONS,
) -> dict[str, Any]:
    """Score buy events only by forward return distribution and positive-return rate."""

    frame = _prices(prices)
    selected_horizons = _horizons(horizons)
    dates, positions = _event_rows(frame, event_dates)
    close = frame["close"].to_numpy(dtype="float64")
    rows: dict[str, dict[str, Any]] = {}
    for horizon in selected_horizons:
        values = np.asarray([
            close[position + horizon] / close[position] - 1.0
            for position in positions
            if position + horizon < len(close)
        ], dtype="float64")
        rows[str(horizon)] = {
            "mean_return": _number(float(values.mean())) if values.size else None,
            "median_return": _number(float(np.median(values))) if values.size else None,
            "win_rate": _number(float(np.mean(values > 0.0))) if values.size else None,
            "events_mature": int(values.size),
            "horizon_sessions": horizon,
        }
    return {
        "side": "buy",
        "events_total": int(len(dates)),
        "events_independent": independent_episodes(dates),
        "horizons": rows,
    }


def _path_max_drawdown(values: np.ndarray) -> float:
    running_peak = np.maximum.accumulate(values)
    return float(np.min(values / running_peak - 1.0))


def score_sell_events(
    prices: pd.DataFrame,
    event_dates: Iterable[Any],
    horizons: Sequence[int],
) -> dict[str, Any]:
    """Score sell events only by subsequent realised volatility and path drawdown."""

    frame = _prices(prices)
    selected_horizons = _horizons(horizons)
    dates, positions = _event_rows(frame, event_dates)
    close = frame["close"].to_numpy(dtype="float64")
    rows: dict[str, dict[str, Any]] = {}
    for horizon in selected_horizons:
        volatilities: list[float] = []
        drawdowns: list[float] = []
        for position in positions:
            if position + horizon >= len(close):
                continue
            path = close[position : position + horizon + 1]
            daily = path[1:] / path[:-1] - 1.0
            volatility = (
                float(np.std(daily, ddof=1) * np.sqrt(252.0))
                if daily.size > 1
                else 0.0
            )
            volatilities.append(volatility)
            drawdowns.append(_path_max_drawdown(path))
        vol = np.asarray(volatilities, dtype="float64")
        mdd = np.asarray(drawdowns, dtype="float64")
        rows[str(horizon)] = {
            "mean_realized_volatility": _number(float(vol.mean())) if vol.size else None,
            "median_realized_volatility": _number(float(np.median(vol))) if vol.size else None,
            "mean_max_drawdown": _number(float(mdd.mean())) if mdd.size else None,
            "median_max_drawdown": _number(float(np.median(mdd))) if mdd.size else None,
            "events_mature": int(vol.size),
            "horizon_sessions": horizon,
        }
    return {
        "side": "sell",
        "events_total": int(len(dates)),
        "events_independent": independent_episodes(dates),
        "horizons": rows,
    }


def _average_path(
    frame: pd.DataFrame, positions: Sequence[int], *, window: int
) -> list[dict[str, Any]]:
    close = frame["close"].to_numpy(dtype="float64")
    rows: list[dict[str, Any]] = []
    for offset in range(-window, window + 1):
        values = [
            close[position + offset] / close[position] * 100.0
            for position in positions
            if 0 <= position + offset < len(close)
        ]
        rows.append({
            "offset_sessions": offset,
            "mean_index": float(np.mean(values)) if values else None,
            "events": len(values),
        })
    return rows


def validate_result_card(card: Mapping[str, Any]) -> None:
    """Reject cards that could hide sample size or median outcomes."""

    required = {"claim", "events_total", "events_independent", "table", "average_path"}
    missing = required.difference(card)
    if missing:
        raise ValueError(f"result card is missing mandatory fields: {sorted(missing)}")
    if not isinstance(card["events_total"], int) or not isinstance(card["events_independent"], int):
        raise ValueError("result card event counts must be integers")
    table = card["table"]
    if not isinstance(table, list) or len(table) != len(BUY_HORIZONS):
        raise ValueError("result card table must contain 1/3/6/12-month rows")
    mandatory = {
        "label", "events_total", "events_independent", "horizon_sessions",
        "events_mature", "mean_return", "median_return", "win_rate",
    }
    for row in table:
        if not isinstance(row, Mapping) or mandatory.difference(row):
            raise ValueError("result card rows require counts, mean, median, and win rate")
        if tuple(row)[:3] != ("mean_return", "median_return", "win_rate"):
            raise ValueError("buy result card columns must put return and win rate first")


def result_card(signal_spec: BuySignalSpec, prices: pd.DataFrame) -> dict[str, Any]:
    """Build the claim, mandatory return table, and ±252-session average path."""

    frame = _prices(prices)
    feature_input = frame.copy()
    if "series_id" not in feature_input:
        feature_input["series_id"] = "SERIES"
    if "basket" not in feature_input:
        feature_input["basket"] = "RESEARCH"
    features = compute_signal_features(feature_input)
    evaluated = evaluate_buy_signal(features, signal_spec)
    signal = evaluated["signal"].astype(bool)
    event_mask = signal & ~signal.shift(1, fill_value=False)
    event_dates = evaluated.loc[event_mask, "date"]
    scores = score_buy_events(frame, event_dates, BUY_HORIZONS)
    _, positions = _event_rows(frame, event_dates)
    table = [
        {
            "mean_return": scores["horizons"][str(horizon)]["mean_return"],
            "median_return": scores["horizons"][str(horizon)]["median_return"],
            "win_rate": scores["horizons"][str(horizon)]["win_rate"],
            "events_total": scores["events_total"],
            "events_independent": scores["events_independent"],
            "events_mature": scores["horizons"][str(horizon)]["events_mature"],
            "label": MONTH_LABELS[horizon],
            "horizon_sessions": horizon,
        }
        for horizon in BUY_HORIZONS
    ]
    card = {
        "claim": signal_claim_ko(signal_spec),
        "events_total": scores["events_total"],
        "events_independent": scores["events_independent"],
        "table": table,
        "average_path": _average_path(frame, positions, window=max(BUY_HORIZONS)),
    }
    validate_result_card(card)
    return card


__all__ = [
    "BUY_HORIZONS",
    "result_card",
    "score_buy_events",
    "score_sell_events",
    "validate_result_card",
]
