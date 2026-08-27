from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable

from issue_state.adapters import adapt_files, evidence_identity
from issue_state.model import (
    IssueRecord, SEVERITIES, canonical_json, evaluate_suppression,
)
from issue_state.store import IssueStateStore


POLICY_SCHEMA = "escalation-policy/v1"
DEFAULT_STORE = Path("artifacts/issue_state/v1/issues.json")
DEFAULT_POLICY = Path("artifacts/issue_state/escalation_policy.json")
QUEUE_MANAGER = Path(__file__).resolve().parents[1] / "request_queue.py"
_QUEUE_FINGERPRINT = re.compile(r"^[a-z0-9][a-z0-9:._=-]{0,255}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9가-힣 _.,:/()=+-]{1,240}$")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_AUTOMATION_ENABLED_DATASETS = "group=automation-enabled-datasets"
_FORBIDDEN = re.compile(
    r"(?i)(?:https?://|webhook|authorization|bearer|token|secret|password|account|holding|balance|order|\d{10,})"
)


def default_inputs(project_root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    candidates.extend((project_root / "artifacts/runtime_logs/application").glob("*.json"))
    candidates.extend((project_root / "artifacts/runtime_logs/data_updates/events").glob("*/*.json"))
    health = project_root / "artifacts/daily_health/universe_data_v2_20260819.json"
    if health.is_file():
        candidates.append(health)
    candidates.extend((project_root / "data/state/provider_scheduler/kr_market_daily_occurrences").glob("*.json"))
    safe_candidates = []
    for candidate in candidates:
        try:
            evidence_identity(project_root, candidate)
        except ValueError:
            continue
        safe_candidates.append(candidate)
    result = tuple(sorted(set(safe_candidates), key=lambda item: item.as_posix()))
    if len(result) > 5_000:
        raise ValueError("issue input bound exceeded")
    return result


def _safe_text(value: object, name: str) -> str:
    if type(value) is not str or not _SAFE_TEXT.fullmatch(value) or _FORBIDDEN.search(value):
        raise ValueError(f"policy {name} is unsafe")
    normalized = value.replace("\\", "/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized)
        or _URI_SCHEME.match(normalized)
        or ".." in normalized.split("/")
    ):
        raise ValueError(f"policy {name} is unsafe")
    return value


def load_policies(path: Path | None) -> tuple[dict[str, object], ...]:
    if path is None or not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if type(payload) is not dict or set(payload) != {"schema", "policies"} or payload["schema"] != POLICY_SCHEMA:
        raise ValueError("escalation policy envelope differs")
    rows = payload["policies"]
    if type(rows) is not list or len(rows) > 100:
        raise ValueError("escalation policy rows differ")
    result: list[dict[str, object]] = []
    for row in rows:
        required = {
            "policy_id", "revision", "enabled", "effective_from", "effective_until",
            "fingerprint", "stable_code", "target_kind", "target_id",
            "minimum_severity", "all_of", "discovery_rate", "cooldown_seconds",
            "queue_fingerprint", "discovery_template",
        }
        if type(row) is not dict or set(row) != required:
            raise ValueError("escalation policy row fields differ")
        if type(row["enabled"]) is not bool or type(row["revision"]) is not int or row["revision"] < 1:
            raise ValueError("escalation policy revision differs")
        if row["minimum_severity"] not in SEVERITIES:
            raise ValueError("escalation policy classification differs")
        for key in ("policy_id", "stable_code", "target_kind"):
            if type(row[key]) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,63}", row[key]):
                raise ValueError(f"escalation policy {key} differs")
        if row["fingerprint"] is not None and (
            type(row["fingerprint"]) is not str or not _DIGEST.fullmatch(row["fingerprint"])
        ):
            raise ValueError("escalation policy fingerprint differs")
        if row["target_id"] is not None and (
            type(row["target_id"]) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.:/=-]{0,159}", row["target_id"])
        ):
            raise ValueError("escalation policy target_id differs")
        if row["fingerprint"] is None and row["target_id"] is None:
            raise ValueError("escalation policy selector must bind an exact target")
        if row["target_id"] == _AUTOMATION_ENABLED_DATASETS and (
            row["fingerprint"] is not None
            or row["target_kind"] != "DATASET"
            or row["stable_code"] not in {"HEALTH_STALE", "HEALTH_UNKNOWN"}
        ):
            raise ValueError("automation-enabled selector is not allowlisted")
        effective_from = _policy_time(row["effective_from"], "effective_from")
        effective_until = None if row["effective_until"] is None else _policy_time(row["effective_until"], "effective_until")
        if effective_until is not None and effective_until <= effective_from:
            raise ValueError("escalation policy effective interval differs")
        predicates = row["all_of"]
        if type(predicates) is not list or not predicates or len(predicates) > 8:
            raise ValueError("escalation policy predicates differ")
        persistence = False
        for predicate in predicates:
            if type(predicate) is not dict or set(predicate) != {"kind", "operator", "value"}:
                raise ValueError("escalation policy predicate fields differ")
            kind, operator, value = predicate["kind"], predicate["operator"], predicate["value"]
            if kind in {"occurrence_count", "active_duration_seconds", "overdue_by_seconds"}:
                if operator != "gte" or type(value) is not int or value < 1:
                    raise ValueError("escalation policy numeric predicate differs")
                if kind == "occurrence_count" and value < 2:
                    raise ValueError("occurrence persistence must ignore a single occurrence")
                persistence = True
            elif kind == "freshness":
                if operator != "in" or type(value) is not list or not value or any(
                    item not in {"CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN", "NOT_APPLICABLE", "BLOCKED"}
                    for item in value
                ):
                    raise ValueError("escalation freshness predicate differs")
            elif kind == "importance":
                if operator != "in" or type(value) is not list or not value or any(
                    item not in {"LOW", "NORMAL", "HIGH", "CRITICAL"} for item in value
                ):
                    raise ValueError("escalation importance predicate differs")
            else:
                raise ValueError("escalation policy predicate kind differs")
        if not persistence:
            raise ValueError("escalation policy lacks persistence predicate")
        rate = row["discovery_rate"]
        if (
            type(rate) is not dict or set(rate) != {"max_count", "window_seconds"}
            or type(rate["max_count"]) is not int or not 1 <= rate["max_count"] <= 10
            or type(rate["window_seconds"]) is not int or not 60 <= rate["window_seconds"] <= 2_592_000
            or type(row["cooldown_seconds"]) is not int or not 60 <= row["cooldown_seconds"] <= 2_592_000
        ):
            raise ValueError("escalation policy discovery bound differs")
        queue_fingerprint = row["queue_fingerprint"]
        if type(queue_fingerprint) is not str or "{fingerprint}" not in queue_fingerprint:
            raise ValueError("queue fingerprint must bind the stable issue fingerprint")
        rendered = queue_fingerprint.replace("{fingerprint}", "a" * 64)
        if not _QUEUE_FINGERPRINT.fullmatch(rendered):
            raise ValueError("queue fingerprint template differs")
        template = row["discovery_template"]
        if type(template) is not dict or set(template) != {"title", "symptom", "impact", "suspected_scope", "reproduce", "priority_hint"}:
            raise ValueError("escalation discovery template differs")
        if template["priority_hint"] not in {"P0", "P1", "P2"}:
            raise ValueError("escalation discovery priority differs")
        for key in ("title", "symptom", "impact", "suspected_scope", "reproduce"):
            _safe_text(template[key], key)
        result.append(dict(row))
    return tuple(result)


