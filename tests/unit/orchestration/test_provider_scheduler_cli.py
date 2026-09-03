from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from stock_data.orchestration.daily_operations import DATASET_UNIVERSE
from stock_data.providers.tossinvest import TossInvestRateLimitError


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/maintenance/run_provider_scheduler.py"
SPEC = importlib.util.spec_from_file_location("provider_scheduler_cli", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _health_pass() -> dict[str, object]:
    return {
        "status": "PASS",
        "dataset_count": 80,
        "runtime_coverage_validated_count": 21,
        "runtime_coverage_failure_count": 0,
        "unacceptable_datasets": [],
        "runtime_coverage_failed_datasets": [],
    }


def _health_degraded(dataset: str, *datasets: str) -> dict[str, object]:
    listed = sorted((dataset, *datasets))
    return {
        "status": "DEGRADED",
        "dataset_count": 80,
        "runtime_coverage_validated_count": 20,
        "runtime_coverage_failure_count": len(listed),
        "unacceptable_datasets": listed,
        "runtime_coverage_failed_datasets": listed,
    }


def test_health_projection_ignores_unmanaged_runtime_probe_failure() -> None:
    report = {
        "dataset_count": 3,
        "datasets": [
            {
                "automation_enabled": True,
                "dataset": "MANAGED_CURRENT",
                "runtime_coverage": "VALIDATED",
                "freshness": "CURRENT",
            },
            {
                "automation_enabled": True,
                "dataset": "MANAGED_EXPECTED_LAG",
                "runtime_coverage": "VALIDATED",
                "freshness": "EXPECTED_LAG",
            },
            {
                "automation_enabled": False,
                "dataset": "UNMANAGED_FAILED",
                "runtime_coverage": "FAILED:PermissionError",
            },
        ],
    }

    assert MODULE._health_projection_from_report(report) == {
        "status": "PASS",
        "dataset_count": 3,
        "runtime_coverage_validated_count": 2,
        "runtime_coverage_failure_count": 0,
        "unacceptable_datasets": [],
        "runtime_coverage_failed_datasets": [],
    }


def test_health_projection_describes_managed_runtime_probe_not_probed() -> None:
    report = {
        "dataset_count": 2,
        "datasets": [
            {
                "automation_enabled": True, "dataset": "CURRENT",
                "runtime_coverage": "VALIDATED", "freshness": "CURRENT",
            },
            {
                "automation_enabled": True, "dataset": "NOT_PROBED",
                "runtime_coverage": "NOT_PROBED", "freshness": "UNKNOWN",
            },
        ],
    }

    assert MODULE._health_projection_from_report(report) == {
        "status": "DEGRADED", "dataset_count": 2,
        "runtime_coverage_validated_count": 1,
        "runtime_coverage_failure_count": 0,
        "unacceptable_datasets": ["NOT_PROBED"],
        "runtime_coverage_failed_datasets": [],
    }


def test_health_projection_describes_managed_stale_row() -> None:
    report = {
        "dataset_count": 1,
        "datasets": [{
            "automation_enabled": True,
            "dataset": "STALE_DATASET",
            "runtime_coverage": "VALIDATED",
            "freshness": "STALE",
        }],
    }

    assert MODULE._health_projection_from_report(report) == {
        "status": "DEGRADED", "dataset_count": 1,
        "runtime_coverage_validated_count": 1,
        "runtime_coverage_failure_count": 0,
        "unacceptable_datasets": ["STALE_DATASET"],
        "runtime_coverage_failed_datasets": [],
    }


def test_health_projection_describes_managed_runtime_probe_failure() -> None:
    report = {
        "dataset_count": 2,
        "datasets": [
            {
                "automation_enabled": True, "dataset": "CURRENT",
                "runtime_coverage": "VALIDATED", "freshness": "CURRENT",
            },
            {
                "automation_enabled": True,
                "dataset": "FAILED_DATASET",
                "runtime_coverage": "FAILED:PermissionError",
                "freshness": "UNKNOWN",
            },
        ],
    }

    assert MODULE._health_projection_from_report(report) == {
        "status": "DEGRADED", "dataset_count": 2,
        "runtime_coverage_validated_count": 1,
        "runtime_coverage_failure_count": 1,
        "unacceptable_datasets": ["FAILED_DATASET"],
        "runtime_coverage_failed_datasets": ["FAILED_DATASET"],
    }


@pytest.mark.parametrize("report", [None, [], "malformed"])
def test_health_projection_rejects_malformed_report(report: object) -> None:
    with pytest.raises(
        MODULE.SchedulerHealthProjectionError,
        match="Health report is not an object",
    ):
        MODULE._health_projection_from_report(report)


def test_scheduler_log_readback_mismatch_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    original_replace = MODULE.os.replace

    def corrupt_after_replace(source: Path, target: Path) -> None:
        result = original_replace(source, target)
        Path(target).write_text("{}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(MODULE.os, "replace", corrupt_after_replace)
    with pytest.raises(OSError, match="readback differs"):
        MODULE._write_lane_log(tmp_path, "FRED_DAILY", {"status": "PASS"})


def test_terminal_occurrence_readback_mismatch_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    scheduled_for = datetime(
        2026, 8, 25, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    receipt, claimed = MODULE._claim_kr_occurrence(
        tmp_path, scheduled_slot="09:10", scheduled_for=scheduled_for,
        started_at=scheduled_for,
    )
    assert claimed is True
    assert json.loads(receipt.read_text(encoding="utf-8"))[
        "lane_contract_version"
    ] == 9
    original_replace = MODULE.os.replace

    def corrupt_after_replace(source: Path, target: Path) -> None:
        result = original_replace(source, target)
        Path(target).write_text("{}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(MODULE.os, "replace", corrupt_after_replace)
    with pytest.raises(MODULE.KrBundleOccurrenceError, match="readback differs"):
        MODULE._finalize_kr_occurrence_receipt(
            tmp_path, receipt, {
                "schema_version": 1,
                "lane_contract_version": 9,
                "bundle": "KR_MARKET_DAILY",
                "scheduled_slot": "09:10",
                "scheduled_for": scheduled_for.isoformat(), "status": "PASS",
            }, exit_code=0,
        )


def test_cli_writes_health_projection_into_success_log(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(MODULE, "run_lane", lambda *_args, **_kwargs: {"status": "NOOP"})
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path), "--lane", "FRED_DAILY",
    ])

    assert MODULE.main() == 0
    payload = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_FRED_DAILY_last.json"
    ).read_text(encoding="utf-8"))
    assert payload == {
        "advancement_status": "NOOP_CURRENT",
        "health_projection": _health_pass(),
        "scheduler_process_status": "SUCCESS",
        "status": "NOOP",
    }
    assert json.loads(capsys.readouterr().out) == payload
    events = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        MODULE.EventState.STARTED, MODULE.EventState.API_ZERO_NOOP,
    ]
    assert len({event.run_id for event in events}) == 1


