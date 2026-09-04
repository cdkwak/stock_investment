"""Retained-data projections and bounded experiments for the Research page.

Leaderboard display remains artifact-backed.  Explicit experiment calls may
import the offline producer, but they read retained Parquet only; candidate
registration is a loopback-gated router concern.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Mapping, Sequence

import pandas as pd

from stock_web.api import datasets as dsx


EMPTY_MESSAGE = (
    "아직 평가 결과가 없습니다 · "
    "`scripts/research/run_rule_leaderboard.py` 실행 후 표시"
)
LEADERBOARD_RELATIVE = Path("artifacts/research/rule_leaderboard/latest.json")
CANDIDATES_RELATIVE = Path("config/research/rule_candidates.json")
FORWARD_RELATIVE = Path("data/local/research/forward_test/signals.jsonl")
COMPOUND_RELATIVE = Path("artifacts/research/compound_ladder")

_RESEARCH_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_FORWARD_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_EXPERIMENT_LOCK = threading.RLock()
_EXPERIMENT_TIMES: dict[str, deque[float]] = defaultdict(deque)
_EXPERIMENT_COUNT = 0
_REGENERATION_LOCK = threading.Lock()
_REGENERATION_WAIT_SECONDS = 60.0
_COMPOUND_STATE_LOCK = threading.RLock()
_COMPOUND_SESSION_VIEWS: dict[tuple[str, str], int] = defaultdict(int)
_COMPOUND_RUN_STATE: dict[str, dict[str, object]] = {}
_COMPOUND_TOKEN = re.compile(r"^[a-zA-Z0-9_]+$")
_COMPOUND_BASKETS = ("KR", "US_TECH", "SEMIS", "FOREIGN")
_COMPOUND_DEFAULT_GRID: dict[str, tuple[object, ...]] = {
    "drawdown_threshold": (-0.10, -0.15, -0.20, -0.25, -0.30, -0.35),
    "disp60_threshold": (-0.05, -0.10, -0.15),
    "levels": (1, 2, 3, 4),
    "leverage_multiple": (1, 2, 3),
    "base_exposure": (0.0, 1.0),
    "exit": ("a", "b60", "b120", "c", "d"),
    "cost_enabled": (False, True),
}
EXPERIMENT_CAUTION = (
    "홀드아웃 성적을 보고 임계값을 고치면 과적합입니다 — "
    "후보 등록 시 시도 횟수에 기록됩니다"
)

_INDICATOR_LABELS = {
    "drawdown252": "252일 낙폭",
    "disp60": "60일 이격",
    "rsi14": "RSI14",
    "volidx_pct": "변동성지수 백분위(VIX/VKOSPI)",
}
_OPERATOR_LABELS = {"<=": "≤", ">=": "≥", "<": "<", ">": ">"}
_PERCENT_INDICATORS = {"drawdown252", "disp60", "volidx_pct"}
_KST = timezone(timedelta(hours=9), name="KST")
_PRICE_SOURCES = {
    "KR": ("data/normalized/kr_index_daily", "KOSPI200"),
    "US_TECH": ("data/normalized/global_index_price_daily", "NASDAQ100"),
    "SEMIS": ("data/normalized/global_index_price_daily", "SOX"),
}
_EXPERIMENT_SIDES = {"drawdown", "overheat"}
_EXPERIMENT_BASKETS = {"KR", "US_TECH", "SEMIS", "POOLED"}
_EXPERIMENT_TYPES = {"ladder", "vol_target", "hybrid"}
_EXPERIMENT_RANGES = {
    "drawdown252": (-0.60, 0.0),
    "disp60": (-0.30, 0.30),
    "rsi14": (10.0, 90.0),
    "volidx_pct": (0.0, 1.0),
}


class ResearchInputError(ValueError):
    """A Korean, user-displayable experiment input error."""


class ExperimentRateLimitError(ResearchInputError):
    """Raised after ten retained-data evaluations in one client minute."""


class CompoundGridNotFound(ResearchInputError):
    """Raised when the requested precomputed compound-ladder grid is absent."""


class CompoundRunConflict(ResearchInputError):
    """Raised when a retained-data compound-ladder run already owns the lock."""


def _query_values(params: object, name: str) -> list[str]:
    getter = getattr(params, "getlist", None)
    if callable(getter):
        return [str(value) for value in getter(name)]
    if isinstance(params, Mapping):
        value = params.get(name)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return [] if value is None else [str(value)]
    return []


def _query_value(params: object, name: str, default: str = "") -> str:
    values = _query_values(params, name)
    return values[-1].strip() if values else default


def _number_input(value: object, label: str) -> float:
    number = _finite(value)
    if number is None:
        raise ResearchInputError(f"{label} 값을 숫자로 입력해 주세요.")
    return number


def _normalise_indicators(
    value: object, *, side: str, required: bool,
) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        value = []
    indicators: list[dict[str, object]] = []
    expected_op = "<=" if side == "drawdown" else ">="
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ResearchInputError(f"{index + 1}번째 지표 형식이 올바르지 않습니다.")
        key = str(item.get("key") or "")
        op = str(item.get("op") or "")
        if key not in _EXPERIMENT_RANGES:
            raise ResearchInputError(f"지원하지 않는 지표입니다: {key or '미입력'}")
        if op != expected_op:
            label = "낙폭" if side == "drawdown" else "과열"
            raise ResearchInputError(f"{label} 조건의 연산자는 {expected_op}만 사용할 수 있습니다.")
        threshold = _number_input(item.get("threshold"), "임계값")
        minimum, maximum = _EXPERIMENT_RANGES[key]
        if not minimum <= threshold <= maximum:
            raise ResearchInputError(f"{_INDICATOR_LABELS[key]} 임계값이 허용 범위를 벗어났습니다.")
        indicators.append({"key": key, "op": op, "threshold": threshold})
    keys = [str(item["key"]) for item in indicators]
    if len(keys) != len(set(keys)):
        raise ResearchInputError("같은 지표를 두 번 선택할 수 없습니다.")
    if required and not indicators:
        raise ResearchInputError("지표를 하나 이상 선택해 주세요.")
    if len(indicators) > 4:
        raise ResearchInputError("지표는 최대 4개까지 선택할 수 있습니다.")
    return indicators


def normalise_experiment_definition(
    *, side: str, definition_type: str, indicators: object,
    levels: object = None, target_vol: object = None,
) -> dict[str, object]:
    if side not in _EXPERIMENT_SIDES:
        raise ResearchInputError("측은 낙폭 또는 과열이어야 합니다.")
    if definition_type not in _EXPERIMENT_TYPES:
        raise ResearchInputError("규칙 유형은 사다리, 변동성 목표, 혼합 중 하나여야 합니다.")
    needs_ladder = definition_type in {"ladder", "hybrid"}
    clean_indicators = _normalise_indicators(indicators, side=side, required=needs_ladder)
    if needs_ladder:
        try:
            clean_levels = int(levels)
        except (TypeError, ValueError) as error:
            raise ResearchInputError("단계 수를 1~4로 입력해 주세요.") from error
        if clean_levels not in {1, 2, 3, 4}:
            raise ResearchInputError("단계 수는 1~4여야 합니다.")
        if clean_levels != len(clean_indicators):
            raise ResearchInputError("단계 수는 선택한 지표 수와 같아야 합니다.")
        ladder: dict[str, object] = {
            "indicators": clean_indicators, "levels": clean_levels,
        }
    if definition_type in {"vol_target", "hybrid"}:
        clean_target = _number_input(target_vol, "목표 변동성")
        if not 0.10 <= clean_target <= 0.25:
            raise ResearchInputError("목표 변동성은 10%~25%여야 합니다.")
        vol_target = {"target_vol": clean_target, "window": 20}
    if definition_type == "ladder":
        return {"type": "ladder", **ladder}
    if definition_type == "vol_target":
        return {"type": "vol_target", **vol_target}
    return {
        "type": "hybrid",
        "ladder": {"side": side, **ladder},
        "vol_target": vol_target,
    }


def parse_experiment_query(params: object) -> dict[str, object]:
    side = _query_value(params, "side")
    basket = _query_value(params, "basket")
    definition_type = _query_value(params, "type")
    if basket not in _EXPERIMENT_BASKETS:
        raise ResearchInputError("바스켓을 KR, US_TECH, SEMIS, POOLED 중에서 선택해 주세요.")
    raw_indicators: list[dict[str, object]] = []
    for raw in _query_values(params, "ind"):
        parts = raw.split(":", 2)
        if len(parts) != 3:
            raise ResearchInputError("지표는 key:op:threshold 형식이어야 합니다.")
        raw_indicators.append({"key": parts[0], "op": parts[1], "threshold": parts[2]})
    levels_text = _query_value(params, "levels", str(len(raw_indicators) or 1))
    target_text = _query_value(params, "target_vol", "0.15")
    definition = normalise_experiment_definition(
        side=side,
        definition_type=definition_type,
        indicators=raw_indicators,
        levels=levels_text,
        target_vol=target_text,
    )
    horizon_text = _query_value(params, "horizon", "60")
    try:
        horizon = int(horizon_text)
    except ValueError as error:
        raise ResearchInputError("평가 기간은 20, 60, 90일 중 하나여야 합니다.") from error
    if horizon not in {20, 60, 90}:
        raise ResearchInputError("평가 기간은 20, 60, 90일 중 하나여야 합니다.")
    return {
        "side": side, "basket": basket, "definition": definition, "horizon": horizon,
    }


def _reserve_experiment(client_key: str, *, now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    with _EXPERIMENT_LOCK:
        recent = _EXPERIMENT_TIMES[client_key]
        while recent and current - recent[0] >= 60.0:
            recent.popleft()
        if len(recent) >= 10:
            raise ExperimentRateLimitError("규칙 평가는 클라이언트당 1분에 10회까지 가능합니다.")
        recent.append(current)


def _next_experiment_count() -> int:
    global _EXPERIMENT_COUNT
    with _EXPERIMENT_LOCK:
        _EXPERIMENT_COUNT += 1
        return _EXPERIMENT_COUNT


def _reset_experiment_session() -> None:
    """Reset process state for isolated tests."""
    global _EXPERIMENT_COUNT
    with _EXPERIMENT_LOCK:
        _EXPERIMENT_TIMES.clear()
        _EXPERIMENT_COUNT = 0
    with _COMPOUND_STATE_LOCK:
        _COMPOUND_SESSION_VIEWS.clear()


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


def _format_number(value: object, *, percent: bool = False) -> str | None:
    number = _finite(value)
    if number is None:
        return None
    if percent:
        number *= 100
    suffix = "%" if percent else ""
    return f"{number:g}{suffix}"


def _ladder_definition_text(definition: object) -> str | None:
    if not isinstance(definition, dict):
        return None
    indicators = definition.get("indicators")
    parts: list[str] = []
    if isinstance(indicators, list):
        for indicator in indicators:
            if not isinstance(indicator, dict):
                continue
            key = str(indicator.get("key") or "")
            label = _INDICATOR_LABELS.get(key)
            operator = _OPERATOR_LABELS.get(str(indicator.get("op") or ""))
            threshold = _format_number(
                indicator.get("threshold"), percent=key in _PERCENT_INDICATORS,
            )
            if label and operator and threshold is not None:
                parts.append(f"{label} {operator} {threshold}")
    levels = _format_number(definition.get("levels"))
    if not parts or levels is None:
        return None
    return f"{' · '.join(parts)} → 각 1점, 단계 0~{levels}"


def _vol_target_definition_text(definition: object) -> str | None:
    if not isinstance(definition, dict):
        return None
    window = _format_number(definition.get("window"))
    target = _format_number(definition.get("target_vol"), percent=True)
    if window is None or target is None:
        return None
    return f"{window}일 실현 변동성 기준 목표 {target} · 노출 = min(1, 목표/실현)"


def _definition_text(definition: object) -> str:
    if not isinstance(definition, dict) or not definition:
        return "정의가 기록되지 않았습니다."
    definition_type = definition.get("type")
    if definition_type == "ladder":
        rendered = _ladder_definition_text(definition)
    elif definition_type == "vol_target":
        rendered = _vol_target_definition_text(definition)
    elif definition_type == "hybrid":
        ladder = _ladder_definition_text(definition.get("ladder"))
        vol_target = _vol_target_definition_text(definition.get("vol_target"))
        rendered = " + ".join(part for part in (ladder, vol_target) if part)
    else:
        rendered = None
    return rendered or "정의 형식을 표시할 수 없습니다."


def _format_kst_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_KST).strftime("%m-%d %H:%M")


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
        "generated_at_display": None,
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
    history = [
        dict(item) for item in raw_history
        if isinstance(item, dict) and item.get("id") != "compound_ladder_holdout"
    ] \
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
        "generated_at_display": _format_kst_timestamp(document.get("generated_at")),
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


def evaluate_experiment(
    project_root: Path,
    params: object,
    *,
    client_key: str,
) -> dict[str, object]:
    """Parse, rate-limit, and evaluate one unsaved retained-data definition."""

    parsed = parse_experiment_query(params)
    _reserve_experiment(client_key)
    from stock_data.research.rule_leaderboard import CYCLES, evaluate_definition

    candidate = evaluate_definition(
        Path(project_root),
        parsed["definition"],
        str(parsed["basket"]),
        str(parsed["side"]),
    )
    labels = {str(item["id"]): str(item["label"]) for item in CYCLES}
    candidate["definition_text"] = _definition_text(candidate.get("definition"))
    candidate["direction_hint"] = (
        "낮을수록 좋음" if parsed["side"] == "overheat" else "높을수록 좋음"
    )
    cycles = candidate.get("cycles")
    if isinstance(cycles, list):
        candidate["cycles"] = [
            {**row, "label": labels.get(str(row.get("id")), str(row.get("id") or "—"))}
            for row in cycles if isinstance(row, dict)
        ]
    candidate["horizon"] = parsed["horizon"]
    candidate["experiment_count"] = _next_experiment_count()
    candidate["caution"] = EXPERIMENT_CAUTION
    return candidate


def _registered_definition(side: str, raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ResearchInputError("규칙 정의를 입력해 주세요.")
    definition_type = str(raw.get("type") or "")
    if definition_type == "hybrid":
        ladder = raw.get("ladder")
        vol_target = raw.get("vol_target")
        if not isinstance(ladder, Mapping) or not isinstance(vol_target, Mapping):
            raise ResearchInputError("혼합 규칙 정의가 올바르지 않습니다.")
        if ladder.get("side") != side:
            raise ResearchInputError("혼합 규칙의 측과 사다리 측이 같아야 합니다.")
        indicators = ladder.get("indicators")
        levels = ladder.get("levels")
        target_vol = vol_target.get("target_vol")
    else:
        indicators = raw.get("indicators")
        levels = raw.get("levels")
        target_vol = raw.get("target_vol")
    return normalise_experiment_definition(
        side=side,
        definition_type=definition_type,
        indicators=indicators,
        levels=levels,
        target_vol=target_vol,
    )


def register_experiment_candidate(
    project_root: Path, body: object,
) -> dict[str, object]:
    """Append an experimental candidate and regenerate its leaderboard artifact."""

    if not isinstance(body, Mapping):
        raise ResearchInputError("후보 등록 내용을 JSON 객체로 보내 주세요.")
    if "compound" in body:
        body = build_compound_candidate_registration(body)
    name = str(body.get("name") or "").strip()
    reason = str(body.get("reason") or "").strip()
    side = str(body.get("side") or "")
    basket = str(body.get("basket") or "")
    if not name:
        raise ResearchInputError("후보 이름을 입력해 주세요.")
    if len(name) > 100:
        raise ResearchInputError("후보 이름은 100자 이하여야 합니다.")
    if not reason:
        raise ResearchInputError("후보 등록 이유를 입력해 주세요.")
    if len(reason) > 500:
        raise ResearchInputError("후보 등록 이유는 500자 이하여야 합니다.")
    metadata = body.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("source") == "compound_ladder_ui":
        allowed = {"exit", "multiple", "cost", "source"}
        clean_metadata = {str(key): metadata[key] for key in metadata if key in allowed}
        suffix = json.dumps(
            clean_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        reason = f"{reason} · {suffix}"
        if len(reason) > 500:
            raise ResearchInputError("후보 메타데이터를 포함한 등록 이유는 500자 이하여야 합니다.")
    if side not in _EXPERIMENT_SIDES:
        raise ResearchInputError("측은 낙폭 또는 과열이어야 합니다.")
    if basket not in _EXPERIMENT_BASKETS:
        raise ResearchInputError("바스켓을 KR, US_TECH, SEMIS, POOLED 중에서 선택해 주세요.")
    definition = _registered_definition(
        side, body.get("_registry_definition", body.get("definition")),
    )

    from stock_data.research.rule_candidates import (
        RuleCandidateError,
        add_experimental_candidate,
        rules_version,
    )
    from stock_data.research.rule_leaderboard import run_rule_leaderboard

    previous = _read_json(Path(project_root) / LEADERBOARD_RELATIVE) or {}
    previous_version = previous.get("rules_version")
    try:
        registry = add_experimental_candidate(
            Path(project_root), name=name, side=side, basket=basket,
            definition=definition, reason=reason,
        )
    except RuleCandidateError as error:
        raise ResearchInputError(str(error)) from error
    candidate_id = str(registry["candidates"][-1]["id"])
    new_version = rules_version(Path(project_root))
    outcome: dict[str, object] = {}

    def regenerate() -> None:
        try:
            with _REGENERATION_LOCK:
                run_rule_leaderboard(Path(project_root))
        except Exception as error:  # surfaced on synchronous completion
            outcome["error"] = error
        finally:
            _RESEARCH_CACHE.pop(str(Path(project_root).resolve()), None)

    worker = threading.Thread(
        target=regenerate,
        name=f"rule-leaderboard-{candidate_id}",
        daemon=True,
    )
    worker.start()
    worker.join(_REGENERATION_WAIT_SECONDS)
    if worker.is_alive():
        return {
            "status": "queued", "candidate_id": candidate_id,
            "rules_version": new_version, "previous_rules_version": previous_version,
        }
    if "error" in outcome:
        raise RuntimeError("후보는 등록했지만 순위표 재생성에 실패했습니다.") from outcome["error"]
    return {
        "status": "ready", "candidate_id": candidate_id,
        "rules_version": new_version, "previous_rules_version": previous_version,
    }


def _compound_catalog(project_root: Path) -> list[dict[str, str]]:
    output = Path(project_root).resolve() / COMPOUND_RELATIVE
    summary = _read_json(output / "summary.json") or {}
    raw_paths = summary.get("grid_artifacts")
    paths: list[Path] = []
    if isinstance(raw_paths, list):
        for value in raw_paths:
            if not isinstance(value, str):
                continue
            path = Path(project_root).resolve() / value
            if (
                path.parent == output and path.name.startswith("grid_")
                and path.suffix == ".json" and path.is_file()
            ):
                paths.append(path)
    paths.extend(path for path in output.glob("grid_*.json") if path not in paths)
    labels = {
        "kospi": "KOSPI", "kospi200": "KOSPI200", "kospi200_it": "KOSPI200 IT",
        "nasdaq100": "NASDAQ100", "sox": "SOX", "nikkei225": "NIKKEI225",
        "taiex": "TAIEX", "euro_stoxx50": "EURO STOXX50",
        "hang_seng": "HANG SENG", "dax": "DAX",
    }
    prefixes = tuple(sorted(
        ((basket.lower(), basket) for basket in _COMPOUND_BASKETS),
        key=lambda item: len(item[0]), reverse=True,
    ))
    catalog: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(paths):
        stem = path.stem.removeprefix("grid_")
        match = next(
            ((slug, basket) for slug, basket in prefixes if stem.startswith(slug + "_")),
            None,
        )
        if match is None:
            continue
        basket_slug, basket = match
        product = stem[len(basket_slug) + 1:]
        key = (basket, product)
        if not product or key in seen:
            continue
        seen.add(key)
        underlying = labels.get(product, product.replace("_", " ").upper())
        catalog.append({
            "basket": basket,
            "product": product,
            "underlying": underlying,
            "label": f"{basket} · {underlying}",
        })
    basket_order = {basket: index for index, basket in enumerate(_COMPOUND_BASKETS)}
    return sorted(catalog, key=lambda item: (basket_order[item["basket"]], item["product"]))


def _compound_summary(project_root: Path) -> dict[str, object]:
    summary = _read_json(Path(project_root).resolve() / COMPOUND_RELATIVE / "summary.json")
    if not summary:
        return {}
    return {
        key: summary.get(key)
        for key in ("schema_version", "experiment", "fit_window", "holdout_window", "quick")
    }


def build_compound_grid_payload(
    project_root: Path, *, basket: str = "", product: str = "",
) -> dict[str, object]:
    """Return one precomputed grid or the cache catalog; never run a backtest."""

    catalog = _compound_catalog(project_root)
    if not basket and not product:
        registry = _read_json(Path(project_root).resolve() / CANDIDATES_RELATIVE) or {}
        history = registry.get("history") if isinstance(registry.get("history"), list) else []
        holdout_views = sum(
            1 for item in history
            if isinstance(item, dict) and item.get("id") == "compound_ladder_holdout"
        )
        return {
            "catalog": catalog, "summary": _compound_summary(project_root),
            "holdout_views": holdout_views,
        }
    clean_basket = basket.strip().upper()
    clean_product = product.strip().lower()
    if (
        clean_basket not in _COMPOUND_BASKETS
        or not _COMPOUND_TOKEN.fullmatch(clean_product)
        or not any(
            item["basket"] == clean_basket and item["product"] == clean_product
            for item in catalog
        )
    ):
        raise CompoundGridNotFound("미계산 조합 · 선택한 바스켓/상품 그리드가 없습니다.")
    path = Path(project_root).resolve() / COMPOUND_RELATIVE / (
        f"grid_{clean_basket.lower()}_{clean_product}.json"
    )
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompoundGridNotFound("미계산 조합 · 그리드 파일을 읽을 수 없습니다.") from error
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CompoundGridNotFound("미계산 조합 · 그리드 행 형식이 올바르지 않습니다.")
    strategy = [row for row in rows if row.get("row_kind") == "strategy"]
    cached_values = {
        "drawdown_thresholds": sorted({row.get("drawdown_threshold") for row in strategy if _finite(row.get("drawdown_threshold")) is not None}),
        "disp60_thresholds": sorted({row.get("disp60_threshold") for row in strategy if _finite(row.get("disp60_threshold")) is not None}),
        "levels": sorted({row.get("levels") for row in strategy if isinstance(row.get("levels"), int)}),
        "leverage_multiples": sorted({row.get("leverage_multiple") for row in strategy if isinstance(row.get("leverage_multiple"), int)}),
        "exits": sorted({str(row.get("exit")) for row in strategy if row.get("exit") is not None}),
        "cost_enabled": sorted({bool(row.get("cost_enabled")) for row in strategy}),
    }
    baseline = next((row for row in rows if row.get("row_kind") == "baseline"), None)
    return {
        "basket": clean_basket,
        "product": clean_product,
        "summary": _compound_summary(project_root),
        "cached_values": cached_values,
        "baseline": baseline,
        "rows": strategy,
    }


def _compound_combination(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ResearchInputError("조합을 JSON 객체로 보내 주세요.")
    basket = str(value.get("basket") or "").strip().upper()
    product = str(value.get("product") or "").strip().lower()
    exit_variant = str(value.get("exit") or "")
    if basket not in _COMPOUND_BASKETS or not _COMPOUND_TOKEN.fullmatch(product):
        raise ResearchInputError("바스켓/상품 조합이 올바르지 않습니다.")
    if exit_variant not in {"a", "b60", "b120", "c", "d"}:
        raise ResearchInputError("출구 방식이 올바르지 않습니다.")
    product_variant = str(value.get("product_variant") or "synthetic_2x")
    if product_variant not in {"index_1x", "synthetic_2x", "synthetic_3x", "actual_adjusted"}:
        raise ResearchInputError("상품 방식이 올바르지 않습니다.")
    try:
        levels = int(value.get("levels"))
        multiple = int(value.get("leverage_multiple"))
    except (TypeError, ValueError) as error:
        raise ResearchInputError("분할 수와 배율을 숫자로 보내 주세요.") from error
    drawdown = _finite(value.get("drawdown_threshold"))
    disp60 = _finite(value.get("disp60_threshold"))
    if drawdown is None or disp60 is None or levels not in {1, 2, 3, 4} or multiple not in {1, 2, 3}:
        raise ResearchInputError("임계값·분할 수·배율 조합이 올바르지 않습니다.")
    return {
        "basket": basket, "product": product,
        "drawdown_threshold": drawdown, "disp60_threshold": disp60,
        "levels": levels, "leverage_multiple": multiple,
        "exit": exit_variant, "cost_enabled": bool(value.get("cost_enabled")),
        "product_variant": product_variant,
    }


def build_compound_candidate_registration(body: Mapping[str, object]) -> dict[str, object]:
    """Adapt compound knobs to the existing two-signal forward-test contract."""

    combination = _compound_combination(body.get("compound"))
    if combination["basket"] == "FOREIGN":
        raise ResearchInputError("FOREIGN은 현재 포워드 테스트 바스켓 계약 밖입니다.")
    indicators = [
        {
            "key": "drawdown252", "op": "<=",
            "threshold": combination["drawdown_threshold"],
        },
        {
            "key": "disp60", "op": "<=",
            "threshold": combination["disp60_threshold"],
        },
    ]
    return {
        "name": body.get("name"), "reason": body.get("reason"),
        "side": "drawdown", "basket": combination["basket"],
        "definition": {
            "type": "ladder", "indicators": indicators,
            "levels": combination["levels"],
        },
        # The forward lane counts conditions, while the compound experiment's
        # levels knob counts position splits. Preserve both meanings explicitly.
        "_registry_definition": {
            "type": "ladder", "indicators": indicators, "levels": 2,
        },
        "metadata": {
            "exit": combination["exit"],
            "multiple": combination["leverage_multiple"],
            "cost": combination["cost_enabled"],
            "source": "compound_ladder_ui",
        },
    }


def record_compound_holdout_view(
    project_root: Path, body: object, *, client_key: str,
) -> dict[str, object]:
    """Count one deliberate hold-out reveal in the versioned candidate history."""

    combination = _compound_combination(body)
    grid = build_compound_grid_payload(
        project_root,
        basket=str(combination["basket"]), product=str(combination["product"]),
    )
    matched = next((
        row for row in grid["rows"]
        if row.get("base_exposure") == 1.0
        and row.get("drawdown_threshold") == combination["drawdown_threshold"]
        and row.get("disp60_threshold") == combination["disp60_threshold"]
        and row.get("levels") == combination["levels"]
        and row.get("leverage_multiple") == combination["leverage_multiple"]
        and row.get("exit") == combination["exit"]
        and row.get("cost_enabled") is combination["cost_enabled"]
    ), None)
    if matched is None or (
        combination["product_variant"] == "actual_adjusted"
        and not isinstance(matched.get("actual_product_basis"), dict)
    ):
        raise CompoundGridNotFound("미계산 조합 · 홀드아웃을 열 수 없습니다.")
    viewed_at = datetime.now(timezone.utc).isoformat()
    from stock_data.research import rule_candidates

    reason = json.dumps(
        {"combination": combination, "viewed_at": viewed_at},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    try:
        with rule_candidates._REGISTRY_LOCK:
            registry = rule_candidates.load_candidates(Path(project_root))
            rule_candidates._record(
                registry, action="edit", candidate_id="compound_ladder_holdout",
                reason=reason, on=viewed_at[:10],
            )
            registry = rule_candidates._write_registry(Path(project_root), registry)
    except rule_candidates.RuleCandidateError as error:
        raise ResearchInputError(str(error)) from error
    persistent_views = sum(
        1 for item in registry["history"]
        if item.get("id") == "compound_ladder_holdout"
    )
    key = (str(Path(project_root).resolve()), client_key)
    with _COMPOUND_STATE_LOCK:
        _COMPOUND_SESSION_VIEWS[key] += 1
        session_views = _COMPOUND_SESSION_VIEWS[key]
    _RESEARCH_CACHE.pop(str(Path(project_root).resolve()), None)
    return {
        "combination": combination, "viewed_at": viewed_at,
        "persistent_views": persistent_views, "session_views": session_views,
        "attempt_count": registry["attempt_count"],
    }


def _range_values(
    value: object, *, name: str, integers: bool, minimum: float, maximum: float,
) -> tuple[object, ...] | None:
    if value is None or value == "":
        return None
    raw = value if isinstance(value, (list, tuple)) else str(value).split(",")
    output: list[object] = []
    for item in raw:
        try:
            number = int(str(item).strip()) if integers else float(str(item).strip())
        except ValueError as error:
            raise ResearchInputError(f"{name} 범위는 쉼표로 구분한 숫자여야 합니다.") from error
        if not minimum <= float(number) <= maximum:
            raise ResearchInputError(f"{name} 범위가 허용 범위를 벗어났습니다.")
        if number not in output:
            output.append(number)
    if not output:
        raise ResearchInputError(f"{name} 범위를 하나 이상 입력해 주세요.")
    return tuple(output)


def normalise_compound_run(body: object) -> dict[str, object]:
    if not isinstance(body, Mapping):
        raise ResearchInputError("계산 조건을 JSON 객체로 보내 주세요.")
    raw_baskets = body.get("baskets")
    if not isinstance(raw_baskets, list):
        raise ResearchInputError("바스켓을 하나 이상 선택해 주세요.")
    baskets = tuple(dict.fromkeys(str(value).strip().upper() for value in raw_baskets if str(value).strip()))
    if not baskets or set(baskets).difference(_COMPOUND_BASKETS):
        raise ResearchInputError("바스켓은 KR, US_TECH, SEMIS, FOREIGN 중에서 선택해 주세요.")
    product = str(body.get("product") or "synthetic_2x")
    if product not in {"index_1x", "synthetic_2x", "synthetic_3x", "actual_adjusted"}:
        raise ResearchInputError("상품 선택이 올바르지 않습니다.")
    ranges = body.get("ranges") if isinstance(body.get("ranges"), Mapping) else body
    grid = dict(_COMPOUND_DEFAULT_GRID)
    specs = (
        ("drawdown_threshold", "낙폭 임계값", False, -0.90, -0.01),
        ("disp60_threshold", "이격도 임계값", False, -0.90, 0.50),
        ("levels", "분할 수", True, 1, 4),
        ("leverage_multiple", "배율", True, 1, 3),
    )
    for key, label, integers, minimum, maximum in specs:
        parsed = _range_values(
            ranges.get(key), name=label, integers=integers,
            minimum=minimum, maximum=maximum,
        )
        if parsed is not None:
            grid[key] = parsed
    return {"baskets": baskets, "product": product, "grid": grid}


def _compound_command(spec: Mapping[str, object]) -> str:
    baskets = tuple(spec["baskets"])
    grid = dict(spec["grid"])
    if grid == _COMPOUND_DEFAULT_GRID:
        return (
            ".venv\\Scripts\\python.exe scripts\\research\\run_compound_backtest.py "
            f"--project-root . --baskets {','.join(baskets)}"
        )
    code = (
        "from pathlib import Path; "
        "from scripts.research import run_compound_backtest as m; "
        f"m.FULL_GRID={grid!r}; m.run(Path('.'), {baskets!r}, quick=False)"
    )
    return f'.venv\\Scripts\\python.exe -c "{code}"'


def _append_compound_log(path: Path, message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{stamp} {message}\n")


def _run_compound_engine(
    project_root: Path, baskets: tuple[str, ...], grid: dict[str, tuple[object, ...]],
) -> None:
    """Invoke the retained-data runner without allowing its report-doc side effect."""

    runner = importlib.import_module("scripts.research.run_compound_backtest")
    previous_grid = runner.FULL_GRID
    previous_write_text = runner._write_text
    result_doc = (
        Path(project_root).resolve() / "docs/research/RESULTS_20260905_compound_ladder.md"
    )

    def scoped_write_text(path: Path, content: str) -> None:
        if Path(path).resolve() == result_doc:
            return
        previous_write_text(path, content)

    try:
        runner.FULL_GRID = grid
        runner._write_text = scoped_write_text
        runner.run(Path(project_root), baskets, quick=False)
    finally:
        runner.FULL_GRID = previous_grid
        runner._write_text = previous_write_text


def start_compound_run(project_root: Path, body: object) -> dict[str, object]:
    spec = normalise_compound_run(body)
    root = Path(project_root).resolve()
    output = root / COMPOUND_RELATIVE
    lock_path = output / "run.lock"
    log_path = output / "run.log"
    output.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    with _COMPOUND_STATE_LOCK:
        state = _COMPOUND_RUN_STATE.get(str(root), {})
        if state.get("running") or lock_path.exists():
            raise CompoundRunConflict("이미 계산이 실행 중입니다.")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise CompoundRunConflict("이미 계산이 실행 중입니다.") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "started_at": started_at}, handle)
        state = {
            "running": True, "started_at": started_at,
            "last_finished_at": state.get("last_finished_at"), "last_error": None,
            "command": _compound_command(spec),
        }
        _COMPOUND_RUN_STATE[str(root)] = state

    def worker() -> None:
        error_text: str | None = None
        _append_compound_log(log_path, f"START baskets={','.join(spec['baskets'])} product={spec['product']}")
        try:
            _append_compound_log(log_path, "retained 데이터 로드 및 grid 계산 시작")
            _run_compound_engine(root, tuple(spec["baskets"]), dict(spec["grid"]))
            _append_compound_log(log_path, "DONE grid/summary 원자적 갱신 완료")
        except Exception as error:  # status endpoint exposes the bounded message
            error_text = f"{type(error).__name__}: {error}"
            _append_compound_log(log_path, f"ERROR {error_text}")
        finally:
            finished_at = datetime.now(timezone.utc).isoformat()
            with _COMPOUND_STATE_LOCK:
                current = _COMPOUND_RUN_STATE.setdefault(str(root), {})
                current.update({
                    "running": False, "last_finished_at": finished_at,
                    "last_error": error_text,
                })
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    threading.Thread(
        target=worker, name="compound-ladder-retained-run", daemon=True,
    ).start()
    return {"running": True, "started_at": started_at, "command": state["command"]}


def build_compound_run_status(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve()
    output = root / COMPOUND_RELATIVE
    lock_path = output / "run.lock"
    log_path = output / "run.log"
    with _COMPOUND_STATE_LOCK:
        state = dict(_COMPOUND_RUN_STATE.get(str(root), {}))
    try:
        progress = log_path.read_text(encoding="utf-8").splitlines()[-20:]
    except (OSError, UnicodeError):
        progress = []
    return {
        "running": bool(state.get("running") or lock_path.exists()),
        "started_at": state.get("started_at"),
        "progress_lines": progress,
        "last_finished_at": state.get("last_finished_at"),
        "last_error": state.get("last_error"),
        "command": state.get("command"),
    }


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
