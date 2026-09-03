from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.provider_scheduler import LANE_SCHEDULES, run_lane
from stock_data.orchestration.daily_operations import DailyRunLock, DailyRunLockError
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.market_daily_incremental import (
    execute_liquidity_credit_two_pass,
    plan_liquidity_credit_two_pass,
)
from stock_data.orchestration.derivatives_daily_live import (
    latest_finalized_session as latest_finalized_derivatives_session,
    oldest_missing_eligible_session as oldest_missing_derivatives_session,
    run_derivatives_daily_catchup,
)
from stock_data.orchestration.update_event_log import (
    CommitResult,
    DEFAULT_RUNTIME_LOG_ROOT,
    EventState,
    FinalityResult,
    LocalUpdateEventLog,
    ReasonCode,
    TriggerType,
    UpdateEvent,
    ValidationResult,
    new_run_id,
)
from stock_data.providers.pykrx.kr_equity_fundamental_observation import (
    EquityFundamentalObservationError,
    capture_equity_fundamental_observation,
    find_valid_equity_fundamental_observation,
)


KR_MARKET_DAILY_BUNDLE = "KR_MARKET_DAILY"
KR_MARKET_DAILY_LANE_CONTRACT_VERSION = 6
KR_MARKET_DAILY_TIMEZONE = ZoneInfo("Asia/Seoul")
KR_MARKET_DAILY_SLOTS = (
    (
        time(9, 10),
        (
            "KR_INDEX_FUNDAMENTAL_DAILY",
            "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
            "SHORT_SELLING_DAILY",
            "LIQUIDITY_CREDIT_DAILY",
        ),
    ),
    (
        time(14, 10),
        ("CANONICAL_EQUITY_DAILY", "SHORT_SELLING_DAILY", "LENDING_DAILY"),
    ),
    (
        time(20, 30),
        (
            "CANONICAL_EQUITY_DAILY",
            "KR_ETF_PRICE_DAILY",
            "KOSPI200_BREADTH_DAILY",
            "SHORT_SELLING_DAILY",
            "SHORT_SELLING_BALANCE_DAILY",
            "SHORT_SELLING_INVESTOR_DAILY",
            "LENDING_DAILY",
            "VKOSPI_DAILY",
            "KR_INDEX_DAILY",
            "DERIVATIVES_PRICE_DAILY",
            "MARKET_INVESTOR_DAILY",
            "LIQUIDITY_CREDIT_DAILY",
            "LS_T8462_DAILY",
            "TOSS_KR_TREASURY_DAILY",
        ),
    ),
)
KR_MARKET_DAILY_SLOT_IDS = tuple(
    slot_time.strftime("%H:%M") for slot_time, _lanes in KR_MARKET_DAILY_SLOTS
)
KR_MARKET_DAILY_MAX_IMPLICIT_DELAY = timedelta(hours=6, minutes=30)
KR_MARKET_DAILY_2030_CATCH_UP_DELAY = timedelta(hours=13)
KR_MARKET_DAILY_EXACT_SLOT_DELAY = timedelta(minutes=30)
KR_MARKET_DAILY_GATES: tuple[dict[str, str], ...] = ()


class KrBundleOverlapError(RuntimeError):
    pass


class KrBundleOccurrenceError(ValueError):
    pass


class SchedulerHealthProjectionError(RuntimeError):
    pass


def _append_event_safely(store: LocalUpdateEventLog, event: UpdateEvent) -> bool:
    """Surface only a safe logging status and never change scheduler control flow."""

    try:
        result = store.append(event)
    except Exception:
        print("runtime_event_log=FAILED error_code=UNEXPECTED", file=sys.stderr)
        return False
    if not result.ok:
        print(
            f"runtime_event_log=FAILED error_code={result.error_code or 'UNKNOWN'}",
            file=sys.stderr,
        )
    return result.ok