def test_cli_dry_run_is_plan_validation_not_already_current(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        MODULE, "run_lane",
        lambda *_args, **_kwargs: {
            "status": "DRY_RUN_PASS", "api_calls": 0,
            "target_session": "2026-08-21",
        },
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path),
        "--lane", "CANONICAL_EQUITY_DAILY", "--dry-run",
    ])

    assert MODULE.main() == 0
    capsys.readouterr()
    events = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    terminal = events[-1]
    assert terminal.state is MODULE.EventState.SUCCEEDED
    assert terminal.reason_code is MODULE.ReasonCode.COMPLETED
    assert terminal.provider_call_count == 0
    assert terminal.promotion_result is MODULE.CommitResult.NOT_RUN
    assert terminal.checkpoint_result is MODULE.CommitResult.NOT_RUN
    assert terminal.finality_result is MODULE.FinalityResult.NOT_APPLICABLE
    assert "ALREADY_CURRENT" not in json.dumps(terminal.to_dict())


def test_cli_preserves_lane_advancement_when_health_projection_fails(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(MODULE, "run_lane", lambda *_args, **_kwargs: {
        "status": "PASS",
        "phases": [{"status": "PROMOTED", "latest_after": "2026-08-20"}],
    })

    def fail_health(_root):
        raise ValueError("sensitive health detail")

    monkeypatch.setattr(MODULE, "_refresh_health", fail_health)
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path), "--lane", "FRED_DAILY",
    ])

    assert MODULE.main() == 1
    payload = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_FRED_DAILY_last.json"
    ).read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["advancement_status"] == "UPDATED"
    assert payload["scheduler_process_status"] == "FAIL_AFTER_LANE"
    assert payload["health_projection"] == {"status": "FAIL", "error_type": "ValueError"}
    assert "sensitive" not in json.dumps(payload)
    assert json.loads(capsys.readouterr().out) == payload


def test_cli_failure_log_exposes_type_only(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("sensitive provider detail must not be serialized")

    monkeypatch.setattr(MODULE, "run_lane", fail)
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path), "--lane", "FRED_DAILY",
    ])

    assert MODULE.main() == 1
    payload = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_FRED_DAILY_last.json"
    ).read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["error_type"] == "RuntimeError"
    assert payload["health_projection"] == "NOT_RUN"
    assert "sensitive" not in json.dumps(payload)
    assert json.loads(capsys.readouterr().out) == payload


def test_kr_market_daily_bundle_selects_only_due_lanes() -> None:
    assert MODULE._kr_market_daily_lanes("09:10") == (
        "KR_INDEX_FUNDAMENTAL_DAILY",
        "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION", "SHORT_SELLING_DAILY",
        "LIQUIDITY_CREDIT_DAILY",
    )


