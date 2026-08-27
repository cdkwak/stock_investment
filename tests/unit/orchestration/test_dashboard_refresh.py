from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

import pytest

from stock_data.orchestration.dashboard_refresh import (
    DASHBOARD_REFRESH_LANES,
    DashboardLocalPoller,
    DashboardRefreshCoordinator,
    DashboardRefreshError,
    LaneOutcome,
    RefreshOutcome,
)


NOW = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)


def test_local_poll_is_api_zero_and_reports_only_metadata_changes(tmp_path: Path) -> None:
    health = tmp_path / "health.json"
    poller = DashboardLocalPoller((health,))

    assert poller.poll() == ()
    health.write_text("{}", encoding="utf-8")
    changed = poller.poll()

    assert len(changed) == 1
    assert changed[0].path == str(health.resolve())
    assert changed[0].exists is True
    poller.close()
    assert poller.poll() == ()


def test_provider_runner_requires_explicit_execution_gate() -> None:
    called = False

    def runner(_policy):
        nonlocal called
        called = True
        return {"status": "NOOP_CURRENT"}

    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)
    with pytest.raises(DashboardRefreshError, match="provider execution is disabled"):
        coordinator.run(("KR_INDEX_DAILY",), trigger="GUI", runner=runner)
    assert called is False


def test_updated_invalidates_only_allowlisted_changed_datasets() -> None:
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)
    report = coordinator.run(
        ("KR_INDEX_DAILY", "GLOBAL_ETF_DAILY"),
        trigger="GUI",
        permit_provider=True,
        runner=lambda policy: {
            "status": "UPDATED" if policy.lane_id == "KR_INDEX_DAILY" else "NOOP_CURRENT",
            "changed_dataset_ids": (
                ("kr_index_daily",) if policy.lane_id == "KR_INDEX_DAILY" else ()
            ),
            "latest": "2026-08-20",
            "expected": "2026-08-20",
            "finality": "FINAL_EOD",
        },
    )

    assert report.outcome is RefreshOutcome.UPDATED
    assert report.changed_dataset_ids == ("kr_index_daily",)
    assert report.lane_results[0].outcome is LaneOutcome.UPDATED
    assert coordinator.last_report == report


def test_process_success_without_advancement_is_rejected() -> None:
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)
    with pytest.raises(DashboardRefreshError, match="typed advancement outcome"):
        coordinator.run(
            ("GLOBAL_ETF_DAILY",), trigger="SCHEDULED", permit_provider=True,
            runner=lambda _policy: {"status": "SUCCESS"},
        )


def test_manual_request_coalesces_with_inflight_scheduled_run() -> None:
    entered = Event()
    release = Event()
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)

    def runner(_policy):
        entered.set()
        release.wait(timeout=2)
        return {"status": "NOOP_CURRENT", "finality": "FINAL_EOD"}

    thread = Thread(target=lambda: coordinator.run(
        ("KR_INDEX_DAILY",), trigger="SCHEDULED", runner=runner, permit_provider=True,
    ))
    thread.start()
    assert entered.wait(timeout=2)
    coalesced = coordinator.run(
        ("KR_INDEX_DAILY",), trigger="GUI", runner=runner, permit_provider=True,
    )
    release.set()
    thread.join(timeout=2)

    assert coalesced.outcome is RefreshOutcome.COALESCED
    assert not thread.is_alive()


def test_intraday_routes_remain_separate_and_activation_gated() -> None:
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)
    called = False

    def runner(_policy):
        nonlocal called
        called = True
        return {"status": "UPDATED"}

    report = coordinator.run(
        ("CBOE_VIX_NATIVE_15M",), trigger="GUI", runner=runner, permit_provider=True,
    )

    policy = DASHBOARD_REFRESH_LANES["CBOE_VIX_NATIVE_15M"]
    assert policy.layer.value == "PROVISIONAL_INTRADAY"
    assert policy.minimum_provider_interval.total_seconds() == 1800
    assert policy.provider_refresh_enabled is False
    assert called is False
    assert report.outcome is RefreshOutcome.FAILED
    assert report.lane_results[0].error_type == "UR111_CORE_RECOVERY_AND_30M_RUNBOOK_REQUIRED"


def test_short_lane_cannot_bypass_the_explicit_execution_approval_gate() -> None:
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)
    called = False

    def runner(_policy):
        nonlocal called
        called = True
        return {"status": "NOOP_CURRENT", "finality": "FINAL_EOD"}

    report = coordinator.run(
        ("SHORT_SELLING_DAILY",), trigger="GUI", runner=runner, permit_provider=True,
    )

    assert called is False
    assert report.outcome is RefreshOutcome.FAILED
    assert report.lane_results[0].error_type == "EXPLICIT_USER_EXECUTION_APPROVAL_REQUIRED"


def test_partial_failure_preserves_successful_lane_advancement() -> None:
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)

    def runner(policy):
        if policy.lane_id == "GLOBAL_ETF_DAILY":
            return {
                "status": "UPDATED",
                "changed_dataset_ids": ("global_etf_price_daily",),
                "finality": "FINAL_EOD",
            }
        raise TimeoutError("provider detail must not be retained")

    report = coordinator.run(
        ("GLOBAL_ETF_DAILY", "GLOBAL_INDEX_DAILY"),
        trigger="GUI",
        runner=runner,
        permit_provider=True,
    )

    assert report.outcome is RefreshOutcome.PARTIAL_FAILURE
    assert report.changed_dataset_ids == ("global_etf_price_daily",)
    assert report.lane_results[1].error_type == "TimeoutError"
    assert "provider detail" not in repr(report)


def test_close_rejects_new_work_without_calling_runner() -> None:
    coordinator = DashboardRefreshCoordinator(clock=lambda: NOW)
    coordinator.close()
    called = False

    def runner(_policy):
        nonlocal called
        called = True
        return {"status": "NOOP_CURRENT"}

    report = coordinator.run(
        ("KR_INDEX_DAILY",), trigger="GUI", runner=runner, permit_provider=True,
    )
    assert report.outcome is RefreshOutcome.CLOSED
    assert called is False
