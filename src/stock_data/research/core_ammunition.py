"""Retained-data measurements for core assets around drawdown episodes.

The functions in this module are provider-free.  Signal-day (T) values are
same-close descriptive marks; a signal observed at T close cannot be acted on
until a later executable decision point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd


TRADING_DAYS = 252
EPISODE_COOLDOWN = 120
LEVEL_ZERO_LOOKBACK = 60
FOLLOWUP_HORIZONS = (0, 20, 60, 120, 250)


@dataclass(frozen=True, slots=True)
class Episode:
    episode_id: str
    market: str
    series_id: str
    signal_index: int
    signal_date: pd.Timestamp
    hold_start_index: int
    hold_start_date: pd.Timestamp
    t20_date: pd.Timestamp | None
    t60_date: pd.Timestamp | None
    cycle: str
    cycle_type: str
    drawdown252: float
    disp60: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def duration_proxy_returns(yield_percent: pd.Series, duration: float) -> pd.Series:
    """Approximate constant-maturity daily total returns from percentage yields.

    For each retained observation after the first valid yield, the return is
    ``-duration * delta_y + prior_y / 252``.  Internal missing observations are
    forward-filled so their retained business-day row earns carry with no price
    change.  Values before the first valid yield remain missing.
    """

    if not np.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    raw = pd.to_numeric(yield_percent, errors="coerce").astype("float64") / 100.0
    valid = raw.notna()
    if not valid.any():
        return pd.Series(np.nan, index=raw.index, dtype="float64")
    first_label = valid[valid].index[0]
    filled = raw.ffill()
    delta = filled.diff()
    carry = filled.shift(1) / TRADING_DAYS
    result = -float(duration) * delta + carry
    first_position = raw.index.get_loc(first_label)
    result.iloc[:first_position] = np.nan
    result.iloc[first_position] = 0.0
    return result.astype("float64")


def cash_proxy_returns(yield_percent: pd.Series) -> pd.Series:
    """Accrue a retained 3-month bill yield with no duration price effect."""

    raw = pd.to_numeric(yield_percent, errors="coerce").astype("float64") / 100.0
    valid = raw.notna()
    if not valid.any():
        return pd.Series(np.nan, index=raw.index, dtype="float64")
    first_label = valid[valid].index[0]
    filled = raw.ffill()
    result = filled.shift(1) / TRADING_DAYS
    first_position = raw.index.get_loc(first_label)
    result.iloc[:first_position] = np.nan
    result.iloc[first_position] = 0.0
    return result.astype("float64")


def returns_to_nav(returns: pd.Series) -> pd.Series:
    """Turn a possibly leading-null return series into a 100-based NAV."""

    values = pd.to_numeric(returns, errors="coerce").astype("float64")
    valid = values.notna()
    if not valid.any():
        return pd.Series(np.nan, index=values.index, dtype="float64")
    first_position = int(np.flatnonzero(valid.to_numpy())[0])
    tail = values.iloc[first_position:]
    if tail.isna().any() or not np.isfinite(tail).all() or tail.le(-1.0).any():
        raise ValueError("returns after inception must be finite and greater than -100%")
    output = pd.Series(np.nan, index=values.index, dtype="float64")
    output.iloc[first_position:] = 100.0 * (1.0 + tail).cumprod().to_numpy()
    return output


def _cycle_label(date: pd.Timestamp, market: str) -> str:
    year = int(date.year)
    if market == "KR" and 1997 <= year <= 1998:
        return "1997–98 외환위기 (KR)"
    if 2000 <= year <= 2002:
        return "2000–02 닷컴"
    if 2008 <= year <= 2009:
        return "2008–09 금융위기"
    if year == 2011:
        return "2011 (EU/미국 신용등급)"
    if 2015 <= year <= 2016:
        return "2015–16"
    if year == 2018:
        return "2018"
    if year == 2020:
        return "2020 코로나"
    if year == 2022:
        return "2022 인플레"
    if 2025 <= year <= 2026:
        return "2025–26"
    return f"기타 ({year})"


def cluster_level_two(
    ladder: pd.DataFrame,
    *,
    market: str,
    series_id: str,
    start_date: str | pd.Timestamp,
    cooldown_sessions: int = EPISODE_COOLDOWN,
) -> list[Episode]:
    """Select the first observed level-2 session, then suppress 120 sessions."""

    required = {"date", "observed_level", "drawdown252", "disp60"}
    missing = required.difference(ladder.columns)
    if missing:
        raise ValueError(f"ladder is missing columns: {sorted(missing)}")
    if cooldown_sessions < 0:
        raise ValueError("cooldown_sessions must be non-negative")
    frame = ladder.reset_index(drop=True).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise ValueError("ladder dates must be unique and increasing")
    threshold = pd.Timestamp(start_date).normalize()
    hit = frame["observed_level"].eq(2).fillna(False)
    candidates = [
        index
        for index in range(len(frame))
        if frame.at[index, "date"] >= threshold and bool(hit.iloc[index])
    ]
    selected: list[int] = []
    for index in candidates:
        if not selected or index - selected[-1] > cooldown_sessions:
            selected.append(index)

    episodes: list[Episode] = []
    zero = frame["observed_level"].eq(0).fillna(False)
    for index in selected:
        earlier_zero = np.flatnonzero(zero.iloc[:index].to_numpy())
        last_zero = int(earlier_zero[-1]) if len(earlier_zero) else 0
        hold_start_index = max(0, index - LEVEL_ZERO_LOOKBACK, last_zero)
        signal_date = pd.Timestamp(frame.at[index, "date"])
        t20_date = pd.Timestamp(frame.at[index + 20, "date"]) if index + 20 < len(frame) else None
        t60_date = pd.Timestamp(frame.at[index + 60, "date"]) if index + 60 < len(frame) else None
        episodes.append(
            Episode(
                episode_id=f"{market}_{signal_date:%Y-%m-%d}",
                market=market,
                series_id=series_id,
                signal_index=index,
                signal_date=signal_date,
                hold_start_index=hold_start_index,
                hold_start_date=pd.Timestamp(frame.at[hold_start_index, "date"]),
                t20_date=t20_date,
                t60_date=t60_date,
                cycle=_cycle_label(signal_date, market),
                cycle_type="인플레형" if signal_date.year == 2022 else "경기침체형",
                drawdown252=float(frame.at[index, "drawdown252"]),
                disp60=float(frame.at[index, "disp60"]),
            )
        )
    return episodes


def prepare_value_series(dates: Iterable[Any], values: Iterable[Any]) -> pd.Series:
    """Validate a unique, increasing, positive asset value series."""

    frame = pd.DataFrame({"date": list(dates), "value": list(values)})
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["value"]).sort_values("date", kind="mergesort")
    if frame["date"].duplicated().any():
        raise ValueError("asset dates must be unique")
    if frame.empty or not np.isfinite(frame["value"]).all() or frame["value"].le(0).any():
        raise ValueError("asset values must contain finite positive observations")
    return frame.set_index("date")["value"].astype("float64")


def _asof(series: pd.Series, date: pd.Timestamp | None) -> float | None:
    if date is None or series.empty:
        return None
    position = int(series.index.searchsorted(pd.Timestamp(date), side="right")) - 1
    return None if position < 0 else float(series.iloc[position])


def episode_session_date(
    episode: Episode,
    session_dates: Iterable[Any],
    offset: int,
) -> pd.Timestamp | None:
    """Return the market-session date at ``T + offset`` for an episode."""

    if isinstance(offset, bool) or not isinstance(offset, (int, np.integer)) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    dates = pd.DatetimeIndex(pd.to_datetime(list(session_dates), errors="raise")).normalize()
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("session dates must be unique and increasing")
    target = episode.signal_index + int(offset)
    if target >= len(dates):
        return None
    date = pd.Timestamp(dates[target])
    if offset == 0 and date != episode.signal_date:
        raise ValueError("episode signal index does not match the supplied session calendar")
    return date


def measure_asset_horizons(
    values: pd.Series,
    episode: Episode,
    equity_close: pd.Series,
    session_dates: Iterable[Any],
    *,
    offsets: Sequence[int] = FOLLOWUP_HORIZONS,
) -> dict[str, Any]:
    """Measure existing asset/equity valuation semantics at extra horizons.

    Values remain normalized to the episode's existing ``hold_start_date``.
    Target dates use the episode market's retained session calendar, with
    asset and equity marks aligned as-of that date.
    """

    if any(
        isinstance(offset, bool)
        or not isinstance(offset, (int, np.integer))
        or offset < 0
        for offset in offsets
    ):
        raise ValueError("offsets must contain non-negative integers")
    normalized_offsets = tuple(int(offset) for offset in offsets)
    if len(set(normalized_offsets)) != len(normalized_offsets):
        raise ValueError("offsets must be unique")
    start_value = _asof(values, episode.hold_start_date)
    equity_start = _asof(equity_close, episode.hold_start_date)
    row: dict[str, Any] = {
        "episode_id": episode.episode_id,
        "market": episode.market,
        "signal_date": episode.signal_date,
        "hold_start_date": episode.hold_start_date,
        "cycle": episode.cycle,
        "cycle_type": episode.cycle_type,
    }
    for offset in normalized_offsets:
        label = "t" if offset == 0 else str(offset)
        target_date = episode_session_date(episode, session_dates, offset)
        target_value = _asof(values, target_date)
        equity_target = _asof(equity_close, target_date)
        row[f"date_{label}"] = target_date
        row[f"value_{label}"] = (
            100.0 * target_value / start_value
            if start_value is not None and target_value is not None
            else None
        )
        row[f"equity_value_{label}"] = (
            100.0 * equity_target / equity_start
            if equity_start is not None and equity_target is not None
            else None
        )
    return row


def peak_after_episode(
    values: pd.Series,
    episode: Episode,
    session_dates: Iterable[Any],
    *,
    max_offset: int = 250,
) -> dict[str, Any]:
    """Locate the first maximum asset level from T through ``max_offset``.

    The returned ``full_window`` distinguishes a settled peak from a
    right-censored, still-observed maximum.  Levels reuse the same hold-start
    normalization and as-of session alignment as the episode valuation.
    """

    if isinstance(max_offset, bool) or not isinstance(max_offset, (int, np.integer)):
        raise ValueError("max_offset must be a non-negative integer")
    if max_offset < 0:
        raise ValueError("max_offset must be a non-negative integer")
    dates = pd.DatetimeIndex(pd.to_datetime(list(session_dates), errors="raise")).normalize()
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("session dates must be unique and increasing")
    if episode.signal_index >= len(dates) or dates[episode.signal_index] != episode.signal_date:
        raise ValueError("episode signal index does not match the supplied session calendar")
    available_offset = min(int(max_offset), len(dates) - 1 - episode.signal_index)
    full_window = available_offset == int(max_offset)
    start_value = _asof(values, episode.hold_start_date)
    if start_value is None:
        return {
            "peak_offset": None,
            "peak_date": None,
            "peak_value": None,
            "observed_through_offset": available_offset,
            "full_window": full_window,
        }
    levels: list[float] = []
    valid_offsets: list[int] = []
    for offset in range(available_offset + 1):
        target_date = pd.Timestamp(dates[episode.signal_index + offset])
        target_value = _asof(values, target_date)
        if target_value is not None:
            valid_offsets.append(offset)
            levels.append(100.0 * target_value / start_value)
    if not levels:
        peak_offset = None
        peak_date = None
        peak_value = None
    else:
        peak_position = int(np.argmax(np.asarray(levels, dtype="float64")))
        peak_offset = valid_offsets[peak_position]
        peak_date = pd.Timestamp(dates[episode.signal_index + peak_offset])
        peak_value = float(levels[peak_position])
    return {
        "peak_offset": peak_offset,
        "peak_date": peak_date,
        "peak_value": peak_value,
        "observed_through_offset": available_offset,
        "full_window": full_window,
    }


def krw_converted_value(local_usd_value: float, start_fx: float, target_fx: float) -> float:
    """Convert a 100-based USD asset value with KRW-per-USD observations."""

    inputs = np.asarray([local_usd_value, start_fx, target_fx], dtype="float64")
    if not np.isfinite(inputs).all() or local_usd_value < 0 or start_fx <= 0 or target_fx <= 0:
        raise ValueError("local value and FX observations must be finite and valid")
    return float(local_usd_value * target_fx / start_fx)


def max_drawdown_in_window(
    values: pd.Series,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp | None,
) -> float | None:
    """Return path max drawdown, including the last mark on/before start."""

    if end_date is None:
        return None
    start_position = int(values.index.searchsorted(start_date, side="right")) - 1
    if start_position < 0:
        return None
    end_position = int(values.index.searchsorted(end_date, side="right"))
    window = values.iloc[start_position:end_position]
    if window.empty:
        return None
    running_max = window.cummax()
    return float((window / running_max - 1.0).min())


def measure_asset_episode(
    values: pd.Series,
    episode: Episode,
    equity_close: pd.Series,
    *,
    window_start_date: pd.Timestamp,
    fx_values: pd.Series | None = None,
) -> dict[str, Any]:
    """Measure a 100-based core holding and its equity comparison at T/+20/+60."""

    start_value = _asof(values, episode.hold_start_date)
    equity_start = _asof(equity_close, episode.hold_start_date)
    fx_start = _asof(fx_values, episode.hold_start_date) if fx_values is not None else None
    row: dict[str, Any] = {
        "episode_id": episode.episode_id,
        "market": episode.market,
        "series_id": episode.series_id,
        "signal_date": episode.signal_date,
        "hold_start_date": episode.hold_start_date,
        "cycle": episode.cycle,
        "cycle_type": episode.cycle_type,
        "max_drawdown": max_drawdown_in_window(values, window_start_date, episode.t60_date),
        "full_120_session_window": episode.t60_date is not None,
    }
    for label, target_date in (
        ("t", episode.signal_date),
        ("20", episode.t20_date),
        ("60", episode.t60_date),
    ):
        target_value = _asof(values, target_date)
        equity_target = _asof(equity_close, target_date)
        value = (
            100.0 * target_value / start_value
            if start_value is not None and target_value is not None
            else None
        )
        equity_value = (
            100.0 * equity_target / equity_start
            if equity_start is not None and equity_target is not None
            else None
        )
        row[f"date_{label}"] = target_date
        row[f"value_{label}"] = value
        row[f"equity_value_{label}"] = equity_value
        if fx_values is not None:
            fx_target = _asof(fx_values, target_date)
            row[f"fx_move_{label}"] = (
                fx_target / fx_start - 1.0
                if fx_start is not None and fx_target is not None
                else None
            )
            row[f"krw_value_{label}"] = (
                krw_converted_value(value, fx_start, fx_target)
                if value is not None and fx_start is not None and fx_target is not None
                else None
            )
    return row


def classify_asset(
    rows: pd.DataFrame,
    *,
    floor_statistic: str = "worst",
) -> dict[str, Any]:
    """Apply fixed ammunition / buy-target thresholds.

    ``floor_statistic='p10'`` changes only the downside floor from the worst
    observation to the empirical 10th percentile; all other criteria stay
    fixed.
    """

    required = {"value_t", "value_60", "equity_value_t", "equity_value_60"}
    missing = required.difference(rows.columns)
    if missing:
        raise ValueError(f"classification rows are missing columns: {sorted(missing)}")
    if floor_statistic not in {"worst", "p10"}:
        raise ValueError("floor_statistic must be 'worst' or 'p10'")
    t_values = pd.to_numeric(rows["value_t"], errors="coerce").dropna()
    if t_values.empty:
        return {
            "classification": "중립",
            "observations_t": 0,
            "share_t_ge_100": None,
            "worst_t": None,
            "p10_t": None,
            "floor_statistic": floor_statistic,
            "floor_value": None,
            "recovery_observations": 0,
            "share_recovery_beats_equity": None,
        }
    share_t = float(t_values.ge(100.0).mean())
    worst_t = float(t_values.min())
    p10_t = float(t_values.quantile(0.10))
    floor_value = worst_t if floor_statistic == "worst" else p10_t
    valid = rows[["value_t", "value_60", "equity_value_t", "equity_value_60"]].apply(
        pd.to_numeric, errors="coerce"
    ).dropna()
    if valid.empty:
        share_recovery = None
    else:
        asset_recovery = valid["value_60"] / valid["value_t"] - 1.0
        equity_recovery = valid["equity_value_60"] / valid["equity_value_t"] - 1.0
        share_recovery = float(asset_recovery.gt(equity_recovery).mean())
    if share_t >= 0.70 and floor_value >= 95.0:
        classification = "실탄"
    elif floor_value < 95.0 and share_recovery is not None and share_recovery > 0.50:
        classification = "매수 대상"
    else:
        classification = "중립"
    return {
        "classification": classification,
        "observations_t": int(len(t_values)),
        "share_t_ge_100": share_t,
        "worst_t": worst_t,
        "p10_t": p10_t,
        "floor_statistic": floor_statistic,
        "floor_value": floor_value,
        "recovery_observations": int(len(valid)),
        "share_recovery_beats_equity": share_recovery,
    }


def quantile_values(
    rows: pd.DataFrame,
    *,
    labels: Sequence[str] = ("t", "20", "60"),
) -> dict[str, dict[str, Any]]:
    """Return empirical p10/p25/median/p75 values for named horizons."""

    output: dict[str, dict[str, Any]] = {}
    for label in labels:
        column = f"value_{label}"
        if column not in rows:
            raise ValueError(f"quantile rows are missing column: {column}")
        values = pd.to_numeric(rows[column], errors="coerce").dropna()
        output[str(label)] = {
            "count": int(len(values)),
            "p10": float(values.quantile(0.10)) if len(values) else None,
            "p25": float(values.quantile(0.25)) if len(values) else None,
            "median": float(values.median()) if len(values) else None,
            "p75": float(values.quantile(0.75)) if len(values) else None,
        }
    return output


def fixed_crisis_types(delta_10y: float, delta_2y: float) -> dict[str, str]:
    """Apply the two predeclared binary crisis-type rules without tuning."""

    values = np.asarray([delta_10y, delta_2y], dtype="float64")
    if not np.isfinite(values).all():
        raise ValueError("yield deltas must be finite")
    return {
        "ten_year_rule": "인플레형" if float(delta_10y) > 0.0 else "침체형",
        "two_year_first_rule": "침체형" if float(delta_2y) < -0.5 else "인플레형",
    }


def aggregate_values(rows: pd.DataFrame) -> dict[str, Any]:
    """Return count/median/worst/best for T, +20 and +60."""

    output: dict[str, Any] = {}
    for label in ("t", "20", "60"):
        values = pd.to_numeric(rows[f"value_{label}"], errors="coerce").dropna()
        output[label] = {
            "count": int(len(values)),
            "median": float(values.median()) if len(values) else None,
            "worst": float(values.min()) if len(values) else None,
            "best": float(values.max()) if len(values) else None,
        }
    return output


__all__ = [
    "EPISODE_COOLDOWN",
    "Episode",
    "FOLLOWUP_HORIZONS",
    "LEVEL_ZERO_LOOKBACK",
    "TRADING_DAYS",
    "aggregate_values",
    "cash_proxy_returns",
    "classify_asset",
    "cluster_level_two",
    "duration_proxy_returns",
    "episode_session_date",
    "fixed_crisis_types",
    "krw_converted_value",
    "max_drawdown_in_window",
    "measure_asset_episode",
    "measure_asset_horizons",
    "peak_after_episode",
    "prepare_value_series",
    "quantile_values",
    "returns_to_nav",
]
