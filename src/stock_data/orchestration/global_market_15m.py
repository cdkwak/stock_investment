from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.contracts.market_15m import (
    MARKET_15M_LANE_SERIES,
    MARKET_15M_SERIES_POLICIES,
    MARKET_PRICE_15M_OBSERVATION,
)
from stock_data.orchestration.daily_operations import DailyRunLock
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.recovery_supervisor import (
    JournalState,
    OperationScopeLock,
    RecoveryAction,
    RecoverySnapshot,
    RetryPolicy,
    ScopeLockBusy,
    plan_recovery,
    recovery_event_pair,
)
from stock_data.orchestration.update_event_log import LocalUpdateEventLog, TriggerType
from stock_data.providers.yahoo_15m import fetch_market_15m
from stock_data.storage.market_15m import (
    merge_market_15m_exact,
    read_market_15m,
    write_market_15m_atomic,
)
from stock_data.validation.market_15m import audit_market_15m_bars, validate_market_price_15m


LANE_IDS = tuple(MARKET_15M_LANE_SERIES)
SERIES_IDS = tuple(MARKET_15M_SERIES_POLICIES)
_FULL_XNYS_SESSION = timedelta(hours=6, minutes=30)


class GlobalMarket15mError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewedMarket15mScope:
    lane_id: str
    session_date: date
    window_start: datetime
    window_end: datetime
    expected_bar_starts: Mapping[str, tuple[datetime, ...]]


def resolve_native_scope(
    as_of: datetime,
    lane_id: str,
    *,
    required_session_date: date | None = None,
    scheduled: bool = False,
) -> ReviewedMarket15mScope:
    """Resolve exactly one manual or scheduled scope before provider access."""
    if (required_session_date is None) == (not scheduled):
        raise ValueError(
            "choose exactly one Yahoo 15m mode: required_session_date or scheduled"
        )
    if required_session_date is not None:
        return reviewed_native_scope(
            as_of,
            lane_id,
            required_session_date=required_session_date,
        )

    calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    local_date = as_of.astimezone(ZoneInfo("America/New_York")).date()
    if calendar.is_trading_day(local_date):
        eligible_at = calendar.session_close(local_date) + timedelta(minutes=30)
        if as_of.astimezone(timezone.utc) < eligible_at:
            raise GlobalMarket15mError(
                "scheduled Yahoo 15m lane is before the 30-minute post-close gate"
            )
        required_session_date = local_date
    else:
        required_session_date = calendar.latest_completed_session(
            as_of, completion_buffer=timedelta(minutes=30)
        )
    return reviewed_native_scope(
        as_of,
        lane_id,
        required_session_date=required_session_date,
    )


