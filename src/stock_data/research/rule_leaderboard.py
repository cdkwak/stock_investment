"""Deterministic leaderboard for versioned retained-data rule candidates."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .condition_backtest import (
    attach_volatility_index,
    build_forward_outcomes,
    build_wide_evaluation_frame,
    compute_signals,
    load_primary_indices,
    load_volatility_indices,
)
from .extreme_ladder import volatility_target_exposure
from .rule_candidates import (
    load_candidates,
    rules_version,
    validate_candidate,
    validate_registry,
)


FIT_END = "2015-12-31"
HOLDOUT_START = "2016-01-01"
MIN_SAMPLE = 15
HORIZONS: tuple[int, ...] = (20, 60, 90)
PRIMARY_SERIES: dict[str, tuple[str, ...]] = {
    "KR": ("KOSPI200", "KOSPI"),
    "US_TECH": ("NASDAQ100",),
    "SEMIS": ("SOX",),
    "POOLED": ("KOSPI200", "KOSPI", "NASDAQ100", "SOX"),
}
CYCLES: tuple[dict[str, str | None], ...] = (
    {"id": "kr_fx_1997", "label": "1997–98 외환위기 (KR)", "start": "1997-01-01", "end": "1998-12-31"},
    {"id": "dotcom_2000", "label": "2000–02 닷컴", "start": "2000-01-01", "end": "2002-12-31"},
    {"id": "gfc_2008", "label": "2008–09 금융위기", "start": "2008-01-01", "end": "2009-12-31"},
    {"id": "credit_2011", "label": "2011 (EU/미국 신용등급)", "start": "2011-01-01", "end": "2011-12-31"},
    {"id": "slowdown_2015", "label": "2015–16", "start": "2015-01-01", "end": "2016-12-31"},
    {"id": "selloff_2018", "label": "2018", "start": "2018-01-01", "end": "2018-12-31"},
    {"id": "covid_2020", "label": "2020 코로나", "start": "2020-01-01", "end": "2020-12-31"},
    {"id": "bear_2022", "label": "2022 인플레", "start": "2022-01-01", "end": "2022-12-31"},
    {"id": "recent_2025", "label": "2025–26", "start": "2025-01-01", "end": "2026-12-31"},
)
RESULT_KEYS = (
    "n", "independent_events", "cycles_with_signal", "signals_outside_cycles",
    "mean_20", "mean_60", "mean_90", "median_60", "hit_60",
    "baseline_60", "diff_60", "vol_60", "mdd_60", "warn_small_sample",
)
# Signal dates closer than this (calendar days, roughly 60 trading sessions = the 60-day
# return horizon) belong to the same episode: their forward windows overlap, so they are
# not independent observations. Dates are pooled across the basket's series first, so
# KOSPI and KOSPI200 signalling on the same crash count once.
EPISODE_GAP_DAYS = 90
COMPOUND_UNDERLYINGS: dict[str, tuple[str, ...]] = {
    "KR": ("KOSPI", "KOSPI200"), "US_TECH": ("NASDAQ100",), "SEMIS": ("SOX",),
}
COMPOUND_REFERENCE_COMBINATION = {
    "leverage_multiple": 2, "base_exposure": 1.0, "exit": "a", "cost_enabled": True,
}
COMPOUND_REFERENCE_LABEL = "합성 2배 · 출구 a · 거래비용 포함 · 기본 노출 1.0"

_CACHE_LOCK = threading.RLock()
_INDICATOR_FRAME_CACHE: dict[
    tuple[str, str],
    tuple[tuple[tuple[str, int, int], ...], pd.DataFrame, pd.DataFrame],
] = {}
_RETAINED_ROOTS: dict[str, tuple[str, ...]] = {
    "KR": (
        "data/normalized/kr_index_daily",
        "data/normalized/kr_kospi200_index_daily",
        "data/normalized/kr_vkospi_daily",
    ),
    "US_TECH": (
        "data/normalized/global_index_price_daily",
        "data/normalized/fred_vix_daily",
    ),
    "SEMIS": (
        "data/normalized/global_index_price_daily",
        "data/normalized/fred_vix_daily",
    ),
    "POOLED": (
        "data/normalized/kr_index_daily",
        "data/normalized/kr_kospi200_index_daily",
        "data/normalized/global_index_price_daily",
        "data/normalized/fred_vix_daily",
        "data/normalized/kr_vkospi_daily",
    ),
}


def _research_prices(prices: pd.DataFrame) -> pd.DataFrame:
    allowed = set(PRIMARY_SERIES["POOLED"])
    return prices.loc[prices["series_id"].astype(str).isin(allowed)].copy()


def prepare_indicator_frame(
    prices: pd.DataFrame, volatility_indices: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Build close-T indicators once for all candidates."""

    selected = _research_prices(prices)
    signals = compute_signals(selected)
    if volatility_indices is None or volatility_indices.empty:
        signals["vol_index_id"] = pd.NA
        signals["vol_index_close"] = np.nan
        signals["vol_index_percentile252"] = np.nan
    else:
        signals = attach_volatility_index(signals, volatility_indices)
    return signals.sort_values(["series_id", "date"], kind="mergesort").reset_index(drop=True)


