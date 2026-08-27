from __future__ import annotations

import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

import market_backtest.phase1_replay as phase1_replay
from stock_data.gui.health_service import HealthArtifactView, HealthDatasetRow
from stock_data.orchestration import release_readiness as subject


def _row(
    *, freshness: str = "CURRENT", operational: str = "INCREMENTAL_READY",
) -> HealthDatasetRow:
    return HealthDatasetRow(
        dataset="kr_index_daily",
        role="PRIMARY", cadence="DAILY", latest="2026-08-20",
        expected="2026-08-20", freshness=freshness,
        operational=operational, blocker="N/A", pit="PIT_LIMITED",
        automation="AUTOMATED / ENABLED", source="retained local source",
        runtime_coverage="VALIDATED",
        display_consumer_eligibility="ELIGIBLE",
        display_consumer_reason="DISPLAY_DIRECT_CONTRACT",
        research_consumer_eligibility="ELIGIBLE",
        research_consumer_reason="RESEARCH_RETAINED_CONTRACT",
        predictive_consumer_eligibility="BLOCKED",
        predictive_consumer_reason="PREDICTIVE_PIT_LIMITED",
    )


def _scheduler_rows(project_root: Path | None = None) -> tuple[dict[str, object], ...]:
    policies = subject._scheduler_definition_policies(project_root or Path.cwd())
    rows = []
    for name in subject.EXPECTED_SCHEDULED_TASKS:
        policy = policies[name]
        rows.append({
            "name": name, "exists": True, "state": "Ready", "last_result": 0,
            "action_count": 1,
            "execute": policy.get("execute", policy.get("execute_basename")),
            "arguments": policy["arguments"],
            "working_directory": policy["working_directory"],
            "trigger_count": 1, "trigger_types": [policy["trigger_type"]],
            "trigger_enabled": [True],
            "start_times": [policy.get("start_time", "13:02")],
            "days_intervals": [policy.get("days_interval", 0)],
            "days_of_week_masks": [policy.get("days_of_week_mask", 0)],
            "repetition_intervals": [policy["repetition_interval"]],
            "repetition_durations": [
                policy.get(
                    "repetition_duration",
                    policy.get("repetition_duration_allowed", ("",))[0],
                )
            ],
            "start_when_available": policy["start_when_available"],
            "disallow_start_if_on_batteries": policy[
                "disallow_start_if_on_batteries"
            ],
            "stop_if_going_on_batteries": policy[
                "stop_if_going_on_batteries"
            ],
            "wake_to_run": policy["wake_to_run"],
            "multiple_instances": policy["multiple_instances"],
            "execution_time_limit": policy["execution_time_limit"],
        })
    return tuple(rows)


def _gui_result() -> dict[str, object]:
    return {
        "baseline_supported": True,
        "font_glyphs_supported": True,
        "dashboard_card_overlaps": (),
        "pages": subject.EXPECTED_GUI_PAGES,
        "page_states": {name: True for name in subject.EXPECTED_GUI_PAGES},
        "clipped_pages": (), "dashboard_loaded": True, "health_loaded": True,
        "health_row_count": 80, "health_managed_total": 20,
        "health_managed_current": 8, "health_managed_expected_lag": 12,
        "health_managed_acceptable": 20, "health_render_elapsed_ms": 2_000,
        "health_render_timeout_ms": subject.NATIVE_GUI_HEALTH_TIMEOUT_MS,
        "index_rendered": True, "market_chart_rendered": True,
        "market_chart_state": "RENDERED", "watchlist_isolated": True,
        "gui_user_data_isolation": "FULLY_ISOLATED", "backtest_runnable": True,
        "worker_states": {name: True for name in subject.EXPECTED_GUI_WORKERS},
        "workers_closed": True, "account_state": "AVAILABLE",
        "net_worth_state": "INTENTIONAL_EMPTY_OR_UNAVAILABLE", "read_files": (),
    }


def _backtest_payload() -> dict[str, object]:
    return {
        "status": "DESCRIPTIVE_SIGNAL_REPLAY_NOT_PORTFOLIO_BACKTEST",
        "frozen_manifest": {
            "dataset": "kr_kospi200_index_daily",
            "contract_version": 1,
            "coverage_start": "2020-01-02",
            "coverage_end": "2021-08-16",
            "rows": 100,
            "files": 7,
            "root_manifest_sha256": "a" * 64,
            "decision_rule": "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
        },
        "thresholds": {"minimum_conditions": 2},
        "metrics": {"observations": 90, "precision": 0.4},
        "crisis_replay": [{
            "event": "development_event",
            "start": "2020-01-02",
            "end": "2020-06-30",
            "status": "DIAGNOSTIC_ONLY",
            "observations": 50,
            "risk_off_observations": 10,
            "mean_forward_20d_return": -0.01,
            "worst_forward_20d_drawdown": -0.2,
        }],
    }


def _write_backtest_result(
    root: Path, payload: object | None = None, *, indent: int | None = None,
) -> Path:
    path = root / "artifacts/backtest/phase1_signal_replay/result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is not None:
        path.write_text(json.dumps(payload, indent=indent), encoding="utf-8")
        return path
    for name, body in _strict_backtest_bodies(root, indent=indent).items():
        (path.parent / name).write_bytes(body)
    return path


@lru_cache(maxsize=1)
def _source_strict_backtest_bodies() -> dict[str, bytes]:
    project_root = Path(__file__).resolve().parents[3]
    return dict(phase1_replay._build_replay_bundle(project_root).bodies)


