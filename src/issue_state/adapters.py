from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterable

from runtime_diagnostics.events import RuntimeDiagnosticEvent
from stock_data.orchestration.update_event_log import (
    EventState, FAILURE_STATES, SUCCESS_STATES, UpdateEvent,
)

from .model import IssueEvent, validate_evidence_identity


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/=-]{0,159}$")
_HEALTH_ENVELOPE_FIELDS = {
    "actionable_incident_count", "as_of", "automation_enabled_count",
    "core_operation_missing", "core_operations_count", "core_reference_time",
    "dataset_count", "datasets", "dimension_summary", "generated_at",
    "operations_registry_count", "run_id", "runtime_coverage_failure_count",
    "runtime_coverage_failures", "runtime_coverage_validated_count", "schema_version",
}
_HEALTH_ROW_FIELDS = {
    "api_calls", "automation_enabled", "automation_policy", "blocker", "calendar",
    "calendar_source", "calendar_version", "dataset", "expected",
    "expected_lag_policy", "finality", "freshness", "gap_status", "grain",
    "last_run", "last_success", "latest", "market_expected_latest", "missing_dates",
    "observation_calendar", "operational", "pit", "pre_network_noop",
    "provider_availability_policy", "refresh", "role", "runtime_coverage",
    "scheduler_lane", "scheduler_management", "source",
    "display_consumer_eligibility", "display_consumer_reason",
    "research_consumer_eligibility", "research_consumer_reason",
    "predictive_consumer_eligibility", "predictive_consumer_reason",
}
_HEALTH_INTRADAY_FIELDS = {
    "expected_bars", "fallback", "latest_complete_session", "missing_bars",
    "observed_bars", "provider",
}
_FAILURE_SEVERITY = {
    EventState.PARTIAL_INELIGIBLE: "WARNING",
    EventState.VALIDATION_FAILURE: "ERROR",
    EventState.PROVIDER_NETWORK_FAILURE: "ERROR",
    EventState.AUTH_PERMISSION_FAILURE: "ERROR",
    EventState.LOCAL_IO_FAILURE: "ERROR",
}


def evidence_identity(project_root: Path, path: Path) -> str:
    root = Path(project_root).resolve()
    target = Path(path).resolve(strict=True)
    relative = target.relative_to(root).as_posix()
    if not target.is_file() or ".." in Path(relative).parts or "@" in relative:
        raise ValueError("evidence path is unsafe")
    return validate_evidence_identity(
        f"{relative}@sha256:{hashlib.sha256(target.read_bytes()).hexdigest()}"
    )


def _is_reparse(path: Path) -> bool:
    value = os.lstat(path)
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _validate_input_path(project_root: Path, path: Path) -> Path:
    root = Path(project_root).resolve()
    supplied = Path(path)
    candidate = Path(os.path.abspath(supplied))
    candidate.relative_to(root)
    current = candidate
    while current != root:
        if current.exists() and _is_reparse(current):
            raise ValueError("issue input path uses redirection")
        current = current.parent
    target = candidate.resolve(strict=True)
    relative = target.relative_to(root)
    current = target
    while current != root:
        if _is_reparse(current):
            raise ValueError("issue input path uses redirection")
        current = current.parent
    parts = relative.parts
    allowed = (
        len(parts) == 4 and parts[:3] == ("artifacts", "runtime_logs", "application")
        and target.suffix == ".json"
    ) or (
        len(parts) == 6 and parts[:4] == ("artifacts", "runtime_logs", "data_updates", "events")
        and target.suffix == ".json"
    ) or relative.as_posix() == "artifacts/daily_health/universe_data_v2_20260819.json" or (
        len(parts) == 5 and parts[:4] == ("data", "state", "provider_scheduler", "kr_market_daily_occurrences")
        and target.suffix == ".json"
    )
    if not allowed or not target.is_file():
        raise ValueError("issue input path is not allowlisted")
    return target