def test_equity_fundamental_current_observation_catches_up_one_session(
    tmp_path: Path, monkeypatch,
) -> None:
    landing = tmp_path / "data/landing/kr_equity_fundamental_current_observation"
    (landing / "date=2026-08-25/bounded").mkdir(parents=True)
    monkeypatch.setattr(
        MODULE, "find_valid_equity_fundamental_observation",
        lambda _root, target: SimpleNamespace() if target == date(2026, 8, 25) else None,
    )
    captured = []

    def capture(target, **kwargs):
        captured.append((target, kwargs))
        return SimpleNamespace(
            market_date=target.isoformat(), rows=2719,
            distinct_security_codes=2719, duplicate_groups=0,
            business_calls=1, retry_count=0, predictive_use=False,
        )

    monkeypatch.setattr(MODULE, "capture_equity_fundamental_observation", capture)
    result = MODULE._run_equity_fundamental_current_observation(
        tmp_path,
        clock=datetime(2026, 8, 27, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        dry_run=False,
    )

    assert [item[0] for item in captured] == [date(2026, 8, 26)]
    assert result["status"] == "CAPTURED_CURRENT_OBSERVATION"
    assert result["latest_before"] == "2026-08-25"
    assert result["latest_after"] == "2026-08-26"
    assert result["api_calls"] == 1 and result["retry_count"] == 0
    assert result["predictive_use"] is False


def test_equity_fundamental_valid_empty_waits_for_next_natural_occurrence(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE, "capture_equity_fundamental_observation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            MODULE.EquityFundamentalObservationError(
                "fundamental valid-empty is not accepted"
            )
        ),
    )
    result = MODULE._run_equity_fundamental_current_observation(
        tmp_path,
        clock=datetime(2026, 8, 27, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        dry_run=False,
    )

    assert result["status"] == "EXPECTED_PROVIDER_LAG"
    assert result["target_session"] == "2026-08-26"
    assert result["api_calls"] == 1 and result["retry_count"] == 0
    assert result["reason"] == "VALID_EMPTY_RETRY_NEXT_NATURAL_OCCURRENCE"
    assert MODULE._advancement_status(result) == "EXPECTED_LAG"


def test_equity_fundamental_current_observation_is_api_zero_when_current(
    tmp_path: Path, monkeypatch,
) -> None:
    landing = tmp_path / "data/landing/kr_equity_fundamental_current_observation"
    (landing / "date=2026-08-26/bounded").mkdir(parents=True)
    monkeypatch.setattr(
        MODULE, "find_valid_equity_fundamental_observation",
        lambda _root, target: SimpleNamespace() if target == date(2026, 8, 26) else None,
    )
    monkeypatch.setattr(
        MODULE, "capture_equity_fundamental_observation",
        lambda *_args, **_kwargs: pytest.fail("current observation must not call provider"),
    )
    result = MODULE._run_equity_fundamental_current_observation(
        tmp_path,
        clock=datetime(2026, 8, 27, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        dry_run=False,
    )

    assert result["status"] == "NOOP_CURRENT"
    assert result["api_calls"] == 0
    assert MODULE._kr_market_daily_lanes("14:10") == (
        "CANONICAL_EQUITY_DAILY", "SHORT_SELLING_DAILY", "LENDING_DAILY",
    )
    assert MODULE._kr_market_daily_lanes("20:30") == (
        "CANONICAL_EQUITY_DAILY", "KR_EQUITY_PROVISIONAL_DAILY",
        "KR_ETF_PRICE_DAILY",
        "KOSPI200_BREADTH_DAILY",
        "SHORT_SELLING_DAILY",
        "SHORT_SELLING_BALANCE_DAILY", "SHORT_SELLING_INVESTOR_DAILY",
        "LENDING_DAILY", "VKOSPI_DAILY",
        "KR_INDEX_DAILY", "KR_FUNDAMENTALS_WEEKLY", "DERIVATIVES_PRICE_DAILY",
        "MARKET_INVESTOR_DAILY",
        "LIQUIDITY_CREDIT_DAILY", "LS_T8462_DAILY", "TOSS_KR_TREASURY_DAILY",
        "BOK_FX_DAILY",
        "RESEARCH_FORWARD_TEST_DAILY",
    )


def test_every_automation_enabled_dataset_lane_has_a_scheduler_route() -> None:
    automated = {
        spec.scheduler_lane
        for spec in DATASET_UNIVERSE.values()
        if spec.automation_enabled
    }
    scheduled = {
        lane
        for _slot, lanes in MODULE.KR_MARKET_DAILY_SLOTS
        for lane in lanes
    } | {
        "FRED_DAILY", "GLOBAL_ETF_DAILY", "GLOBAL_INDEX_DAILY",
        "GLOBAL_COMMODITY_DAILY", "BOK_TREASURY_OBSERVATION_DAILY",
        "BOK_FX_DAILY",
    }

    assert automated <= scheduled


def test_derivatives_bundle_adapter_projects_bounded_catchup(
    tmp_path: Path, monkeypatch,
) -> None:
    scheduled_for = datetime(
        2026, 8, 26, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    calls: list[dict[str, object]] = []
    last_result = SimpleNamespace(
        market_date="2026-08-24",
        stages=("source", "bridge", "basis", "pcr", "wall"),
        rows={"source_futures": 7, "source_options": 4892},
    )

    def run(root, **kwargs):
        calls.append({"root": root, **kwargs})
        return SimpleNamespace(
            status="PARTIAL_LIMIT_REACHED",
            completed_dates=("2026-08-20", "2026-08-21", "2026-08-24"),
            api_calls=6,
            retry_count=0,
            remaining_target="2026-08-25",
            last_result=last_result,
        )

    monkeypatch.setattr(MODULE, "run_derivatives_daily_catchup", run)
    result = MODULE._run_bundle_lane(
        tmp_path, "DERIVATIVES_PRICE_DAILY",
        started_at=scheduled_for, scheduled_for=scheduled_for, dry_run=False,
    )

    assert calls == [{
        "root": tmp_path,
        "now": scheduled_for,
        "max_sessions": 3,
        "max_source_calls": 6,
        "max_elapsed_seconds": 600.0,
    }]
    assert result == {
        "schema_version": 1,
        "lane": "DERIVATIVES_PRICE_DAILY",
        "status": "PARTIAL_LIMIT_REACHED",
        "target_session": "2026-08-24",
        "api_calls": 6,
        "retry_count": 0,
        "completed_dates": ["2026-08-20", "2026-08-21", "2026-08-24"],
        "remaining_target": "2026-08-25",
        "stages": ["source", "bridge", "basis", "pcr", "wall"],
        "rows": {"source_futures": 7, "source_options": 4892},
    }
    assert MODULE._advancement_status(result) == "UPDATED"


def test_derivatives_bundle_dry_run_is_provider_free(
    tmp_path: Path, monkeypatch,
) -> None:
    scheduled_for = datetime(
        2026, 8, 26, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    monkeypatch.setattr(
        MODULE, "oldest_missing_derivatives_session",
        lambda _root, *, now: datetime(2026, 8, 20).date(),
    )
    monkeypatch.setattr(
        MODULE, "latest_finalized_derivatives_session",
        lambda _root, *, now: datetime(2026, 8, 25).date(),
    )
    monkeypatch.setattr(
        MODULE, "run_derivatives_daily_catchup",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not invoke catch-up"),
    )

    result = MODULE._run_bundle_lane(
        tmp_path, "DERIVATIVES_PRICE_DAILY",
        started_at=scheduled_for, scheduled_for=scheduled_for, dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["target_session"] == "2026-08-20"
    assert result["remaining_target"] == "2026-08-20"
    assert result["api_calls"] == 0


def test_ls_t8462_bundle_adapter_is_raw_only_and_bounded(
    tmp_path: Path, monkeypatch,
) -> None:
    scheduled_for = datetime(
        2026, 8, 26, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "status": "DAILY_COLLECTION_COMPLETE",
                "run_id": "safe-run-id",
                "oauth_calls": 1,
                "data_calls": 18,
                "retry_count": 0,
            }),
        )

    monkeypatch.setattr(MODULE.subprocess, "run", run)
    result = MODULE._run_ls_t8462_daily_raw(
        tmp_path, clock=scheduled_for, dry_run=False,
    )

    assert result["status"] == "COMPLETE"
    assert result["api_calls"] == 18
    assert result["normalized_writes"] == 0
    assert "--market-date" in calls[0][0]
    assert "20260826" in calls[0][0]
    assert calls[0][1]["timeout"] == 900


def test_kr_market_daily_bundle_contains_lane_failure_and_preserves_gates(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[str] = []

    def run(_root, lane, **_kwargs):
        calls.append(lane)
        if lane == "SHORT_SELLING_DAILY":
            raise RuntimeError("sensitive provider detail")
        return {"status": "DRY_RUN_PASS", "api_calls": 0}

    monkeypatch.setattr(MODULE, "_run_bundle_lane", lambda root, lane, **kwargs: run(root, lane, **kwargs))
    payload, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path,
        scheduled_slot="20:30",
        as_of=datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc),
        dry_run=True,
        allow_latest_occurrence=True,
    )

    assert exit_code == 1
    assert payload["status"] == "DEGRADED"
    assert calls == [
        "CANONICAL_EQUITY_DAILY", "KR_EQUITY_PROVISIONAL_DAILY",
        "KR_ETF_PRICE_DAILY",
        "KOSPI200_BREADTH_DAILY",
        "SHORT_SELLING_DAILY",
        "SHORT_SELLING_BALANCE_DAILY", "SHORT_SELLING_INVESTOR_DAILY",
        "LENDING_DAILY", "VKOSPI_DAILY",
        "KR_INDEX_DAILY", "KR_FUNDAMENTALS_WEEKLY", "DERIVATIVES_PRICE_DAILY",
        "MARKET_INVESTOR_DAILY",
        "LIQUIDITY_CREDIT_DAILY", "LS_T8462_DAILY", "TOSS_KR_TREASURY_DAILY",
        "BOK_FX_DAILY",
        "RESEARCH_FORWARD_TEST_DAILY",
    ]
    assert payload["outcomes"][4] == {
        "lane": "SHORT_SELLING_DAILY",
        "status": "FAIL",
        "advancement_status": "UNKNOWN",
        "api_calls": None,
        "error_type": "RuntimeError",
        "scheduled_slot": "20:30",
        "scheduled_for": "2026-08-24T20:30:00+09:00",
        "started_at_utc": "2026-08-24T11:30:00+00:00",
    }
    assert payload["gated_lanes"] == []
    assert payload["lane_contract_version"] == 9
    assert "sensitive" not in json.dumps(payload)
    assert not (
        tmp_path / "data/state/provider_scheduler/kr_market_daily_bundle.lock"
    ).exists()


def test_kr_bundle_keeps_seventeen_good_lane_statuses_after_one_lane_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    failed_dataset = "TOSS_KR_TREASURY_DAILY"

    def run(_root, lane, **_kwargs):
        if lane == failed_dataset:
            raise TossInvestRateLimitError("rate limited")
        return {"status": "NOOP", "api_calls": 0}

    monkeypatch.setattr(MODULE, "_run_bundle_lane", run)
    monkeypatch.setattr(
        MODULE, "_refresh_health", lambda _root: _health_degraded(failed_dataset),
    )
    occurrence = datetime(
        2026, 9, 2, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )

    terminal, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=occurrence,
        dry_run=False, scheduled_occurrence=occurrence,
    )

    assert exit_code == 1
    assert terminal["status"] == "DEGRADED"
    assert terminal["scheduler_process_status"] == "FAIL_AFTER_INDEPENDENT_LANES"
    assert terminal["health_projection"] == _health_degraded(failed_dataset)
    good = [item for item in terminal["outcomes"] if item["lane"] != failed_dataset]
    failed = [item for item in terminal["outcomes"] if item["lane"] == failed_dataset]
    assert len(good) == 17 and len(failed) == 1
    assert all(item["result"]["scheduler_process_status"] == "SUCCESS" for item in good)
    failed_log = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_TOSS_KR_TREASURY_DAILY_last.json"
    ).read_text(encoding="utf-8"))
    assert failed_log["scheduler_process_status"] == "FAIL"
    assert failed_log["health_projection"] == _health_degraded(failed_dataset)