def _strict_backtest_bodies(
    target_root: Path, *, indent: int | None,
) -> dict[str, bytes]:
    source = _source_strict_backtest_bodies()
    base = {
        name: source[name]
        for name in (
            "signals.csv", "result.json", "experiments.json",
            "portfolio_ledger.json",
        )
    }
    registry = json.loads(base["experiments.json"])
    project_root = Path(__file__).resolve().parents[3]
    result_path = (
        target_root / phase1_replay.DEFAULT_OUTPUT_RELATIVE / "result.json"
    ).resolve()
    try:
        result_artifact = result_path.relative_to(project_root).as_posix()
    except ValueError:
        result_artifact = result_path.as_posix()
    registry["experiments"][0]["result_artifact"] = result_artifact
    if indent is not None:
        result = json.loads(base["result.json"])
        base["result.json"] = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=indent)
            + "\n"
        ).encode("utf-8")
    registry["experiments"][0]["result_artifact_digest"] = (
        phase1_replay.artifact_bytes_digest(base["result.json"])
    )
    base["experiments.json"] = (
        json.dumps(
            registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return dict(
        phase1_replay._bind_bundle(
            base,
            frozen_input_digest=phase1_replay.EXPECTED_FROZEN_DIGEST,
        ).bodies
    )


def test_health_release_gate_fails_managed_rows_and_degrades_visible_gaps() -> None:
    expected = HealthArtifactView("READY", "fixture", (_row(freshness="EXPECTED_LAG"),))
    check, conditions = subject.assess_health(expected)
    assert check.status == "PASS"
    assert "managed_expected_provider_lag=1" in conditions

    rejected = HealthArtifactView(
        "READY", "fixture",
        (_row(freshness="STALE"), _row(freshness="UNKNOWN", operational="BLOCKED")),
    )
    check, conditions = subject.assess_health(rejected)
    assert check.status == "FAIL"
    assert "stale=1" in check.summary and "unknown=1" in check.summary
    assert "approved_deferred_or_blocked=1" in conditions

    outside = HealthArtifactView(
        "READY", "fixture",
        (_row(), HealthDatasetRow(
            **{**_row(freshness="STALE").__dict__, "automation": "MANUAL_GATE / DISABLED"}
        )),
    )
    check, conditions = subject.assess_health(outside)
    assert check.status == "DEGRADED"
    assert "outside_managed_stale_or_unknown=1" in conditions


def test_invalid_health_is_release_failure() -> None:
    check, conditions = subject.assess_health(
        HealthArtifactView("REPORT NOT AVAILABLE", "fixture", (), "invalid")
    )
    assert check.status == "FAIL" and not conditions


def test_backtest_gui_bundle_uses_service_path_and_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    real_service = subject.BacktestResultService
    project_root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(
        subject,
        "BacktestResultService",
        lambda root: real_service(
            project_root,
            output_root=Path(root) / phase1_replay.DEFAULT_OUTPUT_RELATIVE,
        ),
    )
    check, path, identity = subject.check_backtest_gui_bundle(tmp_path)
    assert path == subject.BacktestResultService(tmp_path).result_path
    assert check.status == "FAIL" and identity.file_count == 0

    _write_backtest_result(tmp_path)
    check, accepted_path, identity = subject.check_backtest_gui_bundle(tmp_path)
    assert accepted_path == path
    assert check.status == "PASS"
    assert identity.file_count == 1 and identity.total_bytes == path.stat().st_size

    rejected_payloads = []
    status_invalid = _backtest_payload()
    status_invalid["status"] = "PORTFOLIO_BACKTEST"
    rejected_payloads.append(status_invalid)
    schema_invalid = _backtest_payload()
    schema_invalid["frozen_manifest"]["contract_version"] = "1"
    rejected_payloads.append(schema_invalid)
    semantic_invalid = _backtest_payload()
    semantic_invalid["frozen_manifest"]["dataset"] = "other_dataset"
    rejected_payloads.append(semantic_invalid)
    digest_tampered = _backtest_payload()
    digest_tampered["frozen_manifest"]["root_manifest_sha256"] = "g" * 64
    rejected_payloads.append(digest_tampered)
    for payload in rejected_payloads:
        _write_backtest_result(tmp_path, payload)
        rejected, rejected_path, rejected_identity = subject.check_backtest_gui_bundle(
            tmp_path
        )
        assert rejected_path == path
        assert rejected.status == "FAIL"
        assert rejected_identity.file_count == 1

    path.write_text("{not-json", encoding="utf-8")
    corrupt, _, corrupt_identity = subject.check_backtest_gui_bundle(tmp_path)
    assert corrupt.status == "FAIL" and corrupt_identity.file_count == 1

    path.unlink()
    deleted, _, deleted_identity = subject.check_backtest_gui_bundle(tmp_path)
    assert deleted.status == "FAIL" and deleted_identity.file_count == 0


def test_health_schema_version_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "artifacts/daily_health/universe_data_v2_20260819.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"datasets":[{}]}', encoding="utf-8")
    assert subject.check_health_schema_version(tmp_path).status == "FAIL"
    path.write_text('{"schema_version":2,"datasets":[{}]}', encoding="utf-8")
    assert subject.check_health_schema_version(tmp_path).status == "FAIL"
    fields = {
        field: "ELIGIBLE" if field.endswith("eligibility") else "ELIGIBLE_BY_POLICY"
        for field in subject.HEALTH_CONSUMER_CONTRACT_FIELDS
    }
    path.write_text(json.dumps({"schema_version": 2, "datasets": [fields]}), encoding="utf-8")
    assert subject.check_health_schema_version(tmp_path).status == "PASS"


def test_local_service_requires_suppression_stability_and_a_renderable_chart() -> None:
    suppression, cache = subject.assess_local_service({
        "snapshot_stable": True, "chart_stable": True, "chart_rows": 120,
        "freshness_leaks": (), "current_unavailable": (),
    })
    assert suppression.status == "PASS" and cache.status == "PASS"

    suppression, cache = subject.assess_local_service({
        "snapshot_stable": False, "chart_stable": True, "chart_rows": 120,
        "freshness_leaks": ("KOSPI",), "current_unavailable": (),
    })
    assert suppression.status == "FAIL" and cache.status == "FAIL"


def test_scheduler_missing_disabled_or_nonzero_is_failure() -> None:
    assert subject.assess_scheduler(_scheduler_rows()).status == "PASS"
    rows = list(_scheduler_rows())
    rows[0] = {**rows[0], "exists": False, "state": "MISSING"}
    rows[1] = {**rows[1], "state": "Disabled"}
    rows[2] = {**rows[2], "last_result": 1}
    check = subject.assess_scheduler(rows)
    assert check.status == "FAIL"
    assert "missing=1" in check.summary
    assert "disabled=1" in check.summary
    assert "nonzero=1" in check.summary

    invisible = tuple(
        {"name": name, "exists": False, "state": "MISSING", "last_result": None}
        for name in subject.EXPECTED_SCHEDULED_TASKS
    )
    check = subject.assess_scheduler(invisible)
    assert check.status == "FAIL"
    assert "namespace_visible=False" in check.summary

    sandbox_invisible = tuple(
        {**row, "namespace_task_count": 0} for row in invisible
    )
    check = subject.assess_scheduler(sandbox_invisible)
    assert check.status == "DEGRADED"
    assert "namespace_probe=UNAVAILABLE" in check.summary

    wrong_definition = list(_scheduler_rows())
    wrong_definition[0] = {**wrong_definition[0], "arguments": "wrong"}
    check = subject.assess_scheduler(wrong_definition)
    assert check.status == "FAIL"
    assert "definition_mismatch=1" in check.summary


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disallow_start_if_on_batteries", True),
        ("stop_if_going_on_batteries", True),
        ("wake_to_run", False),
    ],
)
def test_scheduler_rejects_power_policy_drift(field: str, value: bool) -> None:
    rows = list(_scheduler_rows())
    rows[0] = {**rows[0], field: value}

    check = subject.assess_scheduler(rows)

    assert check.status == "FAIL"
    assert "definition_mismatch=1" in check.summary


@pytest.mark.parametrize("task_name", tuple(subject.KR_MARKET_DAILY_SLOT_TASKS))
def test_scheduler_requires_each_slot_task_and_accepts_never_run(
    task_name: str,
) -> None:
    assert "STOCK_DATA_KR_MARKET_DAILY" not in subject.EXPECTED_SCHEDULED_TASKS
    rows = [
        {**row, "last_result": None}
        if row["name"] in subject.KR_MARKET_DAILY_SLOT_TASKS else row
        for row in _scheduler_rows()
    ]
    assert subject.assess_scheduler(rows).status == "PASS"

    missing = [row for row in rows if row["name"] != task_name]
    check = subject.assess_scheduler(missing)
    assert check.status == "FAIL" and "missing=1" in check.summary

    failed = [
        {**row, "last_result": 1} if row["name"] == task_name else row
        for row in rows
    ]
    check = subject.assess_scheduler(failed)
    assert check.status == "FAIL" and "nonzero=1" in check.summary

    extra_legacy = rows + [{
        "name": "STOCK_DATA_KR_MARKET_DAILY", "exists": False,
        "state": "MISSING", "last_result": None,
    }]
    assert subject.assess_scheduler(extra_legacy).status == "PASS"


