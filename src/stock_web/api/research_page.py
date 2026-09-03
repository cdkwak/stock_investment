"""Read-only projections for the rule-candidate research page.

The producer is intentionally not imported here.  This module consumes its
retained JSON/JSONL artifacts and normalized close series as data so the web
dashboard remains provider-free and usable while the research engine evolves.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import math
import os
from pathlib import Path

import pandas as pd

from stock_web.api import datasets as dsx


EMPTY_MESSAGE = (
    "아직 평가 결과가 없습니다 · "
    "`scripts/research/run_rule_leaderboard.py` 실행 후 표시"
)
LEADERBOARD_RELATIVE = Path("artifacts/research/rule_leaderboard/latest.json")
CANDIDATES_RELATIVE = Path("config/research/rule_candidates.json")
FORWARD_RELATIVE = Path("data/local/research/forward_test/signals.jsonl")

_RESEARCH_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_FORWARD_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}

_DEFINITION_LABELS = {
    "drawdown252": "252거래일 고점 대비 낙폭",
    "disp60": "60일 이동평균 이격도",
    "rsi14": "14일 RSI",
    "volidx_pct": "변동성 지수 백분위",
    "score": "점수",
    "level": "단계",
    "levels": "단계",
    "threshold": "기준값",
    "thresholds": "기준값",
    "exposure": "노출",
    "min": "최솟값",
    "max": "최댓값",
    "operator": "조건",
    "window": "관측 기간",
}
_DEFINITION_VALUES = {
    "drawdown": "낙폭",
    "overheat": "과열",
    "hybrid": "혼합",
    "gte": "이상",
    "lte": "이하",
    "gt": "초과",
    "lt": "미만",
}
_PRICE_SOURCES = {
    "KR": ("data/normalized/kr_index_daily", "KOSPI200"),
    "US_TECH": ("data/normalized/global_index_price_daily", "NASDAQ100"),
    "SEMIS": ("data/normalized/global_index_price_daily", "SOX"),
}


def _file_signature(path: Path) -> tuple[object, ...]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _dataset_signature(path: Path) -> tuple[object, ...]:
    if not path.is_dir():
        return (str(path), None)
    newest = 0
    count = 0
    total_size = 0
    try:
        for directory, _children, names in os.walk(path):
            for name in names:
                if not name.endswith(".parquet"):
                    continue
                stat = (Path(directory) / name).stat()
                newest = max(newest, stat.st_mtime_ns)
                total_size += stat.st_size
                count += 1
    except OSError:
        return (str(path), "unreadable")
    return (str(path), newest, count, total_size)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_definition_value(value: object) -> str:
    if isinstance(value, bool):
        return "예" if value else "아니요"
    if value is None:
        return "없음"
    if isinstance(value, str):
        return _DEFINITION_VALUES.get(value, value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _definition_text(definition: object) -> str:
    if not isinstance(definition, dict) or not definition:
        return "정의가 기록되지 않았습니다."
    parts: list[str] = []

    def visit(prefix: str, value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                label = _DEFINITION_LABELS.get(str(key), str(key))
                visit(f"{prefix} · {label}" if prefix else label, child)
            return
        if isinstance(value, list):
            if any(isinstance(item, (dict, list)) for item in value):
                for index, item in enumerate(value, start=1):
                    visit(f"{prefix} {index}", item)
                return
            rendered = ", ".join(_format_definition_value(item) for item in value)
        else:
            rendered = _format_definition_value(value)
        parts.append(f"{prefix}: {rendered}")

    visit("", definition)
    return " · ".join(parts)


def _candidate_rank(candidate: dict[str, object]) -> float:
    results = candidate.get("results")
    holdout = results.get("holdout") if isinstance(results, dict) else None
    difference = _finite(holdout.get("diff_60")) if isinstance(holdout, dict) else None
    if difference is None:
        return -math.inf
    return -difference if candidate.get("side") == "overheat" else difference


def _status_lines(document: dict[str, object] | None) -> list[str]:
    candidates = document.get("candidates") if isinstance(document, dict) else None
    if not isinstance(candidates, list):
        return ["규칙 평가 없음"]
    lines: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("status") != "active" or candidate.get("basket") != "KR":
            continue
        current = candidate.get("current")
        if not isinstance(current, dict):
            continue
        level = current.get("level")
        max_level = current.get("max_level")
        exposure = _finite(current.get("exposure"))
        analog = current.get("analog")
        mean_60 = _finite(analog.get("mean_60")) if isinstance(analog, dict) else None
        if level is None or max_level is None or exposure is None:
            continue
        analog_text = "—" if mean_60 is None else f"{mean_60 * 100:+.1f}%"
        lines.append(
            f"규칙 현재 상태 · {candidate.get('name') or candidate.get('id')}: "
            f"{level}/{max_level}단계 · 노출 {exposure * 100:.0f}% · "
            f"과거 동일 단계 60일 {analog_text}"
        )
        if len(lines) == 2:
            break
    return lines or ["규칙 평가 없음"]


def build_current_status_lines(project_root: Path) -> list[str]:
    """Return at most two active Korean status lines for Home and Research."""
    return _status_lines(_read_json(Path(project_root) / LEADERBOARD_RELATIVE))


def _empty_research(history: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "EMPTY",
        "message": EMPTY_MESSAGE,
        "generated_at": None,
        "rules_version": None,
        "attempt_count": 0,
        "fit_window": {},
        "holdout_window": {},
        "cycles": [],
        "candidates": [],
        "warnings": [],
        "warning_count": 0,
        "history": history or [],
        "current_status": ["규칙 평가 없음"],
    }


def build_research_payload(project_root: Path) -> dict[str, object]:
    """Build the leaderboard and change-log payload, cached by both file mtimes."""
    root = Path(project_root).resolve()
    leaderboard_path = root / LEADERBOARD_RELATIVE
    candidates_path = root / CANDIDATES_RELATIVE
    signature = (_file_signature(leaderboard_path), _file_signature(candidates_path))
    key = str(root)
    cached = _RESEARCH_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return deepcopy(cached[1])

    config = _read_json(candidates_path)
    raw_history = config.get("history") if isinstance(config, dict) else []
    history = [dict(item) for item in raw_history if isinstance(item, dict)] \
        if isinstance(raw_history, list) else []
    history.sort(key=lambda item: str(item.get("date") or ""), reverse=True)

    document = _read_json(leaderboard_path)
    if not document or document.get("schema_version") != 1:
        payload = _empty_research(history)
        _RESEARCH_CACHE[key] = (signature, payload)
        return deepcopy(payload)

    cycle_rows = document.get("cycles")
    cycles = [dict(item) for item in cycle_rows if isinstance(item, dict)] \
        if isinstance(cycle_rows, list) else []
    labels = {str(item.get("id")): str(item.get("label") or item.get("id")) for item in cycles}
    raw_candidates = document.get("candidates")
    candidates: list[dict[str, object]] = []
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            candidate = deepcopy(item)
            candidate["definition_text"] = _definition_text(candidate.get("definition"))
            candidate["direction_hint"] = (
                "낮을수록 좋음" if candidate.get("side") == "overheat" else "높을수록 좋음"
            )
            rank_value = _candidate_rank(candidate)
            candidate["sort_value"] = None if rank_value == -math.inf else rank_value
            results = candidate.get("results")
            holdout = results.get("holdout") if isinstance(results, dict) else None
            holdout_n = _finite(holdout.get("n")) if isinstance(holdout, dict) else None
            candidate["warn_small_sample"] = bool(
                isinstance(holdout, dict) and holdout.get("warn_small_sample")
            ) or (holdout_n is not None and holdout_n < 15)
            candidate_cycles = candidate.get("cycles")
            if isinstance(candidate_cycles, list):
                candidate["cycles"] = [
                    {**row, "label": labels.get(str(row.get("id")), str(row.get("id") or "—"))}
                    for row in candidate_cycles if isinstance(row, dict)
                ]
            candidates.append(candidate)
    candidates.sort(key=lambda item: (_candidate_rank(item), str(item.get("id") or "")), reverse=True)
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index

    warnings = document.get("warnings")
    warning_rows = [str(item) for item in warnings] if isinstance(warnings, list) else []
    payload = {
        "schema_version": 1,
        "status": "READY",
        "message": "",
        "generated_at": document.get("generated_at"),
        "rules_version": document.get("rules_version"),
        "attempt_count": document.get("attempt_count", 0),
        "fit_window": document.get("fit_window") if isinstance(document.get("fit_window"), dict) else {},
        "holdout_window": document.get("holdout_window") if isinstance(document.get("holdout_window"), dict) else {},
        "cycles": cycles,
        "candidates": candidates,
        "warnings": warning_rows,
        "warning_count": len(warning_rows),
        "history": history,
        "current_status": _status_lines(document),
    }
    _RESEARCH_CACHE[key] = (signature, payload)
    return deepcopy(payload)


def _read_signals(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    signals: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("as_of") and value.get("candidate_id"):
            signals.append(value)
    return signals


def _load_price_series(project_root: Path, basket: str) -> pd.DataFrame | None:
    source = _PRICE_SOURCES.get(basket)
    if source is None:
        return None
    relative, symbol = source
    frame = dsx.load(
        project_root, relative, columns=["date", "symbol", "close"], partitioning=None,
    )
    if frame is None or not {"date", "symbol", "close"}.issubset(frame.columns):
        frame = dsx.load(project_root, relative, partitioning=None)
    if frame is None or not {"date", "symbol", "close"}.issubset(frame.columns):
        return None
    selected = frame.loc[frame["symbol"].astype(str) == symbol, ["date", "close"]].copy()
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce").dt.normalize()
    selected["close"] = pd.to_numeric(selected["close"], errors="coerce")
    selected = selected.dropna().sort_values("date", kind="stable").drop_duplicates("date", keep="last")
    return selected.reset_index(drop=True) if not selected.empty else None


def _realise(signal: dict[str, object], prices: pd.DataFrame | None) -> dict[str, object]:
    row = dict(signal)
    row["signal_close"] = _finite(signal.get("close"))
    row["reference_close"] = None
    for horizon in (20, 60, 90):
        row[f"return_{horizon}"] = None
        row[f"status_{horizon}"] = "대기"
    if prices is None:
        return row
    as_of = pd.to_datetime(signal.get("as_of"), errors="coerce")
    if pd.isna(as_of):
        return row
    if as_of.tzinfo is not None:
        as_of = as_of.tz_localize(None)
    matches = prices.index[prices["date"] == as_of.normalize()].tolist()
    if not matches:
        return row
    index = int(matches[-1])
    base = float(prices.iloc[index]["close"])
    if not math.isfinite(base) or base == 0:
        return row
    row["reference_close"] = base
    for horizon in (20, 60, 90):
        target = index + horizon
        if target >= len(prices):
            continue
        future = float(prices.iloc[target]["close"])
        if math.isfinite(future):
            row[f"return_{horizon}"] = future / base - 1.0
            row[f"status_{horizon}"] = "실현"
    return row


def _forward_summary(rows: list[dict[str, object]]) -> dict[str, object] | None:
    realised_rows = sum(any(row.get(f"return_{h}") is not None for h in (20, 60, 90)) for row in rows)
    if realised_rows < 5:
        return None
    summary: dict[str, object] = {"realised_rows": realised_rows}
    for horizon in (20, 60, 90):
        values = [
            float(row[f"return_{horizon}"])
            for row in rows if row.get(f"return_{horizon}") is not None
        ]
        summary[f"n_{horizon}"] = len(values)
        summary[f"mean_{horizon}"] = sum(values) / len(values) if len(values) >= 5 else None
    return summary


def build_forward_payload(project_root: Path) -> dict[str, object]:
    """Return signals with normalized-data 20/60/90-session outcomes."""
    root = Path(project_root).resolve()
    signal_path = root / FORWARD_RELATIVE
    signature = (
        _file_signature(signal_path),
        *(_dataset_signature(root / relative) for relative, _symbol in _PRICE_SOURCES.values()),
    )
    key = str(root)
    cached = _FORWARD_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return deepcopy(cached[1])

    signals = _read_signals(signal_path)
    if not signals:
        payload: dict[str, object] = {
            "schema_version": 1, "status": "EMPTY", "message": EMPTY_MESSAGE, "groups": [],
        }
        _FORWARD_CACHE[key] = (signature, payload)
        return deepcopy(payload)

    research = build_research_payload(root)
    candidate_meta = {
        str(item.get("id")): item for item in research.get("candidates", [])
        if isinstance(item, dict)
    }
    price_cache: dict[str, pd.DataFrame | None] = {}
    grouped: dict[str, dict[str, list[dict[str, object]]]] = defaultdict(lambda: defaultdict(list))
    for signal in signals:
        candidate_id = str(signal.get("candidate_id"))
        metadata = candidate_meta.get(candidate_id, {})
        basket = str(signal.get("basket") or metadata.get("basket") or "")
        if basket not in price_cache:
            price_cache[basket] = _load_price_series(root, basket)
        realised = _realise({**signal, "basket": basket}, price_cache[basket])
        version = str(signal.get("rules_version") or "미상")
        grouped[version][candidate_id].append(realised)

    groups: list[dict[str, object]] = []
    for version, by_candidate in grouped.items():
        candidates: list[dict[str, object]] = []
        for candidate_id, rows in by_candidate.items():
            rows.sort(key=lambda row: str(row.get("as_of") or ""), reverse=True)
            metadata = candidate_meta.get(candidate_id, {})
            candidates.append({
                "candidate_id": candidate_id,
                "name": metadata.get("name") or candidate_id,
                "basket": rows[0].get("basket") if rows else metadata.get("basket"),
                "rows": rows,
                "summary": _forward_summary(rows),
            })
        candidates.sort(key=lambda item: str(item.get("candidate_id")))
        newest = max(
            (str(row.get("as_of") or "") for item in candidates for row in item["rows"]),
            default="",
        )
        groups.append({"rules_version": version, "newest_as_of": newest, "candidates": candidates})
    groups.sort(key=lambda item: (str(item.get("newest_as_of")), str(item.get("rules_version"))), reverse=True)
    payload = {"schema_version": 1, "status": "READY", "message": "", "groups": groups}
    _FORWARD_CACHE[key] = (signature, payload)
    return deepcopy(payload)
