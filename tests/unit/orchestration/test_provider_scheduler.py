from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pandas as pd

from stock_data.orchestration.daily_operations import (
    DAILY_LANE_READINESS, DailyRunLock, DailyRunLockError, LaneReadinessStatus,
)
from stock_data.contracts.kr_etf import KR_ETF_MASTER
from stock_data.orchestration.kr_etf_daily import normalize_master
from stock_data.orchestration.kr_index_daily_incremental import (
    validate_registered_kr_index_daily,
)
from stock_data.orchestration.kr_index_daily_live import (
    backfill_kospi200_it_history,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_etf import validate_kr_etf_master
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
        "fred_yields": "2026-08-14", "fred_fx": "2026-08-14", "fred_vix": "2026-08-14",
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


def test_kr_index_dry_run_lists_kospi200_it_ticker(tmp_path: Path) -> None:
    result = run_lane(
        tmp_path, "KR_INDEX_DAILY", as_of=AS_OF, dry_run=True,
    )

    assert {tuple(item.values()) for item in result["registered_indices"]} >= {
        ("KOSPI200_IT", "1155"),
    }


def test_fundamentals_weekly_dry_run_reports_count_budget_and_gate(
    tmp_path: Path,
) -> None:
    watchlist = tmp_path / "artifacts/local_user/watchlists.json"
    watchlist.parent.mkdir(parents=True)
    watchlist.write_text(json.dumps({
        "lists": [{"items": [{"market": "KOSPI", "symbol": "005930"}]}],
    }), encoding="utf-8")

    result = run_lane(
        tmp_path,
        "KR_FUNDAMENTALS_WEEKLY",
        as_of=datetime(2026, 9, 4, 11, 30, tzinfo=timezone.utc),
        dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["planned_lane_status"] == "REFRESH_DUE"
    assert result["planned_symbol_count"] == 1
    assert result["max_api_calls"] == 2_600
    assert result["years"] == (2026, 2025, 2024)
    assert result["api_calls"] == 0


def test_research_forward_test_lane_is_registered_and_provider_free(
    tmp_path: Path,
) -> None:
    config = scheduler.LANE_SCHEDULES["RESEARCH_FORWARD_TEST_DAILY"]
    assert config.phases == ("research_forward_test",)
    assert config.dataset_ids == ()
    result = run_lane(
        tmp_path, "RESEARCH_FORWARD_TEST_DAILY", as_of=AS_OF, dry_run=True,
    )
    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["automation_dataset_ids"] == []
    assert result["provider_availability_policies"] == {
        "research_forward_test": "RETAINED_PARQUET_ONLY"
    }


def test_kospi200_it_validation_and_one_call_backfill(tmp_path: Path) -> None:
    raw = pd.DataFrame(
        {
            "시가": [100.0, 101.0], "고가": [102.0, 103.0],
            "저가": [99.0, 100.0], "종가": [101.0, 102.0],
            "거래량": [10, 11], "거래대금": [1000, 1100],
            "상장시가총액": [10000, 11000],
        },
        index=pd.to_datetime(["2010-01-04", "2010-01-05"]),
    )

    class Stock:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def get_index_ohlcv(self, start: str, end: str, ticker: str):
            self.calls.append((start, end, ticker))
            return raw

    stock = Stock()
    receipt = backfill_kospi200_it_history(
        tmp_path,
        start_date="2010-01-04",
        end_date="2010-01-05",
        confirm_live=True,
        stock_module=stock,
        now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert stock.calls == [("20100104", "20100105", "1155")]
    assert receipt["provider_calls"] == 1
    assert receipt["inserted_rows"] == 2
    stored = pd.concat([
        pd.read_parquet(path)
        for path in (tmp_path / "data/normalized/kr_index_daily").rglob("data.parquet")
    ], ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    validate_registered_kr_index_daily(stored)
    assert stored["symbol"].tolist() == ["KOSPI200_IT", "KOSPI200_IT"]


def test_kr_etf_lane_dry_run_is_registered_and_network_free(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler, "run_kr_etf_scheduler_lane",
        lambda *_args, **_kwargs: pytest.fail("dry-run entered ETF provider operation"),
    )

    result = run_lane(
        tmp_path, "KR_ETF_PRICE_DAILY", as_of=AS_OF, dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["automation_dataset_ids"] == [
        "kr_etf_master", "kr_etf_price_daily",
    ]
    assert result["phase_targets"] == {"kr_etf_prices": "2026-08-18"}
    assert result["provider_availability_policies"] == {
        "kr_etf_prices": "KRX_POST_CLOSE_2030",
    }


def test_equity_investor_flow_dry_run_lists_symbols_and_estimated_calls(
    tmp_path: Path, monkeypatch,
) -> None:
    watchlist = tmp_path / "artifacts/local_user/watchlists.json"
    watchlist.parent.mkdir(parents=True)
    watchlist.write_text(json.dumps({
        "schema_version": 1,
        "revision": 1,
        "lists": [{
            "list_id": "favorites", "name": "관심종목",
            "items": [
                {"market": "KOSPI", "symbol": "005930", "name": "삼성전자", "security_type": "보통주"},
                {"market": "KOSDAQ", "symbol": "000660", "name": "테스트우", "security_type": "우선주"},
                {"market": "KRX", "symbol": "123320", "name": "ETF", "security_type": "ETF"},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        scheduler, "run_kr_equity_investor_flow_scheduler_lane",
        lambda *_args, **_kwargs: pytest.fail("dry-run entered investor-flow provider operation"),
    )

    result = run_lane(
        tmp_path, "KR_EQUITY_INVESTOR_FLOW_DAILY",
        as_of=datetime.fromisoformat("2026-09-04T20:30:00+09:00"),
        dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["symbols"] == ["000660", "005930"]
    assert result["planned_symbols"] == ["000660", "005930"]
    assert result["estimated_calls"] == 2
    assert result["symbol_cap"] == 40
    assert result["phase_targets"] == {"kr_equity_investor_flow": "2026-09-04"}
    assert result["provider_availability_policies"] == {
        "kr_equity_investor_flow": "KRX_POST_CLOSE_2030",
    }


def test_equity_investor_flow_phase_projects_provider_lag_and_call_count(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler, "run_kr_equity_investor_flow_scheduler_lane",
        lambda *_args, **_kwargs: {
            "status": "EXPECTED_PROVIDER_LAG",
            "api_calls": 2,
            "symbols": ["000660", "005930"],
            "planned_symbols": ["000660", "005930"],
            "estimated_calls": 2,
        },
    )

    result = scheduler._run_kr_equity_investor_flow_phase(
        tmp_path, "kr_equity_investor_flow", date(2026, 9, 4),
    )

    assert result["status"] == "EXPECTED_PROVIDER_LAG"
    assert result["http_calls"] == 2
    assert result["reason"] == "EXPECTED_PROVIDER_LAG"


def test_kr_etf_provider_lag_remains_an_expected_lane_outcome(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler, "run_kr_etf_scheduler_lane",
        lambda *_args, **_kwargs: {
            "schema_version": 1,
            "lane": "KR_ETF_PRICE_DAILY",
            "status": "EXPECTED_PROVIDER_LAG",
            "target_session": "2026-08-18",
            "latest_before": {"123320": "2026-08-17"},
            "latest_after": {"123320": "2026-08-17"},
            "api_calls": 3,
            "retry_count": 0,
            "predictive_use": False,
            "symbols": ["123320"],
            "provider_gap_dates": {"123320": ["2026-08-18"]},
        },
    )

    result = scheduler._run_kr_etf_price_phase(
        tmp_path, "kr_etf_prices", date(2026, 8, 18),
    )

    assert result["status"] == "EXPECTED_PROVIDER_LAG"
    assert result["http_calls"] == 3
    assert result["reason"] == "EXPECTED_PROVIDER_LAG"


def test_kr_etf_current_price_lane_refreshes_stale_master_with_one_list_call(
    tmp_path: Path, monkeypatch,
) -> None:
    master_root = tmp_path / "data/normalized/kr_etf_master"
    write_dataset_atomic(
        normalize_master({"123320": "TIGER 레버리지"}, source_date=date(2026, 9, 2)),
        master_root, KR_ETF_MASTER, validate_kr_etf_master,
    )

    class OneCallProvider:
        request_count = 0

        def get_etf_ticker_list(self, source_date):
            assert source_date == date(2026, 9, 3)
            self.request_count += 1
            return ("123320", "243880")

    provider = OneCallProvider()
    monkeypatch.setattr(scheduler, "PykrxEtfClient", lambda **_kwargs: provider)
    monkeypatch.setattr(
        scheduler, "run_kr_etf_scheduler_lane",
        lambda *_args, **_kwargs: {
            "status": "ALREADY_CURRENT", "api_calls": 0,
            "latest_before": {"123320": "2026-09-03"},
            "latest_after": {"123320": "2026-09-03"},
            "symbols": ["123320"],
        },
    )

    result = scheduler._run_kr_etf_price_phase(
        tmp_path, "kr_etf_prices", date(2026, 9, 3),
    )

    refreshed = read_dataset(master_root, KR_ETF_MASTER, validate_kr_etf_master)
    assert provider.request_count == 1
    assert result["status"] == "COMPLETE"
    assert result["http_calls"] == 1
    assert result["reason"] == "MASTER_REFRESHED"
    assert refreshed["source_date"].astype(str).unique().tolist() == ["2026-09-03"]
    assert (tmp_path / result["master_refresh"]["landing"]).is_file()


def test_provisional_equity_lane_dry_run_is_registered_and_network_free(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler,
        "run_kr_equity_provisional_daily",
        lambda *_args, **_kwargs: pytest.fail("dry-run entered provisional provider operation"),
    )

    result = run_lane(
        tmp_path,
        "KR_EQUITY_PROVISIONAL_DAILY",
        as_of=datetime(2026, 9, 3, 11, 30, tzinfo=timezone.utc),
        dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["phase_targets"] == {"kr_equity_provisional": "2026-09-03"}
    assert result["automation_dataset_ids"] == [
        "kr_equity_price_provisional_daily",
    ]
    assert result["provider_availability_policies"] == {
        "kr_equity_provisional": "KRX_POST_CLOSE_2030",
    }


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


def test_bok_fx_lane_dry_run_is_registered_and_network_free(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduler, "run_bok_fx_daily_lane",
        lambda *_args, **_kwargs: pytest.fail("dry-run entered BOK provider operation"),
    )

    result = run_lane(
        tmp_path, "BOK_FX_DAILY",
        as_of=datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc),
        dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["phase_targets"] == {"bok_fx": "2026-09-03"}
    assert result["automation_dataset_ids"] == ["bok_ecos_usd_krw_daily"]
    assert result["provider_availability_policies"] == {
        "bok_fx": "BOK_ECOS_FX_DAILY_1600_KST",
    }


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


def _write_yahoo_index_landing(
    tmp_path: Path, *, rows: list[dict[str, object]], symbol: str = "SP500",
) -> Path:
    ticker = {
        "SP500": "^GSPC", "NASDAQ_COMPOSITE": "^IXIC", "NASDAQ100": "^NDX",
        "SOX": "^SOX", "DOW_JONES": "^DJI", "DOLLAR_INDEX": "DX-Y.NYB",
    }[symbol]
    timestamps = [int(pd.Timestamp(row["date"], tz="America/New_York").timestamp()) for row in rows]
    body = json.dumps({
        "chart": {"error": None, "result": [{
            "meta": {"symbol": ticker, "instrumentType": "INDEX", "dataGranularity": "1d"},
            "timestamp": timestamps,
            "indicators": {"quote": [{
                column: [row[column] for row in rows]
                for column in ("open", "high", "low", "close", "volume")
            }]},
        }]},
    }, separators=(",", ":")).encode()
    call = tmp_path / "landing/call.json"
    call.parent.mkdir(parents=True)
    call.with_name("response.body").write_bytes(body)
    call.write_text(json.dumps({
        "request_parameters": {"symbol": symbol},
        "response_body_sha256": hashlib.sha256(body).hexdigest(),
    }), encoding="utf-8")
    return call


def _retained_index_frame() -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2026-08-28", "symbol": "SP500", "source_ticker": "^GSPC",
        "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10,
    }])


def _rewrite_yahoo_landing(call: Path, transform) -> None:
    body_path = call.with_name("response.body")
    payload = json.loads(body_path.read_text(encoding="utf-8"))
    transform(payload)
    body = json.dumps(payload, separators=(",", ":")).encode()
    body_path.write_bytes(body)
    record = json.loads(call.read_text(encoding="utf-8"))
    record["response_body_sha256"] = hashlib.sha256(body).hexdigest()
    call.write_text(json.dumps(record), encoding="utf-8")


def test_global_index_landing_replay_preserves_valid_existing_row_and_accepts_target(
    tmp_path: Path,
) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": None, "high": None, "low": None, "close": None, "volume": None},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])

    recovered = scheduler._replay_global_index_landing(
        _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
    )

    retained = recovered.loc[recovered["date"].eq("2026-08-28")].iloc[0]
    assert retained[["open", "high", "low", "close", "volume"]].tolist() == [
        100.0, 103.0, 99.0, 102.0, 10,
    ]
    assert recovered["date"].tolist() == ["2026-08-28", "2026-08-31"]


def test_global_index_landing_replay_rejects_partial_null_row(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": None, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])

    with pytest.raises(ProviderSchedulerError, match="partial-null"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_finite_revision(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 101.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])

    with pytest.raises(ProviderSchedulerError, match="finite revision"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_accepts_fill_for_retained_null_field(
    tmp_path: Path,
) -> None:
    existing = _retained_index_frame()
    existing["volume"] = existing["volume"].astype("Int64")
    existing.loc[:, "volume"] = pd.NA
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])

    recovered = scheduler._replay_global_index_landing(
        existing, call, symbol="SP500", target=date(2026, 8, 31),
    )

    assert recovered.loc[recovered["date"].eq("2026-08-28"), "volume"].item() == 10


def test_global_index_landing_replay_rejects_hash_mismatch(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])
    record = json.loads(call.read_text(encoding="utf-8"))
    record["response_body_sha256"] = "0" * 64
    call.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ProviderSchedulerError, match="identity/hash"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_request_identity_mismatch(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])
    record = json.loads(call.read_text(encoding="utf-8"))
    record["request_parameters"]["symbol"] = "NASDAQ100"
    call.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ProviderSchedulerError, match="identity/hash"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_provider_identity_mismatch(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])
    _rewrite_yahoo_landing(
        call, lambda payload: payload["chart"]["result"][0]["meta"].update(symbol="^DJI"),
    )

    with pytest.raises(ProviderSchedulerError, match="payload differs"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_null_target(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": None, "high": None, "low": None, "close": None, "volume": None},
    ])

    with pytest.raises(ProviderSchedulerError, match="null row has no retained value"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_duplicate_date(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
        {"date": "2026-08-31", "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 11},
    ])

    with pytest.raises(ProviderSchedulerError, match="duplicate dates"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_infinite_value(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
        {"date": "2026-08-31", "open": 104.0, "high": float("inf"), "low": 103.0, "close": 105.0, "volume": 11},
    ])

    with pytest.raises(ProviderSchedulerError, match="non-finite row"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_landing_replay_rejects_missing_target(tmp_path: Path) -> None:
    call = _write_yahoo_index_landing(tmp_path, rows=[
        {"date": "2026-08-28", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 10},
    ])

    with pytest.raises(ProviderSchedulerError, match="missed target date"):
        scheduler._replay_global_index_landing(
            _retained_index_frame(), call, symbol="SP500", target=date(2026, 8, 31),
        )


def test_global_index_phase_bad_new_symbol_does_not_block_other_landing_promotion(
    tmp_path: Path, monkeypatch,
) -> None:
    tickers = {"SP500": "^GSPC", "NASDAQ_COMPOSITE": "^IXIC", "NASDAQ100": "^NDX"}
    stored = pd.concat([
        _retained_index_frame().assign(symbol=symbol, source_ticker=ticker)
        for symbol, ticker in tickers.items()
    ], ignore_index=True)
    stored["date"] = "2026-08-31"
    module = SimpleNamespace()
    candidates = {}

    def original_fetch(symbol, start, end, *, session, capture_root):
        call = _write_yahoo_index_landing(capture_root / symbol, symbol=symbol, rows=[
            {"date": end.isoformat(), "open": 104.0, "high": 106.0,
             "low": 103.0, "close": 105.0, "volume": 11},
        ])
        if symbol == "SOX":
            _rewrite_yahoo_landing(
                call,
                lambda payload: payload["chart"]["result"][0]["meta"].update(
                    instrumentType="ETF"
                ),
            )
            raise RuntimeError("Yahoo index identity or granularity differs")
        return pd.DataFrame([{
            "date": end.isoformat(), "symbol": symbol,
            "source_ticker": {"DOW_JONES": "^DJI", "DOLLAR_INDEX": "DX-Y.NYB"}.get(symbol, "^DJI"),
            "open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0,
            "volume": 11,
        }], columns=scheduler.GLOBAL_INDEX_PRICE_DAILY.column_names)

    def prepare_phase(project_root, phase, *, end, start, symbols):
        assert len(symbols) == 1 and start == date(2025, 8, 31)
        symbol = symbols[0]
        frame = module.fetch_global_index(
            symbol, start, end, session=object(),
            capture_root=project_root / "captures" / symbol,
        )
        candidates[symbol] = frame
        return {
            "status": "CANDIDATE_REVIEW_REQUIRED", "phase": phase,
            "max_http_calls": 1, "http_calls": 1, "retry_count": 0,
            "http_statuses": [200], "run_id": f"{symbol.lower()}-run",
            "approval_digest": "approved",
            "revision_report": {
                symbol: {
                    "source_omitted_existing_dates": 0,
                    "finite_to_null_cells": 0,
                    "inserted_rows": 1,
                }
            },
        }

    promoted = []

    def promote_phase(project_root, checkpoint_path, *, approval_digest):
        nonlocal stored
        symbol = checkpoint_path.parent.name.removesuffix("-run").upper()
        if symbol in {"DOW_JONES", "DOLLAR_INDEX"}:
            stored = pd.concat([stored, candidates[symbol]], ignore_index=True)
        promoted.append((checkpoint_path, approval_digest))
        return {"status": "PROMOTED"}

    module.fetch_global_index = original_fetch
    module.prepare_phase = prepare_phase
    module.promote_phase = promote_phase
    monkeypatch.setattr(scheduler, "read_dataset", lambda *args: stored.copy())
    monkeypatch.setattr(scheduler, "_load_refresh_module", lambda _root: module)

    result = scheduler._run_global_index_phase(
        tmp_path, "global_indices", date(2026, 8, 31),
    )

    assert result["status"] == "DEGRADED_SYMBOL_FAILURES_PRESERVED"
    assert set(result["promoted_symbols"]) == {"DOLLAR_INDEX", "DOW_JONES"}
    assert set(result["failed_symbols"]) == {"SOX"}
    assert set(stored["symbol"]) == {*tickers, "DOW_JONES", "DOLLAR_INDEX"}
    assert len(list((tmp_path / "captures").rglob("call.json"))) == 3
    assert module.fetch_global_index is original_fetch
    assert sorted(promoted) == sorted([
        (tmp_path / "data/state/global_current_refresh/dollar_index-run/checkpoint.json", "approved"),
        (tmp_path / "data/state/global_current_refresh/dow_jones-run/checkpoint.json", "approved"),
    ])


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