def test_scheduler_probe_reads_each_exact_task_info_and_preserves_never_run(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    payload = [
        {**row, "last_result": subject.SCHEDULER_TASK_HAS_NOT_RUN}
        for row in _scheduler_rows()
    ]

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr(subject.subprocess, "run", run)

    rows = subject.query_windows_scheduler(Path.cwd())

    script = captured["command"][-1]
    assert "Get-ScheduledTaskInfo -TaskName $name" in script
    assert "Get-ScheduledTaskInfo -TaskName 'STOCK_DATA_*'" not in script
    assert "Settings.DisallowStartIfOnBatteries" in script
    assert "Settings.StopIfGoingOnBatteries" in script
    assert "Settings.WakeToRun" in script
    assert {row["last_result"] for row in rows} == {
        subject.SCHEDULER_TASK_HAS_NOT_RUN,
    }
    assert subject.assess_scheduler(rows).status == "PASS"


@pytest.mark.parametrize("task_name", tuple(subject.KR_MARKET_DAILY_SLOT_TASKS))
def test_scheduler_accepts_each_slot_completed_result(task_name: str) -> None:
    rows = [
        {
            **row,
            "last_result": (
                0 if row["name"] == task_name else None
                if row["name"] in subject.KR_MARKET_DAILY_SLOT_TASKS
                else row["last_result"]
            ),
        }
        for row in _scheduler_rows()
    ]
    assert subject.assess_scheduler(rows).status == "PASS"


def _write_required_scheduler_results(
    root: Path, *, finished: datetime, scheduled_slot: str | None = None,
    dataset_count: int = 80, validated_count: int = 21,
    lane_contract_version: int | None = 5,
) -> dict[str, object]:
    log_root = root / "artifacts/scheduler_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    (log_root / "STOCK_DATA_YAHOO_MARKET_30M_last.json").write_text(
        json.dumps({
            "schema_version": 1,
            "status": "PASS",
            "failed": 0,
            "finished_at_utc": finished.isoformat(),
            "series_terminal_outcomes": [
                {
                    "lane": lane,
                    "series_id": series_id,
                    "outcome": (
                        "NO_NEW_30M_BAR_PRESERVED"
                        if lane == "GLOBAL_30M"
                        else "NO_NEW_15M_BAR_PRESERVED"
                    ),
                }
                for lane, series_id in subject.EXPECTED_YAHOO_TERMINAL_ROUTES
            ],
            "api_calls": len(subject.EXPECTED_YAHOO_TERMINAL_ROUTES),
            "max_api_calls": len(subject.EXPECTED_YAHOO_TERMINAL_ROUTES),
            "accepted": len(subject.EXPECTED_YAHOO_TERMINAL_ROUTES),
            "preserved": len(subject.EXPECTED_YAHOO_TERMINAL_ROUTES),
        }),
        encoding="utf-8",
    )
    slot = scheduled_slot or "20:30"
    slot_hour, slot_minute = (int(item) for item in slot.split(":"))
    local_finished = finished.astimezone(subject.KST)
    scheduled_for = local_finished.replace(
        hour=slot_hour, minute=slot_minute, second=0, microsecond=0,
    )
    if scheduled_for > local_finished:
        scheduled_for -= timedelta(days=1)
    lane_contract = (
        subject.KR_MARKET_DAILY_LEGACY_SLOT_LANES
        if lane_contract_version is None
        else subject.KR_MARKET_DAILY_V2_SLOT_LANES
        if lane_contract_version == 2
        else subject.KR_MARKET_DAILY_V3_SLOT_LANES
        if lane_contract_version == 3
        else subject.KR_MARKET_DAILY_V4_SLOT_LANES
        if lane_contract_version == 4
        else subject.KR_MARKET_DAILY_SLOT_LANES
    )
    lanes = lane_contract.get(slot, ("CANONICAL_EQUITY_DAILY",))
    health = {
        "status": "PASS", "dataset_count": dataset_count,
        "runtime_coverage_validated_count": validated_count,
        "runtime_coverage_failure_count": 0,
    }
    outcomes = []
    for lane in lanes:
        advancement = (
            "UNKNOWN"
            if lane in {"LIQUIDITY_CREDIT_OBSERVATION", "LIQUIDITY_CREDIT_DAILY"}
            else "NOOP_CURRENT"
        )
        result = {
            "status": "PASS", "api_calls": 0,
            "scheduled_slot": slot, "scheduled_for": scheduled_for.isoformat(),
            "scheduler_process_status": "SUCCESS", "health_projection": health,
        }
        outcomes.append({
            "lane": lane, "status": "PASS", "advancement_status": advancement,
            "api_calls": 0, "result": result, "scheduled_slot": slot,
            "scheduled_for": scheduled_for.isoformat(),
            "started_at_utc": finished.isoformat(),
        })
    token = scheduled_for.astimezone(subject.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_relative = (
        "data/state/provider_scheduler/kr_market_daily_occurrences/"
        f"{token}-{slot.replace(':', '')}.json"
    )
    payload = {
        "schema_version": 1, "bundle": "KR_MARKET_DAILY", "status": "PASS",
        "scheduler_process_status": "SUCCESS", "finished_at_utc": finished.isoformat(),
        "scheduled_slot": slot, "scheduled_for": scheduled_for.isoformat(),
        "started_at_utc": finished.isoformat(), "eligible_lanes": list(lanes),
        "outcomes": outcomes, "api_calls": 0, "health_projection": health,
        "occurrence_receipt": receipt_relative,
        "occurrence_status": "TERMINAL_SUCCESS", "terminal_exit_code": 0,
        "claimed_at_utc": finished.isoformat(),
    }
    if lane_contract_version is not None:
        payload["lane_contract_version"] = lane_contract_version
    encoded = json.dumps(payload, sort_keys=True)
    (log_root / "STOCK_DATA_KR_MARKET_DAILY_last.json").write_text(
        encoded, encoding="utf-8",
    )
    receipt = root / receipt_relative
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(encoded, encoding="utf-8")
    return payload


def _write_complete_release_gate_inputs(root: Path, *, clock: datetime) -> None:
    generated = clock - timedelta(minutes=1)
    governing_finished = generated - timedelta(seconds=1)
    health_path = root / "artifacts/daily_health/universe_data_v2_20260819.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_payload = {
        "schema_version": 2, "as_of": generated.isoformat(),
        "generated_at": generated.isoformat(), "dataset_count": 1,
        "automation_enabled_count": 1, "actionable_incident_count": 0,
        "runtime_coverage_validated_count": 1,
        "runtime_coverage_failure_count": 0,
        "datasets": [{
            "dataset": "kr_index_daily", "latest": "2026-08-20",
            "expected": "2026-08-20", "freshness": "CURRENT",
            "automation_enabled": True, "operational": "INCREMENTAL_READY",
            "runtime_coverage": "VALIDATED",
            "display_consumer_eligibility": "ELIGIBLE",
            "display_consumer_reason": "DISPLAY_DIRECT_CONTRACT",
            "research_consumer_eligibility": "ELIGIBLE",
            "research_consumer_reason": "RESEARCH_RETAINED_CONTRACT",
            "predictive_consumer_eligibility": "BLOCKED",
            "predictive_consumer_reason": "PREDICTIVE_PIT_LIMITED",
        }],
    }
    health_path.write_text(json.dumps(health_payload), encoding="utf-8")
    log_root = root / "artifacts/scheduler_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    health_projection = {
        "status": "PASS", "dataset_count": 1,
        "runtime_coverage_validated_count": 1,
        "runtime_coverage_failure_count": 0,
    }
    lane_files = {
        "FRED_DAILY": "STOCK_DATA_FRED_DAILY_last.json",
        "GLOBAL_ETF_DAILY": "STOCK_DATA_GLOBAL_ETF_DAILY_last.json",
        "GLOBAL_INDEX_DAILY": "STOCK_DATA_GLOBAL_INDEX_DAILY_last.json",
        "GLOBAL_COMMODITY_DAILY": "STOCK_DATA_GLOBAL_COMMODITY_DAILY_last.json",
    }
    for lane, filename in lane_files.items():
        (log_root / filename).write_text(json.dumps({
            "schema_version": 1, "status": "PASS",
            "scheduler_process_status": "SUCCESS", "lane": lane,
            "advancement_status": "NOOP_CURRENT", "api_calls": 0,
            "finished_at_utc": governing_finished.isoformat(),
            "phases": [{
                "phase": lane, "status": "NOOP_IDEMPOTENT", "http_calls": 0,
            }],
            "health_projection": health_projection,
        }), encoding="utf-8")
    (log_root / "STOCK_DATA_CANONICAL_EQUITY_DAILY_last.json").write_text(
        json.dumps({
            "schema_version": 1, "status": "PASS",
            "finished_at_utc": governing_finished.isoformat(),
            "health_projection": health_projection,
        }), encoding="utf-8",
    )
    kr_due = subject._latest_kr_occurrence(clock)
    _write_required_scheduler_results(
        root, finished=governing_finished,
        scheduled_slot=kr_due.strftime("%H:%M"), dataset_count=1,
        validated_count=1,
    )
    yahoo_path = log_root / "STOCK_DATA_YAHOO_MARKET_30M_last.json"
    yahoo = json.loads(yahoo_path.read_text(encoding="utf-8"))
    yahoo["finished_at_utc"] = clock.isoformat()
    yahoo_path.write_text(json.dumps(yahoo), encoding="utf-8")
    (log_root / "STOCK_DATA_DAILY_HEALTH_last.json").write_text(json.dumps({
        "status": "SUCCESS", "finished_at_utc": clock.isoformat(),
        "api_calls": 0, "dataset_count": 1,
        "runtime_coverage_validated_count": 1,
        "runtime_coverage_failure_count": 0, "output": str(health_path.resolve()),
    }), encoding="utf-8")
    toss_due = subject._latest_toss_occurrence(clock)
    terminal = {
        "schema_version": 1, "operation_id": "UR-246", "receipt_kind": "TERMINAL",
        "scheduled_for": toss_due.isoformat(), "classification": "ELIGIBLE",
        "terminal_status": "TERMINAL_SUCCESS", "terminal_exit_code": 0,
        "finished_at_utc": (toss_due + timedelta(seconds=5)).astimezone(
            subject.timezone.utc
        ).isoformat(),
        "outcomes": {
            slot: "COMPLETE"
            for slot in subject.EXPECTED_TOSS_ELIGIBLE_OUTCOME_SLOTS
        },
        "oauth_calls": 1, "business_calls": 4, "failure_reason": "NONE",
    }
    token = toss_due.astimezone(subject.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_relative = (
        "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
        f"{token}.json"
    )
    receipt = root / receipt_relative
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(terminal), encoding="utf-8")
    pointer = dict(terminal)
    pointer["receipt_path"] = receipt_relative
    pointer_path = root / "data/state/provider_scheduler/toss_domestic_ur246_last.json"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    account_due = subject._latest_daily_occurrence(clock, "07:00")
    normalized = root / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_bytes(b'{"schema_version":1}\n')
    account_terminal = {
        "schema_version": 1,
        "operation": "TOSS_ACCOUNT_READONLY_DAILY",
        "occurrence_date": account_due.date().isoformat(),
        "scheduled_for": account_due.isoformat(),
        "claimed_at_utc": account_due.astimezone(subject.timezone.utc).isoformat(),
        "status": "TERMINAL_SUCCESS",
        "finished_at_utc": (
            account_due + timedelta(seconds=5)
        ).astimezone(subject.timezone.utc).isoformat(),
        "outcome": "SUCCEEDED", "reason": None,
        "token_calls": 1, "account_calls": 3,
        "normalized": "data/normalized/toss_account_snapshot/latest.json",
        "normalized_sha256": subject.sha256(normalized.read_bytes()).hexdigest(),
    }
    account_receipt = (
        root / "data/state/toss_account_snapshot_occurrences"
        / f"{account_due.date().isoformat()}.json"
    )
    account_receipt.parent.mkdir(parents=True, exist_ok=True)
    account_receipt.write_text(json.dumps(account_terminal), encoding="utf-8")
    (log_root / "STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json").write_text(
        json.dumps(account_terminal), encoding="utf-8",
    )
    kb_due = subject._latest_daily_occurrence(clock, "07:10")
    kb_snapshot = root / "data/local/account_snapshots/kb_self.json"
    kb_snapshot.parent.mkdir(parents=True, exist_ok=True)
    kb_snapshot.write_bytes(b'{"schema_version":1,"provider":"kbsec_open_api"}\n')
    kb_terminal = {
        "schema_version": 1,
        "operation": "KBSEC_ACCOUNT_READONLY_DAILY",
        "occurrence_date": kb_due.date().isoformat(),
        "scheduled_for": kb_due.isoformat(),
        "claimed_at_utc": kb_due.astimezone(subject.timezone.utc).isoformat(),
        "status": "TERMINAL_SUCCESS",
        "finished_at_utc": (
            kb_due + timedelta(seconds=5)
        ).astimezone(subject.timezone.utc).isoformat(),
        "outcome": "SUCCEEDED", "reason": None, "supplier_calls": 1,
        "snapshot": "data/local/account_snapshots/kb_self.json",
        "snapshot_sha256": subject.sha256(kb_snapshot.read_bytes()).hexdigest(),
    }
    kb_receipt = (
        root / "data/state/kbsec_account_snapshot_occurrences"
        / f"{kb_due.date().isoformat()}.json"
    )
    kb_receipt.parent.mkdir(parents=True, exist_ok=True)
    kb_receipt.write_text(json.dumps(kb_terminal), encoding="utf-8")
    (log_root / "STOCK_DATA_KBSEC_ACCOUNT_DAILY_last.json").write_text(
        json.dumps(kb_terminal), encoding="utf-8",
    )