def _terminal_event(
    started: UpdateEvent, payload: dict[str, object], *, exit_code: int,
    dry_run: bool,
) -> UpdateEvent:
    """Project one bounded scheduler result into the existing typed event schema."""

    api_calls = payload.get("api_calls", 0)
    safe_api_calls = (
        int(api_calls)
        if isinstance(api_calls, int) and not isinstance(api_calls, bool) and api_calls >= 0
        else 0
    )
    status = str(payload.get("status", "UNKNOWN"))
    ended = max(datetime.now(timezone.utc), started.started_at_utc)
    if exit_code == 0:
        if dry_run:
            return started.terminal(
                state=EventState.SUCCEEDED,
                reason_code=ReasonCode.COMPLETED,
                at=ended,
                provider_call_count=safe_api_calls,
                validation_result=ValidationResult.PASSED,
                promotion_result=CommitResult.NOT_RUN,
                checkpoint_result=CommitResult.NOT_RUN,
                finality_result=FinalityResult.NOT_APPLICABLE,
                message=f"provider scheduler dry-run status={status}",
            )
        outcomes = payload.get("outcomes")
        all_outcomes_noop = (
            isinstance(outcomes, list)
            and bool(outcomes)
            and all(
                isinstance(item, dict)
                and item.get("advancement_status") == "NOOP_CURRENT"
                for item in outcomes
            )
        )
        is_noop = safe_api_calls == 0 and (
            "NOOP" in status
            or payload.get("advancement_status") == "NOOP_CURRENT"
            or all_outcomes_noop
        )
        return started.terminal(
            state=EventState.API_ZERO_NOOP if is_noop else EventState.SUCCEEDED,
            reason_code=(
                ReasonCode.ALREADY_CURRENT_API_ZERO if is_noop
                else ReasonCode.COMPLETED
            ),
            at=ended,
            provider_call_count=safe_api_calls,
            validation_result=ValidationResult.PASSED,
            promotion_result=CommitResult.NOOP if is_noop else CommitResult.SUCCEEDED,
            checkpoint_result=CommitResult.NOOP if is_noop else CommitResult.SUCCEEDED,
            finality_result=(
                FinalityResult.NOT_APPLICABLE if dry_run else FinalityResult.UNKNOWN
            ),
            message=f"provider scheduler status={status}",
        )
    error_type = str(payload.get("error_type", ""))
    if "Permission" in error_type or "Auth" in error_type:
        state, reason = (
            EventState.AUTH_PERMISSION_FAILURE,
            ReasonCode.AUTHENTICATION_OR_PERMISSION_DENIED,
        )
    elif "Lock" in error_type or error_type in {"OSError", "IOError"}:
        state, reason = EventState.LOCAL_IO_FAILURE, ReasonCode.LOCAL_READ_WRITE_ERROR
    elif status == "DEGRADED":
        state, reason = (
            EventState.PARTIAL_INELIGIBLE,
            ReasonCode.PARTIAL_SCOPE_INELIGIBLE,
        )
    else:
        state, reason = EventState.VALIDATION_FAILURE, ReasonCode.VALIDATION_REJECTED
    return started.terminal(
        state=state,
        reason_code=reason,
        at=ended,
        provider_call_count=safe_api_calls,
        validation_result=(
            ValidationResult.PARTIAL
            if state is EventState.PARTIAL_INELIGIBLE else ValidationResult.FAILED
        ),
        promotion_result=CommitResult.FAILED,
        checkpoint_result=CommitResult.FAILED,
        finality_result=FinalityResult.UNKNOWN,
        message=f"provider scheduler status={status} error_type={error_type or 'UNKNOWN'}",
    )


def _kr_occurrence_receipt_path(
    project_root: Path, *, scheduled_slot: str, scheduled_for: datetime,
) -> Path:
    token = scheduled_for.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        project_root.resolve() / "data/state/provider_scheduler"
        / "kr_market_daily_occurrences" / f"{token}-{scheduled_slot.replace(':', '')}.json"
    )