def test_kr_bundle_health_only_degradation_keeps_successful_process(
    tmp_path: Path, monkeypatch,
) -> None:
    stale_dataset = "UNRELATED_MANAGED_DATASET"
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane",
        lambda *_args, **_kwargs: {"status": "NOOP", "api_calls": 0},
    )
    monkeypatch.setattr(
        MODULE, "_refresh_health", lambda _root: {
            "status": "DEGRADED",
            "dataset_count": 80,
            "runtime_coverage_validated_count": 21,
            "runtime_coverage_failure_count": 0,
            "unacceptable_datasets": [stale_dataset],
            "runtime_coverage_failed_datasets": [],
        },
    )
    occurrence = datetime(
        2026, 9, 2, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )

    terminal, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=occurrence,
        dry_run=False, scheduled_occurrence=occurrence,
    )

    assert exit_code == 0
    assert terminal["status"] == "DEGRADED"
    assert terminal["occurrence_status"] == "TERMINAL_SUCCESS"
    assert terminal["scheduler_process_status"] == "SUCCESS"
    assert terminal["health_projection"]["unacceptable_datasets"] == [stale_dataset]
    assert all(
        item["result"]["scheduler_process_status"] == "SUCCESS"
        for item in terminal["outcomes"]
    )


def test_kr_bundle_malformed_health_report_still_fails_every_lane_after_health(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane",
        lambda *_args, **_kwargs: {"status": "NOOP", "api_calls": 0},
    )
    monkeypatch.setattr(
        MODULE, "_refresh_health",
        lambda _root: MODULE._health_projection_from_report("malformed"),
    )
    occurrence = datetime(
        2026, 9, 2, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )

    terminal, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="09:10", as_of=occurrence,
        dry_run=False, scheduled_occurrence=occurrence,
    )

    assert exit_code == 1
    assert terminal["health_projection"] == {
        "status": "FAIL", "error_type": "SchedulerHealthProjectionError",
    }
    assert terminal["scheduler_process_status"] == "FAIL_AFTER_INDEPENDENT_LANES"
    assert all(
        item["result"]["scheduler_process_status"] == "FAIL_AFTER_HEALTH"
        for item in terminal["outcomes"]
    )


@pytest.mark.parametrize(
    ("scheduled_slot", "started_at", "expected_lanes", "expected_scheduled_for"),
    [
        (
            "09:10", datetime(2026, 8, 24, 5, 30, tzinfo=timezone.utc),
            (
                "KR_INDEX_FUNDAMENTAL_DAILY",
                "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION", "SHORT_SELLING_DAILY",
                "LIQUIDITY_CREDIT_DAILY",
            ),
            "2026-08-24T09:10:00+09:00",
        ),
        (
            "14:10", datetime(2026, 8, 24, 11, 35, tzinfo=timezone.utc),
            ("CANONICAL_EQUITY_DAILY", "SHORT_SELLING_DAILY", "LENDING_DAILY"),
            "2026-08-24T14:10:00+09:00",
        ),
        (
            "20:30", datetime(2026, 8, 24, 15, 5, tzinfo=timezone.utc),
            (
                "CANONICAL_EQUITY_DAILY", "KR_EQUITY_PROVISIONAL_DAILY",
                "KR_ETF_PRICE_DAILY",
                "KOSPI200_BREADTH_DAILY",
                "SHORT_SELLING_DAILY",
                "SHORT_SELLING_BALANCE_DAILY", "SHORT_SELLING_INVESTOR_DAILY",
                "LENDING_DAILY", "VKOSPI_DAILY",
                "KR_INDEX_DAILY", "KR_FUNDAMENTALS_WEEKLY", "DERIVATIVES_PRICE_DAILY",
                "MARKET_INVESTOR_DAILY",
                "LIQUIDITY_CREDIT_DAILY", "LS_T8462_DAILY", "TOSS_KR_TREASURY_DAILY",
                "BOK_FX_DAILY",
                "RESEARCH_FORWARD_TEST_DAILY",
            ),
            "2026-08-24T20:30:00+09:00",
        ),
    ],
)
def test_kr_market_daily_bundle_preserves_delayed_slot_identity(
    tmp_path: Path, monkeypatch, scheduled_slot: str, started_at: datetime,
    expected_lanes: tuple[str, ...], expected_scheduled_for: str,
) -> None:
    calls: list[tuple[str, datetime, datetime]] = []

    def run(_root, lane, *, started_at, scheduled_for, dry_run):
        assert dry_run
        calls.append((lane, started_at, scheduled_for))
        return {"status": "DRY_RUN_PASS", "api_calls": 0}

    monkeypatch.setattr(MODULE, "_run_bundle_lane", run)
    payload, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot=scheduled_slot, as_of=started_at, dry_run=True,
        allow_latest_occurrence=True,
    )

    assert exit_code == 0
    assert tuple(item[0] for item in calls) == expected_lanes
    assert {item[1] for item in calls} == {started_at}
    assert {item[2].isoformat() for item in calls} == {expected_scheduled_for}
    assert payload["scheduled_slot"] == scheduled_slot
    assert payload["scheduled_for"] == expected_scheduled_for
    assert payload["started_at_utc"] == started_at.isoformat()
    assert all(item["result"]["scheduled_slot"] == scheduled_slot for item in payload["outcomes"])


@pytest.mark.parametrize(
    "argv",
    [
        ["--bundle", "KR_MARKET_DAILY", "--dry-run"],
        ["--lane", "FRED_DAILY", "--scheduled-slot", "09:10", "--dry-run"],
        ["--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "10:00", "--dry-run"],
        [
            "--lane", "FRED_DAILY", "--scheduled-occurrence",
            "2026-08-24T09:10:00+09:00", "--dry-run",
        ],
        [
            "--lane", "FRED_DAILY", "--allow-latest-occurrence", "--dry-run",
        ],
        [
            "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "09:10",
            "--scheduled-occurrence", "2026-08-24T09:10:00+09:00",
            "--allow-latest-occurrence", "--dry-run",
        ],
        [
            "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "09:10",
            "--as-of", "2026-08-24T09:10:00", "--dry-run",
        ],
    ],
)
def test_cli_rejects_missing_mismatched_or_unknown_scheduled_slot(
    monkeypatch, argv: list[str],
) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *argv])
    with pytest.raises(SystemExit) as error:
        MODULE.main()
    assert error.value.code == 2


