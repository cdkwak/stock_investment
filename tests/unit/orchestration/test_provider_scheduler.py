from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

from stock_data.orchestration.daily_operations import (
    DAILY_LANE_READINESS, DailyRunLock, DailyRunLockError, LaneReadinessStatus,
)
import stock_data.orchestration.provider_scheduler as scheduler
from stock_data.orchestration.provider_scheduler import ProviderSchedulerError, run_lane


AS_OF = datetime(2026, 8, 18, 20, 1, tzinfo=timezone.utc)
FUTURES_AS_OF = datetime(2026, 8, 19, 13, 1, tzinfo=timezone.utc)


def _enable_test_lane(monkeypatch) -> None:
    fred = next(item for item in DAILY_LANE_READINESS if item.lane == "FRED_DAILY")
    monkeypatch.setattr(scheduler, "DAILY_LANE_READINESS", (
        replace(fred, status=LaneReadinessStatus.READY, scheduler_eligible=True, blocker=None),
    ))

    class Registry:
        @staticmethod
        def select(*, executable_only=False):
            assert executable_only
            return tuple(SimpleNamespace(dataset_id=value) for value in (
                "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
            ))

    monkeypatch.setattr(scheduler, "DATASET_OPERATIONS", Registry())


def test_scheduler_dry_run_is_zero_network(tmp_path: Path, monkeypatch) -> None:
    _enable_test_lane(monkeypatch)
    result = run_lane(tmp_path, "FRED_DAILY", as_of=AS_OF, dry_run=True)
    assert result["status"] == "DRY_RUN_PASS"
    assert result["target_session"] == "2026-08-18"
    assert result["phase_targets"] == {
        "fred_yields": "2026-08-14", "fred_fx": "2026-08-14", "fred_vix": "2026-08-17",
    }
    assert result["api_calls"] == 0


def test_index_fundamental_lane_dry_run_is_registered_and_network_free(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler, "run_index_fundamental_daily",
        lambda *_args, **_kwargs: pytest.fail("dry-run entered provider operation"),
    )

    result = run_lane(
        tmp_path, "KR_INDEX_FUNDAMENTAL_DAILY", as_of=AS_OF, dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["automation_dataset_ids"] == ["kr_index_fundamental_daily"]
    assert result["phase_targets"] == {"index_fundamentals": "2026-08-14"}


@pytest.mark.parametrize(
    ("as_of", "expected_target"),
    (
        (datetime(2026, 8, 19, 0, 9, 59, tzinfo=timezone.utc), "2026-08-14"),
        (datetime(2026, 8, 19, 0, 10, 0, tzinfo=timezone.utc), "2026-08-18"),
    ),
)
def test_index_fundamental_lane_advances_at_next_trading_day_0910(
    tmp_path: Path, as_of: datetime, expected_target: str,
) -> None:
    result = run_lane(
        tmp_path, "KR_INDEX_FUNDAMENTAL_DAILY", as_of=as_of, dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["phase_targets"] == {"index_fundamentals": expected_target}


def test_index_fundamental_lane_writes_exact_api_zero_receipt(
    tmp_path: Path,
) -> None:
    result = run_lane(
        tmp_path, "KR_INDEX_FUNDAMENTAL_DAILY", as_of=AS_OF,
        phase_runner=lambda _root, phase, target: {
            "status": "NOOP_IDEMPOTENT", "http_calls": 0, "run_id": None,
            "latest_before": target.isoformat(), "latest_after": target.isoformat(),
            "reason": "TARGET_ALREADY_ACCEPTED_BEFORE_PROVIDER_ACCESS",
        },
    )

    receipt = tmp_path / "artifacts/scheduler_logs/KR_INDEX_FUNDAMENTAL_DAILY_last.json"
    assert result["status"] == "NOOP" and result["api_calls"] == 0
    assert json.loads(receipt.read_text(encoding="utf-8")) == result
    assert not (
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KR_INDEX_FUNDAMENTAL_DAILY_last.json"
    ).exists()


def test_kospi200_breadth_lane_targets_only_latest_canonical_accepted_date(
    tmp_path: Path,
) -> None:
    state = tmp_path / "data/state/canonical_equity_accepted_dates.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "accepted_dates": ["2026-08-13", "2026-08-14"],
        "latest_accepted_date": "2026-08-14",
    }), encoding="utf-8")

    result = run_lane(
        tmp_path, "KOSPI200_BREADTH_DAILY", as_of=AS_OF, dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS" and result["api_calls"] == 0
    assert result["target_session"] == "2026-08-18"
    assert result["phase_targets"] == {"kospi200_breadth": "2026-08-14"}
    assert result["provider_availability_policies"] == {
        "kospi200_breadth": "CANONICAL_ACCEPTED_DATE_ONLY",
    }


def test_kospi200_breadth_lane_writes_api_zero_replay_receipt(tmp_path: Path) -> None:
    state = tmp_path / "data/state/canonical_equity_accepted_dates.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "accepted_dates": ["2026-08-14"],
        "latest_accepted_date": "2026-08-14",
    }), encoding="utf-8")

    result = run_lane(
        tmp_path, "KOSPI200_BREADTH_DAILY", as_of=AS_OF,
        phase_runner=lambda _root, phase, target: {
            "status": "NOOP_IDEMPOTENT", "http_calls": 0, "run_id": None,
            "latest_after": target.isoformat(),
            "reason": "COMPLETE_EXACT_DATE_KOSPI200_SCOPE",
        },
    )

    assert result["status"] == "NOOP" and result["api_calls"] == 0
    assert result["phases"][0]["dataset_id"] == "kr_kospi200_breadth_daily"
    receipt = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_KOSPI200_BREADTH_DAILY_last.json"
    assert json.loads(receipt.read_text(encoding="utf-8")) == result