def _policy_time(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"policy {name} differs")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"policy {name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _record_snapshot_digest(record: IssueRecord) -> str:
    return hashlib.sha256(canonical_json(record.to_dict())).hexdigest()


def matching_policy(record: IssueRecord, policies: Iterable[dict[str, object]]) -> dict[str, object] | None:
    if record.state == "RECOVERED" or record.suppression.get("state") == "ACTIVE":
        return None
    if (
        record.suppression.get("state") in {"EXPIRED", "RELEASED"}
        and record.source_event_count <= int(record.suppression["discovery_after_source_event_count"])
    ):
        return None
    snapshot_at = _policy_time(record.latest_at, "record latest_at")
    for policy in policies:
        if not policy["enabled"]:
            continue
        if snapshot_at < _policy_time(policy["effective_from"], "effective_from"):
            continue
        if policy["effective_until"] is not None and snapshot_at >= _policy_time(policy["effective_until"], "effective_until"):
            continue
        if policy["fingerprint"] is not None and policy["fingerprint"] != record.fingerprint:
            continue
        if policy["stable_code"] != record.stable_code or policy["target_kind"] != record.target_kind:
            continue
        if policy["target_id"] == _AUTOMATION_ENABLED_DATASETS:
            from stock_data.orchestration.daily_operations import DATASET_UNIVERSE
            registered = DATASET_UNIVERSE.get(record.target_id)
            if registered is None or not registered.automation_enabled:
                continue
        elif policy["target_id"] is not None and policy["target_id"] != record.target_id:
            continue
        if SEVERITIES.index(record.severity) < SEVERITIES.index(str(policy["minimum_severity"])):
            continue
        if not all(_predicate_matches(record, predicate) for predicate in policy["all_of"]):
            continue
        return policy
    return None


