from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.orchestration.daily_operations import (
    DAILY_LANE_READINESS, DATASET_OPERATIONS, DATASET_UNIVERSE, DailyRunLock,
)
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.expected_latest import resolve_expected_latest
from stock_data.orchestration.canonical_equity_daily import run_canonical_equity_catchup
from stock_data.orchestration.kospi200_constituent_breadth import (
    latest_accepted_canonical_target,
    run_kospi200_constituent_breadth_daily,
)
from stock_data.orchestration.market_daily_incremental import (
    execute_data_go_kr_daily, execute_short_selling_daily,
    plan_data_go_kr_daily, plan_short_selling_daily,
    short_selling_raw_call_budget,
)
from stock_data.pipelines.short_selling_backfill import (
    AppendOnlyRedactedLedger, AuthenticatedPykrxRawClient, ConservativeThrottle,
)
from stock_data.orchestration.kr_index_daily_incremental import (
    read_registered_kr_index_daily,
    run_atomic_lane_append,
)
from stock_data.orchestration.kr_index_daily_live import capture_one_finalized_date
from stock_data.orchestration.kr_index_fundamental_daily import (
    run_index_fundamental_daily,
)
from stock_data.orchestration.kr_fundamentals_quarterly import (
    plan_weekly_fundamentals_refresh,
    run_weekly_fundamentals_refresh,
)
from stock_data.orchestration.kr_etf_daily import run_kr_etf_scheduler_lane
from stock_data.providers.pykrx.kr_etf import PykrxEtfClient
from stock_data.orchestration.kr_equity_provisional_daily import (
    run_kr_equity_provisional_daily,
)
from stock_data.orchestration.bok_ecos_fx_daily import run_daily_lane as run_bok_fx_daily_lane
from stock_data.orchestration.toss_market_investor_daily import (
    is_toss_market_investor_date_complete, refresh_toss_market_investor_daily,
)
from stock_data.orchestration.toss_kr_treasury_daily import (
    refresh_toss_kr_treasury_daily,
)
from stock_data.providers.tossinvest import TossInvestClient
from stock_data.contracts.kr_index_daily import KR_INDEX_TICKERS
from stock_data.storage.contract_parquet import read_dataset
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.contracts.kr_etf import KR_ETF_MASTER
from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY
from stock_data.contracts.global_etf import (
    GLOBAL_ETF_DAILY_SYMBOLS,
    GLOBAL_ETF_PRICE_DAILY,
)
from stock_data.contracts.data_v1 import (
    KR_STOCK_LENDING_DAILY,
    KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.contracts.kr_short_selling import (
    KR_SHORT_SELLING_BALANCE_DAILY,
    KR_SHORT_SELLING_INVESTOR_DAILY,
)
from stock_data.contracts.global_market import (
    GLOBAL_COMMODITY_FUTURES_DAILY, GLOBAL_INDEX_PRICE_DAILY,
)
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily
from stock_data.validation.global_market import (
    validate_global_commodity_futures, validate_global_etf, validate_global_index,
)
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_etf import validate_kr_etf_master


class ProviderSchedulerError(RuntimeError):
    pass


@dataclass(frozen=True)
class LaneSchedule:
    lane: str
    cadence_group: str
    market: ExchangeMarket
    phases: tuple[str, ...]
    dataset_ids: tuple[str, ...]
    accepted_source: str


LANE_SCHEDULES = MappingProxyType({
    "CANONICAL_EQUITY_DAILY": LaneSchedule(
        lane="CANONICAL_EQUITY_DAILY",
        cadence_group="KR_D_PLUS_1_1300",
        market=ExchangeMarket.KR,
        phases=("canonical_equity",),
        dataset_ids=(
            "kr_equity_canonical_universe_daily", "kr_equity_price_daily",
            "kr_equity_market_cap_daily", "kr_equity_universe_daily",
            "kr_market_breadth_daily",
        ),
        accepted_source="data.go.kr exact-date price/cap and provider-universe streams",
    ),
    "KR_ETF_PRICE_DAILY": LaneSchedule(
        lane="KR_ETF_PRICE_DAILY",
        cadence_group="KR_POST_CLOSE_2030",
        market=ExchangeMarket.KR,
        phases=("kr_etf_prices",),
        dataset_ids=("kr_etf_master", "kr_etf_price_daily"),
        accepted_source="KRX/pykrx current ETF identity and selected-symbol OHLCV",
    ),
    "KOSPI200_BREADTH_DAILY": LaneSchedule(
        lane="KOSPI200_BREADTH_DAILY",
        cadence_group="KR_D_PLUS_1_1300",
        market=ExchangeMarket.KR,
        phases=("kospi200_breadth",),
        dataset_ids=(
            "kr_index_constituent_daily",
            "kr_kospi200_constituent_price_daily",
            "kr_kospi200_breadth_daily",
        ),
        accepted_source=(
            "KRX MDCSTAT00601 exact-date membership plus accepted canonical equity prices"
        ),
    ),
    "FRED_DAILY": LaneSchedule(
        lane="FRED_DAILY",
        cadence_group="GLOBAL_DAILY_FINAL",
        market=ExchangeMarket.US,
        phases=("fred_yields", "fred_fx", "fred_vix"),
        dataset_ids=(
            "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
            "us_treasury_spread_daily",
        ),
        accepted_source="FRED fredgraph CSV",
    ),
    "KR_EQUITY_PROVISIONAL_DAILY": LaneSchedule(
        lane="KR_EQUITY_PROVISIONAL_DAILY",
        cadence_group="KR_POST_CLOSE_2030",
        market=ExchangeMarket.KR,
        phases=("kr_equity_provisional",),
        dataset_ids=("kr_equity_price_provisional_daily",),
        accepted_source=(
            "KRX/pykrx stock.get_market_ohlcv_by_ticker exact-date KOSPI/KOSDAQ"
        ),
    ),
    "BOK_FX_DAILY": LaneSchedule(
        lane="BOK_FX_DAILY",
        cadence_group="KR_POST_CLOSE_2030",
        market=ExchangeMarket.KR,
        phases=("bok_fx",),
        dataset_ids=("bok_ecos_usd_krw_daily",),
        accepted_source="BOK ECOS StatisticSearch 731Y001 item 0000001",
    ),
    "LENDING_DAILY": LaneSchedule(
        lane="LENDING_DAILY",
        cadence_group="KR_D_PLUS_1_1300",
        market=ExchangeMarket.KR,
        phases=("detail", "market", "participant"),
        dataset_ids=(
            "kr_stock_lending_daily", "kr_stock_lending_market_daily",
            "kr_stock_lending_participant_daily",
        ),
        accepted_source="data.go.kr GetStocLendBorrInfoService_V2",
    ),
    "SHORT_SELLING_DAILY": LaneSchedule(
        lane="SHORT_SELLING_DAILY",
        cadence_group="KR_NEXT_XKRX_SESSION",
        market=ExchangeMarket.KR,
        phases=("short_trading",),
        dataset_ids=("kr_short_selling_trading_daily",),
        accepted_source="authenticated KRX/pykrx MDCSTAT30101",
    ),
    "SHORT_SELLING_BALANCE_DAILY": LaneSchedule(
        lane="SHORT_SELLING_BALANCE_DAILY",
        cadence_group="KR_T_PLUS_2_POST_CLOSE_1810",
        market=ExchangeMarket.KR,
        phases=("short_balance",),
        dataset_ids=("kr_short_selling_balance_daily",),
        accepted_source="authenticated KRX/pykrx MDCSTAT30501 as retrieved; revisions possible",
    ),
    "SHORT_SELLING_INVESTOR_DAILY": LaneSchedule(
        lane="SHORT_SELLING_INVESTOR_DAILY",
        cadence_group="KR_SAME_DAY_POST_CLOSE_1810",
        market=ExchangeMarket.KR,
        phases=("short_investor",),
        dataset_ids=("kr_short_selling_investor_daily",),
        accepted_source="authenticated KRX/pykrx MDCSTAT30301 as retrieved",
    ),
    "VKOSPI_DAILY": LaneSchedule(
        lane="VKOSPI_DAILY",
        cadence_group="KR_POST_CLOSE_1830",
        market=ExchangeMarket.KR,
        phases=("vkospi",),
        dataset_ids=("kr_vkospi_daily",),
        accepted_source="KRX MDCSTAT01201:1300",
    ),
    "KR_INDEX_DAILY": LaneSchedule(
        lane="KR_INDEX_DAILY", cadence_group="KR_POST_CLOSE_1830",
        market=ExchangeMarket.KR, phases=("indices",),
        dataset_ids=("kr_index_daily", "kr_kospi200_index_daily"),
        accepted_source="KRX/pykrx exact-date index OHLCV",
    ),
    "KR_INDEX_FUNDAMENTAL_DAILY": LaneSchedule(
        lane="KR_INDEX_FUNDAMENTAL_DAILY", cadence_group="KR_PRIOR_COMPLETED_SESSION",
        market=ExchangeMarket.KR, phases=("index_fundamentals",),
        dataset_ids=("kr_index_fundamental_daily",),
        accepted_source="KRX MDCSTAT00702 exact 1001/2001 range responses",
    ),
    "KR_FUNDAMENTALS_WEEKLY": LaneSchedule(
        lane="KR_FUNDAMENTALS_WEEKLY",
        cadence_group="KR_LAST_XKRX_SESSION_OF_ISO_WEEK_2030",
        market=ExchangeMarket.KR,
        phases=("fundamentals_weekly",),
        dataset_ids=("kr_corp_code_map", "kr_fundamentals_quarterly"),
        accepted_source="OpenDART corpCode.xml and fnlttSinglAcntAll.json",
    ),
    "MARKET_INVESTOR_DAILY": LaneSchedule(
        lane="MARKET_INVESTOR_DAILY", cadence_group="KR_POST_CLOSE_1830",
        market=ExchangeMarket.KR, phases=("market_investor",),
        dataset_ids=(
            "kr_market_investor_trading_daily",
            "kr_market_investor_net_purchase_bridge_daily",
        ),
        accepted_source="Toss Invest KOSPI/KOSDAQ market investor-trading daily aggregates",
    ),
    "TOSS_KR_TREASURY_DAILY": LaneSchedule(
        lane="TOSS_KR_TREASURY_DAILY",
        cadence_group="KR_T_PLUS_1_AS_RETRIEVED",
        market=ExchangeMarket.KR,
        phases=("toss_kr_treasury",),
        dataset_ids=("kr_treasury_yield_daily",),
        accepted_source="Toss Invest six Korean government-bond daily OHLC series",
    ),
    "GLOBAL_INDEX_DAILY": LaneSchedule(
        lane="GLOBAL_INDEX_DAILY", cadence_group="GLOBAL_DAILY_FINAL",
        market=ExchangeMarket.US, phases=("global_indices",),
        dataset_ids=("global_index_price_daily",),
        accepted_source=(
            "Yahoo chart API: registered SP500, NASDAQ_COMPOSITE, NASDAQ100, SOX, "
            "and DOW_JONES"
        ),
    ),
    "GLOBAL_ETF_DAILY": LaneSchedule(
        lane="GLOBAL_ETF_DAILY", cadence_group="GLOBAL_DAILY_FINAL",
        market=ExchangeMarket.US, phases=("global_etfs",),
        dataset_ids=("global_etf_price_daily",),
        accepted_source="Yahoo chart API: contract-registered ETFs",
    ),
    "GLOBAL_COMMODITY_DAILY": LaneSchedule(
        lane="GLOBAL_COMMODITY_DAILY", cadence_group="GLOBAL_DAILY_FINAL",
        market=ExchangeMarket.US, phases=("dashboard_futures",),
        dataset_ids=("global_commodity_futures_daily",),
        accepted_source=(
            "Yahoo chart API: NQ=F, GC=F, CL=F, ES=F, YM=F, and DX=F "
            "completed daily bars"
        ),
    ),
})


PhaseRunner = Callable[[Path, str, object], dict[str, object]]


def _run_canonical_equity_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "canonical_equity" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid canonical-equity scheduler phase")
    result = run_canonical_equity_catchup(
        project_root,
        available_through=target,
        max_sessions=3,
        max_api_calls=6,
        max_elapsed_seconds=600.0,
    )
    status = result.status
    if status == "NOOP_IDEMPOTENT":
        phase_status = "NOOP_IDEMPOTENT"
    elif status.startswith(("DEGRADED_", "FAILED_")):
        phase_status = status
    else:
        phase_status = "COMPLETE"
    return {
        "status": phase_status,
        "http_calls": result.api_calls,
        "run_id": result.run_id,
        "latest_before": result.latest_before.isoformat(),
        "latest_after": result.latest_after.isoformat(),
        "reason": result.reason,
        "selected_dates": [item.isoformat() for item in result.selected_dates],
        "attempted_dates": [item.isoformat() for item in result.attempted_dates],
        "accepted_dates": [item.isoformat() for item in result.accepted_dates],
        "run_ids": list(result.run_ids),
    }


def _run_kospi200_breadth_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "kospi200_breadth" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid KOSPI200 breadth scheduler phase")
    result = run_kospi200_constituent_breadth_daily(
        project_root, market_date=target,
    )
    return {
        "status": (
            "NOOP_IDEMPOTENT"
            if result.status == "NOOP_ALREADY_SUCCEEDED"
            else "COMPLETE"
        ),
        "http_calls": result.api_calls,
        "run_id": None,
        "latest_after": result.market_date,
        "reason": "COMPLETE_EXACT_DATE_KOSPI200_SCOPE",
    }


def _refresh_kr_etf_master_once(
    project_root: Path, target: date,
) -> dict[str, object]:
    """Refresh retained ETF membership date with one Landing-first ticker-list call."""

    master_root = project_root / "data/normalized/kr_etf_master"
    master = read_dataset(master_root, KR_ETF_MASTER, validate_kr_etf_master)
    latest = pd.to_datetime(master["source_date"], errors="raise").max().date()
    if latest >= target:
        return {"status": "MASTER_ALREADY_CURRENT", "api_calls": 0}

    provider = PykrxEtfClient(manual=True, requested_days=1)
    listed = tuple(str(value).strip() for value in provider.get_etf_ticker_list(target))
    if provider.request_count != 1 or not listed or len(listed) != len(set(listed)):
        raise ProviderSchedulerError("Korean ETF master ticker-list call is invalid")
    missing = sorted(set(master["symbol"].astype(str)) - set(listed))
    if missing:
        raise ProviderSchedulerError(
            f"retained Korean ETF identity is absent from target list: {missing}"
        )

    run_id = f"master-{target:%Y%m%d}-{uuid4().hex}"
    landing = (
        project_root / "data/landing/pykrx/kr_etf_daily/master_refresh"
        / f"date={target:%Y%m%d}" / f"run={run_id}" / "ticker_list.json"
    )
    body = (json.dumps(listed, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    landing.parent.mkdir(parents=True, exist_ok=False)
    temporary = landing.with_suffix(".json.tmp")
    temporary.write_bytes(body)
    temporary.replace(landing)
    if landing.read_bytes() != body or tuple(json.loads(body)) != listed:
        raise ProviderSchedulerError("Korean ETF master Landing read-back differs")

    refreshed = master.copy(deep=True)
    refreshed["source_date"] = target
    validate_kr_etf_master(refreshed)
    write_dataset_atomic(
        refreshed, master_root, KR_ETF_MASTER, validate_kr_etf_master,
    )
    read_back = read_dataset(master_root, KR_ETF_MASTER, validate_kr_etf_master)
    try:
        expected_read_back = refreshed.reset_index(drop=True).copy()
        actual_read_back = read_back.reset_index(drop=True).copy()
        expected_read_back["source_date"] = pd.to_datetime(
            expected_read_back["source_date"], errors="raise",
        )
        actual_read_back["source_date"] = pd.to_datetime(
            actual_read_back["source_date"], errors="raise",
        )
        pd.testing.assert_frame_equal(
            expected_read_back, actual_read_back,
            check_dtype=False,
        )
    except AssertionError as error:
        raise ProviderSchedulerError("Korean ETF master read-back differs") from error
    return {
        "status": "MASTER_REFRESHED",
        "api_calls": 1,
        "latest_before": latest.isoformat(),
        "latest_after": target.isoformat(),
        "landing": landing.relative_to(project_root).as_posix(),
    }


def _run_kr_etf_price_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "kr_etf_prices" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid Korean ETF price scheduler phase")
    result = run_kr_etf_scheduler_lane(project_root, target_session=target)
    master_refresh = (
        _refresh_kr_etf_master_once(project_root, target)
        if result.get("status") == "ALREADY_CURRENT"
        else None
    )
    total_calls = int(result.get("api_calls", 0) or 0) + int(
        (master_refresh or {}).get("api_calls", 0) or 0
    )
    phase_status = {
        "ALREADY_CURRENT": (
            "COMPLETE"
            if master_refresh and master_refresh["status"] == "MASTER_REFRESHED"
            else "NOOP_IDEMPOTENT"
        ),
        "NO_SYMBOLS_CONFIGURED": "NOOP_IDEMPOTENT",
        "UPDATED": "COMPLETE",
    }.get(str(result["status"]), str(result["status"]))
    return {
        **result,
        "status": phase_status,
        "api_calls": total_calls,
        "http_calls": total_calls,
        "run_id": None,
        "reason": (
            str(master_refresh["status"])
            if master_refresh is not None else str(result["status"])
        ),
        "master_refresh": master_refresh,
    }


def _load_refresh_module(project_root: Path):
    path = project_root / "scripts/manual/collect/refresh_global_current.py"
    spec = importlib.util.spec_from_file_location("stock_data_global_current_refresh", path)
    if spec is None or spec.loader is None:
        raise ProviderSchedulerError(f"cannot load accepted operation: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_vkospi_module(project_root: Path):
    path = project_root / "scripts/manual/collect/collect_krx_vkospi_daily.py"
    spec = importlib.util.spec_from_file_location("stock_data_vkospi_daily", path)
    if spec is None or spec.loader is None:
        raise ProviderSchedulerError(f"cannot load accepted operation: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_accepted_phase(project_root: Path, phase: str, target: object) -> dict[str, object]:
    if phase not in {"fred_yields", "fred_fx", "fred_vix"}:
        raise ProviderSchedulerError("scheduler may only execute explicitly accepted FRED phases")
    module = _load_refresh_module(project_root)
    checkpoint = module.prepare_phase(project_root, phase, end=target)
    if checkpoint.get("status") == "NOOP_IDEMPOTENT":
        return checkpoint
    if checkpoint.get("status") != "CANDIDATE_REVIEW_REQUIRED":
        raise ProviderSchedulerError(f"unexpected phase status: {checkpoint.get('status')}")
    allowed_calls = {1, 3} if phase == "fred_vix" else {int(checkpoint.get("max_http_calls", -1))}
    if (
        checkpoint.get("phase") != phase
        or checkpoint.get("retry_count") != 0
        or checkpoint.get("http_calls") not in allowed_calls
        or checkpoint.get("http_statuses") != [200] * int(checkpoint["http_calls"])
    ):
        raise ProviderSchedulerError("accepted-source checkpoint validation failed")
    digest = checkpoint.get("approval_digest")
    run_id = checkpoint.get("run_id")
    if not isinstance(digest, str) or not isinstance(run_id, str):
        raise ProviderSchedulerError("accepted-source checkpoint identity is incomplete")
    checkpoint_path = (
        project_root / "data/state/global_current_refresh" / run_id / "checkpoint.json"
    )
    promoted = module.promote_phase(project_root, checkpoint_path, approval_digest=digest)
    if promoted.get("status") != "PROMOTED":
        raise ProviderSchedulerError(f"promotion did not commit: {promoted.get('status')}")
    return promoted


def _run_bok_fx_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "bok_fx" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid BOK ECOS FX scheduler phase")
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", override=False)
    result = run_bok_fx_daily_lane(
        project_root,
        target=target,
        api_key=os.environ.get("BOK_ECOS_API_KEY", ""),
    )
    return {
        **result,
        "http_calls": int(result.get("api_calls", 0) or 0),
    }


def _run_kr_equity_provisional_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "kr_equity_provisional" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid provisional Korean equity scheduler phase")
    result = run_kr_equity_provisional_daily(project_root, target_session=target)
    phase_status = {
        "ALREADY_CURRENT": "NOOP_IDEMPOTENT",
        "UPDATED": "COMPLETE",
    }.get(str(result["status"]), str(result["status"]))
    return {
        **result,
        "status": phase_status,
        "http_calls": result["api_calls"],
        "reason": str(result["status"]),
    }


def _run_lending_phase(project_root: Path, phase: str, target: object) -> dict[str, object]:
    if phase not in {"detail", "market", "participant"} or not isinstance(target, date):
        raise ProviderSchedulerError("invalid lending scheduler phase")
    plan = plan_data_go_kr_daily(
        project_root=project_root, dataset=phase, market_date=target,
        latest_finalized_market_date=target, accepted_market_dates=(target,),
        operation_reviewed=True, max_api_calls=1,
    )
    result = execute_data_go_kr_daily(plan, project_root=project_root)
    contracts = {
        "detail": KR_STOCK_LENDING_DAILY,
        "market": KR_STOCK_LENDING_MARKET_DAILY,
        "participant": KR_STOCK_LENDING_PARTICIPANT_DAILY,
    }
    contract = contracts[phase]
    frame = read_dataset(
        project_root / "data/normalized" / contract.name / f"year={target.year}",
        contract,
        lambda candidate: validate_data_v1(candidate, contract, allow_empty=False),
    )
    latest_after = pd.to_datetime(frame["date"], errors="raise").max().date()
    if latest_after < target:
        raise ProviderSchedulerError("lending promotion did not reach the expected date")
    return {
        "status": "NOOP_IDEMPOTENT" if plan.action == "NOOP_IDEMPOTENT" else result.status,
        "http_calls": result.api_calls,
        "run_id": None,
        "latest_after": latest_after.isoformat(),
    }


def _run_short_selling_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    datasets = {
        "short_trading": "trading",
        "short_balance": "balance",
        "short_investor": "investor",
    }
    if phase not in datasets or not isinstance(target, date):
        raise ProviderSchedulerError("invalid short-selling scheduler phase")
    dataset = datasets[phase]
    targets = [target]
    if dataset in {"balance", "investor"}:
        contract = (
            KR_SHORT_SELLING_BALANCE_DAILY
            if dataset == "balance"
            else KR_SHORT_SELLING_INVESTOR_DAILY
        )
        root = project_root / "data/normalized" / contract.name
        if root.exists():
            retained = read_dataset(
                root,
                contract,
                lambda frame: validate_data_v1(frame, contract, allow_empty=False),
            )
            retained_latest = pd.to_datetime(retained["date"], errors="raise").max().date()
            if dataset == "balance":
                checkpoint_path = (
                    project_root / "data/state/kr_short_selling_balance_daily_v2.json"
                )
                if checkpoint_path.exists():
                    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    reason = str(checkpoint.get("stop_reason", ""))
                    prefix = "ANOMALOUS_VALID_EMPTY:"
                    if reason.startswith(prefix):
                        token = reason.removeprefix(prefix).split("_", 1)[0]
                        valid_empty_date = datetime.strptime(token, "%Y%m%d").date()
                        retained_latest = max(retained_latest, valid_empty_date)
            calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
            if retained_latest < target:
                start = calendar.next_trading_day(retained_latest)
                targets = list(calendar.sessions_in_range(start, target))
            else:
                targets = [target]
    attempted: list[str] = []
    total_raw_calls = 0
    latest_after: date | None = None
    for run_target in targets[:3]:
        plan = plan_short_selling_daily(
            project_root=project_root,
            dataset=dataset,
            market_date=run_target,
            latest_finalized_market_date=run_target,
            accepted_market_dates=(run_target,),
            operation_reviewed=True,
            valid_empty_successor_reviewed=dataset == "balance",
        )
        expected_business_calls = {"trading": 2, "balance": 2, "investor": 4}[dataset]
        if plan.estimated_api_calls not in {0, expected_business_calls}:
            raise ProviderSchedulerError("short-selling plan has an unexpected exact-date scope count")

        def client_factory(ledger: AppendOnlyRedactedLedger):
            return AuthenticatedPykrxRawClient(
                project_root=project_root,
                ledger=ledger,
                max_raw_calls=short_selling_raw_call_budget(
                    dataset, plan.estimated_api_calls
                ),
            )

        result = execute_short_selling_daily(
            plan,
            project_root=project_root,
            client_factory=client_factory,
            throttle=ConservativeThrottle(
                min_interval_seconds=8.0, max_jitter_seconds=2.0
            ),
        )
        attempted.append(run_target.isoformat())
        total_raw_calls += result.raw_http_requests
        latest_after = run_target
    partial = len(targets) > 3
    return {
        "status": (
            "PARTIAL_LIMIT_REACHED"
            if partial
            else "NOOP_IDEMPOTENT"
            if all(value == target and plan.action == "NOOP_IDEMPOTENT" for value in targets)
            else "COMPLETE"
        ),
        "http_calls": total_raw_calls,
        "run_id": None,
        "latest_after": (latest_after or target).isoformat(),
        "attempted_dates": attempted,
        "reason": (
            "TWO_MARKET_ATOMIC"
            if dataset == "trading" and plan.action != "NOOP_IDEMPOTENT"
            else "BOUNDED_CONSECUTIVE_CATCH_UP"
            if partial
            else "EXACT_DATE_AS_RETRIEVED"
            if plan.action != "NOOP_IDEMPOTENT"
            else plan.reason
        ),
    }


def _run_vkospi_phase(project_root: Path, phase: str, target: object) -> dict[str, object]:
    if phase != "vkospi" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid VKOSPI scheduler phase")
    result = _load_vkospi_module(project_root).collect_one_finalized_date(
        project_root, market_date=target, finality_confirmed=True,
    )
    return {
        "status": result["status"],
        "http_calls": int(result.get("business_calls", 0) or 0),
        "run_id": result.get("run_id"),
    }


def _run_index_phase(project_root: Path, phase: str, target: object) -> dict[str, object]:
    if phase != "indices" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid KR index scheduler phase")
    normalized = project_root / "data/normalized"
    kr_root = normalized / "kr_index_daily"
    k200_root = normalized / "kr_kospi200_index_daily"
    kr = read_registered_kr_index_daily(kr_root)
    k200 = read_dataset(k200_root, KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily)
    latest = {
        "kr_index_daily": date.fromisoformat(str(kr["date"].astype(str).max())),
        "kr_kospi200_index_daily": date.fromisoformat(str(k200["date"].astype(str).max())),
    }
    if set(latest.values()) != {next(iter(latest.values()))}:
        raise ProviderSchedulerError(f"KR index split retained latest: {latest}")
    retained = next(iter(latest.values()))
    if retained >= target:
        return {"status": "NOOP_IDEMPOTENT", "http_calls": 0, "run_id": None,
                "latest_before": {key: value.isoformat() for key, value in latest.items()},
                "latest_after": {key: value.isoformat() for key, value in latest.items()},
                "reason": "BOTH_INDEX_DATASETS_CURRENT"}
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    if calendar.next_trading_day(retained) != target:
        raise ProviderSchedulerError(
            f"KR index catch-up is not a one-session append: {retained} -> {target}"
        )
    run_id = f"kr-index-{target:%Y%m%d}-{uuid4().hex}"
    kst = ZoneInfo("Asia/Seoul")
    capture = capture_one_finalized_date(
        target, finalized_at=datetime.combine(target, datetime.min.time(), kst).replace(
            hour=18, minute=30
        ), finality_confirmed=True, run_id=run_id,
        landing_root=project_root / "data/landing/kr_index_daily_live",
        state_root=project_root / "data/state",
    )
    result = run_atomic_lane_append(
        kr_index_landing=capture.kr_index_landing,
        kospi200_landing=capture.kospi200_landing,
        finalized_market_date=target, normalized_root=normalized,
        state_root=project_root / "data/state", run_id=run_id,
        finality_confirmed=True,
    )
    after = {item.dataset: item.retained_latest for item in result.datasets}
    return {"status": result.status, "http_calls": capture.business_calls,
            "run_id": run_id,
            "latest_before": {key: value.isoformat() for key, value in latest.items()},
            "latest_after": after, "reason": "ATOMIC_TWO_DATASET_PROMOTION"}


def _run_index_fundamental_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "index_fundamentals" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid KR index fundamental scheduler phase")
    result = run_index_fundamental_daily(project_root, target_date=target)
    return {
        "status": result.status,
        "http_calls": result.api_calls,
        "run_id": result.run_id,
        "latest_before": result.latest_before,
        "latest_after": result.latest_after,
        "reason": result.reason,
    }


def _run_fundamentals_weekly_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "fundamentals_weekly" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid KR fundamentals weekly scheduler phase")
    return run_weekly_fundamentals_refresh(
        project_root, market_date=target, dry_run=False,
    )


def _run_market_investor_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "market_investor" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid market investor scheduler phase")
    if is_toss_market_investor_date_complete(project_root, target):
        return {
            "status": "NOOP_IDEMPOTENT", "http_calls": 0, "run_id": None,
            "latest_before": target.isoformat(), "latest_after": target.isoformat(),
            "reason": "JOINT_SOURCE_AND_BRIDGE_DATE_ALREADY_COMPLETE",
        }
    client = TossInvestClient.from_environment(project_root=project_root)
    result = refresh_toss_market_investor_daily(
        project_root, intended_date=target, client=client,
    )
    if (
        result.get("status") != "complete"
        or result.get("market_calls") != 2
        or result.get("token_calls") not in {0, 1}
        or result.get("promoted_rows") != 2
    ):
        raise ProviderSchedulerError("market investor joint promotion validation failed")
    return {
        "status": "PROMOTED",
        "http_calls": int(result["token_calls"]) + int(result["market_calls"]),
        "run_id": None, "latest_before": None, "latest_after": target.isoformat(),
        "reason": "VALIDATED_TWO_MARKET_JOINT_PROMOTION",
    }


def _run_toss_kr_treasury_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "toss_kr_treasury" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid Toss Korean treasury scheduler phase")
    result = refresh_toss_kr_treasury_daily(
        project_root,
        intended_date=target,
        client=TossInvestClient.from_environment(project_root=project_root),
    )
    return {
        "status": (
            "NOOP_IDEMPOTENT"
            if result["status"] == "already_complete"
            else "COMPLETE"
        ),
        "http_calls": int(result.get("market_calls", 0) or 0),
        "run_id": None,
        "latest_after": target.isoformat(),
        "reason": "SIX_TENOR_ATOMIC_AS_RETRIEVED",
    }


def _run_etf_phase(project_root: Path, phase: str, target: object) -> dict[str, object]:
    if phase != "global_etfs" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid global ETF scheduler phase")
    return _run_registered_yahoo_phase(
        project_root, phase="yahoo_etf", target=target,
        symbols=_GLOBAL_ETF_SYMBOLS, contract=GLOBAL_ETF_PRICE_DAILY,
        validator=validate_global_etf,
    )


_GLOBAL_INDEX_TICKERS = MappingProxyType({
    "SP500": "^GSPC",
    "NASDAQ_COMPOSITE": "^IXIC",
    "NASDAQ100": "^NDX",
    "SOX": "^SOX",
    "DOW_JONES": "^DJI",
    "DOLLAR_INDEX": "DX-Y.NYB",  # ICE dollar index; provider_native endpoint window (see global_market.py)
})
_GLOBAL_ETF_SYMBOLS = GLOBAL_ETF_DAILY_SYMBOLS
_GLOBAL_FUTURES_SYMBOLS = (
    "NASDAQ100_FUTURES", "GOLD", "WTI_CRUDE_OIL",
    "SP500_FUTURES", "DOW_FUTURES", "DOLLAR_INDEX_FUTURES",
)
_GLOBAL_INDEX_NUMERIC = ("open", "high", "low", "close", "volume")


def _replay_global_index_landing(
    existing: pd.DataFrame, call_path: Path, *, symbol: str, target: date,
) -> pd.DataFrame:
    """Rebuild one Yahoo index response without accepting a null/revised retained row."""
    if symbol not in _GLOBAL_INDEX_TICKERS:
        raise ProviderSchedulerError(f"unregistered global index replay symbol: {symbol}")
    try:
        record = json.loads(call_path.read_text(encoding="utf-8"))
        body = call_path.with_name("response.body").read_bytes()
    except (OSError, json.JSONDecodeError) as error:
        raise ProviderSchedulerError("global index Landing replay artifact is unreadable") from error
    if (
        not isinstance(record, dict)
        or (record.get("request_parameters") or {}).get("symbol") != symbol
        or record.get("response_body_sha256") != hashlib.sha256(body).hexdigest()
    ):
        raise ProviderSchedulerError("global index Landing replay identity/hash differs")
    try:
        payload = json.loads(body)
        chart = payload["chart"]
        results = chart["result"]
        if chart.get("error") is not None or not isinstance(results, list) or len(results) != 1:
            raise ValueError
        item = results[0]
        meta = item["meta"]
        ticker = _GLOBAL_INDEX_TICKERS[symbol]
        if (
            meta.get("symbol") != ticker
            or str(meta.get("instrumentType", "")).upper() != "INDEX"
            or meta.get("dataGranularity") != "1d"
        ):
            raise ValueError
        timestamps = item["timestamp"]
        quote_rows = item["indicators"]["quote"]
        if not timestamps or not isinstance(quote_rows, list) or len(quote_rows) != 1:
            raise ValueError
        values = quote_rows[0]
        if any(
            not isinstance(values.get(column), list)
            or len(values[column]) != len(timestamps)
            for column in _GLOBAL_INDEX_NUMERIC
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ProviderSchedulerError("global index Landing replay payload differs") from error

    source = pd.DataFrame({
        "date": pd.to_datetime(timestamps, unit="s", utc=True)
        .tz_convert(ZoneInfo("America/New_York")).strftime("%Y-%m-%d"),
        "symbol": symbol,
        "source_ticker": ticker,
        **{column: values[column] for column in _GLOBAL_INDEX_NUMERIC},
    })
    if source["date"].duplicated().any():
        raise ProviderSchedulerError("global index Landing replay contains duplicate dates")
    retained = existing.loc[existing["symbol"].astype(str).eq(symbol)].copy()
    if retained.empty or retained["date"].astype(str).duplicated().any():
        raise ProviderSchedulerError("global index retained identity differs")
    retained["date"] = retained["date"].astype(str)
    retained_by_date = retained.set_index("date", drop=False)
    recovered_rows: list[dict[str, object]] = []
    for row in source.to_dict("records"):
        missing = [column for column in _GLOBAL_INDEX_NUMERIC if pd.isna(row[column])]
        market_date = str(row["date"])
        if missing:
            if len(missing) != len(_GLOBAL_INDEX_NUMERIC):
                raise ProviderSchedulerError(
                    f"global index Landing replay partial-null row: {symbol} {market_date}"
                )
            if market_date == target.isoformat() or market_date not in retained_by_date.index:
                raise ProviderSchedulerError(
                    f"global index Landing replay null row has no retained value: {symbol} {market_date}"
                )
            recovered_rows.append(retained_by_date.loc[market_date].to_dict())
            continue
        numeric = pd.to_numeric(pd.Series(
            {column: row[column] for column in _GLOBAL_INDEX_NUMERIC}
        ), errors="coerce")
        if numeric.isna().any() or numeric.abs().eq(float("inf")).any():
            raise ProviderSchedulerError(
                f"global index Landing replay non-finite row: {symbol} {market_date}"
            )
        if market_date in retained_by_date.index:
            old = retained_by_date.loc[market_date]
            if any(
                pd.notna(old[column]) and float(old[column]) != float(row[column])
                for column in _GLOBAL_INDEX_NUMERIC
            ):
                raise ProviderSchedulerError(
                    f"global index Landing replay finite revision: {symbol} {market_date}"
                )
            recovered_rows.append(row)
        else:
            recovered_rows.append(row)
    recovered = pd.DataFrame(recovered_rows, columns=GLOBAL_INDEX_PRICE_DAILY.column_names)
    recovered["date"] = recovered["date"].astype(str)
    for column in _GLOBAL_INDEX_NUMERIC:
        recovered[column] = pd.to_numeric(recovered[column], errors="raise")
    recovered["volume"] = recovered["volume"].astype("Int64")
    recovered = recovered.sort_values("date", kind="stable").reset_index(drop=True)
    if target.isoformat() not in set(recovered["date"]):
        raise ProviderSchedulerError("global index Landing replay missed target date")
    validate_global_index(recovered)
    return recovered


def _prepare_global_index_with_landing_replay(
    module: object, project_root: Path, existing: pd.DataFrame, target: date, *,
    symbols: tuple[str, ...], start: date | None,
) -> tuple[dict[str, object], list[str]]:
    original_fetch = module.fetch_global_index
    replayed: list[str] = []

    def fetch_with_replay(
        symbol: str, start: date, end: date, *, session: object, capture_root: Path,
    ) -> pd.DataFrame:
        before = set(capture_root.rglob("call.json")) if capture_root.exists() else set()
        try:
            return original_fetch(
                symbol, start, end, session=session, capture_root=capture_root,
            )
        except RuntimeError as error:
            calls = []
            for path in set(capture_root.rglob("call.json")) - before:
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (record.get("request_parameters") or {}).get("symbol") == symbol:
                    calls.append(path)
            if len(calls) != 1:
                raise error
            recovered = _replay_global_index_landing(
                existing, calls[0], symbol=symbol, target=target,
            )
            replayed.append(symbol)
            return recovered

    module.fetch_global_index = fetch_with_replay
    try:
        checkpoint = module.prepare_phase(
            project_root, "yahoo", end=target, start=start, symbols=symbols,
        )
    finally:
        module.fetch_global_index = original_fetch
    return checkpoint, replayed


def _run_registered_yahoo_phase(
    project_root: Path, *, phase: str, target: date, symbols: tuple[str, ...],
    contract: object, validator: Callable[[pd.DataFrame], None],
) -> dict[str, object]:
    """Advance registered Yahoo symbols independently through the same CAS path."""
    production = project_root / "data/normalized" / str(contract.name)
    existing = read_dataset(production, contract, validator)
    present = set(existing["symbol"].astype(str))
    unknown = present.difference(symbols)
    if unknown:
        raise ProviderSchedulerError(
            f"{contract.name} retained unregistered symbols: {sorted(unknown)}"
        )
    calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    module = _load_refresh_module(project_root)
    latest_before: dict[str, str | None] = {}
    latest_after: dict[str, str | None] = {}
    symbol_results: list[dict[str, object]] = []
    promoted_symbols: list[str] = []
    failed_symbols: dict[str, str] = {}
    replayed_symbols: list[str] = []
    run_ids: list[str] = []
    http_calls = 0

    for symbol in symbols:
        selected = existing.loc[existing["symbol"].astype(str).eq(symbol), "date"]
        retained = (
            date.fromisoformat(str(selected.astype(str).max()))
            if not selected.empty else None
        )
        latest_before[symbol] = retained.isoformat() if retained is not None else None
        if retained is not None and retained >= target:
            latest_after[symbol] = retained.isoformat()
            symbol_results.append({
                "symbol": symbol, "status": "NOOP_IDEMPOTENT", "http_calls": 0,
                "latest_before": retained.isoformat(), "latest_after": retained.isoformat(),
                "reason": "EXPECTED_SESSION_ALREADY_RETAINED",
            })
            continue
        if retained is not None and calendar.next_trading_day(retained) != target:
            failed_symbols[symbol] = "NON_CONSECUTIVE_RETAINED_SESSION"
            latest_after[symbol] = retained.isoformat()
            symbol_results.append({
                "symbol": symbol, "status": "FAILED_PRESERVED", "http_calls": 0,
                "latest_before": retained.isoformat(), "latest_after": retained.isoformat(),
                "error_type": "NonConsecutiveRetainedSession",
            })
            continue

        prepare_start = retained if phase == "yahoo_etf" else None
        if retained is None:
            prepare_start = target - timedelta(days=365)
        try:
            if phase == "yahoo":
                checkpoint, replayed = _prepare_global_index_with_landing_replay(
                    module, project_root, existing, target,
                    symbols=(symbol,), start=prepare_start,
                )
                replayed_symbols.extend(replayed)
            else:
                checkpoint = module.prepare_phase(
                    project_root, phase, end=target, start=prepare_start,
                    symbols=(symbol,),
                )
            http_calls += int(checkpoint.get("http_calls", 0) or 0)
            if checkpoint.get("status") == "NOOP_IDEMPOTENT":
                latest_after[symbol] = target.isoformat()
                symbol_results.append({
                    "symbol": symbol, "status": "NOOP_IDEMPOTENT", "http_calls": 0,
                    "latest_before": latest_before[symbol],
                    "latest_after": target.isoformat(),
                    "reason": "EXPECTED_SESSION_ALREADY_RETAINED",
                })
                continue
            revision = checkpoint.get("revision_report")
            report = revision.get(symbol) if isinstance(revision, dict) else None
            inserted = report.get("inserted_rows") if isinstance(report, dict) else None
            if (
                checkpoint.get("status") != "CANDIDATE_REVIEW_REQUIRED"
                or checkpoint.get("phase") != phase
                or checkpoint.get("max_http_calls") != 1
                or checkpoint.get("http_calls") != 1
                or checkpoint.get("retry_count") != 0
                or checkpoint.get("http_statuses") != [200]
                or set(revision or {}) != {symbol}
                or not isinstance(report, dict)
                or report.get("source_omitted_existing_dates") != 0
                or report.get("finite_to_null_cells") != 0
                or not isinstance(inserted, int)
                or inserted < 1
                or (retained is not None and inserted != 1)
            ):
                raise ProviderSchedulerError(
                    f"{symbol} accepted-source checkpoint validation failed"
                )
            run_id = checkpoint.get("run_id")
            digest = checkpoint.get("approval_digest")
            if not isinstance(run_id, str) or not isinstance(digest, str):
                raise ProviderSchedulerError(f"{symbol} checkpoint identity is incomplete")
            checkpoint_path = (
                project_root / "data/state/global_current_refresh" / run_id / "checkpoint.json"
            )
            promoted = module.promote_phase(
                project_root, checkpoint_path, approval_digest=digest,
            )
            if promoted.get("status") != "PROMOTED":
                raise ProviderSchedulerError(
                    f"{symbol} promotion did not commit: {promoted.get('status')}"
                )
            existing = read_dataset(production, contract, validator)
            accepted = existing.loc[
                existing["symbol"].astype(str).eq(symbol), "date"
            ].astype(str)
            if target.isoformat() not in set(accepted):
                raise ProviderSchedulerError(f"{symbol} promoted read-back missed target")
            promoted_symbols.append(symbol)
            run_ids.append(run_id)
            latest_after[symbol] = target.isoformat()
            symbol_results.append({
                "symbol": symbol, "status": "PROMOTED", "http_calls": 1,
                "run_id": run_id, "latest_before": latest_before[symbol],
                "latest_after": target.isoformat(),
                "reason": "VALIDATED_SYMBOL_BOUND_ATOMIC_PROMOTION",
            })
        except Exception as error:
            stopped = getattr(error, "checkpoint", None)
            if isinstance(stopped, dict):
                http_calls += int(stopped.get("http_calls", 0) or 0)
            failed_symbols[symbol] = type(error).__name__
            latest_after[symbol] = latest_before[symbol]
            symbol_results.append({
                "symbol": symbol, "status": "FAILED_PRESERVED",
                "http_calls": int(stopped.get("http_calls", 0) or 0)
                if isinstance(stopped, dict) else 0,
                "latest_before": latest_before[symbol],
                "latest_after": latest_before[symbol],
                "error_type": type(error).__name__,
            })

    if failed_symbols:
        status = "DEGRADED_SYMBOL_FAILURES_PRESERVED"
        reason = "FAILED_SYMBOLS_PRESERVED_SUCCESSFUL_SYMBOLS_COMMITTED"
    elif promoted_symbols:
        status = "PROMOTED"
        reason = "REGISTERED_SYMBOLS_VALIDATED_AND_PROMOTED_INDEPENDENTLY"
    else:
        status = "NOOP_IDEMPOTENT"
        reason = "ALL_REGISTERED_SYMBOLS_CURRENT"
    return {
        "status": status, "http_calls": http_calls,
        "run_id": run_ids[-1] if len(run_ids) == 1 else None,
        "run_ids": run_ids, "latest_before": latest_before,
        "latest_after": latest_after, "reason": reason,
        "promoted_symbols": promoted_symbols, "failed_symbols": failed_symbols,
        "symbol_results": symbol_results,
        "landing_replay_symbols": replayed_symbols,
    }


def _run_global_index_phase(
    project_root: Path, phase: str, target: object,
) -> dict[str, object]:
    if phase != "global_indices" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid global index scheduler phase")
    return _run_registered_yahoo_phase(
        project_root, phase="yahoo", target=target,
        symbols=tuple(_GLOBAL_INDEX_TICKERS), contract=GLOBAL_INDEX_PRICE_DAILY,
        validator=validate_global_index,
    )


def _run_futures_phase(project_root: Path, phase: str, target: object) -> dict[str, object]:
    if phase != "dashboard_futures" or not isinstance(target, date):
        raise ProviderSchedulerError("invalid global futures scheduler phase")
    return _run_registered_yahoo_phase(
        project_root, phase="yahoo_dashboard_futures", target=target,
        symbols=_GLOBAL_FUTURES_SYMBOLS,
        contract=GLOBAL_COMMODITY_FUTURES_DAILY,
        validator=validate_global_commodity_futures,
    )


def run_lane(
    project_root: Path, lane: str, *, as_of: datetime | None = None,
    scheduled_for: datetime | None = None, dry_run: bool = False,
    phase_runner: PhaseRunner | None = None,
) -> dict[str, object]:
    root = project_root.resolve()
    if lane not in LANE_SCHEDULES:
        raise ProviderSchedulerError(f"lane is not registered for unattended execution: {lane}")
    config = LANE_SCHEDULES[lane]
    readiness = next((item for item in DAILY_LANE_READINESS if item.lane == lane), None)
    if (
        lane != "KR_FUNDAMENTALS_WEEKLY"
        and (readiness is None or not readiness.scheduler_eligible)
    ):
        raise ProviderSchedulerError(f"lane is not scheduler eligible: {lane}")
    core_enabled = {
        spec.dataset_id for spec in DATASET_OPERATIONS.select(executable_only=True)
    }
    universe_enabled = {
        spec.dataset_id for spec in DATASET_UNIVERSE.values()
        if spec.automation_enabled
    }
    enabled = core_enabled | universe_enabled
    direct = set(config.dataset_ids) - {"us_treasury_spread_daily"}
    if not direct <= enabled:
        raise ProviderSchedulerError(f"lane has disabled direct datasets: {sorted(direct-enabled)}")
    started_at = as_of or datetime.now(timezone.utc)
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if scheduled_for is not None and (
        scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None
    ):
        raise ValueError("scheduled_for must be timezone-aware")
    if scheduled_for is not None and scheduled_for > started_at:
        raise ValueError("scheduled_for cannot be after the actual start time")
    clock = scheduled_for or started_at
    calendar = ExchangeTradingCalendar(config.market)
    market_target = calendar.latest_completed_session(clock)
    phase_dataset = {
        "fred_yields": "fred_treasury_yield_daily",
        "fred_fx": "fred_usd_fx_daily",
        "fred_vix": "fred_vix_daily",
        "bok_fx": "bok_ecos_usd_krw_daily",
        "canonical_equity": "kr_equity_canonical_universe_daily",
        "kr_equity_provisional": "kr_equity_price_provisional_daily",
        "kr_etf_prices": "kr_etf_price_daily",
        "kospi200_breadth": "kr_kospi200_breadth_daily",
        "detail": "kr_stock_lending_daily",
        "market": "kr_stock_lending_market_daily",
        "participant": "kr_stock_lending_participant_daily",
        "short_trading": "kr_short_selling_trading_daily",
        "short_balance": "kr_short_selling_balance_daily",
        "short_investor": "kr_short_selling_investor_daily",
        "vkospi": "kr_vkospi_daily",
        "indices": "kr_index_daily",
        "index_fundamentals": "kr_index_fundamental_daily",
        "fundamentals_weekly": "kr_fundamentals_quarterly",
        "market_investor": "kr_market_investor_net_purchase_bridge_daily",
        "toss_kr_treasury": "kr_treasury_yield_daily",
        "global_indices": "global_index_price_daily",
        "global_etfs": "global_etf_price_daily",
        "dashboard_futures": "global_commodity_futures_daily",
    }
    phase_targets = {}
    availability_policies = {}
    for phase in config.phases:
        if phase == "kospi200_breadth":
            phase_targets[phase] = latest_accepted_canonical_target(root)
            availability_policies[phase] = "CANONICAL_ACCEPTED_DATE_ONLY"
            continue
        if phase == "fundamentals_weekly":
            phase_targets[phase] = market_target
            availability_policies[phase] = "LAST_XKRX_SESSION_OF_ISO_WEEK"
            continue
        resolved = resolve_expected_latest(
            dataset=phase_dataset[phase], lane=lane, retained_latest=None, as_of=clock,
        )
        if resolved is None:
            raise ProviderSchedulerError(f"expected-latest policy is absent: {phase}")
        phase_targets[phase] = resolved.expected_available_observation
        availability_policies[phase] = resolved.provider_availability_policy.value
    run_id = f"{lane.lower()}-{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
    base = {
        "schema_version": 1,
        "run_id": run_id,
        "lane": lane,
        "cadence_group": config.cadence_group,
        "target_session": market_target.isoformat(),
        "phase_targets": {key: value.isoformat() for key, value in phase_targets.items()},
        "provider_availability_policies": availability_policies,
        "calendar": asdict(calendar.provenance),
        "accepted_source": config.accepted_source,
        "automation_dataset_ids": sorted(direct),
        "dependency_refresh_ids": ["us_treasury_spread_daily"] if lane == "FRED_DAILY" else [],
        "retry_count": 0,
        "dry_run": dry_run,
    }
    if lane == "KR_INDEX_DAILY":
        base["registered_indices"] = [
            {"symbol": symbol, "ticker": ticker}
            for symbol, ticker in KR_INDEX_TICKERS.items()
        ] + [{"symbol": "KOSPI200", "ticker": "1028"}]
    if lane == "KR_FUNDAMENTALS_WEEKLY":
        plan = plan_weekly_fundamentals_refresh(
            root, market_date=market_target,
        )
        base.update({
            key: value for key, value in plan.items()
            if key not in {"schema_version", "lane", "target_session", "symbols"}
        })
    if scheduled_for is not None:
        base.update({
            "scheduled_for": scheduled_for.isoformat(),
            "started_at_utc": started_at.astimezone(timezone.utc).isoformat(),
        })
    if dry_run:
        return {**base, "status": "DRY_RUN_PASS", "api_calls": 0, "phases": []}
    lock_path = root / "data/state/provider_scheduler" / f"{lane.lower()}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = DailyRunLock(lock_path, run_id=run_id, acquired_at=started_at).acquire()
    outcomes: list[dict[str, object]] = []
    try:
        execute = phase_runner or {
            "FRED_DAILY": _run_accepted_phase,
            "BOK_FX_DAILY": _run_bok_fx_phase,
            "CANONICAL_EQUITY_DAILY": _run_canonical_equity_phase,
            "KR_EQUITY_PROVISIONAL_DAILY": _run_kr_equity_provisional_phase,
            "KR_ETF_PRICE_DAILY": _run_kr_etf_price_phase,
            "KOSPI200_BREADTH_DAILY": _run_kospi200_breadth_phase,
            "LENDING_DAILY": _run_lending_phase,
            "SHORT_SELLING_DAILY": _run_short_selling_phase,
            "SHORT_SELLING_BALANCE_DAILY": _run_short_selling_phase,
            "SHORT_SELLING_INVESTOR_DAILY": _run_short_selling_phase,
            "VKOSPI_DAILY": _run_vkospi_phase,
            "KR_INDEX_DAILY": _run_index_phase,
            "KR_INDEX_FUNDAMENTAL_DAILY": _run_index_fundamental_phase,
            "KR_FUNDAMENTALS_WEEKLY": _run_fundamentals_weekly_phase,
            "MARKET_INVESTOR_DAILY": _run_market_investor_phase,
            "TOSS_KR_TREASURY_DAILY": _run_toss_kr_treasury_phase,
            "GLOBAL_INDEX_DAILY": _run_global_index_phase,
            "GLOBAL_ETF_DAILY": _run_etf_phase,
            "GLOBAL_COMMODITY_DAILY": _run_futures_phase,
        }[lane]
        for phase in config.phases:
            result = execute(root, phase, phase_targets[phase])
            outcome = {
                "phase": phase,
                "status": result.get("status"),
                "http_calls": int(result.get("http_calls", 0) or 0),
                "run_id": result.get("run_id"),
                "run_ids": result.get("run_ids"),
                "dataset_id": phase_dataset[phase],
                "symbol": result.get("symbol"),
                "promoted_symbols": result.get("promoted_symbols"),
                "failed_symbols": result.get("failed_symbols"),
                "symbol_results": result.get("symbol_results"),
                "symbols": result.get("symbols"),
                "provider_gap_dates": result.get("provider_gap_dates"),
                "predictive_use": result.get("predictive_use"),
                "expected_latest": phase_targets[phase].isoformat(),
                "latest_before": result.get("latest_before"),
                "latest_after": result.get("latest_after"),
                "reason": result.get("reason"),
                "attempted_dates": result.get("attempted_dates"),
                "accepted_dates": result.get("accepted_dates"),
                "planned_symbol_count": result.get("planned_symbol_count"),
                "symbol_cap": result.get("symbol_cap"),
                "years": result.get("years"),
                "max_api_calls": result.get("max_api_calls"),
                "remaining_queries": result.get("remaining_queries"),
                "receipt_date_validation": result.get("receipt_date_validation"),
            }
            outcomes.append(outcome)
    finally:
        lock.release()
    statuses = {str(item["status"]) for item in outcomes}
    status = (
        "DEGRADED"
        if any(value.startswith(("DEGRADED", "FAILED")) for value in statuses)
        else "NOOP" if statuses == {"NOOP_IDEMPOTENT"}
        else "PASS"
    )
    report = {
        **base,
        "status": status,
        "api_calls": sum(int(item["http_calls"]) for item in outcomes),
        "phases": outcomes,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    log_name = (
        "KR_INDEX_FUNDAMENTAL_DAILY_last.json"
        if lane == "KR_INDEX_FUNDAMENTAL_DAILY"
        else f"STOCK_DATA_{lane}_last.json"
    )
    log_path = root / "artifacts/scheduler_logs" / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = log_path.with_suffix(log_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(log_path)
    return report


__all__ = ["LANE_SCHEDULES", "LaneSchedule", "ProviderSchedulerError", "run_lane"]