def test_kr_scheduled_slot_requires_aware_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MODULE._kr_market_daily_scheduled_for(
            datetime(2026, 8, 24, 9, 10), "09:10",
        )
    with pytest.raises(ValueError, match="unsupported"):
        MODULE._kr_market_daily_scheduled_for(
            datetime(2026, 8, 24, 0, 10, tzinfo=timezone.utc), "10:00",
        )


def test_kr_exact_slot_action_accepts_only_current_no_replay_window() -> None:
    occurrence = datetime(
        2026, 8, 25, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    assert MODULE._kr_market_daily_scheduled_for(occurrence, "09:10") == occurrence
    assert MODULE._kr_market_daily_scheduled_for(
        occurrence + MODULE.KR_MARKET_DAILY_EXACT_SLOT_DELAY, "09:10",
    ) == occurrence
    for started_at in (
        occurrence - MODULE.timedelta(seconds=1),
        occurrence + MODULE.KR_MARKET_DAILY_EXACT_SLOT_DELAY
        + MODULE.timedelta(seconds=1),
    ):
        with pytest.raises(MODULE.KrBundleOccurrenceError, match="no-replay window"):
            MODULE._kr_market_daily_scheduled_for(started_at, "09:10")
    next_occurrence = occurrence + MODULE.timedelta(days=1)
    assert MODULE._kr_market_daily_scheduled_for(
        next_occurrence, "09:10",
    ) == next_occurrence


def test_latest_kr_scheduled_occurrence_is_bounded_and_explicitly_enabled() -> None:
    assert MODULE._kr_market_daily_scheduled_for(
        datetime(2026, 8, 24, 8, 0, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        "20:30",
        allow_latest_occurrence=True,
    ).isoformat() == "2026-08-23T20:30:00+09:00"

    assert MODULE._kr_market_daily_scheduled_for(
        datetime(2026, 8, 25, 0, 5, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        "20:30",
        allow_latest_occurrence=True,
    ).isoformat() == "2026-08-24T20:30:00+09:00"

    assert MODULE._kr_market_daily_scheduled_for(
        datetime(2026, 8, 25, 21, 0, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        "20:30",
        allow_latest_occurrence=True,
    ).isoformat() == "2026-08-25T20:30:00+09:00"

    boundary = datetime(
        2026, 8, 24, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    assert MODULE._kr_market_daily_scheduled_for(
        boundary + MODULE.KR_MARKET_DAILY_MAX_IMPLICIT_DELAY, "09:10",
        allow_latest_occurrence=True,
    ) == boundary
    with pytest.raises(MODULE.KrBundleOccurrenceError, match="ambiguous"):
        MODULE._kr_market_daily_scheduled_for(
            boundary + MODULE.KR_MARKET_DAILY_MAX_IMPLICIT_DELAY
            + MODULE.timedelta(seconds=1),
            "09:10",
            allow_latest_occurrence=True,
        )

    post_close = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    assert MODULE._kr_market_daily_scheduled_for(
        post_close + MODULE.KR_MARKET_DAILY_2030_CATCH_UP_DELAY, "20:30",
        allow_latest_occurrence=True,
    ) == post_close
    with pytest.raises(MODULE.KrBundleOccurrenceError, match="ambiguous"):
        MODULE._kr_market_daily_scheduled_for(
            post_close + MODULE.KR_MARKET_DAILY_2030_CATCH_UP_DELAY
            + MODULE.timedelta(seconds=1),
            "20:30",
            allow_latest_occurrence=True,
        )


def test_explicit_kr_scheduled_occurrence_is_validated_and_preserved() -> None:
    started_at = datetime(
        2026, 8, 25, 21, 0, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    prior_occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    assert MODULE._kr_market_daily_scheduled_for(
        started_at, "20:30", prior_occurrence,
    ) == prior_occurrence
    assert MODULE._kr_market_daily_scheduled_for(
        started_at, "20:30",
        datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc),
    ) == prior_occurrence
    with pytest.raises(MODULE.KrBundleOccurrenceError, match="timezone-aware"):
        MODULE._kr_market_daily_scheduled_for(
            started_at, "20:30", datetime(2026, 8, 24, 20, 30),
        )
    with pytest.raises(MODULE.KrBundleOccurrenceError, match="does not match"):
        MODULE._kr_market_daily_scheduled_for(
            started_at, "20:30",
            datetime(2026, 8, 24, 20, 31, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        )
    with pytest.raises(MODULE.KrBundleOccurrenceError, match="after bundle start"):
        MODULE._kr_market_daily_scheduled_for(
            started_at, "20:30",
            datetime(2026, 8, 26, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE),
        )
    friday_occurrence = datetime(
        2026, 8, 21, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    monday_start = datetime(
        2026, 8, 24, 8, 0, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    assert MODULE._kr_market_daily_scheduled_for(
        monday_start, "20:30", friday_occurrence,
    ) == friday_occurrence

    with pytest.raises(MODULE.KrBundleOccurrenceError, match="mutually exclusive"):
        MODULE._kr_market_daily_scheduled_for(
            started_at, "20:30", prior_occurrence,
            allow_latest_occurrence=True,
        )


def test_explicit_prior_day_occurrence_flows_through_bundle_identity(
    tmp_path: Path, monkeypatch,
) -> None:
    started_at = datetime(
        2026, 8, 25, 21, 0, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    calls: list[datetime] = []

    def run(_root, _lane, *, scheduled_for, **_kwargs):
        calls.append(scheduled_for)
        return {"status": "DRY_RUN_PASS", "api_calls": 0}

    monkeypatch.setattr(MODULE, "_run_bundle_lane", run)
    payload, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started_at, dry_run=True,
        scheduled_occurrence=occurrence,
    )

    assert exit_code == 0
    assert payload["scheduled_for"] == occurrence.isoformat()
    assert set(calls) == {occurrence}


def test_cli_reports_occurrence_failure_before_bundle_work(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane",
        lambda *_args, **_kwargs: pytest.fail("ambiguous occurrence must not run lanes"),
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path),
        "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "20:30",
        "--as-of", "2026-08-24T08:00:00+09:00",
    ])

    assert MODULE.main() == 1
    expected = {
        "schema_version": 1,
        "bundle": "KR_MARKET_DAILY",
        "scheduled_slot": "20:30",
        "status": "FAIL_OCCURRENCE",
        "error_type": "KrBundleOccurrenceError",
        "scheduler_process_status": "FAIL_BEFORE_LANES",
    }
    assert json.loads(capsys.readouterr().out) == expected
    assert json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    ).read_text(encoding="utf-8")) == expected


def test_kr_bundle_lock_rejects_contender_before_lane_work_and_preserves_owner(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    lock_path = (
        tmp_path / "data/state/provider_scheduler/kr_market_daily_bundle.lock"
    )
    owner = MODULE.DailyRunLock(
        lock_path, run_id="existing-bundle-owner",
        acquired_at=datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
    ).acquire()
    owner_bytes = lock_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("contender must not begin lane work")

    monkeypatch.setattr(MODULE, "_run_bundle_lane", forbidden)
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path),
        "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "09:10",
        "--as-of", "2026-08-24T09:15:00+09:00",
        "--allow-latest-occurrence", "--dry-run",
    ])
    try:
        assert MODULE.main() == 1
        assert lock_path.read_bytes() == owner_bytes
        payload = json.loads(capsys.readouterr().out)
        assert payload == {
            "schema_version": 1,
            "bundle": "KR_MARKET_DAILY",
            "scheduled_slot": "09:10",
            "status": "FAIL_LOCK",
            "error_type": "KrBundleOverlapError",
            "scheduler_process_status": "FAIL_BEFORE_LANES",
        }
    finally:
        owner.release()


def test_kr_bundle_lock_releases_after_unexpected_bundle_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(MODULE, "_run_kr_market_daily_bundle_unlocked", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        MODULE._run_kr_market_daily_bundle(
            tmp_path, scheduled_slot="14:10",
            as_of=datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc),
            dry_run=True,
            allow_latest_occurrence=True,
        )
    assert not (
        tmp_path / "data/state/provider_scheduler/kr_market_daily_bundle.lock"
    ).exists()


def test_kr_bundle_claims_occurrence_before_lanes_and_repeat_is_api_zero(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[str] = []

    def run(_root, lane, **_kwargs):
        calls.append(lane)
        return {"status": "PASS", "api_calls": 0}

    monkeypatch.setattr(MODULE, "_run_bundle_lane", run)
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    started = datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc)
    occurrence = datetime(2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE)

    first, first_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    first_calls = tuple(calls)
    second, second_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )

    assert first_exit == second_exit == 0
    assert first["status"] == "PASS"
    assert first["lane_contract_version"] == 9
    assert first_calls
    assert tuple(calls) == first_calls
    assert second["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert second["lane_contract_version"] == 9
    assert second["api_calls"] == 0
    assert second["scheduler_process_status"] == "NOOP_TERMINAL_SUCCESS_PRESERVED"
    receipt = MODULE._kr_occurrence_receipt_path(
        tmp_path, scheduled_slot="20:30", scheduled_for=occurrence,
    )
    terminal = json.loads(receipt.read_text(encoding="utf-8"))
    assert terminal["occurrence_status"] == "TERMINAL_SUCCESS"
    assert terminal["lane_contract_version"] == 9
    assert terminal["terminal_exit_code"] == 0
    assert terminal["scheduled_for"] == occurrence.isoformat()
    assert terminal["eligible_lanes"] == list(first_calls)
    assert len(terminal["outcomes"]) == len(first_calls)
    last_log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    assert json.loads(last_log.read_text(encoding="utf-8")) == terminal
    before_replay = last_log.read_bytes()
    MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    assert last_log.read_bytes() == before_replay
    last_log.unlink()
    restored, restored_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    assert restored_exit == 0
    assert restored["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert json.loads(last_log.read_text(encoding="utf-8")) == terminal


def test_duplicate_legacy_v1_occurrence_is_preserved_byte_for_byte(
    tmp_path: Path, monkeypatch,
) -> None:
    occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    receipt = MODULE._kr_occurrence_receipt_path(
        tmp_path, scheduled_slot="20:30", scheduled_for=occurrence,
    )
    receipt.parent.mkdir(parents=True)
    legacy = {
        "schema_version": 1,
        "bundle": MODULE.KR_MARKET_DAILY_BUNDLE,
        "scheduled_slot": "20:30",
        "scheduled_for": occurrence.isoformat(),
        "status": "PASS",
        "occurrence_status": "TERMINAL_SUCCESS",
        "terminal_exit_code": 0,
    }
    receipt.write_text(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    before = receipt.read_bytes()
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane",
        lambda *_args, **_kwargs: pytest.fail("legacy replay must not run lanes"),
    )

    replay, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30",
        as_of=datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc),
        dry_run=False, scheduled_occurrence=occurrence,
    )

    assert exit_code == 0
    assert replay["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert "lane_contract_version" not in replay
    assert receipt.read_bytes() == before


def test_duplicate_occurrence_never_rewinds_a_newer_terminal_pointer(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane", lambda *_args, **_kwargs: {
            "status": "NOOP", "api_calls": 0,
        },
    )
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    started = datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc)
    old_occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=old_occurrence,
    )
    log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    newer = json.loads(log.read_text(encoding="utf-8"))
    newer["scheduled_for"] = "2026-08-25T20:30:00+09:00"
    MODULE._write_lane_log(tmp_path, MODULE.KR_MARKET_DAILY_BUNDLE, newer)
    before = log.read_bytes()
    replay, replay_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=old_occurrence,
    )
    assert replay_exit == 0
    assert replay["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert log.read_bytes() == before


def test_first_claimed_older_occurrence_never_rewinds_newer_terminal_pointer(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane", lambda *_args, **_kwargs: {
            "status": "NOOP", "api_calls": 0,
        },
    )
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    newer = {
        "schema_version": 1,
        "bundle": MODULE.KR_MARKET_DAILY_BUNDLE,
        "scheduled_for": "2026-08-25T20:30:00+09:00",
        "occurrence_status": "TERMINAL_SUCCESS",
    }
    MODULE._write_lane_log(tmp_path, MODULE.KR_MARKET_DAILY_BUNDLE, newer)
    before = log.read_bytes()
    old_occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )

    terminal, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30",
        as_of=datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc),
        dry_run=False, scheduled_occurrence=old_occurrence,
    )

    assert exit_code == 0
    assert terminal["occurrence_status"] == "TERMINAL_SUCCESS"
    assert log.read_bytes() == before
    receipt = MODULE._kr_occurrence_receipt_path(
        tmp_path, scheduled_slot="20:30", scheduled_for=old_occurrence,
    )
    assert json.loads(receipt.read_text(encoding="utf-8")) == terminal


