from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.daily_operations import DATASET_OPERATIONS, DATASET_UNIVERSE
from stock_data.orchestration.dataset_universe import classify_health_display
from stock_data.orchestration.expected_latest import resolve_expected_latest
from stock_data.orchestration.runtime_coverage import validated_runtime_coverage


LATEST_UNIVERSE_FILENAME = "universe_data_v2_latest.json"


def _freshness(row: dict[str, object]) -> str:
    if row.get("freshness_status") == "EXPECTED_LAG":
        return "EXPECTED_LAG"
    actual = row.get("actual_latest")
    expected = row.get("expected_latest")
    if not isinstance(actual, str) or not isinstance(expected, str):
        return "UNKNOWN"
    if actual == expected:
        return "CURRENT"
    return "STALE" if actual < expected else "UNKNOWN"


def reconcile(
    payload: dict[str, object], *, run_id: str, as_of: str,
    overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    source_rows = payload.get("datasets")
    if not isinstance(source_rows, list):
        raise ValueError("health artifact requires datasets")
    rows: list[dict[str, object]] = []
    for source in source_rows:
        if not isinstance(source, dict):
            raise ValueError("health row must be an object")
        dataset_id = source.get("dataset_id")
        if not isinstance(dataset_id, str) or dataset_id not in DATASET_OPERATIONS:
            raise ValueError(f"unknown dataset_id: {dataset_id!r}")
        spec = DATASET_OPERATIONS[dataset_id]
        consumer = DATASET_UNIVERSE[dataset_id]
        effective = {**source, **(overrides or {}).get(dataset_id, {})}
        row = dict(effective)
        row.update({
            "run_id": run_id,
            "freshness_status": _freshness(effective),
            "freshness_classification": _freshness(effective),
            "finality_classification": str(effective.get("finality_classification") or "UNKNOWN"),
            "operational_classification": spec.operational_classification.value,
            "predictive_classification": spec.predictive_classification.value,
            "display_consumer_eligibility": consumer.display_consumer_eligibility.value,
            "display_consumer_reason": consumer.display_consumer_reason.value,
            "research_consumer_eligibility": consumer.research_consumer_eligibility.value,
            "research_consumer_reason": consumer.research_consumer_reason.value,
            "predictive_consumer_eligibility": consumer.predictive_consumer_eligibility.value,
            "predictive_consumer_reason": consumer.predictive_consumer_reason.value,
        })
        rows.append(row)
    rows.sort(key=lambda row: str(row["dataset_id"]))
    if len(rows) != len(DATASET_OPERATIONS) or len({row["dataset_id"] for row in rows}) != len(rows):
        raise ValueError(
            f"health rows must match the {len(DATASET_OPERATIONS)}-entry operations registry"
        )
    freshness = Counter(str(row["freshness_classification"]) for row in rows)
    finality = Counter(str(row["finality_classification"]) for row in rows)
    operational = Counter(str(row["operational_classification"]) for row in rows)
    predictive = Counter(str(row["predictive_classification"]) for row in rows)
    output = dict(payload)
    output.update({
        "run_id": run_id,
        "as_of": as_of,
        "datasets": rows,
        "current_count": freshness["CURRENT"],
        "expected_lag_count": freshness["EXPECTED_LAG"],
        "stale_count": freshness["STALE"],
        "freshness_unknown_count": freshness["UNKNOWN"],
        "finality_confirmed_count": finality["CONFIRMED"],
        "finality_manual_confirmed_count": finality["MANUAL_CONFIRMED"],
        "finality_as_retrieved_count": finality["AS_RETRIEVED"],
        "finality_unknown_count": finality["UNKNOWN"],
        "operational_eligible_count": operational["ELIGIBLE"],
        "operational_manual_only_count": operational["MANUAL_ONLY"],
        "operational_blocked_count": operational["BLOCKED"],
        "predictive_eligible_count": predictive["ELIGIBLE"],
        "predictive_blocked_count": predictive["BLOCKED"],
        "research_only_count": predictive["RESEARCH_ONLY"],
        "dimension_summary": {
            "freshness": dict(sorted(freshness.items())),
            "finality": dict(sorted(finality.items())),
            "operational": dict(sorted(operational.items())),
            "predictive": dict(sorted(predictive.items())),
        },
    })
    return output


def reconcile_universe(
    core_report: dict[str, object], *, as_of_override: str | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Project the reviewed core health facts onto all typed universe rows."""
    core_rows = core_report.get("datasets")
    if not isinstance(core_rows, list):
        raise ValueError("core health report requires datasets")
    core_by_id = {}
    for value in core_rows:
        if not isinstance(value, dict) or not isinstance(value.get("dataset_id"), str):
            raise ValueError("core health row identity is invalid")
        if value["dataset_id"] in core_by_id:
            raise ValueError("core health rows contain duplicates")
        core_by_id[value["dataset_id"]] = value
    unknown_core_ids = set(core_by_id) - set(DATASET_OPERATIONS)
    if unknown_core_ids:
        raise ValueError(
            f"core health rows are outside the operations registry: {sorted(unknown_core_ids)}"
        )
    missing_core_ids = sorted(set(DATASET_OPERATIONS) - set(core_by_id))
    as_of_value = as_of_override or core_report.get("as_of")
    if not isinstance(as_of_value, str):
        raise ValueError("core health report requires timezone-aware as_of")
    as_of = datetime.fromisoformat(as_of_value)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("core health report as_of must be timezone-aware")
    runtime = (
        validated_runtime_coverage(project_root)
        if project_root is not None else None
    )
    runtime_latest = runtime.latest if runtime is not None else {}
    runtime_failures = runtime.failures if runtime is not None else {}
    rows = []
    for dataset_id, spec in DATASET_UNIVERSE.items():
        core = core_by_id.get(dataset_id, {})
        core_actual = core.get("actual_latest")
        runtime_actual = runtime_latest.get(dataset_id)
        actual = (
            runtime_actual
            if isinstance(runtime_actual, str)
            else max(
                (
                    value for value in (core_actual, spec.retained_latest)
                    if isinstance(value, str)
                ),
                default=None,
            )
        )
        expected = core.get("expected_latest")
        retained_date = None
        if isinstance(actual, str):
            try:
                retained_date = datetime.fromisoformat(actual).date()
            except ValueError:
                retained_date = None
        resolved = resolve_expected_latest(
            dataset=dataset_id, lane=spec.scheduler_lane,
            retained_latest=retained_date, as_of=as_of,
        ) if spec.data_grain.value == "DAILY" else None
        if resolved is not None:
            expected = resolved.expected_available_observation.isoformat() if resolved.expected_available_observation else None
        finality = (
            resolved.finality.value
            if resolved is not None and resolved.finality.value != "UNKNOWN"
            else core.get("finality_classification")
        )
        no_freshness_required = (
            not spec.automation_enabled
            or spec.health_preservation_reason is not None
            or spec.refresh_policy.value in {
            "STATIC_COMPLETE", "MANUAL_RESEARCH", "DISABLED_PENDING_CONTRACT",
            }
            or spec.operational_status.value == "NOT_APPLICABLE"
        )
        freshness_value = (
            "NOT_APPLICABLE" if no_freshness_required
            else "UNKNOWN" if dataset_id in runtime_failures
            else resolved.freshness.value if resolved is not None
            else _freshness({"actual_latest": actual, "expected_latest": expected})
        )
        runtime_coverage = (
            "VALIDATED" if dataset_id in runtime_latest
            else f"FAILED:{runtime_failures[dataset_id]}" if dataset_id in runtime_failures
            else "NOT_PROBED"
        )
        display_status, display_reason = classify_health_display(
            spec,
            latest=actual if isinstance(actual, str) else None,
            expected=expected if isinstance(expected, str) else None,
            freshness=freshness_value,
            runtime_coverage=runtime_coverage,
            last_run=core.get("last_run"),
        )
        pending_until = resolved.pending_until if resolved is not None else None
        display_status_value = display_status.value
        if pending_until is not None and display_status_value == "LATE":
            display_status_value = "CURRENT"
            display_reason = f"수집 예정 시각 전 ({pending_until})"
        row = {
            "dataset": dataset_id,
            "role": spec.data_role.value,
            "grain": spec.data_grain.value,
            "latest": actual,
            "expected": expected,
            "freshness": freshness_value,
            "display_status": display_status_value,
            "display_reason": display_reason,
            "due_at": resolved.due_at.isoformat() if resolved is not None and resolved.due_at else None,
            "pending_until": pending_until,
            "finality": finality or "UNKNOWN",
            "operational": spec.operational_status.value,
            "pit": spec.predictive_pit_status.value,
            "display_consumer_eligibility": spec.display_consumer_eligibility.value,
            "display_consumer_reason": spec.display_consumer_reason.value,
            "research_consumer_eligibility": spec.research_consumer_eligibility.value,
            "research_consumer_reason": spec.research_consumer_reason.value,
            "predictive_consumer_eligibility": spec.predictive_consumer_eligibility.value,
            "predictive_consumer_reason": spec.predictive_consumer_reason.value,
            "refresh": spec.refresh_policy.value,
            "automation_policy": spec.automation_policy.value,
            "automation_enabled": spec.automation_enabled,
            "scheduler_lane": spec.scheduler_lane,
            "scheduler_management": spec.scheduler_management.value,
            "last_run": core.get("last_run"),
            "last_success": core.get("last_success"),
            "api_calls": int(core.get("api_calls", 0) or 0),
            "blocker": spec.operational_blocker_reason.value if spec.operational_blocker_reason else None,
            "source": spec.source,
            "gap_status": (
                "CALENDAR_RESOLVED" if resolved is not None
                else "CALENDAR_UNAVAILABLE" if spec.data_grain.value == "DAILY"
                else "NOT_APPLICABLE"
            ),
            "calendar": resolved.calendar if resolved is not None else None,
            "observation_calendar": resolved.observation_calendar.value if resolved is not None else None,
            "provider_availability_policy": resolved.provider_availability_policy.value if resolved is not None else None,
            "expected_lag_policy": resolved.expected_lag_policy.value if resolved is not None else None,
            "market_expected_latest": resolved.expected_market_date.isoformat() if resolved is not None and resolved.expected_market_date else None,
            "calendar_source": resolved.calendar_source if resolved is not None else None,
            "calendar_version": resolved.calendar_version if resolved is not None else None,
            "pre_network_noop": bool(spec.automation_enabled and freshness_value == "CURRENT"),
            "missing_dates": None,
            "runtime_coverage": runtime_coverage,
        }
        if spec.data_grain.value == "INTRADAY":
            row.update({
                "latest_complete_session": None,
                "expected_bars": None,
                "observed_bars": None,
                "missing_bars": None,
                "provider": None,
                "fallback": None,
            })
        rows.append(row)
    dimensions = {}
    for field in (
        "role", "grain", "freshness", "display_status", "finality", "refresh", "operational", "pit",
        "display_consumer_eligibility", "research_consumer_eligibility",
        "predictive_consumer_eligibility", "automation_policy", "scheduler_management",
    ):
        dimensions[field] = dict(sorted(Counter(str(row[field]) for row in rows).items()))
    actionable_incidents = sum(
        bool(row["automation_enabled"])
        and row["operational"] not in {"BLOCKED", "MANUAL_ONLY", "NOT_APPLICABLE"}
        and row["display_status"] in {"LATE", "FAILED"}
        for row in rows
    )
    return {
        "schema_version": 2,
        "run_id": core_report.get("run_id"),
        "as_of": as_of_value,
        "generated_at": as_of_value,
        "core_reference_time": core_report.get("as_of"),
        "dataset_count": len(rows),
        "core_operations_count": len(core_by_id),
        "operations_registry_count": len(DATASET_OPERATIONS),
        "core_operation_missing": missing_core_ids,
        "automation_enabled_count": sum(bool(row["automation_enabled"]) for row in rows),
        "actionable_incident_count": actionable_incidents,
        "runtime_coverage_validated_count": len(runtime_latest),
        "runtime_coverage_failure_count": len(runtime_failures),
        "runtime_coverage_failures": dict(sorted(runtime_failures.items())),
        "dimension_summary": dimensions,
        "datasets": rows,
    }


def write_universe_health_artifact(
    *,
    project_root: Path,
    core_artifact: Path,
    universe_output: Path,
    execution_log: Path | None = None,
    as_of: str | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    payload = json.loads(Path(core_artifact).read_text(encoding="utf-8"))
    universe = reconcile_universe(
        payload,
        as_of_override=as_of or datetime.now(timezone.utc).isoformat(),
        project_root=root,
    )
    latest_output = _write_universe_outputs(universe, universe_output)
    if execution_log:
        execution_log.parent.mkdir(parents=True, exist_ok=True)
        log_temporary = execution_log.with_suffix(execution_log.suffix + ".tmp")
        log_temporary.write_text(json.dumps({
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "SUCCESS", "mode": "universe-only", "api_calls": 0,
            "dataset_count": universe["dataset_count"],
            "runtime_coverage_validated_count": universe["runtime_coverage_validated_count"],
            "runtime_coverage_failure_count": universe["runtime_coverage_failure_count"],
            "source": str(core_artifact), "output": str(universe_output),
            "latest_output": str(latest_output),
        }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        log_temporary.replace(execution_log)
    return universe


def _atomic_json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_universe_outputs(
    universe: dict[str, object], universe_output: Path,
) -> Path:
    """Write the requested compatibility path and the stable latest pointer."""
    output = Path(universe_output)
    latest = output.parent / LATEST_UNIVERSE_FILENAME
    _atomic_json_write(output, universe)
    if latest != output:
        _atomic_json_write(latest, universe)
    return latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--as-of")
    parser.add_argument("--universe-output", type=Path)
    parser.add_argument("--execution-log", type=Path)
    parser.add_argument(
        "--universe-only", action="store_true",
        help="project an already reconciled core operations artifact without changing it",
    )
    parser.add_argument(
        "--override", action="append", default=[], metavar="DATASET=ACTUAL,EXPECTED,FINALITY",
    )
    args = parser.parse_args()
    payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    if args.universe_only:
        if not args.universe_output:
            raise ValueError("--universe-only requires --universe-output")
        if args.override or args.run_id:
            raise ValueError("--universe-only does not accept reconciliation overrides or run-id")
        universe = write_universe_health_artifact(
            project_root=ROOT,
            core_artifact=args.artifact,
            universe_output=args.universe_output,
            execution_log=args.execution_log,
            as_of=args.as_of,
        )
        return 0
    if not args.run_id or not args.as_of:
        raise ValueError("reconciliation requires --run-id and --as-of")
    overrides: dict[str, dict[str, object]] = {}
    for value in args.override:
        dataset_id, separator, fields = value.partition("=")
        parts = fields.split(",")
        if not separator or len(parts) not in {3, 6}:
            raise ValueError(
                "override must be DATASET=ACTUAL,EXPECTED,FINALITY"
                "[,COLLECTOR,VALIDATION,DOWNSTREAM]"
            )
        overrides[dataset_id] = {
            "actual_latest": parts[0], "expected_latest": parts[1],
            "finality_classification": parts[2],
        }
        if len(parts) == 6:
            overrides[dataset_id].update({
                "collector_status": parts[3], "validation_status": parts[4],
                "downstream_status": parts[5],
            })
    result = reconcile(
        payload, run_id=args.run_id, as_of=args.as_of, overrides=overrides,
    )
    temporary = args.artifact.with_suffix(args.artifact.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.artifact)
    if args.universe_output:
        universe = reconcile_universe(result)
        _write_universe_outputs(universe, args.universe_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
