from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Mapping
from uuid import uuid4

import pandas as pd

from stock_data.contracts.kr_equity_investor_flow import (
    KR_EQUITY_INVESTOR_FLOW_DAILY,
)
from stock_data.gui.watchlist_service import LocalWatchlistService
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.providers.pykrx.kr_equity_investor import (
    KrEquityInvestorProvider,
    MAX_LIVE_CALENDAR_DAYS,
    MAX_SYMBOLS_PER_RUN,
    PykrxEquityInvestorClient,
    normalize_investor_flow,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_equity_investor_flow import (
    validate_kr_equity_investor_flow,
)


LANDING_ROOT = Path("data/landing/kr_equity_investor_flow_daily")
NORMALIZED_ROOT = Path("data/normalized/kr_equity_investor_flow_daily")
STATE_PATH = Path("data/state/kr_equity_investor_flow_daily.json")
WATCHLIST_PATH = Path("artifacts/local_user/watchlists.json")
STATE_SCHEMA = "stock_data.kr_equity_investor_flow_daily_state.v1"
CHECKPOINT_SCHEMA = "stock_data.kr_equity_investor_flow_daily_checkpoint.v1"
SCHEDULER_LANE = "KR_EQUITY_INVESTOR_FLOW_DAILY"
SCHEDULER_SESSION_WINDOW = 5
ELIGIBLE_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
ELIGIBLE_SECURITY_TYPES = frozenset({"보통주", "우선주"})


class KrEquityInvestorFlowDailyError(RuntimeError):
    pass


@dataclass(frozen=True)
class KrEquityInvestorFlowPlan:
    target_session: date
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    planned_symbols: tuple[str, ...]
    latest_before: Mapping[str, date | None]

    @property
    def estimated_calls(self) -> int:
        return len(self.planned_symbols)


def normalize_symbols(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    symbols = tuple(str(value).strip().upper() for value in values)
    if not symbols or len(symbols) > MAX_SYMBOLS_PER_RUN:
        raise ValueError(
            f"symbols must contain between 1 and {MAX_SYMBOLS_PER_RUN} values"
        )
    if any(not re.fullmatch(r"[0-9A-Z]{6}", value) for value in symbols):
        raise ValueError(
            "every Korean equity symbol must be a six-character KRX code"
        )
    if len(symbols) != len(set(symbols)):
        raise ValueError("Korean equity symbols must be unique")
    return symbols


def validate_window(start: date, end: date) -> int:
    days = (end - start).days + 1
    if days < 1 or days > MAX_LIVE_CALENDAR_DAYS:
        raise ValueError("Korean equity investor-flow range must contain 1..366 calendar days")
    return days


def resolve_kr_equity_investor_flow_symbols(project_root: Path) -> tuple[str, ...]:
    """Resolve watchlisted Korean common/preferred stocks, then retained symbols."""

    root = project_root.resolve()
    state = LocalWatchlistService(root / WATCHLIST_PATH).load()
    watchlisted = {
        item.identity.symbol.strip().upper()
        for watchlist in state.lists
        for item in watchlist.items
        if item.identity.market in ELIGIBLE_MARKETS
        and item.identity.security_type in ELIGIBLE_SECURITY_TYPES
    }
    retained: set[str] = set()
    normalized_root = root / NORMALIZED_ROOT
    if normalized_root.exists() and any(normalized_root.rglob("data.parquet")):
        frame = read_dataset(
            normalized_root,
            KR_EQUITY_INVESTOR_FLOW_DAILY,
            validate_kr_equity_investor_flow,
        )
        retained.update(frame["symbol"].astype(str).str.upper())
    ordered = (*sorted(watchlisted), *sorted(retained - watchlisted))
    selected = ordered[:MAX_SYMBOLS_PER_RUN]
    if not selected:
        return ()
    return normalize_symbols(list(selected))


def plan_kr_equity_investor_flow_daily(
    project_root: Path,
    *,
    target_session: date,
) -> KrEquityInvestorFlowPlan:
    root = project_root.resolve()
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    if tuple(calendar.sessions_in_range(target_session, target_session)) != (target_session,):
        raise ValueError("Korean equity investor-flow target must be an XKRX session")
    sessions = [target_session]
    for _ in range(SCHEDULER_SESSION_WINDOW - 1):
        sessions.append(calendar.previous_trading_day(sessions[-1]))
    sessions = sorted(sessions)
    symbols = resolve_kr_equity_investor_flow_symbols(root)
    retained_dates = _retained_dates_by_symbol(root, symbols)
    required = set(sessions)
    planned = tuple(
        symbol for symbol in symbols if not required <= retained_dates[symbol]
    )
    latest = {
        symbol: max(retained_dates[symbol]) if retained_dates[symbol] else None
        for symbol in symbols
    }
    return KrEquityInvestorFlowPlan(
        target_session=target_session,
        sessions=tuple(sessions),
        symbols=symbols,
        planned_symbols=planned,
        latest_before=latest,
    )


def run_kr_equity_investor_flow_scheduler_lane(
    project_root: Path,
    *,
    target_session: date,
    provider_factory: Callable[[], KrEquityInvestorProvider] | None = None,
) -> dict[str, object]:
    root = project_root.resolve()
    plan = plan_kr_equity_investor_flow_daily(root, target_session=target_session)
    if not plan.symbols:
        return _scheduler_result(plan, status="NO_SYMBOLS_CONFIGURED", api_calls=0)
    if not plan.planned_symbols:
        return _scheduler_result(plan, status="ALREADY_CURRENT", api_calls=0)
    start = plan.sessions[0]
    provider = (
        provider_factory()
        if provider_factory is not None
        else PykrxEquityInvestorClient(
            manual=True,
            requested_days=(target_session - start).days + 1,
        )
    )
    operation = run_kr_equity_investor_flow_daily(
        root,
        symbols=plan.planned_symbols,
        start=start,
        end=target_session,
        provider=provider,
    )
    retained_after = _retained_dates_by_symbol(root, plan.symbols)
    required = set(plan.sessions)
    gaps = {
        symbol: [value.isoformat() for value in plan.sessions if value not in retained_after[symbol]]
        for symbol in plan.symbols
    }
    gaps = {symbol: values for symbol, values in gaps.items() if values}
    status = "UPDATED" if not gaps else "EXPECTED_PROVIDER_LAG"
    return _scheduler_result(
        plan,
        status=status,
        api_calls=int(operation.get("provider_calls", 0) or 0),
        latest_after={
            symbol: max(values) if values else None
            for symbol, values in retained_after.items()
        },
        provider_gap_dates=gaps or None,
        warnings=tuple(operation.get("warnings", ())),
    )


def run_kr_equity_investor_flow_daily(
    project_root: Path,
    *,
    symbols: tuple[str, ...] | list[str],
    start: date,
    end: date,
    provider: KrEquityInvestorProvider,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Capture each provider call to JSON, validate, and atomically promote."""

    root = project_root.resolve()
    selected = normalize_symbols(symbols)
    days = validate_window(start, end)
    request_key = _request_key(selected, start, end)
    prior = _successful_request(root, request_key)
    if prior is not None:
        return {
            "status": "NOOP_ALREADY_SUCCEEDED",
            "request_key": request_key,
            "symbols": list(selected),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "provider_calls": 0,
            "normalized_writes": False,
            "checkpoint": prior["checkpoint"],
            "warnings": list(prior.get("warnings", ())),
        }

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", selected_run_id):
        raise ValueError("run_id contains unsupported characters")
    run_dir = root / LANDING_ROOT / selected_run_id
    if run_dir.exists():
        raise FileExistsError(run_dir)
    run_dir.mkdir(parents=True)
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint: dict[str, object] = {
        "schema": CHECKPOINT_SCHEMA,
        "status": "RUNNING",
        "run_id": selected_run_id,
        "request_key": request_key,
        "symbols": list(selected),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_days": days,
        "max_provider_calls": len(selected),
        "provider_calls": 0,
        "retry_count": 0,
        "normalized_writes": [],
        "started_at_utc": stamp.isoformat(),
    }
    _atomic_json(checkpoint_path, checkpoint)
    writes: list[str] = []
    warnings: list[str] = []
    try:
        frames: list[pd.DataFrame] = []
        observed_by_symbol: dict[str, set[date]] = {}
        landing: dict[str, object] = {}
        for symbol in selected:
            raw = provider.get_market_trading_value_by_date(start, end, symbol)
            landing_path = run_dir / f"symbol={symbol}.json"
            landing[symbol] = _capture_frame_json_new(
                landing_path, raw, root=root,
            )
            normalized = normalize_investor_flow(
                raw,
                symbol=symbol,
                start=start,
                end=end,
                captured_at=stamp,
            )
            if not normalized.empty:
                warnings.extend(validate_kr_equity_investor_flow(normalized))
                frames.append(normalized)
            observed_by_symbol[symbol] = (
                set(pd.to_datetime(normalized["date"], errors="raise").dt.date)
                if not normalized.empty else set()
            )
            checkpoint.update(
                status="CAPTURING",
                provider_calls=provider.request_count,
                landing=landing,
            )
            _atomic_json(checkpoint_path, checkpoint)
        if provider.request_count != len(selected):
            raise KrEquityInvestorFlowDailyError(
                "pykrx investor-flow provider call accounting differs"
            )

        incoming = (
            pd.concat(frames, ignore_index=True).sort_values(
                ["date", "symbol"], kind="stable"
            ).reset_index(drop=True)
            if frames
            else pd.DataFrame(columns=KR_EQUITY_INVESTOR_FLOW_DAILY.column_names)
        )
        merged = _merge_normalized(root, incoming)
        checkpoint.update(
            status="VALIDATED",
            incoming_rows=len(incoming),
            warning_count=len(warnings),
            warnings=warnings,
        )
        _atomic_json(checkpoint_path, checkpoint)
        if not incoming.empty:
            write_dataset_atomic(
                merged,
                root / NORMALIZED_ROOT,
                KR_EQUITY_INVESTOR_FLOW_DAILY,
                validate_kr_equity_investor_flow,
            )
            observed = read_dataset(
                root / NORMALIZED_ROOT,
                KR_EQUITY_INVESTOR_FLOW_DAILY,
                validate_kr_equity_investor_flow,
            )
            _assert_same_frame(merged, observed)
            writes.append(KR_EQUITY_INVESTOR_FLOW_DAILY.name)

        expected_sessions = set(
            ExchangeTradingCalendar(ExchangeMarket.KR).sessions_in_range(start, end)
        )
        complete = all(
            expected_sessions <= observed_by_symbol[symbol] for symbol in selected
        )
        status = "SUCCEEDED" if complete else "SUCCEEDED_WITH_PROVIDER_GAPS"
        checkpoint.update(
            status=status,
            provider_calls=provider.request_count,
            normalized_writes=writes,
            normalized_manifest=(
                _dataset_manifest(root / NORMALIZED_ROOT) if writes else None
            ),
            completed_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(checkpoint_path, checkpoint)
        _record_attempt(root, request_key, checkpoint_path, checkpoint)
        return {
            "status": status,
            "request_key": request_key,
            "run_id": selected_run_id,
            "symbols": list(selected),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "provider_calls": provider.request_count,
            "rows": len(incoming),
            "warning_count": len(warnings),
            "warnings": warnings,
            "normalized_writes": writes,
            "checkpoint": checkpoint_path.relative_to(root).as_posix(),
        }
    except Exception as error:
        checkpoint.update(
            status="STOPPED",
            provider_calls=provider.request_count,
            normalized_writes=writes,
            error_type=type(error).__name__,
            stopped_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(checkpoint_path, checkpoint)
        raise


def _scheduler_result(
    plan: KrEquityInvestorFlowPlan,
    *,
    status: str,
    api_calls: int,
    latest_after: Mapping[str, date | None] | None = None,
    provider_gap_dates: Mapping[str, list[str]] | None = None,
    warnings: tuple[str, ...] = (),
) -> dict[str, object]:
    after = latest_after or plan.latest_before
    result: dict[str, object] = {
        "schema_version": 1,
        "lane": SCHEDULER_LANE,
        "status": status,
        "target_session": plan.target_session.isoformat(),
        "window_start": plan.sessions[0].isoformat(),
        "window_sessions": [value.isoformat() for value in plan.sessions],
        "symbols": list(plan.symbols),
        "planned_symbols": list(plan.planned_symbols),
        "symbol_cap": MAX_SYMBOLS_PER_RUN,
        "estimated_calls": plan.estimated_calls,
        "latest_before": {
            symbol: value.isoformat() if value else None
            for symbol, value in plan.latest_before.items()
        },
        "latest_after": {
            symbol: value.isoformat() if value else None
            for symbol, value in after.items()
        },
        "api_calls": api_calls,
        "retry_count": 0,
        "predictive_use": False,
        "warning_count": len(warnings),
        "warnings": list(warnings),
    }
    if provider_gap_dates:
        result["provider_gap_dates"] = dict(provider_gap_dates)
    return result


def _successful_request(root: Path, request_key: str) -> dict[str, object] | None:
    state = _read_json(root / STATE_PATH)
    if state is None:
        return None
    if state.get("schema") != STATE_SCHEMA or not isinstance(state.get("runs"), dict):
        raise KrEquityInvestorFlowDailyError("investor-flow state schema differs")
    record = state["runs"].get(request_key)
    if not isinstance(record, dict) or record.get("status") != "SUCCEEDED":
        return None
    checkpoint = _read_json(_safe_relative(root, record.get("checkpoint")))
    if checkpoint is None or checkpoint.get("request_key") != request_key:
        raise KrEquityInvestorFlowDailyError("successful investor-flow checkpoint is unavailable")
    read_dataset(
        root / NORMALIZED_ROOT,
        KR_EQUITY_INVESTOR_FLOW_DAILY,
        validate_kr_equity_investor_flow,
    )
    return record


def _record_attempt(
    root: Path,
    request_key: str,
    checkpoint_path: Path,
    checkpoint: Mapping[str, object],
) -> None:
    state = _read_json(root / STATE_PATH) or {"schema": STATE_SCHEMA, "runs": {}}
    if state.get("schema") != STATE_SCHEMA or not isinstance(state.get("runs"), dict):
        raise KrEquityInvestorFlowDailyError("investor-flow state schema differs")
    runs = dict(state["runs"])
    runs[request_key] = {
        "status": checkpoint["status"],
        "checkpoint": checkpoint_path.relative_to(root).as_posix(),
        "symbols": checkpoint["symbols"],
        "start": checkpoint["start"],
        "end": checkpoint["end"],
        "provider_calls": checkpoint["provider_calls"],
        "incoming_rows": checkpoint["incoming_rows"],
        "warning_count": checkpoint["warning_count"],
        "warnings": checkpoint["warnings"],
        "completed_at_utc": checkpoint["completed_at_utc"],
    }
    _atomic_json(root / STATE_PATH, {"schema": STATE_SCHEMA, "runs": runs})


def _retained_dates_by_symbol(
    root: Path,
    symbols: tuple[str, ...],
) -> dict[str, set[date]]:
    result = {symbol: set() for symbol in symbols}
    dataset_root = root / NORMALIZED_ROOT
    if not symbols or not dataset_root.exists() or not any(dataset_root.rglob("data.parquet")):
        return result
    frame = read_dataset(
        dataset_root,
        KR_EQUITY_INVESTOR_FLOW_DAILY,
        validate_kr_equity_investor_flow,
    )
    selected = frame.loc[frame["symbol"].astype(str).isin(symbols)]
    for symbol, group in selected.groupby("symbol"):
        result[str(symbol)] = set(pd.to_datetime(group["date"], errors="raise").dt.date)
    return result


def _merge_normalized(root: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    dataset_root = root / NORMALIZED_ROOT
    if incoming.empty:
        if dataset_root.exists() and any(dataset_root.rglob("data.parquet")):
            return read_dataset(
                dataset_root,
                KR_EQUITY_INVESTOR_FLOW_DAILY,
                validate_kr_equity_investor_flow,
            )
        return incoming.copy()
    validate_kr_equity_investor_flow(incoming)
    if not dataset_root.exists() or not any(dataset_root.rglob("data.parquet")):
        return incoming.copy()
    existing = read_dataset(
        dataset_root,
        KR_EQUITY_INVESTOR_FLOW_DAILY,
        validate_kr_equity_investor_flow,
    )
    existing = existing.copy()
    incoming = incoming.copy()
    existing["date"] = pd.to_datetime(existing["date"], errors="raise").dt.date
    incoming["date"] = pd.to_datetime(incoming["date"], errors="raise").dt.date
    keys = ["date", "symbol"]
    existing_index = existing.set_index(keys)
    incoming_index = incoming.set_index(keys)
    common = existing_index.index.intersection(incoming_index.index)
    if not common.empty:
        compare = [
            "foreign_net", "institution_net", "individual_net",
            "other_corp_net", "total_net", "source",
        ]
        try:
            pd.testing.assert_frame_equal(
                existing_index.loc[common, compare].sort_index(),
                incoming_index.loc[common, compare].sort_index(),
                check_dtype=False,
            )
        except AssertionError as error:
            raise KrEquityInvestorFlowDailyError(
                "retained Korean equity investor flow changed"
            ) from error
    additions = incoming_index.loc[~incoming_index.index.isin(existing_index.index)].reset_index()
    combined = pd.concat([existing, additions], ignore_index=True)
    combined = combined[list(KR_EQUITY_INVESTOR_FLOW_DAILY.column_names)].sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    validate_kr_equity_investor_flow(combined)
    return combined


def _assert_same_frame(expected: pd.DataFrame, observed: pd.DataFrame) -> None:
    left = expected.copy()
    right = observed.copy()
    for frame in (left, right):
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        frame["captured_at"] = pd.to_datetime(
            frame["captured_at"], errors="raise", utc=True,
        )
    left = left.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    right = right.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(left, right, check_dtype=False)
    except AssertionError as error:
        raise KrEquityInvestorFlowDailyError(
            "Korean equity investor-flow normalized read-back differs"
        ) from error


def _frame_json_payload(frame: pd.DataFrame) -> dict[str, object]:
    rows = []
    for index, row in frame.iterrows():
        values = {
            str(column): _json_scalar(value) for column, value in row.items()
        }
        values["__provider_index__"] = _json_scalar(index)
        rows.append(values)
    return {
        "columns": [str(value) for value in frame.columns],
        "index_name": str(frame.index.name) if frame.index.name is not None else None,
        "rows": rows,
    }


def _json_scalar(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if pd.isna(value):
        return None
    return value


def _capture_frame_json_new(path: Path, frame: pd.DataFrame, *, root: Path) -> dict[str, object]:
    payload = _frame_json_payload(frame)
    body = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes_new(path, body)
    observed = path.read_bytes()
    if observed != body or json.loads(observed) != payload:
        raise KrEquityInvestorFlowDailyError("investor-flow Landing JSON read-back differs")
    return {
        "path": path.relative_to(root).as_posix(),
        "rows": len(frame),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _dataset_manifest(path: Path) -> dict[str, object]:
    files = []
    digest = hashlib.sha256()
    for child in sorted(path.rglob("data.parquet")):
        body = child.read_bytes()
        relative = child.relative_to(path).as_posix()
        sha = hashlib.sha256(body).hexdigest()
        files.append({"path": relative, "bytes": len(body), "sha256": sha})
        digest.update(relative.encode("utf-8") + b"\0" + sha.encode("ascii") + b"\n")
    if not files:
        raise KrEquityInvestorFlowDailyError("normalized investor-flow dataset is empty")
    return {"files": files, "sha256": digest.hexdigest()}


def _request_key(symbols: tuple[str, ...], start: date, end: date) -> str:
    payload = {"symbols": list(symbols), "start": start.isoformat(), "end": end.isoformat()}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    body = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _atomic_bytes_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    if path.exists():
        temporary.unlink(missing_ok=True)
        raise FileExistsError(path)
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise KrEquityInvestorFlowDailyError(f"invalid investor-flow JSON object: {path.name}")
    return payload


def _safe_relative(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise KrEquityInvestorFlowDailyError("retained investor-flow path is invalid")
    path = (root / value).resolve(strict=True)
    if path == root or root not in path.parents or path.is_symlink():
        raise KrEquityInvestorFlowDailyError("retained investor-flow path is outside the project")
    return path


__all__ = [
    "CHECKPOINT_SCHEMA",
    "KrEquityInvestorFlowDailyError",
    "KrEquityInvestorFlowPlan",
    "SCHEDULER_SESSION_WINDOW",
    "normalize_symbols",
    "plan_kr_equity_investor_flow_daily",
    "resolve_kr_equity_investor_flow_symbols",
    "run_kr_equity_investor_flow_daily",
    "run_kr_equity_investor_flow_scheduler_lane",
    "validate_window",
]
