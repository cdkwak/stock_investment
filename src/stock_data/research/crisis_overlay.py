"""Provider-free crisis-aligned paths built from retained valuation inputs."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from stock_data.research.compound_ladder import LadderSpec, ladder_levels
from stock_data.research.condition_backtest import compute_signals
from stock_data.research.core_ammunition import (
    Episode,
    cluster_level_two,
    measure_asset_horizons,
    prepare_value_series,
)


OFFSET_START = -60
OFFSET_END = 250
OFFSETS = tuple(range(OFFSET_START, OFFSET_END + 1))
HOLDOUT_START = pd.Timestamp("2016-01-01")
EQUITY_ASSET_ID = "equity_reference"
SCHEMA_VERSION = 2
NORMALISATION_LABELS = {
    "hold_start": "보유시작 = 100",
    "signal": "신호일 = 100",
}
LADDER_SERIES: dict[str, tuple[str, ...]] = {
    "KR": ("KOSPI", "KOSPI200", "KOSPI200_IT"),
    "US_TECH": ("NASDAQ100",),
    "SEMIS": ("SOX",),
}


def select_first_cycle_episodes(
    episodes: Iterable[Episode], cycle_buckets: Sequence[str],
) -> list[Episode]:
    """Keep the first core-ammunition level-2 episode in each market/cycle."""

    allowed = set(cycle_buckets)
    first: dict[tuple[str, str], Episode] = {}
    for episode in sorted(episodes, key=lambda item: (item.signal_date, item.market)):
        if episode.cycle in allowed:
            first.setdefault((episode.market, episode.cycle), episode)
    market_order = {"KR": 0, "US": 1, "US_TECH": 1, "SEMIS": 2}
    cycle_order = {cycle: index for index, cycle in enumerate(cycle_buckets)}
    return sorted(
        first.values(),
        key=lambda item: (
            cycle_order[item.cycle], market_order.get(item.market, 99), item.signal_date,
        ),
    )


def _calendar(session_dates: Iterable[Any]) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(list(session_dates), errors="raise")).normalize()
    if dates.has_duplicates or not dates.is_monotonic_increasing:
        raise ValueError("session dates must be unique and increasing")
    return dates


def _asof(series: pd.Series, date: pd.Timestamp | None) -> float | None:
    if date is None or series.empty:
        return None
    position = int(series.index.searchsorted(pd.Timestamp(date), side="right")) - 1
    if position < 0:
        return None
    value = float(series.iloc[position])
    return value if math.isfinite(value) else None


def aligned_core_path(
    values: pd.Series,
    episode: Episode,
    equity_close: pd.Series,
    session_dates: Iterable[Any],
    *,
    offsets: Sequence[int] = OFFSETS,
    basis: str = "signal",
) -> tuple[list[float | None], list[str | None], float | None]:
    """Align core-ammunition marks on either signal-day or hold-start basis.

    T, +20, and +60 are obtained through ``measure_asset_horizons`` so those
    checkpoints retain the exact as-of and valuation semantics of the core
    ammunition tables.  Other offsets use the same retained as-of rule.
    ``hold_start`` preserves the table valuation exactly; ``signal`` divides
    those marks by their T value so the signal session is 100.
    """

    if basis not in NORMALISATION_LABELS:
        raise ValueError(f"unsupported normalisation basis: {basis}")
    clean_offsets = tuple(int(offset) for offset in offsets)
    if len(clean_offsets) != len(set(clean_offsets)):
        raise ValueError("offsets must be unique")
    dates = _calendar(session_dates)
    if episode.signal_index >= len(dates) or dates[episode.signal_index] != episode.signal_date:
        raise ValueError("episode signal index does not match the supplied session calendar")
    positive = tuple(offset for offset in clean_offsets if offset in {0, 20, 60})
    if any(offset >= 0 for offset in clean_offsets) and 0 not in positive:
        positive = (0, *positive)
    measured = measure_asset_horizons(
        values, episode, equity_close, dates, offsets=positive,
    ) if positive else {}
    signal_mark = measured.get("value_t") if positive else None
    signal_mark = float(signal_mark) if signal_mark is not None else None
    signal_value = _asof(values, episode.signal_date)
    hold_start_value = _asof(values, episode.hold_start_date)
    output: list[float | None] = []
    real_dates: list[str | None] = []
    for offset in clean_offsets:
        position = episode.signal_index + offset
        target_date = pd.Timestamp(dates[position]) if 0 <= position < len(dates) else None
        real_dates.append(target_date.strftime("%Y-%m-%d") if target_date is not None else None)
        if offset in positive:
            label = "t" if offset == 0 else str(offset)
            mark = measured.get(f"value_{label}")
            output.append(
                float(mark) if basis == "hold_start" and mark is not None else
                100.0 * float(mark) / signal_mark
                if mark is not None and signal_mark not in (None, 0.0) else None
            )
        else:
            target_value = _asof(values, target_date)
            base_value = hold_start_value if basis == "hold_start" else signal_value
            output.append(
                100.0 * target_value / base_value
                if target_value is not None and base_value not in (None, 0.0)
                else None
            )
    return output, real_dates, signal_mark


def aligned_raw_path(
    values: pd.Series,
    episode: Episode,
    session_dates: Iterable[Any],
    *,
    offsets: Sequence[int] = OFFSETS,
) -> list[float | None]:
    """Align an unnormalised retained series to one episode's sessions."""

    dates = _calendar(session_dates)
    output: list[float | None] = []
    for offset in offsets:
        position = episode.signal_index + int(offset)
        date = pd.Timestamp(dates[position]) if 0 <= position < len(dates) else None
        output.append(_asof(values, date))
    return output