def test_scheduler_preserves_explicit_scheduled_for_separately_from_start(
    tmp_path: Path, monkeypatch,
) -> None:
    _enable_test_lane(monkeypatch)
    scheduled_for = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
    started_at = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)

    result = run_lane(
        tmp_path, "FRED_DAILY", as_of=started_at,
        scheduled_for=scheduled_for, dry_run=True,
    )

    assert result["scheduled_for"] == scheduled_for.isoformat()
    assert result["started_at_utc"] == started_at.isoformat()
    assert result["run_id"].startswith("fred_daily-20260818T060000Z-")


def test_scheduler_rejects_naive_scheduled_for(tmp_path: Path, monkeypatch) -> None:
    _enable_test_lane(monkeypatch)
    with pytest.raises(ValueError, match="scheduled_for must be timezone-aware"):
        run_lane(
            tmp_path, "FRED_DAILY", as_of=AS_OF,
            scheduled_for=datetime(2026, 8, 18, 6, 0), dry_run=True,
        )
    with pytest.raises(ValueError, match="cannot be after"):
        run_lane(
            tmp_path, "FRED_DAILY", as_of=AS_OF,
            scheduled_for=datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc),
            dry_run=True,
        )


def test_lending_phase_reports_contract_validated_latest_date(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "plan_data_go_kr_daily",
        lambda **_kwargs: SimpleNamespace(action="NOOP_IDEMPOTENT"),
    )
    monkeypatch.setattr(
        scheduler,
        "execute_data_go_kr_daily",
        lambda *_args, **_kwargs: SimpleNamespace(status="COMPLETE", api_calls=0),
    )
    monkeypatch.setattr(
        scheduler,
        "read_dataset",
        lambda *_args, **_kwargs: pd.DataFrame({"date": [date(2026, 8, 18)]}),
    )

    result = scheduler._run_lending_phase(tmp_path, "market", date(2026, 8, 18))

    assert result["status"] == "NOOP_IDEMPOTENT"
    assert result["http_calls"] == 0
    assert result["latest_after"] == "2026-08-18"


def test_lending_phase_fails_when_success_did_not_reach_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "plan_data_go_kr_daily",
        lambda **_kwargs: SimpleNamespace(action="READY"),
    )
    monkeypatch.setattr(
        scheduler,
        "execute_data_go_kr_daily",
        lambda *_args, **_kwargs: SimpleNamespace(status="COMPLETE", api_calls=1),
    )
    monkeypatch.setattr(
        scheduler,
        "read_dataset",
        lambda *_args, **_kwargs: pd.DataFrame({"date": [date(2026, 8, 14)]}),
    )

    with pytest.raises(ProviderSchedulerError, match="did not reach"):
        scheduler._run_lending_phase(tmp_path, "detail", date(2026, 8, 18))


def test_short_selling_phase_preserves_two_market_atomic_call_budget(
    tmp_path: Path, monkeypatch,
) -> None:
    plan = SimpleNamespace(
        action="READY", estimated_api_calls=2, reason="EXACT_DATE_REVIEWED_AND_FINAL",
    )
    monkeypatch.setattr(scheduler, "plan_short_selling_daily", lambda **_kwargs: plan)
    monkeypatch.setattr(
        scheduler,
        "execute_short_selling_daily",
        lambda *_args, **_kwargs: SimpleNamespace(raw_http_requests=7),
    )

    result = scheduler._run_short_selling_phase(
        tmp_path, "short_trading", date(2026, 8, 19),
    )

    assert result == {
        "status": "COMPLETE",
        "http_calls": 7,
        "run_id": None,
        "latest_after": "2026-08-19",
        "attempted_dates": ["2026-08-19"],
        "reason": "TWO_MARKET_ATOMIC",
    }