def _claim_kr_occurrence(
    project_root: Path, *, scheduled_slot: str, scheduled_for: datetime,
    started_at: datetime,
) -> tuple[Path, bool]:
    """Durably consume one slot occurrence before any provider-capable lane."""
    path = _kr_occurrence_receipt_path(
        project_root, scheduled_slot=scheduled_slot, scheduled_for=scheduled_for,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "lane_contract_version": KR_MARKET_DAILY_LANE_CONTRACT_VERSION,
        "bundle": KR_MARKET_DAILY_BUNDLE,
        "scheduled_slot": scheduled_slot,
        "scheduled_for": scheduled_for.isoformat(),
        "claimed_at_utc": started_at.astimezone(timezone.utc).isoformat(),
        "status": "CLAIMED_BEFORE_LANES",
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return path, False
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path, True


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """Publish canonical JSON through a flushed unique same-parent temporary."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    body = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _finalize_kr_occurrence_receipt(
    project_root: Path, receipt_path: Path, payload: dict[str, object], *, exit_code: int,
) -> dict[str, object]:
    """Atomically replace one pre-lane claim with its immutable terminal evidence."""
    claimed = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        not isinstance(claimed, dict)
        or claimed.get("status") != "CLAIMED_BEFORE_LANES"
        or claimed.get("bundle") != payload.get("bundle")
        or claimed.get("lane_contract_version")
        != payload.get("lane_contract_version")
        or claimed.get("scheduled_slot") != payload.get("scheduled_slot")
        or claimed.get("scheduled_for") != payload.get("scheduled_for")
    ):
        raise KrBundleOccurrenceError("occurrence claim is not eligible for terminalization")
    relative = receipt_path.relative_to(project_root.resolve()).as_posix()
    terminal = {
        **payload,
        "claimed_at_utc": claimed.get("claimed_at_utc"),
        "occurrence_receipt": relative,
        "occurrence_status": (
            "TERMINAL_SUCCESS" if exit_code == 0 else "TERMINAL_FAILURE"
        ),
        "terminal_exit_code": exit_code,
    }
    _write_json_atomic(receipt_path, terminal)
    if json.loads(receipt_path.read_text(encoding="utf-8")) != terminal:
        raise KrBundleOccurrenceError("terminal occurrence receipt readback differs")
    return terminal


def _run_liquidity_credit_observation(
    project_root: Path, *, clock: datetime, dry_run: bool,
) -> dict[str, object]:
    """Observe one completed XKRX date twice without accepting a revision."""
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    target = calendar.latest_completed_session(clock)
    local = clock.astimezone(KR_MARKET_DAILY_TIMEZONE)
    is_morning_confirmation = local.time().replace(tzinfo=None) < time(14, 10)
    if not is_morning_confirmation and (
        not calendar.is_trading_day(local.date()) or target != local.date()
    ):
        return {
            "schema_version": 1,
            "lane": "LIQUIDITY_CREDIT_DAILY",
            "status": "SKIPPED_NON_TRADING_DAY",
            "target_session": target.isoformat(),
            "observations": [],
            "api_calls": 0,
        }
    observations: list[dict[str, object]] = []
    for dataset in ("market_liquidity", "credit_balance"):
        plan = plan_liquidity_credit_two_pass(
            project_root=project_root,
            dataset=dataset,
            market_date=target,
            latest_finalized_market_date=target,
            accepted_market_dates=(target,),
            operation_reviewed=True,
            max_api_calls=1,
        )
        if is_morning_confirmation and plan.action == "CAPTURE_PROVISIONAL":
            observations.append({
                "dataset": plan.dataset,
                "target_date": target.isoformat(),
                "status": "WAITING_FOR_2030_PROVISIONAL",
                "comparison": "PENDING",
                "api_calls": 0,
            })
            continue
        if dry_run:
            observations.append({
                "dataset": plan.dataset,
                "target_date": target.isoformat(),
                "status": plan.action,
                "comparison": "PENDING" if plan.estimated_api_calls else "OK",
                "api_calls": 0,
            })
            continue
        result = execute_liquidity_credit_two_pass(
            plan, project_root=project_root,
        )
        observations.append({
            "dataset": result.dataset,
            "target_date": result.market_date,
            "status": result.status,
            "comparison": (
                "OK" if result.status in {"STABLE", "NOOP_STABLE"}
                else "DIFFERENT" if result.status == "REVISED"
                else "PENDING"
            ),
            # TwoPassResult exposes the bounded collector page count.  This
            # lane permits one request per observation, so pages is also the
            # exact provider call count; no separate api_calls field exists.
            "api_calls": result.pages,
            "observation_count": result.observation_count,
        })
    return {
        "schema_version": 1,
        "lane": "LIQUIDITY_CREDIT_DAILY",
        "status": "DRY_RUN_PASS" if dry_run else "PASS",
        "target_session": target.isoformat(),
        "observations": observations,
        "api_calls": sum(int(item["api_calls"]) for item in observations),
    }


def _run_ls_t8462_daily_raw(
    project_root: Path, *, clock: datetime, dry_run: bool,
) -> dict[str, object]:
    """Run the retained 18-scope Raw-only collector for one completed session."""
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    target = calendar.latest_completed_session(clock)
    local = clock.astimezone(KR_MARKET_DAILY_TIMEZONE)
    if not calendar.is_trading_day(local.date()) or target != local.date():
        return {
            "schema_version": 1,
            "lane": "LS_T8462_DAILY",
            "status": "SKIPPED_NON_TRADING_DAY",
            "target_session": target.isoformat(),
            "api_calls": 0,
        }
    if dry_run:
        return {
            "schema_version": 1,
            "lane": "LS_T8462_DAILY",
            "status": "DRY_RUN_PASS",
            "target_session": target.isoformat(),
            "api_calls": 0,
            "normalized_writes": 0,
        }
    command = [
        sys.executable,
        str(project_root / "scripts/manual/collect/collect_ls_t8462_daily_raw.py"),
        "--root",
        str(project_root),
        "--market-date",
        target.strftime("%Y%m%d"),
        "--confirm-live-daily-raw",
    ]
    completed = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise RuntimeError("LS t8462 collector did not return a safe JSON receipt") from error
    status = str(payload.get("status", "UNKNOWN"))
    if completed.returncode == 3 and status == "NOT_EXECUTED_ALREADY_ATTEMPTED":
        lane_status = "NOOP_IDEMPOTENT"
    elif completed.returncode == 0 and status == "DAILY_COLLECTION_COMPLETE":
        lane_status = "COMPLETE"
    else:
        raise RuntimeError(f"LS t8462 Raw collection stopped with status={status}")
    return {
        "schema_version": 1,
        "lane": "LS_T8462_DAILY",
        "status": lane_status,
        "target_session": target.isoformat(),
        "api_calls": int(payload.get("data_calls", 0) or 0),
        "oauth_calls": int(payload.get("oauth_calls", 0) or 0),
        "retry_count": int(payload.get("retry_count", 0) or 0),
        "normalized_writes": 0,
        "run_id": payload.get("run_id"),
    }


def _run_bundle_lane(
    project_root: Path, lane: str, *, started_at: datetime,
    scheduled_for: datetime, dry_run: bool,
) -> dict[str, object]:
    if lane == "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION":
        return _run_equity_fundamental_current_observation(
            project_root, clock=scheduled_for, dry_run=dry_run,
        )
    if lane == "LIQUIDITY_CREDIT_DAILY":
        return _run_liquidity_credit_observation(
            project_root, clock=scheduled_for, dry_run=dry_run,
        )
    if lane == "LS_T8462_DAILY":
        return _run_ls_t8462_daily_raw(
            project_root, clock=scheduled_for, dry_run=dry_run,
        )
    if lane == "DERIVATIVES_PRICE_DAILY":
        if dry_run:
            remaining = oldest_missing_derivatives_session(
                project_root, now=scheduled_for,
            )
            target = remaining or latest_finalized_derivatives_session(
                project_root, now=scheduled_for,
            )
            return {
                "schema_version": 1,
                "lane": lane,
                "status": "DRY_RUN_PASS",
                "target_session": target.isoformat(),
                "api_calls": 0,
                "retry_count": 0,
                "completed_dates": [],
                "remaining_target": (
                    remaining.isoformat() if remaining is not None else None
                ),
            }
        result = run_derivatives_daily_catchup(
            project_root,
            now=scheduled_for,
            max_sessions=3,
            max_source_calls=6,
            max_elapsed_seconds=600.0,
        )
        last_result = result.last_result
        return {
            "schema_version": 1,
            "lane": lane,
            "status": result.status,
            "target_session": (
                last_result.market_date
                if last_result is not None
                else result.completed_dates[-1]
                if result.completed_dates
                else result.remaining_target
            ),
            "api_calls": result.api_calls,
            "retry_count": result.retry_count,
            "completed_dates": list(result.completed_dates),
            "remaining_target": result.remaining_target,
            "stages": list(last_result.stages) if last_result is not None else [],
            "rows": dict(last_result.rows) if last_result is not None else {},
        }
    return run_lane(
        project_root, lane, as_of=started_at, scheduled_for=scheduled_for,
        dry_run=dry_run,
    )


def _run_equity_fundamental_current_observation(
    project_root: Path, *, clock: datetime, dry_run: bool,
) -> dict[str, object]:
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    completed = calendar.latest_completed_session(clock)
    landing_root = (
        project_root / "data/landing/kr_equity_fundamental_current_observation"
    )
    retained_dates: list[date] = []
    for date_root in landing_root.glob("date=*"):
        token = date_root.name.removeprefix("date=")
        try:
            candidate = date.fromisoformat(token)
        except ValueError:
            continue
        if (
            candidate <= completed
            and find_valid_equity_fundamental_observation(
                landing_root, candidate,
            ) is not None
        ):
            retained_dates.append(candidate)
    retained = max(retained_dates) if retained_dates else None
    if retained is not None and retained >= completed:
        return {
            "schema_version": 1,
            "lane": "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
            "status": "NOOP_CURRENT",
            "target_session": completed.isoformat(),
            "latest_before": retained.isoformat(),
            "latest_after": retained.isoformat(),
            "api_calls": 0,
            "retry_count": 0,
            "predictive_use": False,
        }
    target = (
        calendar.next_trading_day(retained)
        if retained is not None else completed
    )
    if target > completed:
        raise RuntimeError("equity fundamental observation target is not completed")
    if dry_run:
        return {
            "schema_version": 1,
            "lane": "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
            "status": "DRY_RUN_PASS",
            "target_session": target.isoformat(),
            "latest_before": retained.isoformat() if retained else None,
            "latest_after": retained.isoformat() if retained else None,
            "api_calls": 0,
            "retry_count": 0,
            "predictive_use": False,
        }
    run_id = f"scheduled-{target:%Y%m%d}-{uuid4().hex}"
    try:
        result = capture_equity_fundamental_observation(
            target,
            run_id=run_id,
            landing_root=landing_root,
            env_file=project_root / ".env",
        )
    except EquityFundamentalObservationError as error:
        if "valid-empty" not in str(error):
            raise
        return {
            "schema_version": 1,
            "lane": "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
            "status": "EXPECTED_PROVIDER_LAG",
            "target_session": target.isoformat(),
            "latest_before": retained.isoformat() if retained else None,
            "latest_after": retained.isoformat() if retained else None,
            "api_calls": 1,
            "retry_count": 0,
            "predictive_use": False,
            "reason": "VALID_EMPTY_RETRY_NEXT_NATURAL_OCCURRENCE",
        }
    return {
        "schema_version": 1,
        "lane": "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
        "status": "CAPTURED_CURRENT_OBSERVATION",
        "target_session": target.isoformat(),
        "latest_before": retained.isoformat() if retained else None,
        "latest_after": result.market_date,
        "rows": result.rows,
        "distinct_security_codes": result.distinct_security_codes,
        "duplicate_groups": result.duplicate_groups,
        "api_calls": result.business_calls,
        "retry_count": result.retry_count,
        "predictive_use": result.predictive_use,
        "reason": "EXACT_DATE_DESCRIPTIVE_CURRENT_OBSERVATION",
    }


def _write_lane_log(
    project_root: Path, lane: str, payload: dict[str, object],
) -> None:
    log_name = (
        "KR_INDEX_FUNDAMENTAL_DAILY_last.json"
        if lane == "KR_INDEX_FUNDAMENTAL_DAILY"
        else f"STOCK_DATA_{lane}_last.json"
    )
    path = project_root / "artifacts/scheduler_logs" / log_name
    _write_json_atomic(path, payload)
    if json.loads(path.read_text(encoding="utf-8")) != payload:
        raise OSError(f"scheduler log readback differs: {log_name}")


def _restore_kr_terminal_pointer_if_missing_or_older(
    project_root: Path, terminal: dict[str, object],
) -> None:
    """Recover only forward to retained terminal evidence; never rewind the pointer."""
    path = project_root / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json"
    restore = not path.exists()
    if not restore:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            current_for = datetime.fromisoformat(
                str(current["scheduled_for"]).replace("Z", "+00:00")
            )
            terminal_for = datetime.fromisoformat(
                str(terminal["scheduled_for"]).replace("Z", "+00:00")
            )
            if (
                current_for.tzinfo is None or current_for.utcoffset() is None
                or terminal_for.tzinfo is None or terminal_for.utcoffset() is None
            ):
                raise ValueError("terminal pointer timestamp is naive")
            restore = terminal_for.astimezone(timezone.utc) >= current_for.astimezone(
                timezone.utc
            )
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return
    if restore:
        _write_lane_log(project_root, KR_MARKET_DAILY_BUNDLE, terminal)


def _refresh_health(project_root: Path) -> dict[str, object]:
    path = ROOT / "scripts/maintenance/reconcile_daily_health_artifact.py"
    spec = importlib.util.spec_from_file_location("stock_data_health_projection", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load health projection: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.write_universe_health_artifact(
        project_root=project_root,
        core_artifact=project_root / "artifacts/daily_health/core_data_20260818.json",
        universe_output=project_root / "artifacts/daily_health/universe_data_v2_20260819.json",
        execution_log=project_root / "artifacts/scheduler_logs/STOCK_DATA_DAILY_HEALTH_last.json",
    )
    return _health_projection_from_report(report)


def _health_projection_from_report(report: object) -> dict[str, object]:
    """Project scheduler health from managed datasets only.

    Universe Health intentionally retains probe failures for disabled/manual datasets so
    operators can repair them.  Those unrelated failures must not turn a successful
    automated lane into a failed occurrence.  The scheduler projection therefore counts
    runtime coverage only for rows whose registry contract enables automation.
    """
    if not isinstance(report, dict):
        raise SchedulerHealthProjectionError("Health report is not an object")
    rows = report.get("datasets")
    dataset_count = report.get("dataset_count")
    if (
        not isinstance(rows, list)
        or not rows
        or not isinstance(dataset_count, int)
        or isinstance(dataset_count, bool)
        or dataset_count != len(rows)
        or any(not isinstance(row, dict) for row in rows)
        or any(type(row.get("automation_enabled")) is not bool for row in rows)
        or any(
            not isinstance(row.get("dataset"), str) or not row["dataset"]
            for row in rows
        )
        or len({str(row["dataset"]) for row in rows}) != len(rows)
    ):
        raise SchedulerHealthProjectionError("Health report coverage differs")
    managed = [row for row in rows if row["automation_enabled"]]
    managed_validated = sum(
        row.get("runtime_coverage") == "VALIDATED" for row in managed
    )
    runtime_coverage_failed_datasets = sorted(
        str(row["dataset"])
        for row in managed
        if str(row.get("runtime_coverage", "")).startswith("FAILED:")
    )
    unacceptable_datasets = sorted(
        str(row["dataset"])
        for row in managed
        if row.get("freshness") not in {"CURRENT", "EXPECTED_LAG"}
    )
    return _validated_health_projection({
        "status": (
            "PASS"
            if not unacceptable_datasets and not runtime_coverage_failed_datasets
            else "DEGRADED"
        ),
        "dataset_count": dataset_count,
        "runtime_coverage_validated_count": managed_validated,
        "runtime_coverage_failure_count": len(runtime_coverage_failed_datasets),
        "unacceptable_datasets": unacceptable_datasets,
        "runtime_coverage_failed_datasets": runtime_coverage_failed_datasets,
    })


def _validated_health_projection(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise SchedulerHealthProjectionError("Health projection is not an object")
    dataset_count = payload.get("dataset_count")
    validated_count = payload.get("runtime_coverage_validated_count")
    failure_count = payload.get("runtime_coverage_failure_count")
    unacceptable = payload.get("unacceptable_datasets")
    runtime_failed = payload.get("runtime_coverage_failed_datasets")
    if (
        set(payload) != {
            "status", "dataset_count", "runtime_coverage_validated_count",
            "runtime_coverage_failure_count", "unacceptable_datasets",
            "runtime_coverage_failed_datasets",
        }
        or payload.get("status") not in {"PASS", "DEGRADED"}
        or not isinstance(dataset_count, int)
        or isinstance(dataset_count, bool)
        or dataset_count <= 0
        or not isinstance(validated_count, int)
        or isinstance(validated_count, bool)
        or validated_count < 0
        or validated_count > dataset_count
        or not isinstance(failure_count, int)
        or isinstance(failure_count, bool)
        or failure_count < 0
        or failure_count > dataset_count
        or not isinstance(unacceptable, list)
        or not isinstance(runtime_failed, list)
        or any(not isinstance(item, str) or not item for item in unacceptable)
        or any(not isinstance(item, str) or not item for item in runtime_failed)
        or unacceptable != sorted(set(unacceptable))
        or runtime_failed != sorted(set(runtime_failed))
        or failure_count != len(runtime_failed)
        or (
            payload.get("status") == "PASS"
            and (unacceptable or runtime_failed)
        )
        or (
            payload.get("status") == "DEGRADED"
            and not (unacceptable or runtime_failed)
        )
    ):
        raise SchedulerHealthProjectionError(
            "Health projection is incomplete or has runtime coverage failures"
        )
    return dict(payload)


def _advancement_status(result: dict[str, object]) -> str:
    phases = result.get("phases")
    if isinstance(phases, list) and phases:
        statuses = {str(item.get("status")) for item in phases if isinstance(item, dict)}
        if statuses and statuses <= {"NOOP_IDEMPOTENT"}:
            return "NOOP_CURRENT"
        if statuses & {
            "PROMOTED", "COMPLETE", "SUCCEEDED", "UPDATED",
            "CANONICAL_ACCEPTED_DATE", "PARTIAL_LIMIT_REACHED",
        }:
            return "UPDATED"
        if "EXPECTED_PROVIDER_LAG" in statuses:
            return "EXPECTED_LAG"
    if result.get("status") in {
        "NOOP", "NOOP_IDEMPOTENT", "CURRENT", "SKIPPED_NON_TRADING_DAY",
    }:
        return "NOOP_CURRENT"
    if result.get("status") in {
        "AFFECTED_DATE_COMPLETE", "CAUGHT_UP", "PARTIAL_LIMIT_REACHED",
        "CAPTURED_CURRENT_OBSERVATION", "COMPLETE",
    }:
        return "UPDATED"
    if result.get("status") == "EXPECTED_PROVIDER_LAG":
        return "EXPECTED_LAG"
    return "UNKNOWN"


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must be timezone-aware")
    return parsed


def _kr_market_daily_slot(
    scheduled_slot: str,
) -> tuple[time, tuple[str, ...]]:
    for slot_time, lanes in KR_MARKET_DAILY_SLOTS:
        if scheduled_slot == slot_time.strftime("%H:%M"):
            return slot_time, lanes
    raise ValueError(f"unsupported Korean daily scheduled slot: {scheduled_slot}")


def _kr_market_daily_lanes(scheduled_slot: str) -> tuple[str, ...]:
    return _kr_market_daily_slot(scheduled_slot)[1]


def _kr_market_daily_scheduled_for(
    started_at: datetime, scheduled_slot: str,
    scheduled_occurrence: datetime | None = None,
    *, allow_latest_occurrence: bool = False,
) -> datetime:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise KrBundleOccurrenceError("bundle start time must be timezone-aware")
    slot_time, _lanes = _kr_market_daily_slot(scheduled_slot)
    local_start = started_at.astimezone(KR_MARKET_DAILY_TIMEZONE)
    if scheduled_occurrence is not None:
        if allow_latest_occurrence:
            raise KrBundleOccurrenceError(
                "explicit and latest-occurrence provenance are mutually exclusive"
            )
        if (
            scheduled_occurrence.tzinfo is None
            or scheduled_occurrence.utcoffset() is None
        ):
            raise KrBundleOccurrenceError(
                "scheduled occurrence must be timezone-aware"
            )
        occurrence = scheduled_occurrence.astimezone(KR_MARKET_DAILY_TIMEZONE)
        if occurrence.time().replace(tzinfo=None) != slot_time:
            raise KrBundleOccurrenceError(
                "scheduled occurrence clock does not match scheduled slot"
            )
        if occurrence.astimezone(timezone.utc) > started_at.astimezone(timezone.utc):
            raise KrBundleOccurrenceError(
                "scheduled occurrence cannot be after bundle start"
            )
        return occurrence

    if not allow_latest_occurrence:
        occurrence = datetime.combine(
            local_start.date(), slot_time, tzinfo=KR_MARKET_DAILY_TIMEZONE,
        )
        delay = local_start - occurrence
        if delay < timedelta(0) or delay > KR_MARKET_DAILY_EXACT_SLOT_DELAY:
            raise KrBundleOccurrenceError(
                "exact scheduled occurrence is outside its no-replay window"
            )
        return occurrence

    scheduled_date = local_start.date()
    if local_start.time().replace(tzinfo=None) < slot_time:
        scheduled_date -= timedelta(days=1)
    occurrence = datetime.combine(
        scheduled_date, slot_time, tzinfo=KR_MARKET_DAILY_TIMEZONE,
    )
    max_delay = (
        KR_MARKET_DAILY_2030_CATCH_UP_DELAY
        if scheduled_slot == "20:30"
        else KR_MARKET_DAILY_MAX_IMPLICIT_DELAY
    )
    if local_start - occurrence > max_delay:
        raise KrBundleOccurrenceError(
            "scheduled occurrence is ambiguous; provide --scheduled-occurrence"
        )
    return occurrence


def _run_kr_market_daily_bundle_unlocked(
    project_root: Path, *, scheduled_slot: str, started_at: datetime,
    scheduled_for: datetime, dry_run: bool,
    occurrence_receipt: Path | None = None,
) -> tuple[dict[str, object], int]:
    lanes = _kr_market_daily_lanes(scheduled_slot)
    identity = {
        "scheduled_slot": scheduled_slot,
        "scheduled_for": scheduled_for.isoformat(),
        "started_at_utc": started_at.astimezone(timezone.utc).isoformat(),
    }
    outcomes: list[dict[str, object]] = []
    for lane in lanes:
        try:
            result = _run_bundle_lane(
                project_root, lane, started_at=started_at,
                scheduled_for=scheduled_for, dry_run=dry_run,
            )
            result = {**result, **identity}
            outcomes.append({
                "lane": lane,
                "status": result.get("status"),
                "advancement_status": (
                    "DRY_RUN" if dry_run else _advancement_status(result)
                ),
                "api_calls": int(result.get("api_calls", 0) or 0),
                "result": result,
                **identity,
            })
        except Exception as error:
            outcomes.append({
                "lane": lane,
                "status": "FAIL",
                "advancement_status": "UNKNOWN",
                "api_calls": None,
                "error_type": type(error).__name__,
                **identity,
            })

    failures = [
        item for item in outcomes
        if str(item["status"]).startswith(("FAIL", "DEGRADED"))
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "lane_contract_version": KR_MARKET_DAILY_LANE_CONTRACT_VERSION,
        "bundle": KR_MARKET_DAILY_BUNDLE,
        "as_of": scheduled_for.isoformat(),
        "timezone": "Asia/Seoul",
        "eligible_lanes": list(lanes),
        "gated_lanes": list(KR_MARKET_DAILY_GATES),
        "outcomes": outcomes,
        "status": (
            "DRY_RUN_PASS" if dry_run and not failures
            else "DEGRADED" if failures
            else "PASS"
        ),
        "api_calls": sum(
            int(item["api_calls"] or 0) for item in outcomes
            if item["api_calls"] is not None
        ),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        **identity,
    }
    exit_code = 1 if failures else 0
    if not dry_run:
        try:
            payload["health_projection"] = _validated_health_projection(
                _refresh_health(project_root)
            )
        except Exception as health_error:
            payload["health_projection"] = {
                "status": "FAIL", "error_type": type(health_error).__name__,
            }
            payload["status"] = "DEGRADED"
            exit_code = 1
        else:
            if payload["health_projection"]["status"] == "DEGRADED":
                payload["status"] = "DEGRADED"
        payload["scheduler_process_status"] = (
            "SUCCESS" if exit_code == 0 else "FAIL_AFTER_INDEPENDENT_LANES"
        )
        health_failed = payload["health_projection"].get("status") == "FAIL"
        for item in outcomes:
            lane = str(item["lane"])
            lane_payload = item.get("result")
            lane_failed = str(item["status"]).startswith(("FAIL", "DEGRADED"))
            lane_process_status = (
                "FAIL_AFTER_HEALTH" if health_failed
                else "FAIL" if lane_failed
                else "SUCCESS"
            )
            if isinstance(lane_payload, dict):
                lane_payload["advancement_status"] = item["advancement_status"]
                lane_payload["health_projection"] = payload["health_projection"]
                lane_payload["scheduler_process_status"] = lane_process_status
                _write_lane_log(project_root, lane, lane_payload)
            else:
                _write_lane_log(project_root, lane, {
                    **item,
                    "health_projection": payload["health_projection"],
                    "scheduler_process_status": lane_process_status,
                })
        if occurrence_receipt is not None:
            payload = _finalize_kr_occurrence_receipt(
                project_root, occurrence_receipt, payload, exit_code=exit_code,
            )
        _restore_kr_terminal_pointer_if_missing_or_older(project_root, payload)
    return payload, exit_code


def _run_kr_market_daily_bundle(
    project_root: Path, *, scheduled_slot: str, as_of: datetime | None,
    dry_run: bool, scheduled_occurrence: datetime | None = None,
    allow_latest_occurrence: bool = False,
) -> tuple[dict[str, object], int]:
    started_at = as_of or datetime.now(timezone.utc)
    scheduled_for = _kr_market_daily_scheduled_for(
        started_at, scheduled_slot, scheduled_occurrence,
        allow_latest_occurrence=allow_latest_occurrence,
    )
    lock_path = (
        project_root.resolve()
        / "data/state/provider_scheduler/kr_market_daily_bundle.lock"
    )
    run_id = (
        f"kr-market-daily-{scheduled_for.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
        f"-{scheduled_slot.replace(':', '')}"
    )
    try:
        lock = DailyRunLock(
            lock_path, run_id=run_id, acquired_at=started_at,
        ).acquire()
    except DailyRunLockError as error:
        raise KrBundleOverlapError("Korean daily bundle lock is already held") from error
    try:
        if not dry_run:
            receipt_path, claimed = _claim_kr_occurrence(
                project_root, scheduled_slot=scheduled_slot,
                scheduled_for=scheduled_for, started_at=started_at,
            )
            if not claimed:
                retained = json.loads(receipt_path.read_text(encoding="utf-8"))
                retained_status = (
                    retained.get("occurrence_status")
                    if isinstance(retained, dict) else None
                )
                retained_success = retained_status == "TERMINAL_SUCCESS"
                if retained_status in {"TERMINAL_SUCCESS", "TERMINAL_FAILURE"}:
                    _restore_kr_terminal_pointer_if_missing_or_older(
                        project_root, retained,
                    )
                payload = {
                    "schema_version": 1,
                    "bundle": KR_MARKET_DAILY_BUNDLE,
                    "as_of": scheduled_for.isoformat(),
                    "timezone": "Asia/Seoul",
                    "eligible_lanes": list(_kr_market_daily_lanes(scheduled_slot)),
                    "gated_lanes": list(KR_MARKET_DAILY_GATES),
                    "outcomes": [],
                    "status": "NOOP_OCCURRENCE_ALREADY_CLAIMED",
                    "api_calls": 0,
                    "scheduler_process_status": (
                        "NOOP_TERMINAL_SUCCESS_PRESERVED"
                        if retained_success
                        else "NOOP_TERMINAL_FAILURE_PRESERVED"
                        if retained_status == "TERMINAL_FAILURE"
                        else "NOOP_UNRESOLVED_CLAIM_PRESERVED"
                    ),
                    "scheduled_slot": scheduled_slot,
                    "scheduled_for": scheduled_for.isoformat(),
                    "started_at_utc": started_at.astimezone(timezone.utc).isoformat(),
                    "occurrence_receipt": receipt_path.relative_to(
                        project_root.resolve()
                    ).as_posix(),
                    "retained_occurrence_status": retained_status or retained.get("status"),
                }
                if isinstance(retained, dict) and isinstance(
                    retained.get("lane_contract_version"), int,
                ):
                    payload["lane_contract_version"] = retained[
                        "lane_contract_version"
                    ]
                return payload, 0 if retained_success else 1
        return _run_kr_market_daily_bundle_unlocked(
            project_root, scheduled_slot=scheduled_slot, started_at=started_at,
            scheduled_for=scheduled_for, dry_run=dry_run,
            occurrence_receipt=(receipt_path if not dry_run else None),
        )
    finally:
        lock.release()


class _BoundTerminalEmitter:
    """Attempt one terminal event even when compatibility persistence raises."""

    def __init__(self) -> None:
        self.started: UpdateEvent | None = None
        self.store: LocalUpdateEventLog | None = None
        self.dry_run = False
        self.attempted = False

    def bind(
        self, started: UpdateEvent, store: LocalUpdateEventLog, *, dry_run: bool,
    ) -> None:
        self.started = started
        self.store = store
        self.dry_run = dry_run

    def finish(self, payload: dict[str, object], exit_code: int) -> int:
        self.attempted = True
        assert self.started is not None and self.store is not None
        _append_event_safely(
            self.store,
            _terminal_event(
                self.started, payload, exit_code=exit_code,
                dry_run=self.dry_run,
            ),
        )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return exit_code

    def finish_unhandled(self, error: BaseException) -> None:
        if self.attempted or self.started is None or self.store is None:
            return
        self.attempted = True
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "FAIL",
            "error_type": type(error).__name__,
            "api_calls": 0,
        }
        _append_event_safely(
            self.store,
            _terminal_event(
                self.started, payload, exit_code=1, dry_run=self.dry_run,
            ),
        )


def _main(terminal: _BoundTerminalEmitter) -> int:
    parser = argparse.ArgumentParser(description="Run a typed, fail-closed provider scheduler lane.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--lane", choices=tuple(LANE_SCHEDULES))
    target.add_argument("--bundle", choices=(KR_MARKET_DAILY_BUNDLE,))
    parser.add_argument(
        "--as-of", type=_aware_datetime,
        help="Timezone-aware actual start timestamp; intended for deterministic tests/audits.",
    )
    parser.add_argument("--scheduled-slot", choices=KR_MARKET_DAILY_SLOT_IDS)
    parser.add_argument(
        "--scheduled-occurrence", type=_aware_datetime,
        help="Explicit timezone-aware occurrence for a delayed KR bundle run.",
    )
    parser.add_argument(
        "--allow-latest-occurrence", action="store_true",
        help=(
            "Infer the latest bounded occurrence; valid only when the scheduler "
            "action cannot replay missed occurrences."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.bundle and args.scheduled_slot is None:
        parser.error("--bundle KR_MARKET_DAILY requires --scheduled-slot")
    if args.lane and args.scheduled_slot is not None:
        parser.error("--scheduled-slot is valid only with --bundle KR_MARKET_DAILY")
    if args.lane and args.scheduled_occurrence is not None:
        parser.error("--scheduled-occurrence is valid only with --bundle KR_MARKET_DAILY")
    if args.lane and args.allow_latest_occurrence:
        parser.error("--allow-latest-occurrence is valid only with --bundle KR_MARKET_DAILY")
    if args.scheduled_occurrence is not None and args.allow_latest_occurrence:
        parser.error(
            "--scheduled-occurrence and --allow-latest-occurrence are mutually exclusive"
        )
    as_of = args.as_of
    project_root = args.project_root.resolve()
    event_started_at = datetime.now(timezone.utc)
    logical_dataset = str(args.bundle or args.lane)
    started_event = UpdateEvent.started(
        run_id=new_run_id("provider-scheduler", now=event_started_at),
        job_route="provider-scheduler",
        logical_dataset=logical_dataset,
        trigger_type=TriggerType.SCHEDULED,
        requested_scope={
            "lane": args.lane,
            "bundle": args.bundle,
            "scheduled_slot": args.scheduled_slot,
            "dry_run": bool(args.dry_run),
        },
        at=event_started_at,
        message="bounded provider scheduler run started",
    )
    event_store = LocalUpdateEventLog(project_root / DEFAULT_RUNTIME_LOG_ROOT)
    _append_event_safely(event_store, started_event)
    terminal.bind(started_event, event_store, dry_run=bool(args.dry_run))

    def finish(payload: dict[str, object], exit_code: int) -> int:
        return terminal.finish(payload, exit_code)

    if args.bundle == KR_MARKET_DAILY_BUNDLE:
        try:
            payload, exit_code = _run_kr_market_daily_bundle(
                project_root, scheduled_slot=args.scheduled_slot,
                as_of=as_of, dry_run=args.dry_run,
                scheduled_occurrence=args.scheduled_occurrence,
                allow_latest_occurrence=args.allow_latest_occurrence,
            )
        except KrBundleOccurrenceError as error:
            payload = {
                "schema_version": 1,
                "bundle": KR_MARKET_DAILY_BUNDLE,
                "scheduled_slot": args.scheduled_slot,
                "status": "FAIL_OCCURRENCE",
                "error_type": type(error).__name__,
                "scheduler_process_status": "FAIL_BEFORE_LANES",
            }
            if not args.dry_run:
                _write_lane_log(project_root, KR_MARKET_DAILY_BUNDLE, payload)
            return finish(payload, 1)
        except KrBundleOverlapError as error:
            payload = {
                "schema_version": 1,
                "bundle": KR_MARKET_DAILY_BUNDLE,
                "scheduled_slot": args.scheduled_slot,
                "status": "FAIL_LOCK",
                "error_type": type(error).__name__,
                "scheduler_process_status": "FAIL_BEFORE_LANES",
            }
            return finish(payload, 1)
        except DailyRunLockError as error:
            payload = {
                "schema_version": 1,
                "bundle": KR_MARKET_DAILY_BUNDLE,
                "scheduled_slot": args.scheduled_slot,
                "status": "FAIL_LOCK_RELEASE",
                "error_type": type(error).__name__,
                "scheduler_process_status": "FAIL_AFTER_BUNDLE",
            }
            return finish(payload, 1)
        return finish(payload, exit_code)
    lane = str(args.lane)
    try:
        result = run_lane(project_root, lane, as_of=as_of, dry_run=args.dry_run)
        if not args.dry_run:
            result["advancement_status"] = _advancement_status(result)
            try:
                result["health_projection"] = _validated_health_projection(
                    _refresh_health(project_root)
                )
            except Exception as health_error:
                result["health_projection"] = {
                    "status": "FAIL", "error_type": type(health_error).__name__,
                }
                result["scheduler_process_status"] = "FAIL_AFTER_LANE"
                _write_lane_log(project_root, lane, result)
                return finish(result, 1)
            result["scheduler_process_status"] = "SUCCESS"
            _write_lane_log(project_root, lane, result)
    except Exception as error:
        result = {
            "schema_version": 1,
            "lane": lane,
            "status": "FAIL",
            "error_type": type(error).__name__,
            "api_calls": None,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "health_projection": "NOT_RUN",
        }
        if not args.dry_run:
            _write_lane_log(project_root, lane, result)
        return finish(result, 1)
    return finish(result, 0)


def main() -> int:
    terminal = _BoundTerminalEmitter()
    try:
        return _main(terminal)
    except BaseException as error:
        terminal.finish_unhandled(error)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