def test_scheduler_results_require_fresh_well_formed_success(tmp_path: Path) -> None:
    clock = datetime(2026, 8, 24, 0, 30, tzinfo=subject.KST)
    assert subject.assess_scheduler_results(tmp_path, now=clock).status == "FAIL"
    assert "missing=2" in subject.assess_scheduler_results(tmp_path, now=clock).summary

    _write_required_scheduler_results(tmp_path, finished=clock - timedelta(minutes=30))
    assert subject.assess_scheduler_results(tmp_path, now=clock).status == "PASS"

    for slot in subject.KR_MARKET_DAILY_SLOT_TASKS.values():
        _write_required_scheduler_results(
            tmp_path, finished=clock - timedelta(minutes=30),
            scheduled_slot=slot,
        )
        assert subject.assess_scheduler_results(tmp_path, now=clock).status == "PASS"

    _write_required_scheduler_results(
        tmp_path, finished=clock - timedelta(minutes=30),
        scheduled_slot="10:00",
    )
    check = subject.assess_scheduler_results(tmp_path, now=clock)
    assert check.status == "FAIL" and "failed=1" in check.summary
    _write_required_scheduler_results(
        tmp_path, finished=clock - timedelta(minutes=30),
        scheduled_slot="20:30",
    )

    yahoo = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json"
    payload = json.loads(yahoo.read_text(encoding="utf-8"))
    payload.update(status="PARTIAL_FAILURE", failed=1)
    yahoo.write_text(json.dumps(payload), encoding="utf-8")
    check = subject.assess_scheduler_results(tmp_path, now=clock)
    assert check.status == "FAIL" and "failed=1" in check.summary

    yahoo.write_text("{not-json", encoding="utf-8")
    check = subject.assess_scheduler_results(tmp_path, now=clock)
    assert check.status == "FAIL" and "malformed=1" in check.summary

    _write_required_scheduler_results(tmp_path, finished=clock - timedelta(days=3))
    check = subject.assess_scheduler_results(tmp_path, now=clock)
    assert check.status == "FAIL" and "stale=2" in check.summary


def test_kr_scheduler_lane_contract_has_bounded_legacy_cutover(
    tmp_path: Path,
) -> None:
    assert "DERIVATIVES_PRICE_DAILY" in subject.KR_MARKET_DAILY_SLOT_LANES["20:30"]
    assert subject.KR_MARKET_DAILY_SLOT_LANES["20:30"][:2] == (
        "CANONICAL_EQUITY_DAILY", "KOSPI200_BREADTH_DAILY",
    )

    cutover_clock = datetime(2026, 8, 26, 22, 0, tzinfo=subject.KST)
    legacy = _write_required_scheduler_results(
        tmp_path,
        finished=cutover_clock - timedelta(minutes=10),
        scheduled_slot="20:30",
        lane_contract_version=None,
    )
    assert legacy["eligible_lanes"] == list(
        subject.KR_MARKET_DAILY_LEGACY_SLOT_LANES["20:30"]
    )
    assert subject.assess_scheduler_results(tmp_path, now=cutover_clock).status == "PASS"

    v2 = _write_required_scheduler_results(
        tmp_path,
        finished=cutover_clock - timedelta(minutes=10),
        scheduled_slot="20:30",
        lane_contract_version=2,
    )
    assert v2["eligible_lanes"] == list(
        subject.KR_MARKET_DAILY_V2_SLOT_LANES["20:30"]
    )
    assert subject.assess_scheduler_results(tmp_path, now=cutover_clock).status == "PASS"

    post_cutover_clock = datetime(2026, 8, 27, 22, 0, tzinfo=subject.KST)
    _write_required_scheduler_results(
        tmp_path,
        finished=post_cutover_clock - timedelta(minutes=10),
        scheduled_slot="20:30",
        lane_contract_version=None,
    )
    check = subject.assess_scheduler_results(tmp_path, now=post_cutover_clock)
    assert check.status == "FAIL" and "failed=1" in check.summary

    current = _write_required_scheduler_results(
        tmp_path,
        finished=post_cutover_clock - timedelta(minutes=10),
        scheduled_slot="20:30",
    )
    assert current["lane_contract_version"] == 5
    assert subject.assess_scheduler_results(
        tmp_path, now=post_cutover_clock,
    ).status == "PASS"


def test_release_gate_rejects_forged_pass_for_yahoo_null_bar_prior_preservation(
    tmp_path: Path,
) -> None:
    clock = datetime(2026, 8, 24, 0, 30, tzinfo=subject.KST)
    _write_required_scheduler_results(tmp_path, finished=clock - timedelta(minutes=1))
    yahoo = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json"
    payload = json.loads(yahoo.read_text(encoding="utf-8"))
    futures = {
        "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M",
        "NQ_FUTURES_CURRENT_60M", "GOLD_CURRENT_60M", "WTI_CURRENT_60M",
    }
    for row in payload["series_terminal_outcomes"]:
        if row["series_id"] in futures:
            row["outcome"] = (
                "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE_PRIOR_VALUE_PRESERVED"
            )
    yahoo.write_text(json.dumps(payload), encoding="utf-8")

    assert subject.assess_scheduler_results(tmp_path, now=clock).status == "FAIL"


@pytest.mark.parametrize(
    "series_id",
    (
        "USD_KRW_60M", "KOSPI_CURRENT_60M", "KOSDAQ_CURRENT_60M",
        "SP500_CURRENT_60M", "NASDAQ_CURRENT_60M", "SOXX_CURRENT_60M",
        "BITCOIN_CURRENT_60M",
    ),
)
def test_release_gate_rejects_null_bar_preservation_for_every_nonfuture_30m_route(
    tmp_path: Path, series_id: str,
) -> None:
    clock = datetime(2026, 8, 24, 0, 30, tzinfo=subject.KST)
    _write_required_scheduler_results(tmp_path, finished=clock - timedelta(minutes=1))
    yahoo = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json"
    payload = json.loads(yahoo.read_text(encoding="utf-8"))
    target = next(
        row for row in payload["series_terminal_outcomes"]
        if row["series_id"] == series_id
    )
    target["outcome"] = "NULL_30M_BAR_PRIOR_VALUE_PRESERVED"
    yahoo.write_text(json.dumps(payload), encoding="utf-8")

    assert subject.assess_scheduler_results(tmp_path, now=clock).status == "FAIL"


@pytest.mark.parametrize(
    ("field", "spoof"),
    (
        ("failed", False), ("failed", 0.0),
        ("accepted", 17.0), ("api_calls", 17.0),
        ("max_api_calls", 17.0), ("preserved", 17.0),
    ),
)
def test_release_gate_rejects_bool_and_integral_float_yahoo_count_spoofs(
    tmp_path: Path, field: str, spoof: object,
) -> None:
    clock = datetime(2026, 8, 24, 0, 30, tzinfo=subject.KST)
    _write_required_scheduler_results(tmp_path, finished=clock - timedelta(minutes=1))
    yahoo = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json"
    payload = json.loads(yahoo.read_text(encoding="utf-8"))
    payload[field] = spoof
    yahoo.write_text(json.dumps(payload), encoding="utf-8")

    assert subject.assess_scheduler_results(tmp_path, now=clock).status == "FAIL"


