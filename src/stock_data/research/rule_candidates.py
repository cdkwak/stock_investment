"""Versioned registry for deterministic, provider-free research rules."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import threading
from typing import Any, Mapping
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
CANDIDATE_PATH = Path("config/research/rule_candidates.json")
# "active" = registered candidate under evaluation (NOT adopted); "adopted" = the user adopted
# the rule in 투자 규칙.md (2026-09-05: candidate lines were read as adopted rules — the label
# must carry adoption, so adoption is an explicit status, never implied by registration).
VALID_STATUSES = frozenset({"active", "adopted", "experimental", "retired"})
VALID_SIDES = frozenset({"drawdown", "overheat", "hybrid"})
VALID_BASKETS = frozenset({"KR", "US_TECH", "SEMIS", "POOLED"})
VALID_TYPES = frozenset({"ladder", "vol_target", "hybrid"})
VALID_INDICATORS = frozenset({"drawdown252", "disp60", "rsi14", "volidx_pct"})
VALID_OPS = frozenset({"<=", ">="})
_CANDIDATE_KEYS = frozenset(
    {"id", "name", "side", "basket", "status", "definition", "added_on", "reason"}
)
_REGISTRY_LOCK = threading.RLock()


class RuleCandidateError(ValueError):
    """Raised when a candidate registry violates its versioned contract."""


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleCandidateError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise RuleCandidateError(f"{field} must be a finite number")
    return result


def _validate_indicator(item: object, *, path: str) -> None:
    if not isinstance(item, Mapping) or set(item) != {"key", "op", "threshold"}:
        raise RuleCandidateError(f"{path} must contain exactly key/op/threshold")
    if item["key"] not in VALID_INDICATORS:
        raise RuleCandidateError(f"{path}.key is unsupported: {item['key']!r}")
    if item["op"] not in VALID_OPS:
        raise RuleCandidateError(f"{path}.op must be <= or >=")
    _finite_number(item["threshold"], f"{path}.threshold")


def _validate_ladder(definition: object, *, path: str, nested: bool = False) -> None:
    if not isinstance(definition, Mapping):
        raise RuleCandidateError(f"{path} must be an object")
    required = {"indicators", "levels"} if nested else {"type", "indicators", "levels"}
    optional = {"side"} if nested else set()
    if set(definition) - (required | optional) or not required <= set(definition):
        raise RuleCandidateError(f"{path} has invalid keys")
    if not nested and definition.get("type") != "ladder":
        raise RuleCandidateError(f"{path}.type must be ladder")
    if nested and definition.get("side") not in {"drawdown", "overheat"}:
        raise RuleCandidateError(f"{path}.side must be drawdown or overheat")
    indicators = definition["indicators"]
    if not isinstance(indicators, list) or not indicators:
        raise RuleCandidateError(f"{path}.indicators must be a non-empty list")
    if len(indicators) > 4:
        raise RuleCandidateError(f"{path}.indicators supports at most four entries")
    for index, indicator in enumerate(indicators):
        _validate_indicator(indicator, path=f"{path}.indicators[{index}]")
    keys = [str(item["key"]) for item in indicators]
    if len(keys) != len(set(keys)):
        raise RuleCandidateError(f"{path}.indicators contains duplicate keys")
    levels = definition["levels"]
    if isinstance(levels, bool) or not isinstance(levels, int) or levels != len(indicators):
        raise RuleCandidateError(f"{path}.levels must equal the indicator count")


def _validate_vol_target(definition: object, *, path: str, nested: bool = False) -> None:
    if not isinstance(definition, Mapping):
        raise RuleCandidateError(f"{path} must be an object")
    required = {"target_vol", "window"} if nested else {"type", "target_vol", "window"}
    if set(definition) != required:
        raise RuleCandidateError(f"{path} has invalid keys")
    if not nested and definition.get("type") != "vol_target":
        raise RuleCandidateError(f"{path}.type must be vol_target")
    target = _finite_number(definition["target_vol"], f"{path}.target_vol")
    if target <= 0:
        raise RuleCandidateError(f"{path}.target_vol must be positive")
    window = definition["window"]
    if isinstance(window, bool) or not isinstance(window, int) or window < 2:
        raise RuleCandidateError(f"{path}.window must be an integer of at least two")


def validate_candidate(candidate: object) -> dict[str, Any]:
    """Validate and return a detached candidate mapping."""

    if not isinstance(candidate, Mapping) or set(candidate) != _CANDIDATE_KEYS:
        raise RuleCandidateError("candidate keys do not match the rule-candidate contract")
    for field in ("id", "name", "added_on", "reason"):
        if not isinstance(candidate[field], str) or not candidate[field].strip():
            raise RuleCandidateError(f"candidate {field} must be a non-empty string")
    candidate_id = str(candidate["id"])
    if not candidate_id.replace("_", "").isalnum() or candidate_id.lower() != candidate_id:
        raise RuleCandidateError(
            "candidate id must contain lowercase letters, numbers, or underscores"
        )
    try:
        date.fromisoformat(str(candidate["added_on"]))
    except ValueError as error:
        raise RuleCandidateError("candidate added_on must be YYYY-MM-DD") from error
    if candidate["side"] not in VALID_SIDES:
        raise RuleCandidateError(f"candidate side is unsupported: {candidate['side']!r}")
    if candidate["basket"] not in VALID_BASKETS:
        raise RuleCandidateError(f"candidate basket is unsupported: {candidate['basket']!r}")
    if candidate["status"] not in VALID_STATUSES:
        raise RuleCandidateError(f"candidate status is unsupported: {candidate['status']!r}")
    definition = candidate["definition"]
    if not isinstance(definition, Mapping) or definition.get("type") not in VALID_TYPES:
        raise RuleCandidateError("candidate definition.type is unsupported")
    definition_type = definition["type"]
    if definition_type == "ladder":
        _validate_ladder(definition, path="definition")
        if candidate["side"] == "hybrid":
            raise RuleCandidateError("a ladder candidate side must be drawdown or overheat")
    elif definition_type == "vol_target":
        _validate_vol_target(definition, path="definition")
    else:
        if set(definition) != {"type", "ladder", "vol_target"}:
            raise RuleCandidateError("hybrid definition must contain type/ladder/vol_target")
        _validate_ladder(definition["ladder"], path="definition.ladder", nested=True)
        _validate_vol_target(
            definition["vol_target"], path="definition.vol_target", nested=True
        )
        if candidate["side"] != "hybrid":
            raise RuleCandidateError("a hybrid definition uses side=hybrid")
    return deepcopy(dict(candidate))


def validate_registry(payload: object) -> dict[str, Any]:
    """Validate the complete schema-v1 registry and preserve candidate order."""

    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version", "attempt_count", "history", "candidates"
    }:
        raise RuleCandidateError("registry keys do not match schema version 1")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise RuleCandidateError("unsupported rule-candidate schema_version")
    attempts = payload["attempt_count"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise RuleCandidateError("attempt_count must be a non-negative integer")
    history = payload["history"]
    if not isinstance(history, list):
        raise RuleCandidateError("history must be a list")
    for index, event in enumerate(history):
        if not isinstance(event, Mapping) or set(event) != {"date", "action", "id", "reason"}:
            raise RuleCandidateError(f"history[{index}] has invalid keys")
        if event["action"] not in {"add", "edit", "remove", "retire"}:
            raise RuleCandidateError(f"history[{index}].action is unsupported")
        for field in ("date", "id", "reason"):
            if not isinstance(event[field], str) or not event[field].strip():
                raise RuleCandidateError(f"history[{index}].{field} must be non-empty")
        try:
            date.fromisoformat(str(event["date"]))
        except ValueError as error:
            raise RuleCandidateError(f"history[{index}].date must be YYYY-MM-DD") from error
    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise RuleCandidateError("candidates must be a list")
    validated = [validate_candidate(item) for item in candidates]
    identifiers = [item["id"] for item in validated]
    if len(identifiers) != len(set(identifiers)):
        raise RuleCandidateError("candidate ids must be unique")
    return {
        "schema_version": SCHEMA_VERSION,
        "attempt_count": attempts,
        "history": [deepcopy(dict(item)) for item in history],
        "candidates": validated,
    }


def candidate_file(project_root: Path) -> Path:
    return project_root.resolve() / CANDIDATE_PATH


def rules_version(project_root: Path) -> str:
    """Return the SHA-256 of the exact versioned candidate-list bytes."""

    return hashlib.sha256(candidate_file(project_root).read_bytes()).hexdigest()


def load_candidates(project_root: Path) -> dict[str, Any]:
    path = candidate_file(project_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuleCandidateError(f"cannot load candidate registry: {path}") from error
    return validate_registry(payload)


def _write_registry(project_root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_registry(payload)
    path = candidate_file(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
    return validated


def _mutation_date(value: str | None) -> str:
    result = value or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    try:
        date.fromisoformat(result)
    except ValueError as error:
        raise RuleCandidateError("mutation date must be YYYY-MM-DD") from error
    return result


def _record(
    payload: dict[str, Any], *, action: str, candidate_id: str, reason: str, on: str | None
) -> None:
    if not reason.strip():
        raise RuleCandidateError("every mutation requires a non-empty reason")
    payload["attempt_count"] += 1
    payload["history"].append(
        {"date": _mutation_date(on), "action": action, "id": candidate_id, "reason": reason}
    )


def add_candidate(
    project_root: Path, candidate: Mapping[str, Any], *, reason: str, on: str | None = None
) -> dict[str, Any]:
    payload = load_candidates(project_root)
    validated = validate_candidate(candidate)
    if any(item["id"] == validated["id"] for item in payload["candidates"]):
        raise RuleCandidateError(f"candidate already exists: {validated['id']}")
    payload["candidates"].append(validated)
    _record(payload, action="add", candidate_id=validated["id"], reason=reason, on=on)
    return _write_registry(project_root, payload)


def add_experimental_candidate(
    project_root: Path,
    *,
    name: str,
    side: str,
    basket: str,
    definition: Mapping[str, Any],
    reason: str,
    on: str | None = None,
) -> dict[str, Any]:
    """Register one UI experiment with an atomic history/attempt update."""

    if side not in {"drawdown", "overheat"}:
        raise RuleCandidateError("experimental side must be drawdown or overheat")
    if not isinstance(name, str) or not name.strip():
        raise RuleCandidateError("candidate name must be a non-empty string")
    if not isinstance(reason, str) or not reason.strip():
        raise RuleCandidateError("every mutation requires a non-empty reason")
    copied_definition = deepcopy(dict(definition))
    if copied_definition.get("type") == "hybrid":
        ladder = copied_definition.get("ladder")
        if not isinstance(ladder, Mapping) or ladder.get("side") != side:
            raise RuleCandidateError("hybrid ladder side must match the experiment side")
        stored_side = "hybrid"
    else:
        stored_side = side

    with _REGISTRY_LOCK:
        payload = load_candidates(project_root)
        digest = hashlib.sha256(json.dumps(
            {
                "name": name.strip(), "side": side, "basket": basket,
                "definition": copied_definition,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()[:10]
        base_id = f"experiment_{digest}"
        identifiers = {str(item["id"]) for item in payload["candidates"]}
        candidate_id = base_id
        suffix = 2
        while candidate_id in identifiers:
            candidate_id = f"{base_id}_{suffix}"
            suffix += 1
        candidate = validate_candidate({
            "id": candidate_id,
            "name": name.strip(),
            "side": stored_side,
            "basket": basket,
            "status": "experimental",
            "definition": copied_definition,
            "added_on": _mutation_date(on),
            "reason": reason.strip(),
        })
        payload["candidates"].append(candidate)
        _record(
            payload, action="add", candidate_id=candidate_id, reason=reason.strip(), on=on,
        )
        return _write_registry(project_root, payload)


def edit_candidate(
    project_root: Path,
    candidate_id: str,
    replacement: Mapping[str, Any],
    *,
    reason: str,
    on: str | None = None,
) -> dict[str, Any]:
    payload = load_candidates(project_root)
    validated = validate_candidate(replacement)
    if validated["id"] != candidate_id:
        raise RuleCandidateError("edit cannot change a candidate id")
    matches = [
        index
        for index, item in enumerate(payload["candidates"])
        if item["id"] == candidate_id
    ]
    if not matches:
        raise RuleCandidateError(f"candidate does not exist: {candidate_id}")
    payload["candidates"][matches[0]] = validated
    _record(payload, action="edit", candidate_id=candidate_id, reason=reason, on=on)
    return _write_registry(project_root, payload)


def retire_candidate(
    project_root: Path, candidate_id: str, *, reason: str, on: str | None = None
) -> dict[str, Any]:
    payload = load_candidates(project_root)
    matches = [item for item in payload["candidates"] if item["id"] == candidate_id]
    if not matches:
        raise RuleCandidateError(f"candidate does not exist: {candidate_id}")
    if matches[0]["status"] == "retired":
        raise RuleCandidateError(f"candidate is already retired: {candidate_id}")
    matches[0]["status"] = "retired"
    _record(payload, action="retire", candidate_id=candidate_id, reason=reason, on=on)
    return _write_registry(project_root, payload)


def remove_candidate(
    project_root: Path, candidate_id: str, *, reason: str, on: str | None = None
) -> dict[str, Any]:
    payload = load_candidates(project_root)
    before = len(payload["candidates"])
    payload["candidates"] = [
        item for item in payload["candidates"] if item["id"] != candidate_id
    ]
    if len(payload["candidates"]) == before:
        raise RuleCandidateError(f"candidate does not exist: {candidate_id}")
    _record(payload, action="remove", candidate_id=candidate_id, reason=reason, on=on)
    return _write_registry(project_root, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or mutate the rule candidate registry")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    for command in ("add", "edit"):
        child = subparsers.add_parser(command)
        child.add_argument("--candidate-json", required=True)
        child.add_argument("--reason", required=True)
        child.add_argument("--date")
        if command == "edit":
            child.add_argument("--id", required=True)
    for command in ("retire", "remove"):
        child = subparsers.add_parser(command)
        child.add_argument("--id", required=True)
        child.add_argument("--reason", required=True)
        child.add_argument("--date")
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = load_candidates(args.project_root)
    elif args.command == "add":
        payload = add_candidate(
            args.project_root, json.loads(args.candidate_json), reason=args.reason, on=args.date
        )
    elif args.command == "edit":
        payload = edit_candidate(
            args.project_root, args.id, json.loads(args.candidate_json),
            reason=args.reason, on=args.date,
        )
    elif args.command == "retire":
        payload = retire_candidate(args.project_root, args.id, reason=args.reason, on=args.date)
    else:
        payload = remove_candidate(args.project_root, args.id, reason=args.reason, on=args.date)
    print(json.dumps({
        "attempt_count": payload["attempt_count"],
        "rules_version": rules_version(args.project_root),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANDIDATE_PATH", "RuleCandidateError", "add_candidate", "add_experimental_candidate", "candidate_file",
    "edit_candidate", "load_candidates", "remove_candidate", "retire_candidate",
    "rules_version", "validate_candidate", "validate_registry",
]
