"""Retained-data projections and bounded experiments for the Research page.

Leaderboard display remains artifact-backed.  Explicit experiment calls may
import the offline producer, but they read retained Parquet only; candidate
registration is a loopback-gated router concern.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
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

_RESEARCH_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_FORWARD_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_EXPERIMENT_LOCK = threading.RLock()
_EXPERIMENT_TIMES: dict[str, deque[float]] = defaultdict(deque)
_EXPERIMENT_COUNT = 0
_REGENERATION_LOCK = threading.Lock()
_REGENERATION_WAIT_SECONDS = 60.0
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
    if side not in _EXPERIMENT_SIDES:
        raise ResearchInputError("측은 낙폭 또는 과열이어야 합니다.")
    if basket not in _EXPERIMENT_BASKETS:
        raise ResearchInputError("바스켓을 KR, US_TECH, SEMIS, POOLED 중에서 선택해 주세요.")
    definition = _registered_definition(side, body.get("definition"))

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
