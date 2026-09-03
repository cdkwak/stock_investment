"""Daily, version-bound forward observations for rule candidates."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .condition_backtest import load_primary_indices
from .rule_candidates import load_candidates, rules_version
from .rule_leaderboard import (
    HORIZONS,
    PRIMARY_SERIES,
    current_candidate_state,
    load_indicator_frame,
)


FORWARD_LOG = Path("data/local/research/forward_test/signals.jsonl")
ROW_KEYS = (
    "as_of", "candidate_id", "rules_version", "score", "level", "exposure",
    "close", "basket",
)


def _as_of(value: str | date | None) -> str:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    result = value.isoformat() if isinstance(value, date) else value
    try:
        return date.fromisoformat(result).isoformat()
    except ValueError as error:
        raise ValueError("as_of must be YYYY-MM-DD") from error


def _json_number(value: object, field: str) -> float | int:
    if value is pd.NA or pd.isna(value):
        raise ValueError(f"current candidate {field} is unavailable")
    if field in {"score", "level"}:
        return int(value)
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"current candidate {field} is unavailable")
    return result


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid forward-test JSON on line {line_number}") from error
        if not isinstance(row, dict) or set(row) != set(ROW_KEYS):
            raise ValueError(f"forward-test line {line_number} has invalid schema")
        rows.append(row)
    return rows


def _write_rows(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        for row in rows
    )
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def record_forward_signals(
    project_root: Path, *, as_of: str | date | None = None
) -> dict[str, Any]:
    """Append one current retained observation per non-retired rule version."""

    root = project_root.resolve()
    observation_date = _as_of(as_of)
    registry = load_candidates(root)
    version = rules_version(root)
    indicators = load_indicator_frame(root)
    proposed: list[dict[str, Any]] = []
    for candidate in registry["candidates"]:
        if candidate["status"] not in {"active", "experimental"}:
            continue
        row, state, _ = current_candidate_state(
            indicators, candidate, as_of=observation_date
        )
        record = {
            "as_of": observation_date,
            "candidate_id": candidate["id"],
            "rules_version": version,
            "score": _json_number(state["score"], "score"),
            "level": _json_number(state["level"], "level"),
            "exposure": _json_number(state["exposure"], "exposure"),
            "close": _json_number(row["close"], "close"),
            "basket": candidate["basket"],
        }
        assert tuple(record) == ROW_KEYS
        proposed.append(record)

    path = root / FORWARD_LOG
    existing = _read_rows(path)
    by_key = {
        (row["as_of"], row["candidate_id"], row["rules_version"]): row
        for row in existing
    }
    appended = 0
    for row in proposed:
        key = (row["as_of"], row["candidate_id"], row["rules_version"])
        prior = by_key.get(key)
        if prior is not None:
            if prior != row:
                raise ValueError(f"conflicting forward-test replay for {key}")
            continue
        existing.append(row)
        by_key[key] = row
        appended += 1
    if appended or not path.exists():
        _write_rows(path, existing)
    return {
        "status": "APPENDED" if appended else "NOOP_IDEMPOTENT",
        "as_of": observation_date,
        "rules_version": version,
        "candidate_count": len(proposed),
        "appended": appended,
        "path": str(path),
        "api_calls": 0,
    }


def _series_prices(prices: pd.DataFrame, basket: str) -> pd.DataFrame:
    for series_id in PRIMARY_SERIES[basket]:
        selected = prices.loc[prices["series_id"].astype(str).eq(series_id)].copy()
        if not selected.empty:
            selected["date"] = pd.to_datetime(selected["date"], errors="raise").dt.normalize()
            return selected.sort_values("date", kind="mergesort").reset_index(drop=True)
    return pd.DataFrame(columns=prices.columns)


def load_forward_test(project_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join mature 20/60/90 retained-session returns and summarize by version."""

    root = project_root.resolve()
    rows = _read_rows(root / FORWARD_LOG)
    columns = [*ROW_KEYS]
    for horizon in HORIZONS:
        columns.extend((f"realised_date_{horizon}", f"realised_return_{horizon}"))
    if not rows:
        return pd.DataFrame(columns=columns), pd.DataFrame(
            columns=["candidate_id", "rules_version"]
        )
    prices = load_primary_indices(root)
    cache = {
        basket: _series_prices(prices, basket)
        for basket in sorted({str(row["basket"]) for row in rows})
    }
    joined: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        series = cache[str(source["basket"])]
        eligible = series.index[series["date"].le(pd.Timestamp(str(source["as_of"])))]
        start = int(eligible[-1]) if len(eligible) else -1
        if start >= 0 and not np.isclose(
            float(series.loc[start, "close"]), float(source["close"]), rtol=1e-10, atol=1e-10
        ):
            raise ValueError(
                f"retained close changed for {source['candidate_id']} at {source['as_of']}"
            )
        for horizon in HORIZONS:
            position = start + horizon
            if start >= 0 and position < len(series):
                row[f"realised_date_{horizon}"] = series.loc[position, "date"].strftime("%Y-%m-%d")
                row[f"realised_return_{horizon}"] = (
                    float(series.loc[position, "close"]) / float(source["close"]) - 1.0
                )
            else:
                row[f"realised_date_{horizon}"] = None
                row[f"realised_return_{horizon}"] = np.nan
        joined.append(row)
    frame = pd.DataFrame(joined, columns=columns)
    summaries: list[dict[str, Any]] = []
    for (candidate_id, version), group in frame.groupby(
        ["candidate_id", "rules_version"], sort=True
    ):
        summary: dict[str, Any] = {
            "candidate_id": candidate_id,
            "rules_version": version,
        }
        for horizon in HORIZONS:
            values = pd.to_numeric(
                group[f"realised_return_{horizon}"], errors="coerce"
            ).dropna()
            summary[f"n_{horizon}"] = int(values.size)
            summary[f"mean_{horizon}"] = (
                float(values.mean()) if not values.empty else np.nan
            )
            summary[f"hit_{horizon}"] = (
                float(values.gt(0).mean()) if not values.empty else np.nan
            )
        summaries.append(summary)
    return frame, pd.DataFrame(summaries)


__all__ = [
    "FORWARD_LOG", "ROW_KEYS", "load_forward_test", "record_forward_signals",
]
