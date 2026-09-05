"""Landing-first per-symbol investor flows for selected KRX-listed ETFs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping
from uuid import uuid4

import pandas as pd

from stock_data.contracts.kr_etf_investor_flow import (
    KR_ETF_INVESTOR_FLOW_DAILY,
)
from stock_data.contracts.kr_etf import KR_ETF_MASTER
from stock_data.orchestration.current_observation_supervisor import (
    CurrentObservationProcessLock,
)
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.orchestration.kr_etf_daily import (
    MAX_SYMBOLS,
    _manual_account_etf_symbols,
    normalize_symbols,
    resolve_kr_etf_symbols,
)
from stock_data.providers.pykrx.kr_etf import KrEtfProvider, PykrxEtfClient
from stock_data.providers.pykrx.safety import PykrxRequestPolicy
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_etf import validate_kr_etf_master


LANE = "KR_ETF_INVESTOR_FLOW_DAILY"
DATASET_ROOT = Path("data/normalized/kr_etf_investor_flow_daily")
LANDING_ROOT = Path("data/landing/pykrx/kr_etf_investor_flow_daily")
RECEIPT_PATH = Path(
    "artifacts/scheduler_logs/STOCK_DATA_KR_ETF_INVESTOR_FLOW_DAILY_last.json"
)
LOCK_PATH = Path("data/state/kr_etf_investor_flow_daily.lock")
MAX_WINDOW_CALENDAR_DAYS = 10
PYKRX_COLUMNS = (
    "기관",
    "기타법인",
    "개인",
    "외국인",
    "전체",
)
VALUE_COLUMNS = (
    "institution_net_krw",
    "other_corporation_net_krw",
    "individual_net_krw",
    "foreign_net_krw",
    "total_net_krw",
)
PYKRX_COLUMN_MAP = dict(zip(PYKRX_COLUMNS, VALUE_COLUMNS, strict=True))


class KrEtfInvestorFlowDailyError(RuntimeError):
    pass


@dataclass(frozen=True)
class KrEtfInvestorFlowWindow:
    symbol: str
    start: date
    end: date
    sessions: tuple[date, ...]


@dataclass(frozen=True)
class KrEtfInvestorFlowPlan:
    start: date
    end: date
    symbols: tuple[str, ...]
    windows: tuple[KrEtfInvestorFlowWindow, ...]
    retained_latest: Mapping[str, date | None]

    @property
    def estimated_calls(self) -> int:
        return len(self.windows)


def validate_kr_etf_investor_flow(dataframe: pd.DataFrame) -> None:
    contract = KR_ETF_INVESTOR_FLOW_DAILY
    if list(dataframe.columns) != list(contract.column_names) or dataframe.empty:
        raise ValueError("Korean ETF investor-flow schema is invalid or empty")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate Korean ETF investor-flow date/symbol key")
    if not dataframe["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all():
        raise ValueError("Korean ETF investor-flow symbol is invalid")
    dates = pd.to_datetime(dataframe["date"], errors="coerce")
    retrieved = pd.to_datetime(dataframe["retrieved_at"], errors="coerce", utc=True)
    if dates.isna().any() or retrieved.isna().any():
        raise ValueError("Korean ETF investor-flow date or retrieval time is invalid")
    if dataframe[["date", "symbol", "provider", "retrieved_at"]].isna().any().any():
        raise ValueError("Korean ETF investor-flow identity/provenance is incomplete")
    if not dataframe["provider"].astype(str).eq("pykrx").all():
        raise ValueError("Korean ETF investor-flow provider differs")
    for column in VALUE_COLUMNS:
        values = pd.to_numeric(dataframe[column], errors="coerce")
        if values.isna().any() or not pd.api.types.is_integer_dtype(dataframe[column].dtype):
            raise ValueError(f"Korean ETF investor-flow {column} must be int64")
        if dataframe[column].dtype.itemsize != 8:
            raise ValueError(f"Korean ETF investor-flow {column} must be int64")
    components = dataframe[list(VALUE_COLUMNS[:-1])].sum(axis=1)
    if not components.eq(dataframe["total_net_krw"]).all():
        raise ValueError("Korean ETF investor-flow participant sum differs from total")


def normalize_etf_investor_flow(
    raw: pd.DataFrame,
    *,
    symbol: str,
    start: date,
    end: date,
    retrieved_at: datetime,
) -> pd.DataFrame:
    selected = normalize_symbols([symbol])[0]
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    if raw.empty:
        return pd.DataFrame(columns=KR_ETF_INVESTOR_FLOW_DAILY.column_names)
    if raw.index.has_duplicates:
        raise KrEtfInvestorFlowDailyError(
            f"pykrx ETF investor-flow date index contains duplicates: {selected}"
        )
    if list(raw.columns) != list(PYKRX_COLUMNS):
        raise KrEtfInvestorFlowDailyError(
            f"pykrx ETF investor-flow columns differ: {selected}: {list(raw.columns)}"
        )
    frame = raw.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "date", **PYKRX_COLUMN_MAP})
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    if frame["date"].min() < start or frame["date"].max() > end:
        raise KrEtfInvestorFlowDailyError(
            f"pykrx ETF investor-flow rows exceed requested window: {selected}"
        )
    frame.insert(1, "symbol", selected)
    for column in VALUE_COLUMNS:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or (values % 1 != 0).any():
            raise KrEtfInvestorFlowDailyError(
                f"pykrx ETF investor-flow integer field differs: {selected}/{column}"
            )
        frame[column] = values.astype("int64")
    frame["provider"] = "pykrx"
    frame["retrieved_at"] = pd.Timestamp(retrieved_at.astimezone(timezone.utc))
    frame = frame[list(KR_ETF_INVESTOR_FLOW_DAILY.column_names)].sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    validate_kr_etf_investor_flow(frame)
    return frame


def _calendar_windows(start: date, end: date) -> tuple[tuple[date, date], ...]:
    if end < start:
        raise ValueError("end must not precede start")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=MAX_WINDOW_CALENDAR_DAYS - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


def resolve_kr_etf_investor_flow_symbols(project_root: Path) -> tuple[str, ...]:
    """Reuse the price universe, keeping watchlist ETFs first at its hard cap."""
    root = Path(project_root).resolve()
    try:
        return resolve_kr_etf_symbols(root)
    except ValueError as error:
        if f"between 1 and {MAX_SYMBOLS}" not in str(error):
            raise

    watchlisted: set[str] = set()
    watchlist_path = root / "artifacts/local_user/watchlists.json"
    if watchlist_path.is_file():
        payload = json.loads(watchlist_path.read_text(encoding="utf-8"))
        for watchlist in payload.get("lists", []) if isinstance(payload, dict) else []:
            for item in watchlist.get("items", []) if isinstance(watchlist, dict) else []:
                if (
                    isinstance(item, dict)
                    and item.get("market") == "KRX"
                    and item.get("security_type") == "ETF"
                ):
                    watchlisted.add(str(item.get("symbol", "")).strip().upper())
    manual = _manual_account_etf_symbols(root)
    retained: set[str] = set()
    master_root = root / "data/normalized/kr_etf_master"
    if master_root.exists() and any(master_root.rglob("data.parquet")):
        master = read_dataset(master_root, KR_ETF_MASTER, validate_kr_etf_master)
        retained.update(master["symbol"].astype(str).str.upper())
    ordered = (
        *sorted(watchlisted),
        *sorted(manual - watchlisted),
        *sorted(retained - watchlisted - manual),
    )
    if not ordered:
        return ()
    return normalize_symbols(list(ordered[:MAX_SYMBOLS]))


def plan_kr_etf_investor_flow_daily(
    project_root: Path,
    *,
    start: date,
    end: date,
    symbols: tuple[str, ...] | list[str] | None = None,
) -> KrEtfInvestorFlowPlan:
    root = Path(project_root).resolve()
    selected = (
        resolve_kr_etf_investor_flow_symbols(root)
        if symbols is None
        else normalize_symbols(symbols)
    )
    if len(selected) > MAX_SYMBOLS:
        raise ValueError(f"Korean ETF investor-flow symbol cap is {MAX_SYMBOLS}")
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    retained = _retained_dates_by_symbol(root, selected)
    windows: list[KrEtfInvestorFlowWindow] = []
    for symbol in selected:
        for window_start, window_end in _calendar_windows(start, end):
            sessions = tuple(calendar.sessions_in_range(window_start, window_end))
            if sessions and not set(sessions) <= retained[symbol]:
                windows.append(KrEtfInvestorFlowWindow(
                    symbol=symbol,
                    start=window_start,
                    end=window_end,
                    sessions=sessions,
                ))
    latest = {
        symbol: max(values) if values else None
        for symbol, values in retained.items()
    }
    return KrEtfInvestorFlowPlan(
        start=start,
        end=end,
        symbols=selected,
        windows=tuple(windows),
        retained_latest=latest,
    )


def _plan_payload(plan: KrEtfInvestorFlowPlan) -> dict[str, object]:
    return {
        "lane": LANE,
        "dataset": KR_ETF_INVESTOR_FLOW_DAILY.name,
        "start": plan.start.isoformat(),
        "end": plan.end.isoformat(),
        "symbols": list(plan.symbols),
        "window_calendar_days": MAX_WINDOW_CALENDAR_DAYS,
        "planned_windows": [
            {
                "symbol": window.symbol,
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "sessions": [value.isoformat() for value in window.sessions],
            }
            for window in plan.windows
        ],
        "estimated_calls": plan.estimated_calls,
        "api_calls": 0,
        "automation_enabled": False,
        "predictive_use": False,
    }


def run_kr_etf_investor_flow_daily(
    project_root: Path,
    *,
    start: date,
    end: date,
    symbols: tuple[str, ...] | list[str] | None = None,
    provider: KrEtfProvider | None = None,
    dry_run: bool = False,
    confirm_live: bool = False,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    plan = plan_kr_etf_investor_flow_daily(
        root, start=start, end=end, symbols=symbols,
    )
    base = _plan_payload(plan)
    if dry_run:
        return {**base, "status": "DRY_RUN_PASS"}
    if not confirm_live:
        raise KrEtfInvestorFlowDailyError("live execution requires --confirm-live")
    if not plan.symbols:
        return _receipt(root, {**base, "status": "NO_SYMBOLS_CONFIGURED"})
    if not plan.windows:
        return _receipt(root, {**base, "status": "NOOP_ALREADY_CURRENT"})

    lock = CurrentObservationProcessLock(root / LOCK_PATH)
    if not lock.acquire():
        return _receipt(root, {**base, "status": "PROCESS_LOCKED_API_ZERO"})
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_run_id = run_id or stamp.strftime("%Y%m%dT%H%M%S.%fZ")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", selected_run_id):
        lock.release()
        raise ValueError("run_id contains unsupported characters")
    landing_dir = (
        root / LANDING_ROOT / f"range={start:%Y%m%d}_{end:%Y%m%d}"
        / f"run={selected_run_id}"
    )
    if landing_dir.exists():
        lock.release()
        raise FileExistsError(landing_dir)
    active_provider = provider
    frames: list[pd.DataFrame] = []
    landing_files: list[dict[str, object]] = []
    try:
        if active_provider is None:
            active_provider = PykrxEtfClient(
                manual=True,
                requested_days=MAX_WINDOW_CALENDAR_DAYS,
                policy=PykrxRequestPolicy(
                    max_consecutive_requests=max(1, plan.estimated_calls),
                ),
            )
        for window in plan.windows:
            raw = active_provider.get_etf_investor_flow_by_date(
                window.start, window.end, window.symbol,
            )
            target = (
                landing_dir / f"symbol={window.symbol}"
                / f"window={window.start:%Y%m%d}_{window.end:%Y%m%d}.json"
            )
            landing_files.append(_capture_frame_json_new(target, raw, root=root))
            normalized = normalize_etf_investor_flow(
                raw,
                symbol=window.symbol,
                start=window.start,
                end=window.end,
                retrieved_at=stamp,
            )
            if not normalized.empty:
                frames.append(normalized)
        if active_provider.request_count != plan.estimated_calls:
            raise KrEtfInvestorFlowDailyError(
                "pykrx ETF investor-flow call accounting differs"
            )
        incoming = (
            pd.concat(frames, ignore_index=True).sort_values(
                ["date", "symbol"], kind="stable"
            ).reset_index(drop=True)
            if frames
            else pd.DataFrame(columns=KR_ETF_INVESTOR_FLOW_DAILY.column_names)
        )
        merged = _merge_normalized(root, incoming)
        wrote = False
        if not incoming.empty:
            write_dataset_atomic(
                merged,
                root / DATASET_ROOT,
                KR_ETF_INVESTOR_FLOW_DAILY,
                validate_kr_etf_investor_flow,
            )
            observed = read_dataset(
                root / DATASET_ROOT,
                KR_ETF_INVESTOR_FLOW_DAILY,
                validate_kr_etf_investor_flow,
            )
            _assert_same_frame(merged, observed)
            wrote = True
        retained_after = _retained_dates_by_symbol(root, plan.symbols)
        gaps = {
            window.symbol: sorted({
                value.isoformat()
                for candidate in plan.windows
                if candidate.symbol == window.symbol
                for value in candidate.sessions
                if value not in retained_after[window.symbol]
            })
            for window in plan.windows
        }
        gaps = {symbol: values for symbol, values in gaps.items() if values}
        return _receipt(root, {
            **base,
            "status": "EXPECTED_PROVIDER_GAPS" if gaps else "COMPLETE",
            "api_calls": active_provider.request_count,
            "rows_observed": len(incoming),
            "normalized_writes": [KR_ETF_INVESTOR_FLOW_DAILY.name] if wrote else [],
            "landing_files": landing_files,
            "provider_gap_dates": gaps,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as error:
        return _receipt(root, {
            **base,
            "status": "FAILED_LANDING_PRESERVED",
            "api_calls": int(getattr(active_provider, "request_count", 0) or 0),
            "landing_files": landing_files,
            "error_type": type(error).__name__,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        lock.release()


def _retained_dates_by_symbol(
    root: Path, symbols: tuple[str, ...],
) -> dict[str, set[date]]:
    result = {symbol: set() for symbol in symbols}
    dataset_root = root / DATASET_ROOT
    if not symbols or not dataset_root.exists() or not any(dataset_root.rglob("data.parquet")):
        return result
    frame = read_dataset(
        dataset_root,
        KR_ETF_INVESTOR_FLOW_DAILY,
        validate_kr_etf_investor_flow,
    )
    selected = frame.loc[frame["symbol"].astype(str).isin(symbols)]
    for symbol, rows in selected.groupby("symbol"):
        result[str(symbol)] = set(pd.to_datetime(rows["date"], errors="raise").dt.date)
    return result


def _merge_normalized(root: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    dataset_root = root / DATASET_ROOT
    has_existing = dataset_root.exists() and any(dataset_root.rglob("data.parquet"))
    if incoming.empty:
        if has_existing:
            return read_dataset(
                dataset_root,
                KR_ETF_INVESTOR_FLOW_DAILY,
                validate_kr_etf_investor_flow,
            )
        return incoming.copy()
    validate_kr_etf_investor_flow(incoming)
    if not has_existing:
        return incoming.copy()
    existing = read_dataset(
        dataset_root,
        KR_ETF_INVESTOR_FLOW_DAILY,
        validate_kr_etf_investor_flow,
    ).copy()
    incoming = incoming.copy()
    for frame in (existing, incoming):
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    keys = ["date", "symbol"]
    old = existing.set_index(keys)
    new = incoming.set_index(keys)
    common = old.index.intersection(new.index)
    compare = [*VALUE_COLUMNS, "provider"]
    if not common.empty:
        try:
            pd.testing.assert_frame_equal(
                old.loc[common, compare].sort_index(),
                new.loc[common, compare].sort_index(),
                check_dtype=False,
            )
        except AssertionError as error:
            raise KrEtfInvestorFlowDailyError(
                "retained Korean ETF investor flow changed"
            ) from error
    additions = new.loc[~new.index.isin(old.index)].reset_index()
    combined = pd.concat([existing, additions], ignore_index=True)
    combined = combined[list(KR_ETF_INVESTOR_FLOW_DAILY.column_names)].sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    validate_kr_etf_investor_flow(combined)
    return combined


def _assert_same_frame(expected: pd.DataFrame, observed: pd.DataFrame) -> None:
    left = expected.copy()
    right = observed.copy()
    for frame in (left, right):
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        frame["retrieved_at"] = pd.to_datetime(
            frame["retrieved_at"], errors="raise", utc=True,
        )
    left = left.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    right = right.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(left, right, check_dtype=False)
    except AssertionError as error:
        raise KrEtfInvestorFlowDailyError(
            "Korean ETF investor-flow normalized read-back differs"
        ) from error


def _frame_json_payload(frame: pd.DataFrame) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for index, row in frame.iterrows():
        values = {str(column): _json_scalar(value) for column, value in row.items()}
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
        raise KrEtfInvestorFlowDailyError("ETF investor-flow Landing read-back differs")
    return {
        "path": path.relative_to(root).as_posix(),
        "rows": len(frame),
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


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


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    body = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _receipt(root: Path, payload: dict[str, object]) -> dict[str, object]:
    receipt = dict(payload)
    receipt.setdefault("receipt_path", RECEIPT_PATH.as_posix())
    _atomic_json(root / RECEIPT_PATH, receipt)
    return receipt


def _default_dates(now: datetime, start: date | None, end: date | None) -> tuple[date, date]:
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    selected_end = end or calendar.latest_completed_session(now)
    selected_start = start or selected_end - timedelta(days=MAX_WINDOW_CALENDAR_DAYS - 1)
    return selected_start, selected_end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the selected KRX ETF investor-flow daily lane",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    start, end = _default_dates(now, args.start, args.end)
    try:
        result = run_kr_etf_investor_flow_daily(
            args.project_root,
            start=start,
            end=end,
            dry_run=args.dry_run,
            confirm_live=args.confirm_live,
            now=now,
        )
    except (KrEtfInvestorFlowDailyError, ValueError, LookupError):
        result = {
            "lane": LANE,
            "status": "CLI_FAILURE",
            "api_calls": 0,
            "error_type": "SANITIZED_INTERNAL_FAILURE",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if str(result["status"]).startswith(("COMPLETE", "NOOP", "DRY_RUN")) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATASET_ROOT",
    "KrEtfInvestorFlowDailyError",
    "KrEtfInvestorFlowPlan",
    "KrEtfInvestorFlowWindow",
    "LANE",
    "LANDING_ROOT",
    "MAX_WINDOW_CALENDAR_DAYS",
    "RECEIPT_PATH",
    "normalize_etf_investor_flow",
    "plan_kr_etf_investor_flow_daily",
    "resolve_kr_etf_investor_flow_symbols",
    "run_kr_etf_investor_flow_daily",
    "validate_kr_etf_investor_flow",
]