@pytest.mark.parametrize(
    "mutation", ["managed_stale", "consumer_contract", "receipt_after_health"],
)
def test_health_consistency_binds_managed_slo_and_governing_chronology(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    health_path = tmp_path / "artifacts/daily_health/universe_data_v2_20260819.json"
    if mutation in {"managed_stale", "consumer_contract"}:
        payload = json.loads(health_path.read_text(encoding="utf-8"))
        if mutation == "managed_stale":
            payload["datasets"][0]["freshness"] = "STALE"
            payload["actionable_incident_count"] = 1
        else:
            payload["datasets"][0]["display_consumer_eligibility"] = "LIMITED"
        health_path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        receipt_path = (
            tmp_path / "artifacts/scheduler_logs/"
            "STOCK_DATA_CANONICAL_EQUITY_DAILY_last.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["finished_at_utc"] = (clock + timedelta(minutes=1)).isoformat()
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    check = subject.assess_health_consistency(
        tmp_path, HealthArtifactView("READY", "fixture", (_row(),)), now=clock,
    )
    assert check.status == "FAIL"


@pytest.mark.parametrize(
    "mutation", ["provider_phase_failure", "stale_yahoo", "toss_pointer_mismatch"],
)
def test_due_occurrence_gate_rejects_incomplete_or_unbound_results(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    if mutation == "provider_phase_failure":
        path = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_FRED_DAILY_last.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["phases"][0]["status"] = "FAIL_PROVIDER"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutation == "stale_yahoo":
        path = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["finished_at_utc"] = (clock - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path = tmp_path / "data/state/provider_scheduler/toss_domestic_ur246_last.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["business_calls"] += 1
        path.write_text(json.dumps(payload), encoding="utf-8")
    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)
    assert check.status == "FAIL"
    assert "failed=" in check.summary


@pytest.mark.parametrize(
    "mutation",
    [
        "terminal_failure", "float_account_calls", "uppercase_digest",
        "wrong_digest", "wrong_occurrence", "claimed_before_due",
        "pointer_mismatch", "duplicate_json_key",
    ],
)
def test_due_occurrence_gate_requires_exact_toss_account_success(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    due = subject._latest_daily_occurrence(clock, "07:00")
    receipt_path = (
        tmp_path / "data/state/toss_account_snapshot_occurrences"
        / f"{due.date().isoformat()}.json"
    )
    pointer_path = (
        tmp_path / "artifacts/scheduler_logs/"
        "STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    pointer = dict(receipt)
    if mutation == "terminal_failure":
        receipt.update({
            "status": "TERMINAL_FAILURE",
            "outcome": "FAILED_PRESERVED_PRIOR",
            "reason": "ACCOUNT_REFRESH_FAILED_CLOSED",
            "account_calls": 0,
            "normalized": None,
            "normalized_sha256": None,
        })
        pointer = dict(receipt)
    elif mutation == "float_account_calls":
        receipt["account_calls"] = pointer["account_calls"] = 3.0
    elif mutation == "uppercase_digest":
        digest = receipt["normalized_sha256"].upper()
        receipt["normalized_sha256"] = pointer["normalized_sha256"] = digest
    elif mutation == "wrong_digest":
        digest = "0" * 64
        receipt["normalized_sha256"] = pointer["normalized_sha256"] = digest
    elif mutation == "wrong_occurrence":
        wrong_date = (due.date() - timedelta(days=1)).isoformat()
        receipt["occurrence_date"] = pointer["occurrence_date"] = wrong_date
    elif mutation == "claimed_before_due":
        claimed = (due - timedelta(seconds=1)).astimezone(subject.timezone.utc)
        receipt["claimed_at_utc"] = pointer["claimed_at_utc"] = claimed.isoformat()
    elif mutation == "pointer_mismatch":
        pointer["finished_at_utc"] = clock.isoformat()

    receipt_encoded = json.dumps(receipt)
    if mutation == "duplicate_json_key":
        needle = '"account_calls": 3'
        receipt_encoded = receipt_encoded.replace(
            needle, f'{needle}, {needle}', 1,
        )
        pointer_encoded = receipt_encoded
    else:
        pointer_encoded = json.dumps(pointer)
    receipt_path.write_text(receipt_encoded, encoding="utf-8")
    pointer_path.write_text(pointer_encoded, encoding="utf-8")

    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)

    assert check.status == "FAIL"
    assert "due_task_groups=10 complete=9 failed=1" == check.summary


@pytest.mark.parametrize(
    "mutation",
    [
        "terminal_failure", "float_supplier_calls", "wrong_digest",
        "wrong_occurrence", "claimed_before_due", "pointer_mismatch",
        "duplicate_json_key",
    ],
)
def test_due_occurrence_gate_requires_exact_kb_account_success(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    due = subject._latest_daily_occurrence(clock, "07:10")
    receipt_path = (
        tmp_path / "data/state/kbsec_account_snapshot_occurrences"
        / f"{due.date().isoformat()}.json"
    )
    pointer_path = (
        tmp_path / "artifacts/scheduler_logs/"
        "STOCK_DATA_KBSEC_ACCOUNT_DAILY_last.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    pointer = dict(receipt)
    if mutation == "terminal_failure":
        receipt.update({
            "status": "TERMINAL_FAILURE",
            "outcome": "FAILED_PRESERVED_PRIOR",
            "reason": "KB_ACCOUNT_SUPPLIER_TIMEOUT",
            "snapshot": None, "snapshot_sha256": None,
        })
        pointer = dict(receipt)
    elif mutation == "float_supplier_calls":
        receipt["supplier_calls"] = pointer["supplier_calls"] = 1.0
    elif mutation == "wrong_digest":
        receipt["snapshot_sha256"] = pointer["snapshot_sha256"] = "0" * 64
    elif mutation == "wrong_occurrence":
        wrong_date = (due.date() - timedelta(days=1)).isoformat()
        receipt["occurrence_date"] = pointer["occurrence_date"] = wrong_date
    elif mutation == "claimed_before_due":
        claimed = (due - timedelta(seconds=1)).astimezone(subject.timezone.utc)
        receipt["claimed_at_utc"] = pointer["claimed_at_utc"] = claimed.isoformat()
    elif mutation == "pointer_mismatch":
        pointer["finished_at_utc"] = clock.isoformat()

    receipt_encoded = json.dumps(receipt)
    if mutation == "duplicate_json_key":
        needle = '"supplier_calls": 1'
        receipt_encoded = receipt_encoded.replace(
            needle, f'{needle}, {needle}', 1,
        )
        pointer_encoded = receipt_encoded
    else:
        pointer_encoded = json.dumps(pointer)
    receipt_path.write_text(receipt_encoded, encoding="utf-8")
    pointer_path.write_text(pointer_encoded, encoding="utf-8")

    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)

    assert check.status == "FAIL"
    assert check.summary == "due_task_groups=10 complete=9 failed=1"


@pytest.mark.parametrize(
    "mutation",
    [
        "yahoo_missing_route", "yahoo_duplicate_route", "yahoo_extra_route",
        "yahoo_wrong_lane", "yahoo_wrong_order", "yahoo_wrong_outcome",
        "yahoo_aggregate_mismatch", "yahoo_duplicate_json_key",
        "toss_missing_route", "toss_extra_route",
        "toss_substitute_route", "toss_ineligible_extra",
        "toss_ineligible_wrong_outcome", "toss_duplicate_json_key",
        "toss_wrong_receipt_directory", "toss_wrong_receipt_name",
        "toss_wrong_receipt_token",
    ],
)
def test_due_occurrence_gate_requires_exact_unique_yahoo_and_toss_routes(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    if mutation.startswith("yahoo_"):
        path = (
            tmp_path / "artifacts/scheduler_logs/"
            "STOCK_DATA_YAHOO_MARKET_30M_last.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        outcomes = payload["series_terminal_outcomes"]
        if mutation == "yahoo_missing_route":
            outcomes.pop()
        elif mutation == "yahoo_duplicate_route":
            outcomes[-1] = dict(outcomes[0])
        elif mutation == "yahoo_extra_route":
            outcomes.append({
                "lane": "GLOBAL_30M", "series_id": "UNCONTRACTED",
                "outcome": "NO_NEW_30M_BAR_PRESERVED",
            })
        elif mutation == "yahoo_wrong_lane":
            outcomes[0]["lane"] = "NATIVE_15M"
        elif mutation == "yahoo_wrong_order":
            outcomes[0], outcomes[1] = outcomes[1], outcomes[0]
        elif mutation == "yahoo_wrong_outcome":
            outcomes[0]["outcome"] = "UNCONTRACTED_ACCEPTED"
        else:
            payload["preserved"] -= 1
        payload["api_calls"] = len(outcomes)
        payload["max_api_calls"] = len(outcomes)
        payload["accepted"] = len(outcomes)
        if mutation != "yahoo_aggregate_mismatch":
            payload["preserved"] = len(outcomes)
        encoded = json.dumps(payload)
        if mutation == "yahoo_duplicate_json_key":
            needle = '"series_id": "USD_KRW_60M"'
            encoded = encoded.replace(needle, f'{needle}, {needle}', 1)
        path.write_text(encoded, encoding="utf-8")
    else:
        pointer_path = (
            tmp_path / "data/state/provider_scheduler/"
            "toss_domestic_ur246_last.json"
        )
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        outcomes = pointer["outcomes"]
        original_receipt_path = tmp_path / pointer["receipt_path"]
        if mutation == "toss_missing_route":
            outcomes.pop(next(iter(outcomes)))
        elif mutation == "toss_extra_route":
            outcomes["DOMESTIC_ROUTE_EXTRA"] = "COMPLETE"
        elif mutation == "toss_substitute_route":
            outcomes.pop(next(iter(outcomes)))
            outcomes["DOMESTIC_ROUTE_SUBSTITUTE"] = "COMPLETE"
        elif mutation.startswith("toss_ineligible_"):
            pointer["classification"] = "INELIGIBLE"
            pointer["oauth_calls"] = pointer["business_calls"] = 0
            pointer["outcomes"] = {
                "OPERATION": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO",
            }
            outcomes = pointer["outcomes"]
            if mutation == "toss_ineligible_extra":
                outcomes["DOMESTIC_ROUTE_1"] = "NO_REPEAT"
            else:
                outcomes["OPERATION"] = "COMPLETE"
        elif mutation == "toss_wrong_receipt_directory":
            pointer["receipt_path"] = "artifacts/not-an-occurrence-receipt.json"
        elif mutation == "toss_wrong_receipt_name":
            pointer["receipt_path"] = (
                "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
                "terminal.json"
            )
        elif mutation == "toss_wrong_receipt_token":
            scheduled = datetime.fromisoformat(pointer["scheduled_for"])
            wrong_token = (
                scheduled.astimezone(subject.timezone.utc) + timedelta(minutes=1)
            ).strftime("%Y%m%dT%H%M%SZ")
            pointer["receipt_path"] = (
                "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
                f"{wrong_token}.json"
            )
        receipt_path = tmp_path / pointer["receipt_path"]
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        terminal = dict(pointer)
        terminal.pop("receipt_path")
        receipt_encoded = json.dumps(terminal)
        pointer_encoded = json.dumps(pointer)
        if mutation == "toss_duplicate_json_key":
            needle = '"DOMESTIC_ROUTE_1": "COMPLETE"'
            duplicate = f'{needle}, {needle}'
            receipt_encoded = receipt_encoded.replace(needle, duplicate, 1)
            pointer_encoded = pointer_encoded.replace(needle, duplicate, 1)
        receipt_path.write_text(receipt_encoded, encoding="utf-8")
        pointer_path.write_text(pointer_encoded, encoding="utf-8")
        if receipt_path != original_receipt_path and original_receipt_path.exists():
            original_receipt_path.unlink()
    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)
    assert check.status == "FAIL"
    assert "complete=9 failed=1" in check.summary


@pytest.mark.parametrize(
    "mutation",
    ["wrong_directory", "wrong_name", "wrong_token"],
)
def test_toss_due_occurrence_rejects_rebound_terminal_receipt_path(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    pointer_path = (
        tmp_path
        / "data/state/provider_scheduler/toss_domestic_ur246_last.json"
    )
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    original_receipt = tmp_path / pointer["receipt_path"]
    terminal_bytes = original_receipt.read_bytes()
    token = datetime.fromisoformat(pointer["scheduled_for"]).astimezone(
        subject.timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    if mutation == "wrong_directory":
        replacement = f"artifacts/scheduler_logs/{token}.json"
    elif mutation == "wrong_name":
        replacement = (
            "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
            f"terminal-{token}.json"
        )
    else:
        replacement = (
            "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
            "19990101T000000Z.json"
        )
    rebound = tmp_path / replacement
    rebound.parent.mkdir(parents=True, exist_ok=True)
    rebound.write_bytes(terminal_bytes)
    pointer["receipt_path"] = replacement
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)

    assert check.status == "FAIL"
    assert "complete=9 failed=1" in check.summary


def test_toss_due_occurrence_accepts_newer_verified_ineligible_pointer(
    tmp_path: Path,
) -> None:
    clock = datetime(2026, 8, 26, 16, 15, tzinfo=subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    due = subject._latest_toss_occurrence(clock)
    later = due + timedelta(minutes=30)
    terminal = {
        "schema_version": 1,
        "operation_id": "UR-246",
        "receipt_kind": "TERMINAL",
        "scheduled_for": later.isoformat(),
        "classification": "INELIGIBLE",
        "terminal_status": "TERMINAL_SUCCESS",
        "terminal_exit_code": 0,
        "finished_at_utc": (later + timedelta(seconds=5)).astimezone(
            subject.timezone.utc
        ).isoformat(),
        "outcomes": {
            "OPERATION": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO",
        },
        "oauth_calls": 0,
        "business_calls": 0,
        "failure_reason": "NONE",
    }
    token = later.astimezone(subject.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    relative = (
        "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
        f"{token}.json"
    )
    receipt = tmp_path / relative
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(terminal), encoding="utf-8")
    pointer = {**terminal, "receipt_path": relative}
    pointer_path = (
        tmp_path / "data/state/provider_scheduler/toss_domestic_ur246_last.json"
    )
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)

    assert check.status == "PASS"
    assert "complete=10 failed=0" in check.summary


def test_toss_due_occurrence_rejects_newer_eligible_pointer(
    tmp_path: Path,
) -> None:
    clock = datetime(2026, 8, 26, 16, 15, tzinfo=subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    pointer_path = (
        tmp_path / "data/state/provider_scheduler/toss_domestic_ur246_last.json"
    )
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    later = subject._latest_toss_occurrence(clock) + timedelta(minutes=30)
    pointer["scheduled_for"] = later.isoformat()
    pointer["finished_at_utc"] = (later + timedelta(seconds=5)).astimezone(
        subject.timezone.utc
    ).isoformat()
    token = later.astimezone(subject.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pointer["receipt_path"] = (
        "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
        f"{token}.json"
    )
    terminal = dict(pointer)
    terminal.pop("receipt_path")
    receipt = tmp_path / pointer["receipt_path"]
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(terminal), encoding="utf-8")
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    check = subject.assess_due_scheduler_outcomes(tmp_path, now=clock)

    assert check.status == "FAIL"
    assert "complete=9 failed=1" in check.summary


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_receipt", "pointer_receipt_mismatch", "terminal_failure",
        "empty_outcomes", "eligible_lane_mismatch", "lane_api_mismatch",
        "managed_advancement_unknown", "occurrence_mismatch", "health_mismatch",
        "health_failure_count_boolean", "health_validated_count_missing",
    ],
)
def test_kr_scheduler_result_requires_exact_terminal_occurrence_evidence(
    tmp_path: Path, mutation: str,
) -> None:
    clock = datetime(2026, 8, 24, 22, 0, tzinfo=subject.KST)
    payload = _write_required_scheduler_results(
        tmp_path, finished=clock - timedelta(minutes=10), scheduled_slot="20:30",
    )
    log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    receipt = tmp_path / str(payload["occurrence_receipt"])
    if mutation == "missing_receipt":
        receipt.unlink()
    else:
        changed = json.loads(log.read_text(encoding="utf-8"))
        if mutation == "pointer_receipt_mismatch":
            changed["finished_at_utc"] = clock.isoformat()
        elif mutation == "terminal_failure":
            changed["occurrence_status"] = "TERMINAL_FAILURE"
            changed["terminal_exit_code"] = 1
        elif mutation == "empty_outcomes":
            changed["outcomes"] = []
        elif mutation == "eligible_lane_mismatch":
            changed["eligible_lanes"] = changed["eligible_lanes"][:-1]
        elif mutation == "lane_api_mismatch":
            changed["outcomes"][0]["api_calls"] = 1
        elif mutation == "managed_advancement_unknown":
            changed["outcomes"][0]["advancement_status"] = "UNKNOWN"
        elif mutation == "occurrence_mismatch":
            changed["scheduled_for"] = "2026-08-24T20:31:00+09:00"
        elif mutation == "health_mismatch":
            changed["health_projection"]["runtime_coverage_failure_count"] = 1
        elif mutation == "health_failure_count_boolean":
            changed["health_projection"]["runtime_coverage_failure_count"] = False
        else:
            changed["health_projection"].pop("runtime_coverage_validated_count")
        encoded = json.dumps(changed)
        log.write_text(encoded, encoding="utf-8")
        if mutation != "pointer_receipt_mismatch":
            receipt.write_text(encoded, encoding="utf-8")
    check = subject.assess_scheduler_results(tmp_path, now=clock)
    assert check.status == "FAIL" and "failed=1" in check.summary


def test_native_gui_requires_pages_charts_no_clipping_and_worker_cleanup() -> None:
    assert subject.EXPECTED_GUI_PAGES == (
        "Dashboard", "Index Graph", "종목 차트", "미국 ETF",
        "Research Workspace", "관심종목", "Data Status", "Account",
        "순자산", "Backtest",
    )
    result = _gui_result()
    assert subject.assess_native_gui(result).status == "PASS"
    result["clipped_pages"] = ("Dashboard",)
    assert subject.assess_native_gui(result).status == "FAIL"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("health_row_count", 0),
        ("health_managed_total", 0),
        ("health_managed_acceptable", 19),
        ("health_render_elapsed_ms", subject.NATIVE_GUI_HEALTH_TIMEOUT_MS + 1),
        ("health_render_timeout_ms", subject.NATIVE_GUI_HEALTH_TIMEOUT_MS + 1),
    ],
)
def test_native_gui_fails_closed_on_empty_unmanaged_or_late_health(
    field: str, value: int,
) -> None:
    result = _gui_result()
    result[field] = value
    check = subject.assess_native_gui(result)
    assert check.status == "FAIL"
    assert "health_contract=False" in check.summary


@pytest.mark.parametrize("page", subject.EXPECTED_GUI_PAGES)
def test_native_gui_rejects_each_missing_or_failed_registered_page(page: str) -> None:
    result = {
        "baseline_supported": True,
        "font_glyphs_supported": True,
        "dashboard_card_overlaps": (),
        "pages": subject.EXPECTED_GUI_PAGES,
        "page_states": {name: True for name in subject.EXPECTED_GUI_PAGES},
        "clipped_pages": (), "dashboard_loaded": True, "health_loaded": True,
        "index_rendered": True, "market_chart_rendered": True,
        "market_chart_state": "RENDERED", "watchlist_isolated": True,
        "gui_user_data_isolation": "FULLY_ISOLATED",
        "backtest_runnable": True,
        "worker_states": {name: True for name in subject.EXPECTED_GUI_WORKERS},
        "workers_closed": True,
    }
    result["page_states"][page] = False
    assert subject.assess_native_gui(result).status == "FAIL"
    result["page_states"][page] = True
    result["pages"] = tuple(name for name in subject.EXPECTED_GUI_PAGES if name != page)
    assert subject.assess_native_gui(result).status == "FAIL"


@pytest.mark.parametrize("worker", subject.EXPECTED_GUI_WORKERS)
def test_native_gui_rejects_each_missing_or_live_worker(worker: str) -> None:
    result = {
        "baseline_supported": True,
        "font_glyphs_supported": True,
        "dashboard_card_overlaps": (),
        "pages": subject.EXPECTED_GUI_PAGES,
        "page_states": {name: True for name in subject.EXPECTED_GUI_PAGES},
        "clipped_pages": (), "dashboard_loaded": True, "health_loaded": True,
        "index_rendered": True, "market_chart_rendered": True,
        "market_chart_state": "RENDERED", "watchlist_isolated": True,
        "gui_user_data_isolation": "FULLY_ISOLATED",
        "backtest_runnable": True,
        "worker_states": {name: True for name in subject.EXPECTED_GUI_WORKERS},
        "workers_closed": True,
    }
    result["worker_states"][worker] = False
    assert subject.assess_native_gui(result).status == "FAIL"
    result["worker_states"].pop(worker)
    assert subject.assess_native_gui(result).status == "FAIL"


def test_qwidget_horizontal_overflow_is_detected() -> None:
    class Size:
        def __init__(self, width: int):
            self._width = width

        def width(self) -> int:
            return self._width

    class Widget:
        def contentsRect(self):
            return Size(200)

        def layout(self):
            return SimpleNamespace(
                minimumSize=lambda: Size(500), sizeHint=lambda: Size(500),
            )

    class ScrollArea(Widget):
        pass

    qt_widgets = SimpleNamespace(QWidget=Widget, QScrollArea=ScrollArea)
    assert subject._page_has_horizontal_overflow(Widget(), qt_widgets) is True


def test_market_chart_smoke_accepts_only_typed_stale_intentional_unavailable() -> None:
    metric = SimpleNamespace(freshness="STALE", displays_value=False)
    dashboard = SimpleNamespace(
        _market_frame=(), _market_frame_issue=None,
        market_asset=SimpleNamespace(currentText=lambda: "KOSPI"),
        CHART_METRICS={"KOSPI": "KOSPI"}, _metrics={"KOSPI": metric},
    )

    assert subject._market_chart_smoke_state(dashboard) == "INTENTIONAL_UNAVAILABLE"
    dashboard._metrics = {}
    assert subject._market_chart_smoke_state(dashboard) == "RENDER_FAILED"
    dashboard._metrics = {"KOSPI": SimpleNamespace(freshness="STALE")}
    assert subject._market_chart_smoke_state(dashboard) == "RENDER_FAILED"
    dashboard._metrics = {"KOSPI": metric}
    dashboard._market_frame_issue = "invalid retained frame"
    assert subject._market_chart_smoke_state(dashboard) == "RENDER_FAILED"


def test_native_gui_accepts_intentional_unavailable_chart_compatibility_flag() -> None:
    result = _gui_result()
    result["market_chart_state"] = "INTENTIONAL_UNAVAILABLE"
    assert subject.assess_native_gui(result).status == "PASS"
    result["watchlist_isolated"] = False
    assert subject.assess_native_gui(result).status == "FAIL"
    result["watchlist_isolated"] = True
    result["market_chart_rendered"] = False
    result["market_chart_state"] = "RENDER_FAILED"
    assert subject.assess_native_gui(result).status == "FAIL"
    result["market_chart_rendered"] = True
    assert subject.assess_native_gui(result).status == "FAIL"
    result["market_chart_state"] = "INTENTIONAL_UNAVAILABLE"
    result["market_chart_rendered"] = False
    assert subject.assess_native_gui(result).status == "FAIL"
    result["market_chart_rendered"] = True
    result["gui_user_data_isolation"] = "UNVERIFIED"
    assert subject.assess_native_gui(result).status == "FAIL"


def test_native_gui_rejects_missing_glyphs_and_internal_card_overlap() -> None:
    result = _gui_result()
    result["font_glyphs_supported"] = False
    check = subject.assess_native_gui(result)
    assert check.status == "FAIL"
    assert "font_glyphs=False" in check.summary

    result["font_glyphs_supported"] = True
    result["dashboard_card_overlaps"] = ("KOSPI:compactValue->compactMeta",)
    check = subject.assess_native_gui(result)
    assert check.status == "FAIL"
    assert "card_overlaps=1" in check.summary


def test_native_gui_teardown_quits_only_a_locally_created_application() -> None:
    events: list[str] = []

    class Window:
        def close(self) -> None:
            events.append("window.close")

        def deleteLater(self) -> None:
            events.append("window.deleteLater")

    class App:
        def processEvents(self) -> None:
            events.append("app.processEvents")

        def closeAllWindows(self) -> None:
            events.append("app.closeAllWindows")

        def quit(self) -> None:
            events.append("app.quit")

    class CoreApplication:
        @staticmethod
        def sendPostedEvents(_receiver, event_type) -> None:
            events.append(f"sendPostedEvents:{event_type}")

    qt_core = SimpleNamespace(
        QCoreApplication=CoreApplication,
        QEvent=SimpleNamespace(DeferredDelete="DeferredDelete"),
    )
    subject._teardown_native_gui(App(), Window(), qt_core, created_app=True)
    assert events.count("window.close") == 1
    assert events.count("window.deleteLater") == 1
    assert events.count("sendPostedEvents:DeferredDelete") == 2
    assert events[-3:] == [
        "app.quit", "sendPostedEvents:DeferredDelete", "app.processEvents",
    ]

    events.clear()
    subject._teardown_native_gui(App(), Window(), qt_core, created_app=False)
    assert "app.closeAllWindows" not in events
    assert "app.quit" not in events
    assert events.count("sendPostedEvents:DeferredDelete") == 1


def test_native_gui_quiescence_drains_events_until_delayed_worker_releases() -> None:
    waits: list[int] = []

    class Window:
        thread = object()

        def _managed_worker_threads(self):
            return (self.thread,)

    window = Window()

    class App:
        def processEvents(self) -> None:
            pass

    class CoreApplication:
        @staticmethod
        def sendPostedEvents(_receiver, _event_type) -> None:
            pass

    class QTest:
        @staticmethod
        def qWait(wait_ms: int) -> None:
            waits.append(wait_ms)
            if len(waits) == 3:
                window.thread = None

    qt_core = SimpleNamespace(
        QCoreApplication=CoreApplication,
        QEvent=SimpleNamespace(DeferredDelete="DeferredDelete"),
    )
    result = subject._wait_for_managed_gui_quiescence(
        window, App(), qt_core,
        timeout_ms=500, poll_interval_ms=100, sleep_ms=QTest.qWait,
    )

    assert result == subject.NativeGuiQuiescence(
        state="QUIESCENT", polls=4, waited_ms=300, active_threads=0,
    )
    assert waits == [100, 100, 100]


def test_native_gui_quiescence_reports_exact_bounded_timeout() -> None:
    waits: list[int] = []

    class Window:
        def _managed_worker_threads(self):
            return (object(), None)

    class App:
        def processEvents(self) -> None:
            pass

    class CoreApplication:
        @staticmethod
        def sendPostedEvents(_receiver, _event_type) -> None:
            pass

    class QTest:
        @staticmethod
        def qWait(wait_ms: int) -> None:
            waits.append(wait_ms)

    qt_core = SimpleNamespace(
        QCoreApplication=CoreApplication,
        QEvent=SimpleNamespace(DeferredDelete="DeferredDelete"),
    )
    result = subject._wait_for_managed_gui_quiescence(
        Window(), App(), qt_core,
        timeout_ms=250, poll_interval_ms=100, sleep_ms=QTest.qWait,
    )

    assert result == subject.NativeGuiQuiescence(
        state="TIMEOUT", polls=4, waited_ms=250, active_threads=1,
    )
    assert waits == [100, 100, 50]


def test_native_gui_assessment_rejects_worker_quiescence_timeout() -> None:
    result = {
        "baseline_supported": True,
        "font_glyphs_supported": True,
        "dashboard_card_overlaps": (),
        "pages": subject.EXPECTED_GUI_PAGES,
        "page_states": {name: True for name in subject.EXPECTED_GUI_PAGES},
        "clipped_pages": (), "dashboard_loaded": True, "health_loaded": True,
        "index_rendered": True, "market_chart_rendered": True,
        "market_chart_state": "RENDERED", "watchlist_isolated": True,
        "gui_user_data_isolation": "FULLY_ISOLATED",
        "backtest_runnable": True,
        "worker_states": {name: True for name in subject.EXPECTED_GUI_WORKERS},
        "workers_closed": True,
        "worker_quiescence_state": "TIMEOUT",
    }

    check = subject.assess_native_gui(result)

    assert check.status == "FAIL"
    assert "worker_quiescence=TIMEOUT" in check.summary


def test_native_gui_user_data_is_staged_away_from_canonical_paths(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    isolated_root = tmp_path / "isolated"
    inputs = {
        "data/normalized/toss_account_snapshot/latest.json": b'{"toss":1}\n',
        "data/local/account_snapshots/kb_self.json": b'{"kb":1}\n',
        "data/local/account_snapshots/family_mirae_etf.json": b'{"family":1}\n',
        "data/local/net_worth_history/record-00000000-safe.json": b'{"net":1}\n',
        "artifacts/local_user/watchlists.json": b'{"watchlists":1}\n',
    }
    for relative, body in inputs.items():
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    options = subject._stage_native_gui_user_data(project_root, isolated_root)

    expected = {
        "account_snapshot_path": "data/normalized/toss_account_snapshot/latest.json",
        "kb_account_snapshot_path": "data/local/account_snapshots/kb_self.json",
        "family_account_snapshot_path": "data/local/account_snapshots/family_mirae_etf.json",
    }
    for option, relative in expected.items():
        staged = options[option]
        assert staged == isolated_root / relative
        assert staged.read_bytes() == inputs[relative]
        staged.write_bytes(b"changed only in isolation")
        assert (project_root / relative).read_bytes() == inputs[relative]

    staged_net_worth = (
        options["net_worth_history_root"] / "record-00000000-safe.json"
    )
    assert staged_net_worth.read_bytes() == inputs[
        "data/local/net_worth_history/record-00000000-safe.json"
    ]
    assert options["dashboard_preferences_path"].is_relative_to(isolated_root)
    assert options["watchlist_path"].is_relative_to(isolated_root)
    assert options["watchlist_path"].read_bytes() == inputs[
        "artifacts/local_user/watchlists.json"
    ]
    options["watchlist_path"].write_bytes(b"changed only in isolation")
    assert (project_root / "artifacts/local_user/watchlists.json").read_bytes() == inputs[
        "artifacts/local_user/watchlists.json"
    ]
    assert options["toss_runtime_enabled"] is False


def test_protected_tree_identity_detects_a_user_data_change(tmp_path: Path) -> None:
    data = tmp_path / "data/normalized"
    data.mkdir(parents=True)
    artifact = data / "retained.bin"
    artifact.write_bytes(b"before")
    before = subject.tree_metadata_identity(tmp_path)
    artifact.write_bytes(b"changed-size")
    after = subject.tree_metadata_identity(tmp_path)
    assert before != after


def test_full_report_is_secret_safe_and_passes_only_complete_release_gates(
    tmp_path: Path, monkeypatch,
) -> None:
    real_service = subject.BacktestResultService
    project_root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(
        subject,
        "BacktestResultService",
        lambda root: real_service(
            project_root,
            output_root=Path(root) / phase1_replay.DEFAULT_OUTPUT_RELATIVE,
        ),
    )
    for relative in subject.REQUIRED_DATA_ROOTS:
        (tmp_path / relative).mkdir(parents=True, exist_ok=True)
    (tmp_path / "app.py").write_text("# staged app\n", encoding="utf-8")
    health_source = tmp_path / "src/stock_data/gui"
    health_source.mkdir(parents=True)
    (health_source / "health_service.py").write_text("# staged source\n", encoding="utf-8")
    clock = datetime.now(subject.KST)
    _write_complete_release_gate_inputs(tmp_path, clock=clock)
    monkeypatch.setattr(
        subject, "DailyHealthArtifactService",
        lambda _root: SimpleNamespace(load=lambda: HealthArtifactView(
            "READY", "fixture", (_row(),),
        )),
    )
    _write_backtest_result(tmp_path)

    service = lambda _root: {
        "snapshot_stable": True, "chart_stable": True, "chart_rows": 120,
        "freshness_leaks": (), "current_unavailable": (), "read_files": (),
        "snapshot_digest": "snapshot", "chart_digest": "chart",
    }
    gui = lambda _root: _gui_result()
    report = subject.run_release_readiness(
        tmp_path, scheduler_probe=lambda: _scheduler_rows(tmp_path),
        service_runner=service, gui_runner=gui, now=clock,
    )
    body = json.dumps(report, sort_keys=True).lower()
    assert report["status"] == "PASS"
    assert report["release_blockers"] == []
    assert report["data_mutations"] == 0
    assert report["sensitive_values_in_report"] is False
    assert not any(token in body for token in (
        "client_secret", "access_token", "account_number", "authorization",
    ))
    assert report["data_identity"]["protected_before"] == report["data_identity"]["protected_after"]
    assert report["data_identity"]["exact_user_data_before"] == report["data_identity"]["exact_user_data_after"]
    assert report["gui_user_data_isolation"] == "FULLY_ISOLATED"
    assert report["user_data_change_attribution"] == "UNCHANGED"
    checks = {row["check_id"]: row for row in report["checks"]}
    assert checks["BACKTEST_GUI_BUNDLE"]["status"] == "PASS"
    first_backtest = report["data_identity"]["backtest_gui_bundle"]
    first_retained = report["data_identity"]["retained_inputs"]
    assert first_backtest["file_count"] == 1

    exact_user_path = tmp_path / "artifacts/local_user/watchlists.json"
    exact_user_path.parent.mkdir(parents=True, exist_ok=True)
    exact_user_path.write_bytes(b"before")

    def externally_drifting_gui(root):
        exact_user_path.write_bytes(b"external change")
        return gui(root)

    external_drift = subject.run_release_readiness(
        tmp_path, scheduler_probe=lambda: _scheduler_rows(tmp_path),
        service_runner=service, gui_runner=externally_drifting_gui, now=clock,
    )
    external_checks = {row["check_id"]: row for row in external_drift["checks"]}
    assert external_checks["USER_DATA_BYTE_IDENTITY"]["status"] == "FAIL"
    assert external_drift["user_data_change_attribution"] == "CONCURRENT_EXTERNAL_DRIFT"
    assert external_drift["data_mutations"] == "CONCURRENT_EXTERNAL_DRIFT"

    exact_user_path.write_bytes(b"before")

    def unverified_drifting_gui(root):
        exact_user_path.write_bytes(b"unattributed change")
        result = gui(root)
        result["gui_user_data_isolation"] = "UNVERIFIED"
        return result

    unverified_drift = subject.run_release_readiness(
        tmp_path, scheduler_probe=lambda: _scheduler_rows(tmp_path),
        service_runner=service, gui_runner=unverified_drifting_gui, now=clock,
    )
    assert unverified_drift["user_data_change_attribution"] == (
        "IN_PROCESS_MUTATION_NOT_EXCLUDED"
    )
    assert unverified_drift["data_mutations"] == "IN_PROCESS_MUTATION_NOT_EXCLUDED"

    _write_backtest_result(tmp_path, indent=4)
    changed = subject.run_release_readiness(
        tmp_path, scheduler_probe=lambda: _scheduler_rows(tmp_path),
        service_runner=service, gui_runner=gui, now=clock,
    )
    assert changed["status"] == "FAIL"
    assert "BACKTEST_GUI_BUNDLE" in changed["release_blockers"]
    assert changed["data_identity"]["backtest_gui_bundle"]["sha256"] != first_backtest["sha256"]
    assert changed["data_identity"]["retained_inputs"]["sha256"] != first_retained["sha256"]

    invalid = _backtest_payload()
    invalid["status"] = "PORTFOLIO_BACKTEST"
    _write_backtest_result(tmp_path, invalid)
    blocked = subject.run_release_readiness(
        tmp_path, scheduler_probe=lambda: _scheduler_rows(tmp_path),
        service_runner=service, gui_runner=gui, now=clock,
    )
    assert blocked["status"] == "FAIL"
    assert "BACKTEST_GUI_BUNDLE" in blocked["release_blockers"]

    _write_backtest_result(tmp_path)

    def tampering_service(root):
        _write_backtest_result(root, indent=8)
        return service(root)

    tampered = subject.run_release_readiness(
        tmp_path, scheduler_probe=lambda: _scheduler_rows(tmp_path),
        service_runner=tampering_service, gui_runner=gui, now=clock,
    )
    assert tampered["status"] == "FAIL"
    assert "BACKTEST_GUI_BUNDLE" in tampered["release_blockers"]