def _predicate_matches(record: IssueRecord, predicate: dict[str, object]) -> bool:
    kind, value = predicate["kind"], predicate["value"]
    if kind == "occurrence_count":
        prior_occurrences = record.historical_occurrence_count + sum(
            int(item["occurrence_count"]) for item in record.previous_epochs
        )
        return record.occurrence_count - prior_occurrences >= int(value)
    if kind == "active_duration_seconds":
        opened = _policy_time(record.opened_at, "record opened_at")
        latest = _policy_time(record.latest_at, "record latest_at")
        return (latest - opened).total_seconds() >= int(value)
    if kind == "overdue_by_seconds":
        if record.expected_by is None:
            return False
        expected = _policy_time(record.expected_by, "record expected_by")
        latest = _policy_time(record.latest_at, "record latest_at")
        return (latest - expected).total_seconds() >= int(value)
    if kind == "freshness":
        return record.freshness in value
    if kind == "importance":
        return record.importance in value
    raise ValueError("unknown escalation predicate")


def _queue_entries(queue_root: Path) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    state_roots = (
        queue_root / "inbox/new", queue_root / "inbox/ready",
        queue_root / "active", queue_root / "review",
        queue_root / "blocked", queue_root / "done",
    )
    for state_root in state_roots:
        if not state_root.exists():
            continue
        for task_dir in state_root.iterdir():
            if not task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            meta_path = task_dir / "META.json"
            if not meta_path.is_file():
                raise ValueError("queue task lacks META.json")
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if type(payload) is not dict or type(payload.get("fingerprint")) is not str or type(payload.get("id")) is not str:
                raise ValueError("queue task metadata differs")
            entries.append({
                "fingerprint": payload["fingerprint"], "id": payload.get("id"),
                "state": payload.get("state"), "created_at": payload.get("created_at"),
                "completed_at": payload.get("completed_at"),
            })
    index_path = queue_root / "COMPLETED_INDEX.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if type(index) is not dict or set(index) != {"schema_version", "entries", "entries_sha256"} or index.get("schema_version") != 1 or type(index.get("entries")) is not list:
            raise ValueError("completed queue index differs")
        expected_digest = hashlib.sha256(canonical_json(index["entries"])).hexdigest()
        if index["entries_sha256"] != expected_digest:
            raise ValueError("completed queue index digest differs")
        for item in index["entries"]:
            if type(item) is not dict or type(item.get("fingerprint")) is not str:
                raise ValueError("completed queue entry differs")
            entries.append({
                "fingerprint": item["fingerprint"], "id": item.get("id"),
                "state": "compacted", "created_at": None,
                "completed_at": item.get("completed_at"),
            })
    return tuple(entries)


def queue_decision(record: IssueRecord, policy: dict[str, object], queue_root: Path) -> tuple[str, str | None, dict[str, object] | None]:
    base = str(policy["queue_fingerprint"]).replace("{fingerprint}", record.fingerprint)
    entries = _queue_entries(queue_root)
    if any(entry["fingerprint"] == record.fingerprint for entry in entries):
        return "DUPLICATE_STABLE_ISSUE", None, None
    exact = [entry for entry in entries if entry["fingerprint"] == base]
    if not exact:
        return "DISCOVER", base, None
    if any(entry["state"] not in {"done", "compacted"} for entry in exact):
        return "DUPLICATE_ACTIVE", None, None
    done = next((entry for entry in exact if entry["state"] == "done"), None)
    if done is not None:
        return "RECURRENCE_REVIEW_REQUIRED", None, None
    completed = exact[0]
    if record.recovery_count < 1 or record.epoch < 2 or type(completed.get("id")) is not str:
        return "DUPLICATE_COMPLETED", None, None
    identity = {
        "completed_task_id": completed["id"], "recovery_epoch": record.epoch,
        "schema": "queue-recurrence/v1", "stable_issue_fingerprint": record.fingerprint,
    }
    recurrence = hashlib.sha256(canonical_json(identity)).hexdigest()
    recurrence_fingerprints = {
        hashlib.sha256(canonical_json({**identity, "recovery_epoch": epoch})).hexdigest()
        for epoch in range(2, record.epoch + 1)
    }
    if any(entry["fingerprint"] == recurrence for entry in entries):
        return "DUPLICATE_RECURRENCE", None, None
    relevant = [entry for entry in entries if entry["fingerprint"] in recurrence_fingerprints | {base}]
    snapshot = _policy_time(record.latest_at, "record latest_at")
    timestamps = []
    for entry in relevant:
        raw = entry.get("completed_at") or entry.get("created_at")
        if raw is not None:
            timestamps.append(_policy_time(raw, "queue entry timestamp"))
    if timestamps:
        latest = max(timestamps)
        if (snapshot - latest).total_seconds() < int(policy["cooldown_seconds"]):
            return "COOLDOWN_ACTIVE", None, None
        rate = policy["discovery_rate"]
        within = sum(
            (snapshot - instant).total_seconds() <= int(rate["window_seconds"])
            for instant in timestamps if instant <= snapshot
        )
        if within >= int(rate["max_count"]):
            return "RATE_LIMITED", None, None
    return "DISCOVER_RECURRENCE", recurrence, identity


