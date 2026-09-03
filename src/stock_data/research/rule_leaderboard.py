"""Deterministic leaderboard for versioned retained-data rule candidates."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
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
from .rule_candidates import load_candidates, rules_version, validate_registry


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
    {"id": "dotcom_2000", "label": "2000 닷컴", "start": "2000-03-01", "end": "2002-10-31"},
    {"id": "gfc_2008", "label": "2008 금융위기", "start": "2007-10-01", "end": "2009-03-31"},
    {"id": "covid_2020", "label": "2020 코로나", "start": "2020-02-01", "end": "2020-06-30"},
    {"id": "bear_2022", "label": "2022 약세장", "start": "2022-01-01", "end": "2022-12-31"},
    {"id": "recent_2025", "label": "2025-26", "start": "2025-01-01", "end": None},
)
RESULT_KEYS = (
    "n", "mean_20", "mean_60", "mean_90", "median_60", "hit_60",
    "baseline_60", "diff_60", "vol_60", "mdd_60", "warn_small_sample",
)


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


def _stats(
    frame: pd.DataFrame,
    state: pd.DataFrame,
    candidate: Mapping[str, Any],
    period_mask: pd.Series,
) -> dict[str, Any]:
    eligible = period_mask & frame["forward_return_90"].notna()
    selected = eligible & state["signal"] & state["exposure"].notna()
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
    result = {
        "n": int(return_60.size),
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


def build_leaderboard(
    evaluation_frame: pd.DataFrame,
    registry: Mapping[str, Any],
    *,
    version: str,
    generated_at: str,
) -> dict[str, Any]:
    """Build the exact schema-v1 web-consumer contract in registry order."""

    validated = validate_registry(registry)
    candidates: list[dict[str, Any]] = []
    for candidate in validated["candidates"]:
        frame = _scope(evaluation_frame, candidate["basket"])
        state = score_candidate(frame, candidate)
        masks = _period_masks(frame)
        candidates.append({
            **candidate,
            "results": {
                "fit": _stats(frame, state, candidate, masks["fit"]),
                "holdout": _stats(frame, state, candidate, masks["holdout"]),
            },
            "levels": _levels(frame, state, candidate, masks),
            "cycles": _cycle_results(frame, state, candidate),
            "current": _current(frame, state, candidate),
        })
    payload = {
        "schema_version": 1,
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
    )
    output = root / "artifacts/research/rule_leaderboard"
    latest = output / "latest.json"
    dated = output / f"{timestamp.astimezone(ZoneInfo('Asia/Seoul')):%Y%m%d}.json"
    _write_json(dated, payload)
    _write_json(latest, payload)
    return latest, dated, payload


__all__ = [
    "CYCLES", "FIT_END", "HOLDOUT_START", "PRIMARY_SERIES", "RESULT_KEYS",
    "build_leaderboard", "current_candidate_state", "load_evaluation_frame",
    "load_indicator_frame", "prepare_indicator_frame", "run_rule_leaderboard",
    "score_candidate",
]