def load_indicator_frame(project_root: Path) -> pd.DataFrame:
    """Load only retained Parquet inputs and return their cached signal frame."""

    root = project_root.resolve()
    return prepare_indicator_frame(
        load_primary_indices(root), load_volatility_indices(root)
    )


def load_evaluation_frame(project_root: Path) -> pd.DataFrame:
    root = project_root.resolve()
    prices = _research_prices(load_primary_indices(root))
    signals = prepare_indicator_frame(prices, load_volatility_indices(root))
    outcomes = build_forward_outcomes(prices, horizons=HORIZONS)
    return build_wide_evaluation_frame(signals, outcomes)


def _retained_signature(project_root: Path, basket: str) -> tuple[tuple[str, int, int], ...]:
    rows: list[tuple[str, int, int]] = []
    for relative in _RETAINED_ROOTS[basket]:
        path = project_root / relative
        if path.is_file() and path.suffix == ".parquet":
            paths = (path,)
        elif path.is_dir():
            paths = tuple(sorted(path.rglob("*.parquet"), key=lambda item: item.as_posix()))
        else:
            paths = ()
        for item in paths:
            stat = item.stat()
            rows.append((item.relative_to(project_root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(rows)


def _load_cached_evaluation_frame(project_root: Path, basket: str) -> pd.DataFrame:
    """Return a per-basket frame invalidated by exact retained-Parquet metadata."""

    root = project_root.resolve()
    signature = _retained_signature(root, basket)
    key = (str(root), basket)
    with _CACHE_LOCK:
        cached = _INDICATOR_FRAME_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[2]

    prices = _scope(_research_prices(load_primary_indices(root)), basket)
    signals = prepare_indicator_frame(prices, load_volatility_indices(root))
    outcomes = build_forward_outcomes(prices, horizons=HORIZONS)
    evaluation = build_wide_evaluation_frame(signals, outcomes)
    with _CACHE_LOCK:
        current_signature = _retained_signature(root, basket)
        if current_signature == signature:
            _INDICATOR_FRAME_CACHE[key] = (signature, signals, evaluation)
    return evaluation


def _scope(frame: pd.DataFrame, basket: str) -> pd.DataFrame:
    series = PRIMARY_SERIES[basket]
    return frame.loc[frame["series_id"].astype(str).isin(series)].copy()


def _ladder_definition(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    definition = candidate["definition"]
    if definition["type"] == "ladder":
        return definition
    if definition["type"] == "hybrid":
        return definition["ladder"]
    return None


def _direction(candidate: Mapping[str, Any]) -> str:
    if candidate["side"] in {"drawdown", "overheat"}:
        return str(candidate["side"])
    ladder = _ladder_definition(candidate)
    return str(ladder.get("side", "drawdown")) if ladder is not None else "drawdown"


def _realized_volatility(frame: pd.DataFrame, window: int) -> pd.Series:
    if window == 20 and "realized_volatility_20d" in frame:
        return pd.to_numeric(frame["realized_volatility_20d"], errors="coerce")
    result = pd.Series(np.nan, index=frame.index, dtype="float64")
    for _, group in frame.groupby("series_id", sort=True):
        close = pd.to_numeric(group["close"], errors="coerce")
        values = close.pct_change(fill_method=None).rolling(
            window, min_periods=window
        ).std(ddof=1) * np.sqrt(252.0)
        result.loc[group.index] = values
    return result


def score_candidate(frame: pd.DataFrame, candidate: Mapping[str, Any]) -> pd.DataFrame:
    """Return score, level, exposure, and headline-signal columns."""

    definition = candidate["definition"]
    ladder = _ladder_definition(candidate)
    state = pd.DataFrame(index=frame.index)
    if ladder is None:
        level = pd.Series(0, index=frame.index, dtype="Int64")
        score = level.copy()
        maximum = 0
    else:
        flags: list[pd.Series] = []
        for item in ladder["indicators"]:
            column = "vol_index_percentile252" if item["key"] == "volidx_pct" else item["key"]
            values = pd.to_numeric(frame[column], errors="coerce")
            flag = (
                values.le(float(item["threshold"]))
                if item["op"] == "<="
                else values.ge(float(item["threshold"]))
            )
            flags.append(flag.where(values.notna()).astype("boolean"))
        flag_frame = pd.concat(flags, axis=1)
        valid = flag_frame.notna().all(axis=1)
        score = flag_frame.fillna(False).astype("int8").sum(axis=1).astype("Int64")
        level = score.where(valid, pd.NA)
        maximum = int(ladder["levels"])
    if definition["type"] == "ladder":
        direction = _direction(candidate)
        if direction == "drawdown":
            exposure = level.astype("float64") / maximum
        else:
            exposure = 1.0 - level.astype("float64") / maximum
    else:
        vol_definition = (
            definition
            if definition["type"] == "vol_target"
            else definition["vol_target"]
        )
        base = volatility_target_exposure(
            _realized_volatility(frame, int(vol_definition["window"])),
            target_vol=float(vol_definition["target_vol"]),
        )
        if definition["type"] == "vol_target":
            exposure = base
        elif _direction(candidate) == "drawdown":
            exposure = np.minimum(1.0, base * (1.0 + level.astype("float64") / maximum))
        else:
            exposure = base * (1.0 - level.astype("float64") / maximum)
    state["score"] = score
    state["level"] = level
    state["max_level"] = maximum
    state["exposure"] = pd.to_numeric(exposure, errors="coerce").clip(0.0, 1.0)
    if definition["type"] == "vol_target":
        state["signal"] = state["exposure"].notna()
    else:
        state["signal"] = state["level"].eq(maximum).fillna(False)
    return state


def current_candidate_state(
    frame: pd.DataFrame, candidate: Mapping[str, Any], *, as_of: str | None = None
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Select the primary current row and return it with all scoped state."""

    scoped = _scope(frame, str(candidate["basket"]))
    if as_of is not None:
        scoped = scoped.loc[pd.to_datetime(scoped["date"]).le(pd.Timestamp(as_of))]
    if scoped.empty:
        raise ValueError(f"no retained observations for candidate {candidate['id']}")
    state = score_candidate(scoped, candidate)
    preferred = PRIMARY_SERIES[str(candidate["basket"])]
    selected_index: int | None = None
    for series_id in preferred:
        rows = scoped.loc[scoped["series_id"].eq(series_id)]
        if not rows.empty:
            selected_index = int(rows.sort_values("date", kind="mergesort").index[-1])
            break
    if selected_index is None:
        raise ValueError(f"no primary retained series for candidate {candidate['id']}")
    return scoped.loc[selected_index], state.loc[selected_index], state


def _period_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    observation = pd.to_datetime(frame["observation_date"], errors="raise")
    end_90 = pd.to_datetime(frame["outcome_end_date_90"], errors="coerce")
    return {
        "fit": end_90.le(pd.Timestamp(FIT_END)),
        "holdout": observation.ge(pd.Timestamp(HOLDOUT_START)) & end_90.notna(),
    }


def _adjusted(
    frame: pd.DataFrame,
    state: pd.DataFrame,
    column: str,
    candidate: Mapping[str, Any],
) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    return values if candidate["definition"]["type"] == "ladder" else values * state["exposure"]


def _independent_episodes(signal_dates: pd.Series) -> int:
    """Count signal episodes: pooled dates split wherever the gap exceeds EPISODE_GAP_DAYS."""

    dates = pd.to_datetime(signal_dates, errors="coerce").dropna().drop_duplicates().sort_values()
    episodes = 0
    previous: pd.Timestamp | None = None
    for date in dates:
        if previous is None or (date - previous).days > EPISODE_GAP_DAYS:
            episodes += 1
        previous = date
    return episodes


def _stats(
    frame: pd.DataFrame,
    state: pd.DataFrame,
    candidate: Mapping[str, Any],
    period_mask: pd.Series,
) -> dict[str, Any]:
    eligible = period_mask & frame["forward_return_90"].notna()
    selected = eligible & state["signal"] & state["exposure"].notna()
    # A hybrid whose signal rows carry zero exposure (overheat ladder at its top level
    # scales the vol-target exposure to 0) has nothing to measure: exposure × return is
    # identically 0 and would print as a perfect "0.0%" row. Report the sample size but
    # leave every metric undefined instead of a fake zero.
    zero_exposure_n = 0
    if selected.any() and pd.to_numeric(state.loc[selected, "exposure"], errors="coerce").eq(0.0).all():
        zero_exposure_n = int(selected.sum())
        selected = selected & False
    return_20 = _adjusted(frame, state, "forward_return_20", candidate).loc[selected].dropna()
    return_60 = _adjusted(frame, state, "forward_return_60", candidate).loc[selected].dropna()
    return_90 = _adjusted(frame, state, "forward_return_90", candidate).loc[selected].dropna()
    baseline = pd.to_numeric(frame.loc[eligible, "forward_return_60"], errors="coerce").dropna()
    volatility = _adjusted(
        frame, state, "forward_realized_volatility_60", candidate
    ).loc[selected].dropna()
    drawdown = _adjusted(
        frame, state, "forward_max_drawdown_60", candidate
    ).loc[selected].dropna()
    mean_60 = float(return_60.mean()) if not return_60.empty else np.nan
    baseline_60 = float(baseline.mean()) if not baseline.empty else np.nan
    observation = pd.to_datetime(frame["observation_date"], errors="raise")
    event_signals = period_mask & state["signal"] & state["exposure"].notna()
    episodes = _independent_episodes(observation.loc[event_signals])
    cycles_with_signal = 0
    inside_any = pd.Series(False, index=frame.index)
    for cycle in CYCLES:
        inside = observation.ge(pd.Timestamp(str(cycle["start"])))
        if cycle["end"] is not None:
            inside &= observation.le(pd.Timestamp(str(cycle["end"])))
        cycles_with_signal += int(bool((event_signals & inside).any()))
        inside_any |= inside
    result = {
        "n": int(return_60.size) if not zero_exposure_n else zero_exposure_n,
        "independent_events": episodes,
        "cycles_with_signal": cycles_with_signal,
        "signals_outside_cycles": int((event_signals & ~inside_any).sum()),
        "mean_20": float(return_20.mean()) if not return_20.empty else np.nan,
        "mean_60": mean_60,
        "mean_90": float(return_90.mean()) if not return_90.empty else np.nan,
        "median_60": float(return_60.median()) if not return_60.empty else np.nan,
        "hit_60": float(return_60.gt(0).mean()) if not return_60.empty else np.nan,
        "baseline_60": baseline_60,
        "diff_60": mean_60 - baseline_60,
        "vol_60": float(volatility.mean()) if not volatility.empty else np.nan,
        "mdd_60": float(drawdown.mean()) if not drawdown.empty else np.nan,
        "warn_small_sample": int(return_60.size) < MIN_SAMPLE,
    }
    assert tuple(result) == RESULT_KEYS
    return result


def _levels(
    frame: pd.DataFrame,
    state: pd.DataFrame,
    candidate: Mapping[str, Any],
    masks: Mapping[str, pd.Series],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    maximum = int(state["max_level"].iloc[0])
    adjusted = _adjusted(frame, state, "forward_return_60", candidate)
    for level in range(maximum + 1):
        row: dict[str, Any] = {"level": level}
        for period in ("fit", "holdout"):
            selected = masks[period] & state["level"].eq(level) & frame["forward_return_90"].notna()
            values = adjusted.loc[selected].dropna()
            row[period] = {
                "n": int(values.size),
                "mean_60": float(values.mean()) if not values.empty else np.nan,
            }
        rows.append(row)
    return rows


def _cycle_results(
    frame: pd.DataFrame, state: pd.DataFrame, candidate: Mapping[str, Any]
) -> list[dict[str, Any]]:
    observation = pd.to_datetime(frame["observation_date"], errors="raise")
    adjusted = _adjusted(frame, state, "forward_return_60", candidate)
    rows: list[dict[str, Any]] = []
    direction = _direction(candidate)
    for cycle in CYCLES:
        inside = observation.ge(pd.Timestamp(str(cycle["start"])))
        if cycle["end"] is not None:
            inside &= observation.le(pd.Timestamp(str(cycle["end"])))
        complete = inside & frame["forward_return_60"].notna()
        selected = complete & state["signal"] & state["exposure"].notna()
        values = adjusted.loc[selected].dropna()
        baseline = pd.to_numeric(frame.loc[complete, "forward_return_60"], errors="coerce").dropna()
        if values.empty or baseline.empty:
            verdict = "none"
        elif direction == "overheat":
            verdict = "hit" if float(values.mean()) < float(baseline.mean()) else "miss"
        else:
            verdict = "hit" if float(values.mean()) > float(baseline.mean()) else "miss"
        rows.append({
            "id": cycle["id"],
            "signals": int(values.size),
            "first_signal": (
                observation.loc[values.index].min().strftime("%Y-%m-%d")
                if not values.empty
                else None
            ),
            "mean_60": float(values.mean()) if not values.empty else np.nan,
            "verdict": verdict,
        })
    return rows


def _current(
    frame: pd.DataFrame, state: pd.DataFrame, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    current_row, current_state, _ = current_candidate_state(frame, candidate)
    same_level = state["level"].eq(current_state["level"]) & frame["forward_return_60"].notna()
    outcomes = pd.to_numeric(frame.loc[same_level, "forward_return_60"], errors="coerce").dropna()
    indicators = {
        "drawdown252": current_row.get("drawdown252"),
        "disp60": current_row.get("disp60"),
        "rsi14": current_row.get("rsi14"),
        "volidx_pct": current_row.get("vol_index_percentile252"),
    }
    return {
        "date": pd.Timestamp(current_row["date"]).strftime("%Y-%m-%d"),
        "score": current_state["score"],
        "level": current_state["level"],
        "max_level": current_state["max_level"],
        "exposure": current_state["exposure"],
        "indicators": indicators,
        "analog": {
            "n": int(outcomes.size),
            "mean_60": float(outcomes.mean()) if not outcomes.empty else np.nan,
            "hit_60": float(outcomes.gt(0).mean()) if not outcomes.empty else np.nan,
        },
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is pd.NA or value is None:
        return None
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def _compound_definition(candidate: Mapping[str, Any]) -> tuple[float, float, int] | None:
    definition = candidate.get("definition")
    if not isinstance(definition, Mapping) or definition.get("type") != "ladder":
        return None
    indicators = definition.get("indicators")
    if not isinstance(indicators, list) or len(indicators) != 2:
        return None
    by_key = {
        str(item.get("key")): item
        for item in indicators if isinstance(item, Mapping) and item.get("op") == "<="
    }
    if set(by_key) != {"drawdown252", "disp60"}:
        return None
    try:
        return (
            float(by_key["drawdown252"]["threshold"]),
            float(by_key["disp60"]["threshold"]),
            int(definition["levels"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _read_compound_references(project_root: Path) -> dict[str, dict[str, Any]]:
    """Read the existing compound-ladder summary and grids without recomputation."""

    output = project_root.resolve() / "artifacts/research/compound_ladder"
    try:
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    baskets = summary.get("baskets") if isinstance(summary, Mapping) else None
    if not isinstance(baskets, Mapping):
        return {}
    references: dict[str, list[dict[str, Any]]] = {}
    for basket, underlyings in COMPOUND_UNDERLYINGS.items():
        entries = baskets.get(basket)
        if not isinstance(entries, list):
            continue
        for underlying in underlyings:
            entry = next((
                item for item in entries
                if isinstance(item, Mapping) and item.get("underlying") == underlying
            ), None)
            if entry is None or not isinstance(entry.get("grid_path"), str):
                continue
            grid_path = (project_root.resolve() / str(entry["grid_path"])).resolve()
            if not grid_path.is_relative_to(output) or grid_path.suffix != ".json":
                continue
            try:
                rows = json.loads(grid_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            plateau_rows = entry.get("plateau")
            plateau = next((
                item for item in plateau_rows
                if isinstance(item, Mapping) and item.get("surface") == "threshold_x_levels"
            ), None) if isinstance(plateau_rows, list) else None
            if not isinstance(rows, list) or not isinstance(plateau, Mapping):
                continue
            references.setdefault(basket, []).append({
                "underlying": underlying,
                "rows": [dict(row) for row in rows if isinstance(row, Mapping)],
                "plateau": dict(plateau),
            })
    return references


def _compound_cross_reference(
    candidate: Mapping[str, Any], references: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> dict[str, Any]:
    """Cross-reference every underlying of the candidate's basket (KR = KOSPI and KOSPI200).

    The leaderboard pools a basket's series, so showing a single underlying would hide
    the weaker one. The combination is fixed (COMPOUND_REFERENCE_COMBINATION) and
    labelled in the payload so the UI never implies a different product or exit.
    """

    unavailable = {"status": "unavailable"}
    definition = _compound_definition(candidate)
    basket = str(candidate.get("basket") or "")
    entries = references.get(basket) if references else None
    if definition is None or not isinstance(entries, Sequence) or not entries:
        return unavailable
    drawdown, disp60, levels = definition

    def same_number(value: object, expected: float) -> bool:
        try:
            return math.isclose(float(value), expected, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False

    underlyings: list[dict[str, Any]] = []
    for reference in entries:
        rows = reference.get("rows")
        plateau = reference.get("plateau")
        if not isinstance(rows, list) or not isinstance(plateau, Mapping):
            continue
        matched = next((
            row for row in rows
            if isinstance(row, Mapping)
            and row.get("row_kind") == "strategy"
            and same_number(row.get("drawdown_threshold"), drawdown)
            and same_number(row.get("disp60_threshold"), disp60)
            and row.get("levels") == levels
            and all(row.get(key) == value for key, value in COMPOUND_REFERENCE_COMBINATION.items())
        ), None)
        holdout = matched.get("holdout") if isinstance(matched, Mapping) else None
        best = plateau.get("best_fit_relative_to_baseline")
        neighbour = plateau.get("neighbourhood_mean")
        if (
            not isinstance(holdout, Mapping)
            or not isinstance(best, (int, float)) or not isinstance(neighbour, (int, float))
        ):
            continue
        verdict = "뾰족한 봉우리" if plateau.get("sharp_peak") else "넓은 고원"
        underlyings.append({
            "underlying": reference.get("underlying"),
            "holdout_final_wealth_multiple": holdout.get("final_wealth_multiple"),
            "holdout_baseline_final_wealth_multiple": holdout.get("baseline_final_wealth_multiple"),
            "holdout_relative_to_baseline": holdout.get("relative_to_baseline"),
            "plateau_verdict": f"{verdict} · 최적 {float(best):.2f}배 / 이웃 {float(neighbour):.2f}배",
        })
    if not underlyings:
        return unavailable
    first = underlyings[0]
    return {
        "status": "matched",
        "product_basis": "synthetic_2x",
        "cost_enabled": True,
        "combination_label": COMPOUND_REFERENCE_LABEL,
        "underlyings": underlyings,
        # Backward-compatible scalar view = first underlying.
        "underlying": first["underlying"],
        "holdout_final_wealth_multiple": first["holdout_final_wealth_multiple"],
        "holdout_baseline_final_wealth_multiple": first["holdout_baseline_final_wealth_multiple"],
        "holdout_relative_to_baseline": first["holdout_relative_to_baseline"],
        "plateau_verdict": first["plateau_verdict"],
    }


def _evaluate_candidate(
    evaluation_frame: pd.DataFrame, candidate: Mapping[str, Any],
    compound_references: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    frame = _scope(evaluation_frame, str(candidate["basket"]))
    state = score_candidate(frame, candidate)
    masks = _period_masks(frame)
    return {
        **deepcopy(dict(candidate)),
        "results": {
            "fit": _stats(frame, state, candidate, masks["fit"]),
            "holdout": _stats(frame, state, candidate, masks["holdout"]),
        },
        "levels": _levels(frame, state, candidate, masks),
        "cycles": _cycle_results(frame, state, candidate),
        "current": _current(frame, state, candidate),
        "compound_ladder": _compound_cross_reference(candidate, compound_references),
    }


def evaluate_definition(
    project_root: Path,
    definition: dict[str, Any],
    basket: str,
    side: str,
    horizons: Sequence[int] = HORIZONS,
) -> dict[str, Any]:
    """Evaluate one unsaved definition against retained data deterministically.

    The returned mapping has the same candidate shape as a leaderboard row.  The
    public horizon selector is validated here while the stable leaderboard
    contract continues to report all three 20/60/90-session columns.
    """

    if basket not in PRIMARY_SERIES:
        raise ValueError(f"unsupported basket: {basket}")
    if side not in {"drawdown", "overheat"}:
        raise ValueError(f"unsupported side: {side}")
    selected_horizons = tuple(sorted(set(int(value) for value in horizons)))
    if not selected_horizons or any(value not in HORIZONS for value in selected_horizons):
        raise ValueError("horizons must use 20, 60, or 90")

    evaluation_definition = deepcopy(definition)
    candidate = {
        "id": "experiment",
        "name": "규칙 실험",
        "side": side,
        "basket": basket,
        "status": "experimental",
        "definition": evaluation_definition,
        "added_on": "1970-01-01",
        "reason": "저장되지 않은 retained-data 실험",
    }
    validation_candidate = deepcopy(candidate)
    if evaluation_definition.get("type") == "hybrid":
        ladder = evaluation_definition.get("ladder")
        if not isinstance(ladder, Mapping) or ladder.get("side") != side:
            raise ValueError("hybrid ladder side must match side")
        validation_candidate["side"] = "hybrid"
    validated = validate_candidate(validation_candidate)
    validated["side"] = side
    return _json_value(
        _evaluate_candidate(_load_cached_evaluation_frame(Path(project_root), basket), validated)
    )


def build_leaderboard(
    evaluation_frame: pd.DataFrame,
    registry: Mapping[str, Any],
    *,
    version: str,
    generated_at: str,
    compound_references: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build the schema-v2 web-consumer contract in registry order."""

    validated = validate_registry(registry)
    candidates: list[dict[str, Any]] = []
    for candidate in validated["candidates"]:
        candidates.append(_evaluate_candidate(evaluation_frame, candidate, compound_references))
    payload = {
        "schema_version": 2,
        "generated_at": generated_at,
        "rules_version": version,
        "attempt_count": validated["attempt_count"],
        "fit_window": {"end": FIT_END},
        "holdout_window": {"start": HOLDOUT_START},
        "cycles": [dict(item) for item in CYCLES],
        "candidates": candidates,
        "warnings": [
            "기술적 조건부 분포이며 투자성과·추천·체결 결과가 아닙니다.",
            "신호는 종가 T 이후 관측되며 실제 의사결정에는 다음 retained 세션부터만 사용할 수 있습니다.",
            "현재 retained snapshot은 원천 당시 빈티지·개정 이력을 완전히 재현하지 않을 수 있습니다.",
        ],
    }
    return _json_value(payload)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def run_rule_leaderboard(
    project_root: Path, *, generated_at: datetime | None = None
) -> tuple[Path, Path, dict[str, Any]]:
    root = project_root.resolve()
    registry = load_candidates(root)
    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    payload = build_leaderboard(
        load_evaluation_frame(root),
        registry,
        version=rules_version(root),
        generated_at=timestamp.isoformat(),
        compound_references=_read_compound_references(root),
    )
    output = root / "artifacts/research/rule_leaderboard"
    latest = output / "latest.json"
    dated = output / f"{timestamp.astimezone(ZoneInfo('Asia/Seoul')):%Y%m%d}.json"
    _write_json(dated, payload)
    _write_json(latest, payload)
    return latest, dated, payload


__all__ = [
    "CYCLES", "FIT_END", "HOLDOUT_START", "PRIMARY_SERIES", "RESULT_KEYS",
    "build_leaderboard", "current_candidate_state", "evaluate_definition", "load_evaluation_frame",
    "load_indicator_frame", "prepare_indicator_frame", "run_rule_leaderboard",
    "score_candidate",
]