@pytest.mark.parametrize(
    "health",
    (
        {"status": "PASS", "dataset_count": 80,
         "runtime_coverage_validated_count": 21,
         "runtime_coverage_failure_count": 1},
        {"status": "PASS", "dataset_count": 80,
         "runtime_coverage_validated_count": 0,
         "runtime_coverage_failure_count": 0},
        {"status": "PASS", "dataset_count": 0,
         "runtime_coverage_validated_count": 21,
         "runtime_coverage_failure_count": 0},
    ),
)
def test_incomplete_health_terminalizes_occurrence_as_failure(
    tmp_path: Path, monkeypatch, health: dict[str, object],
) -> None:
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane", lambda *_args, **_kwargs: {
            "status": "NOOP", "api_calls": 0,
        },
    )
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: health)
    occurrence = datetime(
        2026, 8, 25, 9, 10, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )

    terminal, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="09:10", as_of=occurrence,
        dry_run=False, scheduled_occurrence=occurrence,
    )

    assert exit_code == 1
    assert terminal["occurrence_status"] == "TERMINAL_FAILURE"
    assert terminal["terminal_exit_code"] == 1
    assert terminal["scheduler_process_status"] == "FAIL_AFTER_INDEPENDENT_LANES"
    assert terminal["health_projection"] == {
        "status": "FAIL", "error_type": "SchedulerHealthProjectionError",
    }
    assert all(
        item["result"]["scheduler_process_status"] == "FAIL_AFTER_HEALTH"
        for item in terminal["outcomes"]
        if isinstance(item.get("result"), dict)
    )