def test_scheduler_first_pass_then_all_noop(tmp_path: Path, monkeypatch) -> None:
    _enable_test_lane(monkeypatch)
    calls = []

    def first(root, phase, target):
        calls.append((phase, target))
        return {"status": "PROMOTED", "http_calls": 1, "run_id": phase}

    result = run_lane(tmp_path, "FRED_DAILY", as_of=AS_OF, phase_runner=first)
    assert result["status"] == "PASS"
    assert result["api_calls"] == 3
    assert [phase for phase, _ in calls] == ["fred_yields", "fred_fx", "fred_vix"]

    def noop(root, phase, target):
        return {"status": "NOOP_IDEMPOTENT", "http_calls": 0}

    result = run_lane(tmp_path, "FRED_DAILY", as_of=AS_OF, phase_runner=noop)
    assert result["status"] == "NOOP"
    assert result["api_calls"] == 0


def test_scheduler_propagates_phase_degradation(tmp_path: Path, monkeypatch) -> None:
    _enable_test_lane(monkeypatch)
    result = run_lane(
        tmp_path, "FRED_DAILY", as_of=AS_OF,
        phase_runner=lambda *_args: {
            "status": "DEGRADED_VALID_EMPTY_PRESERVED", "http_calls": 1,
        },
    )
    assert result["status"] == "DEGRADED"
    assert result["api_calls"] == 3


def test_current_registry_enables_canonical_equity_with_d_plus_one_target(
    tmp_path: Path,
) -> None:
    result = run_lane(
        tmp_path, "CANONICAL_EQUITY_DAILY", as_of=AS_OF, dry_run=True,
    )
    assert result["status"] == "DRY_RUN_PASS"
    # 2026-08-17 is an XKRX substitute holiday, so the prior eligible
    # observation before the 13:00 KST publication gate is 2026-08-14.
    assert result["phase_targets"] == {"canonical_equity": "2026-08-14"}
    assert result["provider_availability_policies"] == {
        "canonical_equity": "DATA_GO_KR_D_PLUS_1_1300",
    }
    assert result["api_calls"] == 0


def test_canonical_phase_preserves_ordered_attempted_and_accepted_dates(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler,
        "run_canonical_equity_catchup",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="FAILED_PRESERVED", api_calls=3, run_id=None,
            latest_before=date(2026, 8, 13), latest_after=date(2026, 8, 14),
            reason="FIRST_UNRESOLVED_DATE_CanonicalEquityDailyError",
            selected_dates=(date(2026, 8, 14), date(2026, 8, 18)),
            attempted_dates=(date(2026, 8, 14), date(2026, 8, 18)),
            accepted_dates=(date(2026, 8, 14),), run_ids=("run-first",),
        ),
    )

    phase = scheduler._run_canonical_equity_phase(
        tmp_path, "canonical_equity", date(2026, 8, 19),
    )

    assert phase["status"] == "FAILED_PRESERVED"
    assert phase["http_calls"] == 3
    assert phase["attempted_dates"] == ["2026-08-14", "2026-08-18"]
    assert phase["accepted_dates"] == ["2026-08-14"]

    report = run_lane(
        tmp_path, "CANONICAL_EQUITY_DAILY", as_of=AS_OF,
        phase_runner=lambda *_args: phase,
    )
    assert report["status"] == "DEGRADED"
    assert report["api_calls"] == 3
    assert report["phases"][0]["attempted_dates"] == ["2026-08-14", "2026-08-18"]
    assert report["phases"][0]["accepted_dates"] == ["2026-08-14"]
    retained = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_CANONICAL_EQUITY_DAILY_last.json"
    ).read_text(encoding="utf-8"))
    assert retained["phases"][0]["attempted_dates"] == ["2026-08-14", "2026-08-18"]


def test_scheduler_failure_stops_remaining_lane_phases_and_releases_lock(tmp_path: Path, monkeypatch) -> None:
    _enable_test_lane(monkeypatch)
    calls = []

    def fail(root, phase, target):
        calls.append(phase)
        raise ProviderSchedulerError("VALIDATION_FAILURE")

    with pytest.raises(ProviderSchedulerError, match="VALIDATION_FAILURE"):
        run_lane(tmp_path, "FRED_DAILY", as_of=AS_OF, phase_runner=fail)
    assert calls == ["fred_yields"]
    assert not (tmp_path / "data/state/provider_scheduler/fred_daily.lock").exists()