def discover(
    project_root: Path, record: IssueRecord, policy: dict[str, object], fingerprint: str,
    recurrence: dict[str, object] | None,
) -> str:
    evidence_parts = list(record.evidence[:3])
    if recurrence is not None:
        evidence_parts.append(
            "stable_issue_sha256={stable_issue_fingerprint},completed_task={completed_task_id},recovery_epoch={recovery_epoch}".format(**recurrence)
        )
    evidence = ",".join(evidence_parts)
    template = policy["discovery_template"]
    command = [
        sys.executable, str(QUEUE_MANAGER), "--root",
        str(project_root / "artifacts/request_queue"), "discover",
        "--title", str(template["title"]), "--discovered-by", "issue-state-sync",
        "--source-task", "ISSUE_STATE_SYNC", "--fingerprint", fingerprint,
        "--symptom", str(template["symptom"]), "--evidence", evidence,
        "--impact", str(template["impact"]), "--suspected-scope", str(template["suspected_scope"]),
        "--reproduce", str(template["reproduce"]), "--priority-hint", str(template["priority_hint"]),
    ]
    completed = subprocess.run(command, cwd=project_root, check=True, text=True, capture_output=True)
    result = completed.stdout.strip()
    if not re.fullmatch(r"(?:RQ-[A-Z0-9-]+|duplicate:[A-Za-z0-9_.:-]+)", result):
        raise RuntimeError("queue discover returned an unexpected result")
    return result


def sync(
    project_root: Path, inputs: tuple[Path, ...], *, store_path: Path,
    policy_path: Path | None, enable_discovery: bool,
) -> dict[str, object]:
    project_root = project_root.resolve()
    canonical_store = project_root / DEFAULT_STORE
    if store_path.resolve() != canonical_store:
        raise ValueError("issue store path must be the canonical project store")
    canonical_policy = project_root / DEFAULT_POLICY
    if policy_path is not None and policy_path.resolve() != canonical_policy:
        raise ValueError("escalation policy path must be the canonical project policy")
    events = adapt_files(project_root, inputs)
    store = IssueStateStore(project_root)
    records = store.update(events)
    evaluated_at = datetime.now(timezone.utc).isoformat()
    evaluated = tuple(evaluate_suppression(record, evaluated_at=evaluated_at) for record in records)
    if [item.to_dict() for item in evaluated] != [item.to_dict() for item in records]:
        records = store.replace_records(evaluated)
    policies = load_policies(policy_path)
    decisions: list[dict[str, object]] = []
    for record in records:
        policy = matching_policy(record, policies)
        if policy is None:
            continue
        state, fingerprint, recurrence = queue_decision(record, policy, project_root / "artifacts/request_queue")
        task = None
        if state.startswith("DISCOVER") and fingerprint is not None and enable_discovery:
            task = discover(project_root, record, policy, fingerprint, recurrence)
        decisions.append({
            "fingerprint": record.fingerprint, "decision": state, "task": task,
            "policy_id": policy["policy_id"], "policy_revision": policy["revision"],
            "issue_snapshot_sha256": _record_snapshot_digest(record),
        })
    return {
        "schema": "issue-state-sync-result/v1", "provider_calls": 0,
        "input_count": len(inputs), "event_count": len(events),
        "issue_count": len(records), "discovery_enabled": enable_discovery,
        "decisions": decisions,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate sanitized local operational issue state.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--input", type=Path, action="append")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--enable-discovery", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = args.project_root.resolve()
    inputs = tuple(path.resolve() for path in args.input) if args.input else default_inputs(project_root)
    store_path = args.store.resolve() if args.store else project_root / DEFAULT_STORE
    policy_path = args.policy.resolve() if args.policy else project_root / DEFAULT_POLICY
    result = sync(
        project_root, inputs, store_path=store_path, policy_path=policy_path,
        enable_discovery=args.enable_discovery,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