def aligned_level_path(
    ladder: pd.DataFrame,
    episode: Episode,
    *,
    offsets: Sequence[int] = OFFSETS,
) -> list[int | None]:
    """Align the observed 0/1/2 ladder state without forward fabrication."""

    levels = ladder["observed_level"].reset_index(drop=True)
    output: list[int | None] = []
    for offset in offsets:
        position = episode.signal_index + int(offset)
        if not 0 <= position < len(levels) or pd.isna(levels.iloc[position]):
            output.append(None)
        else:
            output.append(int(levels.iloc[position]))
    return output


def median_path(paths: Iterable[Sequence[float | None]]) -> list[float | None]:
    rows = list(paths)
    if not rows:
        return [None] * len(OFFSETS)
    if len({len(row) for row in rows}) != 1:
        raise ValueError("all paths must have equal length")
    output: list[float | None] = []
    for values in zip(*rows, strict=True):
        finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
        output.append(float(np.median(finite)) if finite else None)
    return output


def worst_path_id(paths: Mapping[str, Sequence[float | None]], *, zero_index: int) -> str | None:
    """Return the path with the lowest post-signal retained normalized mark."""

    scored: list[tuple[float, str]] = []
    for path_id, values in paths.items():
        finite = [
            float(value) for value in values[zero_index:]
            if value is not None and math.isfinite(float(value))
        ]
        if finite:
            scored.append((min(finite), str(path_id)))
    return min(scored, default=(math.inf, ""))[1] or None


def _short_cycle(cycle: str) -> str:
    if cycle.startswith("2000"):
        return "2000"
    if cycle.startswith("2008"):
        return "2008"
    match = re.match(r"(\d{4}(?:–\d{2})?)", cycle)
    return match.group(1) if match else cycle


def _episode_type(episode: Episode) -> str:
    return "inflation-type" if episode.cycle_type == "인플레형" else "recession-type"


def build_ladder_overlay(
    universe: pd.DataFrame, cycle_buckets: Sequence[str], *, basis: str = "signal",
) -> dict[str, dict[str, dict[str, Any]]]:
    """Build per-index cycle paths with the same two-condition ladder levels."""

    spec = LadderSpec(drawdown_threshold=-0.20, disp60_threshold=-0.10, levels=2)
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for basket, series_ids in LADDER_SERIES.items():
        basket_payload: dict[str, dict[str, Any]] = {}
        for series_id in series_ids:
            frame = universe.loc[
                universe["series_id"].astype(str).eq(series_id),
                ["date", "series_id", "basket", "close", "volume"],
            ].copy().sort_values("date", kind="mergesort").reset_index(drop=True)
            if frame.empty:
                continue
            signals = compute_signals(frame)
            ladder = ladder_levels(signals, spec)
            clustered = cluster_level_two(
                ladder, market=basket, series_id=series_id,
                start_date=frame["date"].iloc[0],
            )
            episodes = select_first_cycle_episodes(clustered, cycle_buckets)
            equity = prepare_value_series(frame["date"], frame["close"])
            paths: dict[str, list[float | None]] = {}
            level_paths: dict[str, list[int | None]] = {}
            cycle_dates: dict[str, list[str | None]] = {}
            signal_dates: dict[str, str] = {}
            for episode in episodes:
                path, dates, _mark = aligned_core_path(
                    equity, episode, equity, frame["date"], basis=basis,
                )
                paths[episode.cycle] = path
                level_paths[episode.cycle] = aligned_level_path(ladder, episode)
                cycle_dates[episode.cycle] = dates
                signal_dates[episode.cycle] = episode.signal_date.strftime("%Y-%m-%d")
            payload: dict[str, Any] = {cycle: path for cycle, path in paths.items()}
            payload.update({
                "median": median_path(paths.values()),
                "worst": worst_path_id(paths, zero_index=-OFFSET_START),
                "signal_dates": signal_dates,
                "dates": cycle_dates,
                "levels": level_paths,
                "hold_start_offsets": {
                    episode.cycle: episode.hold_start_index - episode.signal_index
                    for episode in episodes
                },
            })
            basket_payload[series_id] = payload
        output[basket] = basket_payload
    return output