def test_scheduler_overlap_is_rejected_without_mutating_owner_lock(tmp_path: Path, monkeypatch) -> None:
    _enable_test_lane(monkeypatch)
    path = tmp_path / "data/state/provider_scheduler/fred_daily.lock"
    path.parent.mkdir(parents=True)
    owner = DailyRunLock(path, run_id="owner", acquired_at=AS_OF).acquire()
    original = path.read_bytes()
    try:
        with pytest.raises(DailyRunLockError):
            run_lane(tmp_path, "FRED_DAILY", as_of=AS_OF, phase_runner=lambda *_: {})
        assert path.read_bytes() == original
    finally:
        owner.release()


def test_current_registry_enables_fred_with_source_specific_targets(tmp_path: Path) -> None:
    result = run_lane(tmp_path, "FRED_DAILY", as_of=AS_OF, dry_run=True)
    assert result["status"] == "DRY_RUN_PASS"
    assert result["automation_dataset_ids"] == [
        "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
    ]


def test_current_registry_enables_completed_dashboard_futures(tmp_path: Path) -> None:
    result = run_lane(
        tmp_path, "GLOBAL_COMMODITY_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )
    assert result["status"] == "DRY_RUN_PASS"
    assert result["phase_targets"] == {"dashboard_futures": "2026-08-18"}
    assert result["automation_dataset_ids"] == ["global_commodity_futures_daily"]
    assert result["provider_availability_policies"] == {
        "dashboard_futures": "YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET",
    }
    assert result["api_calls"] == 0


def test_current_registry_enables_registered_global_indices(tmp_path: Path) -> None:
    result = run_lane(
        tmp_path, "GLOBAL_INDEX_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )
    assert result["status"] == "DRY_RUN_PASS"
    assert result["phase_targets"] == {"global_indices": "2026-08-18"}
    assert result["automation_dataset_ids"] == ["global_index_price_daily"]
    assert result["provider_availability_policies"] == {
        "global_indices": "MARKET_SESSION_COMPLETE",
    }
    assert result["api_calls"] == 0


def test_current_registry_enables_exact_date_market_investor_flow(tmp_path: Path) -> None:
    result = run_lane(
        tmp_path, "MARKET_INVESTOR_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )
    assert result["status"] == "DRY_RUN_PASS"
    assert result["phase_targets"] == {"market_investor": "2026-08-19"}
    assert result["automation_dataset_ids"] == [
        "kr_market_investor_net_purchase_bridge_daily",
        "kr_market_investor_trading_daily",
    ]
    assert result["provider_availability_policies"] == {
        "market_investor": "KRX_POST_CLOSE_1830",
    }
    assert result["api_calls"] == 0


def test_current_registry_enables_next_session_short_selling_trading(
    tmp_path: Path,
) -> None:
    result = run_lane(
        tmp_path, "SHORT_SELLING_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )
    assert result["status"] == "DRY_RUN_PASS"
    assert result["phase_targets"] == {"short_trading": "2026-08-18"}
    assert result["automation_dataset_ids"] == ["kr_short_selling_trading_daily"]
    assert result["provider_availability_policies"] == {
        "short_trading": "KRX_SHORT_TRADING_T_PLUS_1",
    }
    assert result["api_calls"] == 0


def test_new_short_and_treasury_lanes_are_registered_and_provider_free_in_dry_run(
    tmp_path: Path,
) -> None:
    balance = run_lane(
        tmp_path, "SHORT_SELLING_BALANCE_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )
    investor = run_lane(
        tmp_path, "SHORT_SELLING_INVESTOR_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )
    treasury = run_lane(
        tmp_path, "TOSS_KR_TREASURY_DAILY", as_of=FUTURES_AS_OF, dry_run=True,
    )

    assert balance["phase_targets"] == {"short_balance": "2026-08-14"}
    assert investor["phase_targets"] == {"short_investor": "2026-08-19"}
    assert treasury["phase_targets"] == {"toss_kr_treasury": "2026-08-18"}
    assert balance["api_calls"] == investor["api_calls"] == treasury["api_calls"] == 0


def test_market_investor_replay_does_not_load_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "is_toss_market_investor_date_complete", lambda *_: True)

    def forbidden(**_kwargs):
        raise AssertionError("credential loader must not run for a completed date")

    monkeypatch.setattr(scheduler.TossInvestClient, "from_environment", forbidden)
    result = scheduler._run_market_investor_phase(
        tmp_path, "market_investor", FUTURES_AS_OF.date(),
    )
    assert result["status"] == "NOOP_IDEMPOTENT"
    assert result["http_calls"] == 0