def test_duplicate_occurrence_preserves_malformed_pointer_for_readiness_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE, "_run_bundle_lane", lambda *_args, **_kwargs: {
            "status": "NOOP", "api_calls": 0,
        },
    )
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    started = datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc)
    occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    log.write_text("{malformed", encoding="utf-8")
    replay, replay_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    assert replay_exit == 0
    assert replay["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert log.read_text(encoding="utf-8") == "{malformed"


def test_kr_bundle_occurrence_claim_is_fail_closed_after_lane_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("injected after durable occurrence claim")

    monkeypatch.setattr(MODULE, "_run_kr_market_daily_bundle_unlocked", fail)
    started = datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc)
    occurrence = datetime(2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE)
    with pytest.raises(RuntimeError, match="durable occurrence"):
        MODULE._run_kr_market_daily_bundle(
            tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
            scheduled_occurrence=occurrence,
        )
    replay, exit_code = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    assert calls == 1
    assert exit_code == 1
    assert replay["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert replay["scheduler_process_status"] == "NOOP_UNRESOLVED_CLAIM_PRESERVED"
    receipt = MODULE._kr_occurrence_receipt_path(
        tmp_path, scheduled_slot="20:30", scheduled_for=occurrence,
    )
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "CLAIMED_BEFORE_LANES"
    assert not (
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    ).exists()


def test_kr_bundle_terminal_failure_replay_stays_nonzero_and_preserves_log(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = 0

    def fail_lane(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("injected lane failure")

    monkeypatch.setattr(MODULE, "_run_bundle_lane", fail_lane)
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    started = datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc)
    occurrence = datetime(
        2026, 8, 24, 20, 30, tzinfo=MODULE.KR_MARKET_DAILY_TIMEZONE,
    )
    first, first_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    first_calls = calls
    log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    before = log.read_bytes()
    replay, replay_exit = MODULE._run_kr_market_daily_bundle(
        tmp_path, scheduled_slot="20:30", as_of=started, dry_run=False,
        scheduled_occurrence=occurrence,
    )
    assert first_exit == replay_exit == 1
    assert first["occurrence_status"] == "TERMINAL_FAILURE"
    assert replay["scheduler_process_status"] == "NOOP_TERMINAL_FAILURE_PRESERVED"
    assert calls == first_calls
    assert log.read_bytes() == before


def test_liquidity_credit_observation_reports_match_and_revision(
    tmp_path: Path, monkeypatch,
) -> None:
    class Result:
        def __init__(self, dataset: str, status: str) -> None:
            self.dataset = f"kr_{dataset}_daily"
            self.market_date = "2026-08-24"
            self.status = status
            self.response_status = "COMPLETE"
            self.pages = 1
            self.observation_count = 2

    monkeypatch.setattr(
        MODULE.ExchangeTradingCalendar,
        "latest_completed_session",
        lambda *_args, **_kwargs: datetime(2026, 8, 24).date(),
    )
    monkeypatch.setattr(
        MODULE, "plan_liquidity_credit_two_pass",
        lambda **kwargs: type("Plan", (), {
            "dataset": f"kr_{kwargs['dataset']}_daily", "estimated_api_calls": 1,
            "action": "CAPTURE_CONFIRMATION",
        })(),
    )
    statuses = iter(("STABLE", "REVISED"))
    monkeypatch.setattr(
        MODULE, "execute_liquidity_credit_two_pass",
        lambda plan, **_kwargs: Result(plan.dataset.removeprefix("kr_").removesuffix("_daily"), next(statuses)),
    )

    result = MODULE._run_liquidity_credit_observation(
        tmp_path,
        clock=datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc),
        dry_run=False,
    )

    assert [item["comparison"] for item in result["observations"]] == [
        "OK", "DIFFERENT",
    ]
    assert result["api_calls"] == 2


def test_liquidity_credit_morning_does_not_create_first_observation(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE.ExchangeTradingCalendar,
        "latest_completed_session",
        lambda *_args, **_kwargs: datetime(2026, 8, 21).date(),
    )
    monkeypatch.setattr(
        MODULE, "plan_liquidity_credit_two_pass",
        lambda **kwargs: type("Plan", (), {
            "dataset": f"kr_{kwargs['dataset']}_daily",
            "estimated_api_calls": 1,
            "action": "CAPTURE_PROVISIONAL",
        })(),
    )

    result = MODULE._run_liquidity_credit_observation(
        tmp_path,
        clock=datetime(2026, 8, 24, 0, 10, tzinfo=timezone.utc),
        dry_run=False,
    )

    assert result["api_calls"] == 0
    assert {item["status"] for item in result["observations"]} == {
        "WAITING_FOR_2030_PROVISIONAL",
    }


def test_liquidity_credit_empty_credit_uses_one_lag_fallback_only(
    tmp_path: Path, monkeypatch,
) -> None:
    target = date(2026, 8, 24)
    fallback_date = date(2026, 8, 21)
    monkeypatch.setattr(
        MODULE.ExchangeTradingCalendar,
        "latest_completed_session",
        lambda *_args, **_kwargs: target,
    )
    monkeypatch.setattr(
        MODULE.ExchangeTradingCalendar,
        "previous_trading_day",
        lambda _self, value: value.replace(day=value.day - 1),
    )
    selected: list[tuple[date, ...]] = []

    def select_fallback(**kwargs):
        selected.append(tuple(kwargs["candidate_dates"]))
        return fallback_date

    monkeypatch.setattr(MODULE, "select_credit_balance_fallback_date", select_fallback)
    monkeypatch.setattr(
        MODULE, "plan_liquidity_credit_two_pass",
        lambda **kwargs: SimpleNamespace(
            dataset=f"kr_{kwargs['dataset']}_daily",
            market_date=kwargs["market_date"],
            estimated_api_calls=1,
            action=(
                "CAPTURE_CONFIRMATION"
                if kwargs["market_date"] == fallback_date
                else "CAPTURE_PROVISIONAL"
            ),
        ),
    )

    def execute(plan, **_kwargs):
        is_credit = plan.dataset == "kr_credit_balance_daily"
        is_fallback = plan.market_date == fallback_date
        return SimpleNamespace(
            dataset=plan.dataset,
            market_date=plan.market_date.isoformat(),
            status="REVISED" if is_fallback else "PROVISIONAL",
            response_status=(
                "COMPLETE" if not is_credit or is_fallback else "VALID_EMPTY"
            ),
            pages=1,
            observation_count=2 if is_fallback else 1,
        )

    monkeypatch.setattr(MODULE, "execute_liquidity_credit_two_pass", execute)
    result = MODULE._run_liquidity_credit_observation(
        tmp_path,
        clock=datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc),
        dry_run=False,
    )

    market, credit = result["observations"]
    assert "fallback" not in market and market["api_calls"] == 1
    assert credit["response_status"] == "VALID_EMPTY"
    assert credit["fallback"] == {
        "target_date": "2026-08-21",
        "status": "REVISED",
        "response_status": "COMPLETE",
        "comparison": "DIFFERENT",
        "api_calls": 1,
        "observation_count": 2,
    }
    assert len(selected[0]) == 3
    assert result["api_calls"] == 3


def test_provider_cli_retains_bound_success_and_noop_events(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    reports = iter((
        {"status": "PASS", "api_calls": 2},
        {"status": "NOOP", "api_calls": 0},
    ))
    monkeypatch.setattr(MODULE, "run_lane", lambda *_args, **_kwargs: next(reports))
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    argv = [
        str(SCRIPT), "--project-root", str(tmp_path), "--lane", "FRED_DAILY",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert MODULE.main() == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", argv)
    assert MODULE.main() == 0
    capsys.readouterr()

    events = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        MODULE.EventState.STARTED,
        MODULE.EventState.SUCCEEDED,
        MODULE.EventState.STARTED,
        MODULE.EventState.API_ZERO_NOOP,
    ]
    assert events[0].run_id == events[1].run_id
    assert events[2].run_id == events[3].run_id
    assert events[0].run_id != events[2].run_id
    assert events[1].provider_call_count == 2
    encoded = json.dumps([event.to_dict() for event in events], ensure_ascii=False)
    assert "http://" not in encoded and "https://" not in encoded
    assert "api_key" not in encoded.lower() and "payload" not in encoded.lower()


def test_provider_bundle_cli_emits_one_bound_api_zero_pair(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    report = {
        "status": "NOOP_OCCURRENCE_ALREADY_CLAIMED",
        "api_calls": 0,
    }
    monkeypatch.setattr(
        MODULE,
        "_run_kr_market_daily_bundle",
        lambda *_args, **_kwargs: (report.copy(), 0),
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path),
        "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "20:30",
    ])

    assert MODULE.main() == 0
    assert json.loads(capsys.readouterr().out) == report
    events = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        MODULE.EventState.STARTED, MODULE.EventState.API_ZERO_NOOP,
    ]
    assert len({event.run_id for event in events}) == 1
    assert events[0].logical_dataset == "KR_MARKET_DAILY"
    assert events[0].requested_scope == {
        "lane": None,
        "bundle": "KR_MARKET_DAILY",
        "scheduled_slot": "20:30",
        "dry_run": False,
    }
    assert events[1].provider_call_count == 0