def reviewed_native_scope(
    as_of: datetime,
    lane_id: str,
    *,
    completion_buffer: timedelta = timedelta(minutes=30),
    required_session_date: date | None = None,
) -> ReviewedMarket15mScope:
    """Build one reviewed provider-native lane without cross-lane assumptions."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if lane_id not in MARKET_15M_LANE_SERIES:
        raise ValueError(f"unknown Yahoo 15m lane: {lane_id}")
    calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    session_date = calendar.latest_completed_session(
        as_of, completion_buffer=completion_buffer
    )
    if required_session_date is not None and session_date != required_session_date:
        raise GlobalMarket15mError(
            "reviewed Yahoo 15m session differs from required exact date: "
            f"selected={session_date.isoformat()} "
            f"required={required_session_date.isoformat()}"
        )
    xnys_open = calendar.session_open(session_date)
    xnys_close = calendar.session_close(session_date)

    if lane_id in {"XNYS_MARKET_INDEX", "CBOE_VIX"}:
        window_start, window_end = xnys_open, xnys_close
        starts = tuple(
            stamp.to_pydatetime()
            for stamp in pd.date_range(
                pd.Timestamp(window_start),
                pd.Timestamp(window_end),
                freq="15min",
                inclusive="left",
            )
        )
    else:
        if xnys_close - xnys_open != _FULL_XNYS_SESSION:
            raise GlobalMarket15mError(
                "Treasury quote early-close provider grid is not reviewed"
            )
        source_zone = ZoneInfo("America/Chicago")
        window_start = datetime.combine(session_date, time(8, 20), source_zone)
        window_end = datetime.combine(session_date, time(14, 5), source_zone)
        starts = tuple(
            stamp.to_pydatetime()
            for stamp in pd.date_range(
                pd.Timestamp(window_start),
                pd.Timestamp(window_end),
                freq="15min",
                inclusive="left",
            )
        )

    expected = {
        series_id: starts for series_id in MARKET_15M_LANE_SERIES[lane_id]
    }
    return ReviewedMarket15mScope(
        lane_id=lane_id,
        session_date=session_date,
        window_start=window_start,
        window_end=window_end,
        expected_bar_starts=expected,
    )


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _scope_key(start: datetime, end: datetime) -> tuple[str, str]:
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def _is_complete_replay(
    state_path: Path,
    production: Path,
    *,
    lane_id: str,
    start: datetime,
    end: datetime,
    expected_bar_starts: Mapping[str, Sequence[datetime]],
) -> bool:
    if not state_path.exists():
        return False
    series_ids = MARKET_15M_LANE_SERIES[lane_id]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        expected_scope = _scope_key(start, end)
        if (
            state.get("status") != "PASS"
            or state.get("lane_id") != lane_id
            or tuple(state.get("scope_utc", ())) != expected_scope
            or tuple(state.get("series_ids", ())) != series_ids
        ):
            return False
        retained = read_market_15m(production)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return False
    starts = pd.to_datetime(retained["bar_start"], utc=True)
    inside = retained.loc[
        retained["series_id"].isin(series_ids)
        & starts.ge(pd.Timestamp(start).tz_convert("UTC"))
        & starts.lt(pd.Timestamp(end).tz_convert("UTC"))
    ]
    for series_id in series_ids:
        observed = pd.to_datetime(
            inside.loc[inside["series_id"].eq(series_id), "bar_start"], utc=True
        ).tolist()
        if audit_market_15m_bars(
            observed, expected_bar_starts[series_id]
        ).status != "COMPLETE":
            return False
    return True


def _record_cboe_vix_api_zero_replay(
    event_log: LocalUpdateEventLog,
    *,
    clock: datetime,
    session_date: date,
    series_ids: Sequence[str],
) -> str:
    """Record one Cboe-VIX replay without affecting its accepted outcome."""

    decision = plan_recovery(
        RecoverySnapshot(
            now=clock,
            expected_date=session_date,
            retained_date=session_date,
            scheduled_for=clock,
            available_after=clock,
            requested_scopes=tuple(series_ids),
            completed_scopes=tuple(series_ids),
            checkpoint_complete=True,
            journal_state=JournalState.CLEAN,
            schedule_attempted=True,
        ),
        RetryPolicy(provider_call_budget=0, retry_budget=0),
    )
    if decision.action is not RecoveryAction.API_ZERO_REPLAY:
        return "FAILED"
    started, terminal = recovery_event_pair(
        decision=decision,
        operation="global-market-15m-cboe-vix",
        logical_dataset=MARKET_PRICE_15M_OBSERVATION.name,
        datasets=series_ids,
        trigger_type=TriggerType.API_ZERO_REPLAY,
        expected_date=session_date,
        retained_date=session_date,
        at=clock,
    )
    try:
        started_result = event_log.append(started)
        if not started_result.ok:
            return "FAILED"
        terminal_result = event_log.append(terminal)
        return "PASS" if terminal_result.ok else "FAILED"
    except Exception:
        return "FAILED"


def run_global_market_15m(
    project_root: Path,
    *,
    lane_id: str,
    window_start: datetime,
    window_end: datetime,
    expected_bar_starts: Mapping[str, Sequence[datetime]],
    as_of: datetime | None = None,
    fetcher: Callable[..., pd.DataFrame] = fetch_market_15m,
    event_log: LocalUpdateEventLog | None = None,
) -> dict[str, object]:
    """Refresh one authorized provider-native lane with retry zero."""
    values = (window_start, window_end)
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("15m scope bounds must be timezone-aware")
    if window_start >= window_end or window_end - window_start > timedelta(days=8):
        raise ValueError("15m scope must be ordered and at most 8 days")
    if lane_id not in MARKET_15M_LANE_SERIES:
        raise ValueError(f"unknown Yahoo 15m lane: {lane_id}")
    series_ids = MARKET_15M_LANE_SERIES[lane_id]
    if set(expected_bar_starts) != set(series_ids):
        raise ValueError("15m expected-bar calendar must cover the exact lane")
    expected = {
        series_id: tuple(pd.Timestamp(value).tz_convert("UTC") for value in starts)
        for series_id, starts in expected_bar_starts.items()
    }
    counts = {series_id: len(starts) for series_id, starts in expected.items()}
    if all(count == 0 for count in counts.values()):
        return {
            "schema_version": 2,
            "dataset_id": MARKET_PRICE_15M_OBSERVATION.name,
            "lane_id": lane_id,
            "status": "NOOP_MARKET_CLOSED",
            "api_calls": 0,
            "scope_utc": list(_scope_key(window_start, window_end)),
            "series_ids": list(series_ids),
            "expected_bars": counts,
        }
    if any(count == 0 for count in counts.values()):
        raise ValueError("15m atomic lane requires expected bars for every identity")
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    root = Path(project_root).resolve()
    production = root / "data/normalized/market_price_15m_observation"
    state_path = root / "data/state/global_market_15m" / f"{lane_id.lower()}.json"
    log_path = root / (
        "artifacts/scheduler_logs/"
        f"STOCK_DATA_GLOBAL_MARKET_15M_{lane_id}_last.json"
    )
    scope = _scope_key(window_start, window_end)
    if _is_complete_replay(
        state_path,
        production,
        lane_id=lane_id,
        start=window_start,
        end=window_end,
        expected_bar_starts=expected,
    ):
        session_date = (
            next(iter(expected.values()))[0]
            .tz_convert(MARKET_15M_SERIES_POLICIES[series_ids[0]].source_timezone)
            .date()
        )
        report = {
            "schema_version": 2,
            "dataset_id": MARKET_PRICE_15M_OBSERVATION.name,
            "lane_id": lane_id,
            "status": "NOOP_ALREADY_ACCEPTED",
            "api_calls": 0,
            "scope_utc": list(scope),
            "series_ids": list(series_ids),
            "session_date": session_date.isoformat(),
        }
        if lane_id == "CBOE_VIX" and event_log is not None:
            lock = OperationScopeLock(
                root / "artifacts/runtime_locks/data_updates",
                operation="global-market-15m-cboe-vix",
                datasets=series_ids,
                run_id=f"cboe-vix-replay-{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
                clock=lambda: clock,
            )
            try:
                with lock:
                    if not _is_complete_replay(
                        state_path,
                        production,
                        lane_id=lane_id,
                        start=window_start,
                        end=window_end,
                        expected_bar_starts=expected,
                    ):
                        report["event_log_status"] = "FAILED"
                    else:
                        report["event_log_status"] = _record_cboe_vix_api_zero_replay(
                            event_log,
                            clock=clock,
                            session_date=session_date,
                            series_ids=series_ids,
                        )
            except ScopeLockBusy:
                report["event_log_status"] = "SKIPPED_ACTIVE_WRITER"
            except Exception:
                report["event_log_status"] = "FAILED"
        _atomic_json(log_path, report)
        return report

    run_id = (
        f"global15m-{lane_id.lower()}-"
        f"{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
    )
    landing = root / "data/landing/global_market_15m" / run_id
    staging = root / "data/staging/global_market_15m" / run_id / "candidate"
    lock_path = root / "data/state/provider_scheduler/global_market_15m.lock"
    base = {
        "schema_version": 2,
        "run_id": run_id,
        "dataset_id": MARKET_PRICE_15M_OBSERVATION.name,
        "lane_id": lane_id,
        "series_ids": list(series_ids),
        "scope_utc": list(scope),
        "session_date": next(
            iter(expected.values())
        )[0].tz_convert(MARKET_15M_SERIES_POLICIES[series_ids[0]].source_timezone).date().isoformat(),
        "source": "Yahoo indicative/delayed chart API; not licensed realtime",
        "retry_count": 0,
        "max_api_calls": len(series_ids),
        "expected_bars": counts,
        "started_at_utc": clock.astimezone(timezone.utc).isoformat(),
    }
    calls = 0
    try:
        with DailyRunLock(lock_path, run_id=run_id, acquired_at=clock):
            frames = []
            for series_id in series_ids:
                calls += 1
                frame = fetcher(
                    series_id,
                    start=window_start,
                    end=window_end,
                    capture_root=landing,
                    retrieved_at=clock,
                )
                expected_timezone = MARKET_15M_SERIES_POLICIES[series_id].source_timezone
                if not frame["source_timezone"].eq(expected_timezone).all():
                    raise GlobalMarket15mError(
                        f"15m provider timezone differs: {series_id}"
                    )
                frames.append(frame)
            incoming = pd.concat(frames, ignore_index=True)
            validate_market_price_15m(incoming)
            if set(incoming["series_id"].astype(str)) != set(series_ids):
                raise GlobalMarket15mError("15m candidate does not contain the exact lane")
            for series_id in series_ids:
                observed = pd.to_datetime(
                    incoming.loc[incoming["series_id"].eq(series_id), "bar_start"], utc=True
                ).tolist()
                audit = audit_market_15m_bars(observed, expected[series_id])
                if audit.status != "COMPLETE":
                    raise GlobalMarket15mError(
                        f"15m expected bars differ: {series_id}; "
                        f"missing={len(audit.missing_bars)} "
                        f"unexpected={len(audit.unexpected_bars)}"
                    )
            try:
                existing = read_market_15m(production)
            except FileNotFoundError:
                existing = pd.DataFrame(columns=MARKET_PRICE_15M_OBSERVATION.column_names)
            candidate = merge_market_15m_exact(existing, incoming)
            write_market_15m_atomic(candidate, staging)
            verified = read_market_15m(staging)
            if len(verified) != len(candidate):
                raise GlobalMarket15mError("staged 15m row count differs")
            write_market_15m_atomic(verified, production)
            report = {
                **base,
                "status": "PASS",
                "api_calls": calls,
                "incoming_rows": len(incoming),
                "retained_rows_before": len(existing),
                "retained_rows_after": len(candidate),
                "latest_bar_end_utc": {
                    series_id: pd.to_datetime(
                        candidate.loc[candidate["series_id"].eq(series_id), "bar_end"],
                        utc=True,
                    ).max().isoformat()
                    for series_id in series_ids
                },
                "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _atomic_json(state_path, report)
        _atomic_json(log_path, report)
        return report
    except Exception as error:
        _atomic_json(log_path, {
            **base,
            "status": "FAIL",
            "api_calls": calls,
            "error_type": type(error).__name__,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        raise


__all__ = [
    "GlobalMarket15mError",
    "LANE_IDS",
    "ReviewedMarket15mScope",
    "resolve_native_scope",
    "SERIES_IDS",
    "reviewed_native_scope",
    "run_global_market_15m",
]