def build_overlay_payload(
    *,
    episodes: Iterable[Episode],
    frames: Mapping[str, pd.DataFrame],
    ladders: Mapping[str, pd.DataFrame],
    assets: Mapping[str, Mapping[str, Any]],
    dgs10: pd.Series,
    cycle_buckets: Sequence[str],
    ladder_universe: pd.DataFrame,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the complete crisis-overlay document without provider calls."""

    allowed = set(cycle_buckets)
    cycle_order = {cycle: index for index, cycle in enumerate(cycle_buckets)}
    market_order = {"KR": 0, "US": 1, "US_TECH": 1, "SEMIS": 2}
    selected = sorted(
        (episode for episode in episodes if episode.cycle in allowed),
        key=lambda episode: (
            cycle_order[episode.cycle], market_order.get(episode.market, 99),
            episode.signal_date,
        ),
    )
    asset_rows = [{"id": EQUITY_ASSET_ID, "label": "주식 기준 (KOSPI / NASDAQ100)"}]
    asset_rows.extend(
        {"id": asset_id, "label": str(metadata["label"])}
        for asset_id, metadata in assets.items()
    )
    episode_rows: list[dict[str, Any]] = []
    series: dict[str, dict[str, dict[str, list[float | None]]]] = {
        basis: {} for basis in NORMALISATION_LABELS
    }
    signal_values: dict[str, dict[str, float | None]] = {}
    dates: dict[str, list[str | None]] = {}
    yields: dict[str, list[float | None]] = {}
    levels: dict[str, list[int | None]] = {}
    for episode in selected:
        market_frame = frames[episode.market]
        equity = prepare_value_series(market_frame["date"], market_frame["close"])
        episode_rows.append({
            "id": episode.episode_id,
            "market": episode.market,
            "cycle": episode.cycle,
            "label": f"{_short_cycle(episode.cycle)} · {episode.market}",
            "type": _episode_type(episode),
            "signal_date": episode.signal_date.strftime("%Y-%m-%d"),
            "hold_start_date": episode.hold_start_date.strftime("%Y-%m-%d"),
            "hold_start_offset": episode.hold_start_index - episode.signal_index,
            "is_holdout": bool(episode.signal_date >= HOLDOUT_START),
        })
        episode_series = {basis: {} for basis in NORMALISATION_LABELS}
        episode_marks: dict[str, float | None] = {}
        real_dates: list[str | None] = []
        equity_mark: float | None = None
        for basis in NORMALISATION_LABELS:
            equity_path, basis_dates, basis_mark = aligned_core_path(
                equity, episode, equity, market_frame["date"], basis=basis,
            )
            episode_series[basis][EQUITY_ASSET_ID] = equity_path
            real_dates = basis_dates
            equity_mark = basis_mark
        episode_marks[EQUITY_ASSET_ID] = equity_mark
        for asset_id, metadata in assets.items():
            signal_mark: float | None = None
            for basis in NORMALISATION_LABELS:
                path, _dates, basis_mark = aligned_core_path(
                    metadata["values"], episode, equity, market_frame["date"],
                    basis=basis,
                )
                episode_series[basis][asset_id] = path
                signal_mark = basis_mark
            episode_marks[asset_id] = signal_mark
        for basis in NORMALISATION_LABELS:
            series[basis][episode.episode_id] = episode_series[basis]
        signal_values[episode.episode_id] = episode_marks
        dates[episode.episode_id] = real_dates
        yields[episode.episode_id] = aligned_raw_path(
            dgs10, episode, market_frame["date"],
        )
        levels[episode.episode_id] = aligned_level_path(ladders[episode.market], episode)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "offset_start": OFFSET_START,
        "offset_end": OFFSET_END,
        "episodes": episode_rows,
        "assets": asset_rows,
        "normalisations": [
            {"id": basis, "label": label}
            for basis, label in NORMALISATION_LABELS.items()
        ],
        "series": series,
        "signal_values": signal_values,
        "dates": dates,
        "yields": yields,
        "levels": levels,
        "ladder": {
            basis: build_ladder_overlay(ladder_universe, cycle_buckets, basis=basis)
            for basis in NORMALISATION_LABELS
        },
    }


def round_payload(value: Any, *, digits: int = 2) -> Any:
    """Convert scientific scalars and round finite floats for compact JSON."""

    if isinstance(value, dict):
        return {str(key): round_payload(item, digits=digits) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_payload(item, digits=digits) for item in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return round(number, digits) if math.isfinite(number) else None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")
    return value


def validate_overlay_payload(payload: Mapping[str, Any]) -> None:
    """Fail closed on incomplete path dimensions or inconsistent episode keys."""

    required = {
        "schema_version", "generated_at", "episodes", "assets", "normalisations",
        "series", "signal_values", "dates", "yields", "levels", "ladder",
    }
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"overlay payload is missing keys: {sorted(missing)}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"overlay schema_version must be {SCHEMA_VERSION}")
    episode_ids = [str(row["id"]) for row in payload["episodes"]]
    asset_ids = [str(row["id"]) for row in payload["assets"]]
    basis_ids = [str(row["id"]) for row in payload["normalisations"]]
    if not episode_ids or len(episode_ids) != len(set(episode_ids)):
        raise ValueError("episode ids must be present and unique")
    if not asset_ids or len(asset_ids) != len(set(asset_ids)):
        raise ValueError("asset ids must be present and unique")
    if basis_ids != list(NORMALISATION_LABELS):
        raise ValueError("normalisation bases are incomplete or out of order")
    expected_length = OFFSET_END - OFFSET_START + 1
    zero_index = -OFFSET_START
    episode_meta = {str(row["id"]): row for row in payload["episodes"]}
    for basis in basis_ids:
        basis_series = payload["series"].get(basis, {})
        if set(basis_series) != set(episode_ids):
            raise ValueError(f"episode paths are incomplete for {basis}")
        for episode_id in episode_ids:
            if set(basis_series.get(episode_id, {})) != set(asset_ids):
                raise ValueError(f"asset paths are incomplete for {basis}/{episode_id}")
            for asset_id, path in basis_series[episode_id].items():
                if len(path) != expected_length:
                    raise ValueError(f"path length is invalid: {basis}/{episode_id}/{asset_id}")
                base_index = zero_index if basis == "signal" else (
                    zero_index + int(episode_meta[episode_id]["hold_start_offset"])
                )
                if path[base_index] is not None and not math.isclose(
                    float(path[base_index]), 100.0, abs_tol=0.01,
                ):
                    raise ValueError(f"{basis} normalization is invalid: {episode_id}/{asset_id}")
    for episode_id in episode_ids:
        for key in ("dates", "yields", "levels"):
            if len(payload[key].get(episode_id, [])) != expected_length:
                raise ValueError(f"{key} path length is invalid: {episode_id}")
    for basis in basis_ids:
        for basket in LADDER_SERIES:
            if basket not in payload["ladder"].get(basis, {}):
                raise ValueError(f"ladder basket is missing: {basis}/{basket}")
            for series_id, item in payload["ladder"][basis][basket].items():
                if len(item.get("median", [])) != expected_length:
                    raise ValueError(f"ladder median length is invalid: {basis}/{series_id}")
                cycles = [key for key in item.get("signal_dates", {}) if key in item]
                if not cycles or any(len(item[cycle]) != expected_length for cycle in cycles):
                    raise ValueError(f"ladder cycle paths are incomplete: {basis}/{series_id}")
                if any(len(item.get("levels", {}).get(cycle, [])) != expected_length for cycle in cycles):
                    raise ValueError(f"ladder level paths are incomplete: {basis}/{series_id}")


__all__ = [
    "EQUITY_ASSET_ID",
    "HOLDOUT_START",
    "LADDER_SERIES",
    "NORMALISATION_LABELS",
    "OFFSET_END",
    "OFFSET_START",
    "OFFSETS",
    "SCHEMA_VERSION",
    "aligned_core_path",
    "aligned_level_path",
    "aligned_raw_path",
    "build_ladder_overlay",
    "build_overlay_payload",
    "median_path",
    "round_payload",
    "select_first_cycle_episodes",
    "validate_overlay_payload",
    "worst_path_id",
]