def _utc(value: str, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_date_at_end(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("source date differs")
    return datetime.combine(date.fromisoformat(value), time.max, timezone.utc).isoformat().replace("+00:00", "Z")


def _event_id(prefix: str, *parts: object) -> str:
    body = "\0".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(body).hexdigest()}"


def adapt_runtime_diagnostic(payload: object, *, evidence: str) -> tuple[IssueEvent, ...]:
    event = RuntimeDiagnosticEvent.from_dict(payload)
    if event.kind == "LIFECYCLE":
        return ()
    target_id = f"{event.domain.lower()}:{event.stage.lower()}"
    if not _SAFE.fullmatch(target_id):
        raise ValueError("runtime diagnostic target is unsafe")
    return (IssueEvent(
        source_schema="runtime-diagnostic/v1", source_event_id=event.event_id,
        occurred_at=event.occurred_at, stable_code=event.code, domain=event.domain,
        target_kind="COMPONENT", target_id=target_id, outcome="FAILURE",
        severity="ERROR", retryability="NOT_RETRYABLE", evidence=(evidence,),
    ),)


def adapt_update_event(payload: object, *, evidence: str) -> tuple[IssueEvent, ...]:
    if type(payload) is not dict:
        raise ValueError("update event must be an object")
    event = UpdateEvent.from_dict(payload)
    if event.state is EventState.STARTED or event.state is EventState.EXPECTED_DELAY:
        return ()
    if event.state not in FAILURE_STATES | SUCCESS_STATES:
        raise ValueError("update event terminal state differs")
    freshness = event.freshness_result.value
    failure_codes = tuple(state for state in FAILURE_STATES)
    projected_states = (event.state,) if event.state in FAILURE_STATES else failure_codes
    result: list[IssueEvent] = []
    for failure_state in sorted(projected_states, key=lambda item: item.value):
        retryability = (
            "AUTHORIZED_OPERATION_REQUIRED"
            if failure_state in {EventState.PROVIDER_NETWORK_FAILURE, EventState.AUTH_PERMISSION_FAILURE}
            else "SAFE_LOCAL_RETRY"
            if failure_state is EventState.LOCAL_IO_FAILURE else "NOT_RETRYABLE"
        )
        result.append(IssueEvent(
            source_schema="data-update-event/v1",
            source_event_id=_event_id("update", event.event_id.lower(), failure_state.value),
            occurred_at=event.event_at_utc.isoformat(),
            stable_code={
                EventState.PARTIAL_INELIGIBLE: "PARTIAL_SCOPE_INELIGIBLE",
                EventState.VALIDATION_FAILURE: "VALIDATION_REJECTED",
                EventState.PROVIDER_NETWORK_FAILURE: "PROVIDER_OR_NETWORK_ERROR",
                EventState.AUTH_PERMISSION_FAILURE: "AUTHENTICATION_OR_PERMISSION_DENIED",
                EventState.LOCAL_IO_FAILURE: "LOCAL_READ_WRITE_ERROR",
            }[failure_state],
            domain="DATA", target_kind="DATASET", target_id=event.logical_dataset.lower(),
            outcome="FAILURE" if event.state in FAILURE_STATES else "SUCCESS",
            severity=_FAILURE_SEVERITY.get(failure_state, "INFO") if event.state in FAILURE_STATES else "INFO",
            retryability=retryability, freshness=freshness,
            source_as_of=_source_date_at_end(event.resulting_source_date),
            expected_by=_source_date_at_end(event.expected_date), evidence=(evidence,),
        ))
    return tuple(result)


def adapt_health_v2(payload: object, *, evidence: str) -> tuple[IssueEvent, ...]:
    if (
        type(payload) is not dict or set(payload) != _HEALTH_ENVELOPE_FIELDS
        or payload.get("schema_version") != 2
    ):
        raise ValueError("Health V2 envelope differs")
    rows = payload.get("datasets")
    generated = payload.get("generated_at")
    if type(rows) is not list or not rows or type(generated) is not str:
        raise ValueError("Health V2 fields differ")
    from stock_data.orchestration.daily_operations import DATASET_UNIVERSE
    if (
        type(payload["dataset_count"]) is not int
        or payload["dataset_count"] != len(rows) or len(rows) != len(DATASET_UNIVERSE)
        or type(payload["automation_enabled_count"]) is not int
        or type(payload["runtime_coverage_validated_count"]) is not int
        or type(payload["runtime_coverage_failure_count"]) is not int
        or type(payload["runtime_coverage_failures"]) is not dict
        or type(payload["core_operation_missing"]) is not list
        or type(payload["dimension_summary"]) is not dict
    ):
        raise ValueError("Health V2 coverage differs")
    occurred = _utc(generated, "generated_at")
    result: list[IssueEvent] = []
    seen: set[str] = set()
    for row in rows:
        if type(row) is not dict or frozenset(row) not in {
            frozenset(_HEALTH_ROW_FIELDS),
            frozenset(_HEALTH_ROW_FIELDS | _HEALTH_INTRADAY_FIELDS),
        }:
            raise ValueError("Health V2 row differs")
        dataset = row.get("dataset")
        freshness = row.get("freshness")
        operational = row.get("operational")
        if (
            type(dataset) is not str or dataset not in DATASET_UNIVERSE
            or not _SAFE.fullmatch(dataset.lower()) or dataset in seen
        ):
            raise ValueError("Health V2 dataset identity differs")
        if freshness not in {"CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN", "NOT_APPLICABLE", "BLOCKED"}:
            raise ValueError("Health V2 freshness differs")
        if type(operational) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", operational):
            raise ValueError("Health V2 operational state differs")
        if (
            type(row["automation_enabled"]) is not bool
            or row["automation_enabled"] != DATASET_UNIVERSE[dataset].automation_enabled
            or row["blocker"] is not None and row["blocker"] not in {"PERMISSION", "SEMANTICS"}
            or row["runtime_coverage"] not in {"VALIDATED", "NOT_PROBED"}
        ):
            raise ValueError("Health V2 registered row coverage differs")
        registered = DATASET_UNIVERSE[dataset]
        for field in (
            "display_consumer_eligibility", "display_consumer_reason",
            "research_consumer_eligibility", "research_consumer_reason",
            "predictive_consumer_eligibility", "predictive_consumer_reason",
        ):
            value = row[field]
            if type(value) is not str or value != getattr(registered, field).value:
                raise ValueError("Health V2 consumer eligibility differs")
        seen.add(dataset)
        source_as_of = _source_date_at_end(row.get("latest"))
        expected_by = _source_date_at_end(row.get("expected"))
        conditions = (
            ("HEALTH_STALE", freshness == "STALE", "ERROR"),
            ("HEALTH_UNKNOWN", freshness in {"UNKNOWN", "BLOCKED"}, "WARNING"),
            ("HEALTH_OPERATIONAL_BLOCKED", operational == "BLOCKED", "ERROR"),
        )
        for code, failed, severity in conditions:
            result.append(IssueEvent(
                source_schema="health-artifact/v2",
                source_event_id=_event_id("health", generated, dataset, code),
                occurred_at=occurred, stable_code=code, domain="DATA",
                target_kind="DATASET", target_id=dataset.lower(),
                outcome="FAILURE" if failed else "SUCCESS",
                severity=severity if failed else "INFO", retryability="NOT_RETRYABLE",
                freshness=freshness, source_as_of=source_as_of,
                expected_by=expected_by, evidence=(evidence,),
            ))
    if seen != set(DATASET_UNIVERSE):
        raise ValueError("Health V2 registered universe differs")
    if payload["automation_enabled_count"] != sum(bool(row["automation_enabled"]) for row in rows):
        raise ValueError("Health V2 automation count differs")
    validated = sum(row["runtime_coverage"] == "VALIDATED" for row in rows)
    if payload["runtime_coverage_validated_count"] != validated:
        raise ValueError("Health V2 runtime coverage count differs")
    if payload["runtime_coverage_failure_count"] != len(payload["runtime_coverage_failures"]):
        raise ValueError("Health V2 runtime coverage failures differ")
    return tuple(result)


def adapt_scheduler_occurrence(payload: object, *, evidence: str) -> tuple[IssueEvent, ...]:
    if type(payload) is not dict or payload.get("schema_version") != 1:
        raise ValueError("scheduler occurrence envelope differs")
    status = payload.get("occurrence_status")
    if status is None and payload.get("status") == "CLAIMED_BEFORE_LANES":
        return ()
    if status not in {"TERMINAL_SUCCESS", "TERMINAL_FAILURE"}:
        raise ValueError("scheduler occurrence terminal state differs")
    bundle, slot, scheduled = payload.get("bundle"), payload.get("scheduled_slot"), payload.get("scheduled_for")
    if any(type(value) is not str for value in (bundle, slot, scheduled)):
        raise ValueError("scheduler occurrence identity differs")
    occurred = _utc(payload.get("finished_at_utc"), "finished_at_utc")
    _utc(scheduled, "scheduled_for")
    _utc(payload.get("started_at_utc"), "started_at_utc")
    lanes = payload.get("eligible_lanes")
    outcomes = payload.get("outcomes")
    api_calls = payload.get("api_calls")
    health = payload.get("health_projection")
    health_pass = (
        type(health) is dict
        and set(health) == {
            "status", "dataset_count", "runtime_coverage_validated_count",
            "runtime_coverage_failure_count",
        }
        and health.get("status") == "PASS"
        and type(health.get("dataset_count")) is int and health["dataset_count"] > 0
        and type(health.get("runtime_coverage_validated_count")) is int
        and type(health.get("runtime_coverage_failure_count")) is int
    )
    health_fail = (
        type(health) is dict
        and set(health) == {"status", "error_type"}
        and health.get("status") == "FAIL"
        and type(health.get("error_type")) is str
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", health["error_type"])
    )
    if (
        type(lanes) is not list or not lanes or len(set(lanes)) != len(lanes)
        or any(type(lane) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", lane) for lane in lanes)
        or type(outcomes) is not list or len(outcomes) != len(lanes)
        or type(api_calls) is not int or api_calls < 0
        or not (health_pass or health_fail)
    ):
        raise ValueError("scheduler occurrence coverage differs")
    outcome_calls = 0
    outcome_lanes: list[str] = []
    for row in outcomes:
        if (
            type(row) is not dict
            or not {"lane", "status", "advancement_status", "api_calls", "scheduled_slot", "scheduled_for", "started_at_utc"} <= set(row)
            or type(row["status"]) is not str
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", row["status"])
            or row["advancement_status"] not in {"UPDATED", "NOOP_CURRENT", "UNKNOWN", "FAILED"}
            or row["scheduled_slot"] != slot or row["scheduled_for"] != scheduled
        ):
            raise ValueError("scheduler lane outcome differs")
        lane_failed = row["status"].startswith(("FAIL", "DEGRADED"))
        if lane_failed and row["advancement_status"] not in {"UNKNOWN", "FAILED"}:
            raise ValueError("scheduler failed lane advancement differs")
        if not lane_failed and row["advancement_status"] == "FAILED":
            raise ValueError("scheduler successful lane advancement differs")
        lane_result = row.get("result")
        if lane_result is None:
            if (
                set(row) != {
                    "lane", "status", "advancement_status", "api_calls", "error_type",
                    "scheduled_slot", "scheduled_for", "started_at_utc",
                }
                or row["status"] != "FAIL"
                or row["advancement_status"] != "UNKNOWN"
                or row["api_calls"] is not None
                or type(row["error_type"]) is not str
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", row["error_type"])
            ):
                raise ValueError("scheduler direct lane failure differs")
            lane_calls = 0
        else:
            if (
                type(lane_result) is not dict
                or type(row["api_calls"]) is not int or row["api_calls"] < 0
                or lane_result.get("scheduler_process_status") not in {
                    "SUCCESS", "FAIL", "FAIL_AFTER_HEALTH",
                }
                or lane_result.get("health_projection") != health
            ):
                raise ValueError("scheduler lane result differs")
            expected_lane_process = (
                "FAIL_AFTER_HEALTH" if health_fail
                else "FAIL" if lane_failed
                else "SUCCESS"
            )
            if lane_result["scheduler_process_status"] != expected_lane_process:
                raise ValueError("scheduler lane process status contradicts outcome")
            lane_calls = row["api_calls"]
        _utc(row["started_at_utc"], "lane started_at_utc")
        outcome_lanes.append(row["lane"])
        outcome_calls += lane_calls
    if outcome_lanes != lanes or outcome_calls != api_calls:
        raise ValueError("scheduler lane totals differ")
    success = (
        payload.get("status") == "PASS" and payload.get("scheduler_process_status") == "SUCCESS"
        and payload.get("terminal_exit_code") == 0 and health_pass
        and health["runtime_coverage_failure_count"] == 0
        and all(not row["status"].startswith(("FAIL", "DEGRADED")) for row in outcomes)
    )
    failure = (
        payload.get("status") == "DEGRADED"
        and payload.get("scheduler_process_status") == "FAIL_AFTER_INDEPENDENT_LANES"
        and payload.get("terminal_exit_code") == 1
        and (
            health_fail
            or any(row["status"].startswith(("FAIL", "DEGRADED")) for row in outcomes)
        )
    )
    if (status == "TERMINAL_SUCCESS" and not success) or (
        status == "TERMINAL_FAILURE" and not failure
    ):
        raise ValueError("scheduler terminal outcome differs")
    evidence_path = evidence.split("@sha256:", 1)[0]
    if payload.get("occurrence_receipt") != evidence_path:
        raise ValueError("scheduler occurrence readback identity differs")
    target = f"{bundle.lower()}:{slot.replace(':', '')}"
    if not _SAFE.fullmatch(target):
        raise ValueError("scheduler occurrence target differs")
    return (IssueEvent(
        source_schema="scheduler-occurrence/v1",
        source_event_id=_event_id("occurrence", scheduled, bundle, slot),
        occurred_at=occurred, stable_code="SCHEDULER_OCCURRENCE_FAILURE",
        domain="DATA", target_kind="SCHEDULER_LANE", target_id=target,
        outcome="SUCCESS" if status == "TERMINAL_SUCCESS" else "FAILURE",
        severity="ERROR" if status == "TERMINAL_FAILURE" else "INFO",
        retryability="AUTHORIZED_OPERATION_REQUIRED", evidence=(evidence,),
    ),)


def adapt_file(project_root: Path, path: Path) -> tuple[IssueEvent, ...]:
    target = _validate_input_path(project_root, path)
    evidence = evidence_identity(project_root, target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if type(payload) is not dict:
        raise ValueError("source evidence must be an object")
    schema = payload.get("schema", payload.get("schema_version"))
    if schema == "runtime-diagnostic/v1":
        return adapt_runtime_diagnostic(payload, evidence=evidence)
    if schema == "data-update-event/v1":
        return adapt_update_event(payload, evidence=evidence)
    if schema == 2 and "datasets" in payload:
        return adapt_health_v2(payload, evidence=evidence)
    if schema == 1 and ("occurrence_status" in payload or payload.get("status") == "CLAIMED_BEFORE_LANES"):
        return adapt_scheduler_occurrence(payload, evidence=evidence)
    raise ValueError("source evidence schema is not allowlisted")


def adapt_files(project_root: Path, paths: Iterable[Path]) -> tuple[IssueEvent, ...]:
    events: list[IssueEvent] = []
    for path in sorted({Path(item) for item in paths}, key=lambda item: item.as_posix()):
        events.extend(adapt_file(project_root, path))
    return tuple(events)