def test_provider_bundle_dry_run_event_does_not_claim_currency(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    report = {"status": "DRY_RUN_PASS", "api_calls": 0, "outcomes": []}
    monkeypatch.setattr(
        MODULE, "_run_kr_market_daily_bundle",
        lambda *_args, **_kwargs: (report.copy(), 0),
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path),
        "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "20:30",
        "--dry-run",
    ])

    assert MODULE.main() == 0
    capsys.readouterr()
    terminal = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()[-1]
    assert terminal.state is MODULE.EventState.SUCCEEDED
    assert terminal.reason_code is MODULE.ReasonCode.COMPLETED
    assert terminal.promotion_result is MODULE.CommitResult.NOT_RUN
    assert terminal.checkpoint_result is MODULE.CommitResult.NOT_RUN
    assert "ALREADY_CURRENT" not in json.dumps(terminal.to_dict())


def test_provider_cli_failure_is_typed_and_logger_failure_preserves_result(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        MODULE, "run_lane",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("payload=https://secret.invalid token=abc")
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path / "typed"),
        "--lane", "FRED_DAILY",
    ])
    assert MODULE.main() == 1
    failure_payload = json.loads(capsys.readouterr().out)
    assert failure_payload["status"] == "FAIL"
    events = MODULE.LocalUpdateEventLog(
        tmp_path / "typed/artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        MODULE.EventState.STARTED, MODULE.EventState.VALIDATION_FAILURE,
    ]
    assert len({event.run_id for event in events}) == 1
    assert "secret.invalid" not in json.dumps(events[-1].to_dict())

    class FailingLog:
        def append(self, _event):
            raise OSError("logger unavailable")

    report = {"status": "PASS", "api_calls": 1}
    monkeypatch.setattr(MODULE, "run_lane", lambda *_args, **_kwargs: report.copy())
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    monkeypatch.setattr(MODULE, "LocalUpdateEventLog", lambda _path: FailingLog())
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path / "logger"),
        "--lane", "FRED_DAILY",
    ])
    assert MODULE.main() == 0
    streams = capsys.readouterr()
    assert json.loads(streams.out)["status"] == "PASS"
    assert streams.err.count("runtime_event_log=FAILED") == 2
    assert "logger unavailable" not in streams.err


def test_provider_cli_last_result_write_failure_still_emits_one_terminal(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE, "run_lane", lambda *_args, **_kwargs: {"status": "PASS", "api_calls": 1},
    )
    monkeypatch.setattr(MODULE, "_refresh_health", lambda _root: _health_pass())
    monkeypatch.setattr(
        MODULE, "_write_lane_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("last result unavailable")),
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path), "--lane", "FRED_DAILY",
    ])

    with pytest.raises(OSError, match="last result unavailable"):
        MODULE.main()

    events = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        MODULE.EventState.STARTED, MODULE.EventState.LOCAL_IO_FAILURE,
    ]
    assert len({event.run_id for event in events}) == 1


def test_provider_bundle_last_result_write_failure_still_emits_one_terminal(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "_run_kr_market_daily_bundle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            MODULE.KrBundleOccurrenceError("invalid occurrence")
        ),
    )
    monkeypatch.setattr(
        MODULE, "_write_lane_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bundle result unavailable")),
    )
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT), "--project-root", str(tmp_path),
        "--bundle", "KR_MARKET_DAILY", "--scheduled-slot", "20:30",
    ])

    with pytest.raises(OSError, match="bundle result unavailable"):
        MODULE.main()

    events = MODULE.LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        MODULE.EventState.STARTED, MODULE.EventState.LOCAL_IO_FAILURE,
    ]
    assert len({event.run_id for event in events}) == 1
