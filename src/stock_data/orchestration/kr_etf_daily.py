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

from stock_data.contracts.kr_etf import (
    KR_ETF_MASTER,
    KR_ETF_PRICE_DAILY,
    infer_kr_etf_leverage_multiple,
)
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.providers.pykrx.kr_etf import KrEtfProvider
from stock_data.providers.pykrx.kr_etf import PykrxEtfClient
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_etf import (
    validate_kr_etf_master,
    validate_kr_etf_price_daily,
)


LANDING_ROOT = Path("data/landing/pykrx/kr_etf_daily")
STATE_PATH = Path("data/state/kr_etf_daily.json")
MASTER_ROOT = Path("data/normalized/kr_etf_master")
PRICE_ROOT = Path("data/normalized/kr_etf_price_daily")
STATE_SCHEMA = "stock_data.kr_etf_daily_state.v1"
CHECKPOINT_SCHEMA = "stock_data.kr_etf_daily_checkpoint.v1"
MAX_SYMBOLS = 10
MAX_CALENDAR_DAYS = 10
MAX_SCHEDULER_SESSIONS = 30
MARKET = "KRX"
SCHEDULER_LANE = "KR_ETF_PRICE_DAILY"
WATCHLIST_PATH = Path("artifacts/local_user/watchlists.json")

PYKRX_PRICE_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
    "NAV": "nav",
}


class KrEtfDailyError(RuntimeError):
    pass


@dataclass(frozen=True)
class KrEtfSymbolWindow:
    symbol: str
    start: date
    end: date
    latest_before: date | None
    sessions: tuple[date, ...]


def normalize_symbols(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    symbols = tuple(str(value).strip() for value in values)
    if not symbols or len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"symbols must contain between 1 and {MAX_SYMBOLS} values")
    if any(not re.fullmatch(r"[0-9A-Z]{6}", value) for value in symbols):
        raise ValueError("every Korean ETF symbol must be a six-character KRX code (digits or upper-case letters)")
    if len(symbols) != len(set(symbols)):
        raise ValueError("Korean ETF symbols must be unique")
    return symbols


def validate_window(
    start: date, end: date, *, max_calendar_days: int | None = MAX_CALENDAR_DAYS,
) -> int:
    days = (end - start).days + 1
    if days < 1 or (max_calendar_days is not None and days > max_calendar_days):
        upper = str(max_calendar_days) if max_calendar_days is not None else "unbounded"
        raise ValueError(
            f"Korean ETF live range must contain 1..{upper} calendar days"
        )
    return days


def resolve_kr_etf_symbols(project_root: Path) -> tuple[str, ...]:
    """Return the bounded union of watchlisted and retained current-list ETFs."""

    root = project_root.resolve()
    selected: set[str] = set()
    watchlist_path = root / WATCHLIST_PATH
    if watchlist_path.is_file():
        payload = _read_json(watchlist_path)
        lists = payload.get("lists") if payload is not None else None
        if not isinstance(lists, list):
            raise KrEtfDailyError("Korean ETF watchlist lists are invalid")
        for watchlist in lists:
            if not isinstance(watchlist, dict) or not isinstance(watchlist.get("items"), list):
                raise KrEtfDailyError("Korean ETF watchlist items are invalid")
            for item in watchlist["items"]:
                if not isinstance(item, dict):
                    raise KrEtfDailyError("Korean ETF watchlist item is invalid")
                if item.get("market") == MARKET and item.get("security_type") == "ETF":
                    selected.add(str(item.get("symbol", "")).strip())

    master_root = root / MASTER_ROOT
    if master_root.exists() and any(master_root.rglob("data.parquet")):
        master = read_dataset(master_root, KR_ETF_MASTER, validate_kr_etf_master)
        selected.update(master["symbol"].astype(str))

    # Manual (web-entered) accounts: any Korean ETF the user holds is priced by this
    # lane too, so the 내 계좌 page never needs a hand-typed 현재가 for KRX ETFs.
    selected.update(_manual_account_etf_symbols(root))

    if not selected:
        return ()
    return normalize_symbols(sorted(selected))


MANUAL_ACCOUNT_PATHS = (
    Path("artifacts/local_user/manual_accounts.json"),
    Path("artifacts/local_user/manual_accounts_web.json"),
)
UNIVERSE_ROOT = Path("data/normalized/kr_etf_universe_daily")


def _manual_account_etf_symbols(root: Path) -> set[str]:
    """Tickers held in manual accounts that the retained KRX ETF universe knows as ETFs."""

    tickers: set[str] = set()
    for relative in MANUAL_ACCOUNT_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        payload = _read_json(path)
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        if not isinstance(accounts, list):
            continue
        for account in accounts:
            positions = account.get("positions") if isinstance(account, dict) else None
            for position in positions or []:
                if not isinstance(position, dict):
                    continue
                ticker = str(position.get("ticker") or "").strip().upper()
                if len(ticker) == 6 and ticker.isalnum():
                    tickers.add(ticker)
    if not tickers:
        return set()
    universe_root = root / UNIVERSE_ROOT
    if not universe_root.exists() or not any(universe_root.rglob("*.parquet")):
        return set()
    import pyarrow.dataset as ds

    table = ds.dataset(str(universe_root), format="parquet", partitioning=None).to_table(
        columns=["symbol", "security_type"],
    ).to_pandas()
    known = set(
        table.loc[table["security_type"].astype(str).eq("ETF"), "symbol"].astype(str).str.upper()
    )
    return tickers & known


def plan_kr_etf_symbol_windows(
    project_root: Path,
    *,
    symbols: tuple[str, ...] | list[str],
    target_session: date,
    max_sessions: int = MAX_SCHEDULER_SESSIONS,
) -> tuple[KrEtfSymbolWindow, ...]:
    """Plan recent, per-symbol XKRX windows without exceeding the session cap."""

    selected = normalize_symbols(symbols)
    if max_sessions < 1 or max_sessions > MAX_SCHEDULER_SESSIONS:
        raise ValueError(f"max_sessions must be between 1 and {MAX_SCHEDULER_SESSIONS}")
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    if tuple(calendar.sessions_in_range(target_session, target_session)) != (target_session,):
        raise ValueError("Korean ETF target must be an XKRX session")
    latest = _retained_latest_by_symbol(project_root.resolve(), selected)
    windows: list[KrEtfSymbolWindow] = []
    for symbol in selected:
        retained = latest[symbol]
        if retained is not None and retained >= target_session:
            continue
        if retained is None:
            start = target_session
            for _ in range(max_sessions - 1):
                start = calendar.previous_trading_day(start)
            sessions = tuple(calendar.sessions_in_range(start, target_session))
        else:
            start = calendar.next_trading_day(retained)
            sessions = tuple(calendar.sessions_in_range(start, target_session))
            if len(sessions) > max_sessions:
                sessions = sessions[-max_sessions:]
        if not sessions:
            continue
        windows.append(KrEtfSymbolWindow(
            symbol=symbol,
            start=sessions[0],
            end=target_session,
            latest_before=retained,
            sessions=sessions,
        ))
    return tuple(windows)


def run_kr_etf_scheduler_lane(
    project_root: Path,
    *,
    target_session: date,
    provider_factory: Callable[[], KrEtfProvider] | None = None,
) -> dict[str, object]:
    """Run the bounded selected-ETF lane for one completed KRX target session."""

    root = project_root.resolve()
    symbols = resolve_kr_etf_symbols(root)
    if not symbols:
        return _scheduler_result(
            status="NO_SYMBOLS_CONFIGURED", target_session=target_session,
            latest_before={}, latest_after={}, api_calls=0, symbols=(),
        )
    latest_before = _retained_latest_by_symbol(root, symbols)
    windows = plan_kr_etf_symbol_windows(
        root, symbols=symbols, target_session=target_session,
    )
    if not windows:
        return _scheduler_result(
            status="ALREADY_CURRENT", target_session=target_session,
            latest_before=latest_before, latest_after=latest_before,
            api_calls=0, symbols=symbols,
        )

    active = tuple(window.symbol for window in windows)
    starts = {window.symbol: window.start for window in windows}
    provider = (
        provider_factory()
        if provider_factory is not None
        else PykrxEtfClient(manual=True, requested_days=1)
    )
    operation = run_kr_etf_daily(
        root,
        symbols=active,
        start=min(starts.values()),
        end=target_session,
        provider=provider,
        symbol_starts=starts,
        max_calendar_days=None,
    )
    latest_after = _retained_latest_by_symbol(root, symbols)
    observed = _retained_dates_by_symbol(root, active)
    gaps = {
        window.symbol: [
            session.isoformat() for session in window.sessions
            if session not in observed[window.symbol]
        ]
        for window in windows
    }
    gaps = {symbol: values for symbol, values in gaps.items() if values}
    target_missing = any(
        target_session not in observed[window.symbol] for window in windows
    )
    return _scheduler_result(
        status="EXPECTED_PROVIDER_LAG" if target_missing else "UPDATED",
        target_session=target_session,
        latest_before=latest_before,
        latest_after=latest_after,
        api_calls=int(operation.get("provider_calls", 0) or 0),
        symbols=symbols,
        provider_gap_dates=gaps or None,
    )


def normalize_master(names: Mapping[str, str], *, source_date: date) -> pd.DataFrame:
    rows = [{
        "symbol": symbol,
        "name": str(name).strip(),
        "market": MARKET,
        "security_type": "ETF",
        "listing_status": "LISTED_AT_SOURCE_DATE",
        "listing_date": None,
        "leverage_multiple": infer_kr_etf_leverage_multiple(str(name)),
        "source": "pykrx",
        "source_operation": "get_etf_ticker_list+get_etf_ticker_name",
        "source_date": source_date.isoformat(),
    } for symbol, name in sorted(names.items())]
    frame = pd.DataFrame(rows, columns=KR_ETF_MASTER.column_names)
    validate_kr_etf_master(frame)
    return frame


def normalize_prices(
    raw: pd.DataFrame, *, symbol: str, start: date, end: date,
) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=KR_ETF_PRICE_DAILY.column_names)
    if raw.index.has_duplicates:
        raise KrEtfDailyError(f"pykrx ETF OHLCV index contains duplicates: {symbol}")
    missing = set(PYKRX_PRICE_COLUMNS) - set(raw.columns)
    if missing:
        raise KrEtfDailyError(
            f"pykrx ETF OHLCV columns are missing for {symbol}: {sorted(missing)}"
        )
    frame = raw.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "date", **PYKRX_PRICE_COLUMNS})
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    if frame["date"].min() < start.isoformat() or frame["date"].max() > end.isoformat():
        raise KrEtfDailyError(f"pykrx ETF rows exceed the requested range: {symbol}")
    frame.insert(1, "symbol", symbol)
    for column in ("open", "high", "low", "close", "volume", "trading_value"):
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any() or (numeric % 1 != 0).any():
            raise KrEtfDailyError(f"pykrx ETF integer field differs: {symbol}/{column}")
        frame[column] = numeric.astype("int64")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce").astype("Float64")
    frame["source"] = "pykrx"
    frame["source_operation"] = "get_etf_ohlcv_by_date"
    frame["source_date"] = frame["date"]
    frame = frame[list(KR_ETF_PRICE_DAILY.column_names)].sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    validate_kr_etf_price_daily(frame)
    return frame


def run_kr_etf_daily(
    project_root: Path,
    *,
    symbols: tuple[str, ...] | list[str],
    start: date,
    end: date,
    provider: KrEtfProvider,
    run_id: str | None = None,
    now: datetime | None = None,
    symbol_starts: Mapping[str, date] | None = None,
    max_calendar_days: int | None = MAX_CALENDAR_DAYS,
) -> dict[str, object]:
    """Capture, read back, validate, and atomically promote one bounded ETF run."""

    root = project_root.resolve()
    selected = normalize_symbols(symbols)
    days = validate_window(start, end, max_calendar_days=max_calendar_days)
    starts = _normalize_symbol_starts(selected, start, end, symbol_starts)
    request_key = _request_key(
        selected, start, end, starts if symbol_starts is not None else None,
    )
    prior = _successful_request(root, request_key, selected)
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
        }

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_run_id = run_id or stamp.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", selected_run_id):
        raise ValueError("run_id contains unsupported characters")
    run_dir = root / LANDING_ROOT / f"range={start:%Y%m%d}_{end:%Y%m%d}" / f"run={selected_run_id}"
    if run_dir.exists():
        raise FileExistsError(run_dir)
    run_dir.mkdir(parents=True)
    checkpoint_path = run_dir / "checkpoint.json"
    max_calls = 1 + 2 * len(selected)
    checkpoint: dict[str, object] = {
        "schema": CHECKPOINT_SCHEMA,
        "status": "RUNNING",
        "run_id": selected_run_id,
        "request_key": request_key,
        "symbols": list(selected),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbol_starts": {symbol: starts[symbol].isoformat() for symbol in selected},
        "calendar_days": days,
        "max_provider_calls": max_calls,
        "provider_calls": 0,
        "retry_count": 0,
        "normalized_writes": [],
        "started_at_utc": stamp.isoformat(),
    }
    _atomic_json(checkpoint_path, checkpoint)
    writes: list[str] = []
    try:
        listed = provider.get_etf_ticker_list(end)
        list_receipt = _capture_json_new(run_dir / "ticker_list.json", list(listed))
        list_receipt["path"] = (run_dir / "ticker_list.json").relative_to(root).as_posix()
        checkpoint.update(status="CAPTURING", provider_calls=provider.request_count)
        _atomic_json(checkpoint_path, checkpoint)
        missing = sorted(set(selected) - set(listed))
        if missing:
            raise KrEtfDailyError(
                f"requested symbols are not in the exact-date ETF list: {missing}"
            )

        names: dict[str, str] = {}
        raw_prices: dict[str, pd.DataFrame] = {}
        landing: dict[str, object] = {"ticker_list": list_receipt, "symbols": {}}
        for symbol in selected:
            symbol_dir = run_dir / f"symbol={symbol}"
            name = provider.get_etf_ticker_name(symbol)
            name_receipt = _capture_json_new(symbol_dir / "name.json", {"name": name})
            name_receipt["path"] = (symbol_dir / "name.json").relative_to(root).as_posix()
            raw = provider.get_etf_ohlcv_by_date(starts[symbol], end, symbol)
            raw_receipt = _capture_frame_new(symbol_dir / "ohlcv.parquet", raw)
            raw_receipt["path"] = (symbol_dir / "ohlcv.parquet").relative_to(root).as_posix()
            names[symbol] = name
            raw_prices[symbol] = raw
            landing["symbols"][symbol] = {
                "name": name_receipt,
                "ohlcv": raw_receipt,
            }
            checkpoint["provider_calls"] = provider.request_count
            _atomic_json(checkpoint_path, checkpoint)
        if provider.request_count != max_calls:
            raise KrEtfDailyError("pykrx ETF provider call accounting differs")

        incoming_master = normalize_master(names, source_date=end)
        normalized = [
            normalize_prices(
                raw_prices[symbol], symbol=symbol, start=starts[symbol], end=end,
            )
            for symbol in selected
        ]
        nonempty = [frame for frame in normalized if not frame.empty]
        incoming_prices = (
            pd.concat(nonempty, ignore_index=True).sort_values(
                ["date", "symbol"], kind="stable"
            ).reset_index(drop=True)
            if nonempty else pd.DataFrame(columns=KR_ETF_PRICE_DAILY.column_names)
        )
        if not incoming_prices.empty:
            validate_kr_etf_price_daily(incoming_prices)

        master = _merge_master(root, incoming_master)
        prices = _merge_prices(root, incoming_prices)
        checkpoint.update(
            status="VALIDATED",
            landing=landing,
            incoming_master_rows=len(incoming_master),
            incoming_price_rows=len(incoming_prices),
        )
        _atomic_json(checkpoint_path, checkpoint)

        if not incoming_prices.empty:
            write_dataset_atomic(
                prices, root / PRICE_ROOT, KR_ETF_PRICE_DAILY,
                validate_kr_etf_price_daily,
            )
            read_back = read_dataset(
                root / PRICE_ROOT, KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily,
            )
            _assert_same_frame(prices, read_back, keys=("date", "symbol"))
            writes.append(KR_ETF_PRICE_DAILY.name)
        write_dataset_atomic(
            master, root / MASTER_ROOT, KR_ETF_MASTER, validate_kr_etf_master,
        )
        master_read_back = read_dataset(
            root / MASTER_ROOT, KR_ETF_MASTER, validate_kr_etf_master,
        )
        _assert_same_frame(master, master_read_back, keys=("market", "symbol"))
        writes.append(KR_ETF_MASTER.name)

        completed = datetime.now(timezone.utc).isoformat()
        checkpoint.update(
            status=("SUCCEEDED" if not incoming_prices.empty else "SUCCEEDED_VALID_EMPTY_PRICES"),
            provider_calls=provider.request_count,
            normalized_writes=writes,
            normalized_manifests={
                dataset: _dataset_manifest(root / "data/normalized" / dataset)
                for dataset in writes
            },
            completed_at_utc=completed,
        )
        _atomic_json(checkpoint_path, checkpoint)
        _record_success(root, request_key, checkpoint_path, checkpoint)
        return {
            "status": checkpoint["status"],
            "request_key": request_key,
            "run_id": selected_run_id,
            "symbols": list(selected),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "provider_calls": provider.request_count,
            "master_rows": len(incoming_master),
            "price_rows": len(incoming_prices),
            "normalized_writes": list(writes),
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


def _successful_request(
    root: Path, request_key: str, symbols: tuple[str, ...],
) -> dict[str, object] | None:
    state = _read_json(root / STATE_PATH)
    if state is None:
        return None
    if state.get("schema") != STATE_SCHEMA or not isinstance(state.get("runs"), dict):
        raise KrEtfDailyError("Korean ETF state schema differs")
    record = state["runs"].get(request_key)
    if not isinstance(record, dict) or record.get("status") != "SUCCEEDED":
        return None
    checkpoint_path = _safe_relative(root, record.get("checkpoint"))
    checkpoint = _read_json(checkpoint_path)
    if checkpoint is None or checkpoint.get("request_key") != request_key:
        raise KrEtfDailyError("Korean ETF successful checkpoint is unavailable")
    master = read_dataset(root / MASTER_ROOT, KR_ETF_MASTER, validate_kr_etf_master)
    if not set(symbols) <= set(master["symbol"].astype(str)):
        raise KrEtfDailyError("Korean ETF successful state differs from master")
    if int(record.get("price_rows", 0)):
        read_dataset(root / PRICE_ROOT, KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily)
    return record


def _record_success(
    root: Path, request_key: str, checkpoint_path: Path, checkpoint: Mapping[str, object],
) -> None:
    state_path = root / STATE_PATH
    state = _read_json(state_path) or {"schema": STATE_SCHEMA, "runs": {}}
    if state.get("schema") != STATE_SCHEMA or not isinstance(state.get("runs"), dict):
        raise KrEtfDailyError("Korean ETF state schema differs")
    runs = dict(state["runs"])
    runs[request_key] = {
        "status": checkpoint["status"],
        "checkpoint": checkpoint_path.relative_to(root).as_posix(),
        "symbols": checkpoint["symbols"],
        "start": checkpoint["start"],
        "end": checkpoint["end"],
        "provider_calls": checkpoint["provider_calls"],
        "price_rows": checkpoint["incoming_price_rows"],
        "completed_at_utc": checkpoint["completed_at_utc"],
    }
    _atomic_json(state_path, {"schema": STATE_SCHEMA, "runs": runs})


def _merge_master(root: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    path = root / MASTER_ROOT
    if not path.exists() or not any(path.rglob("data.parquet")):
        return incoming.copy()
    existing = read_dataset(path, KR_ETF_MASTER, validate_kr_etf_master)
    keys = ["market", "symbol"]
    common = set(map(tuple, existing[keys].to_numpy())) & set(map(tuple, incoming[keys].to_numpy()))
    if common:
        compare = [
            "name", "security_type", "listing_status",
            "listing_date", "leverage_multiple", "source", "source_operation",
        ]
        old = existing.set_index(keys).loc[sorted(common), compare].reset_index(drop=True)
        new = incoming.set_index(keys).loc[sorted(common), compare].reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                _null_normalized(old), _null_normalized(new), check_dtype=False,
            )
        except AssertionError as error:
            raise KrEtfDailyError("Korean ETF master identity changed") from error
    # A historical backfill must not roll the master back: per key keep the row
    # with the newest source_date (existing wins ties).
    combined = pd.concat([incoming, existing], ignore_index=True)
    combined["_source_date"] = pd.to_datetime(combined["source_date"])
    combined = combined.sort_values(["_source_date"], kind="stable").drop_duplicates(keys, keep="last")
    combined = combined.drop(columns="_source_date")
    combined = combined[list(KR_ETF_MASTER.column_names)].sort_values(keys, kind="stable").reset_index(drop=True)
    validate_kr_etf_master(combined)
    return combined


def _merge_prices(root: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    path = root / PRICE_ROOT
    if incoming.empty:
        if path.exists() and any(path.rglob("data.parquet")):
            return read_dataset(path, KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily)
        return incoming.copy()
    if not path.exists() or not any(path.rglob("data.parquet")):
        return incoming.copy()
    existing = read_dataset(path, KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily)
    keys = ["date", "symbol"]
    common = set(map(tuple, existing[keys].to_numpy())) & set(map(tuple, incoming[keys].to_numpy()))
    if common:
        compare = list(KR_ETF_PRICE_DAILY.column_names)
        old = existing.set_index(keys).loc[sorted(common), compare[2:]].reset_index(drop=True)
        new = incoming.set_index(keys).loc[sorted(common), compare[2:]].reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                _null_normalized(old), _null_normalized(new), check_dtype=False,
            )
        except AssertionError as error:
            raise KrEtfDailyError("Korean ETF retained daily price changed") from error
    existing_keys = set(map(tuple, existing[keys].to_numpy()))
    additions = incoming.loc[~incoming[keys].apply(tuple, axis=1).isin(existing_keys)]
    combined = pd.concat([existing, additions], ignore_index=True)
    combined = combined[list(KR_ETF_PRICE_DAILY.column_names)].sort_values(keys, kind="stable").reset_index(drop=True)
    validate_kr_etf_price_daily(combined)
    return combined


def _assert_same_frame(
    expected: pd.DataFrame, observed: pd.DataFrame, *, keys: tuple[str, ...],
) -> None:
    left = expected.sort_values(list(keys), kind="stable").reset_index(drop=True)
    right = observed.sort_values(list(keys), kind="stable").reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            _null_normalized(left), _null_normalized(right), check_dtype=False,
        )
    except AssertionError as error:
        raise KrEtfDailyError("Korean ETF normalized read-back differs") from error


def _null_normalized(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.astype(object)
    return result.where(pd.notna(result), None)


def _capture_json_new(path: Path, payload: object) -> dict[str, object]:
    body = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes_new(path, body)
    observed = path.read_bytes()
    if observed != body or json.loads(observed) != payload:
        raise KrEtfDailyError("Korean ETF JSON Landing read-back differs")
    return {"path": path.as_posix(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def _capture_frame_new(path: Path, frame: pd.DataFrame) -> dict[str, object]:
    prepared = frame.copy(deep=True)
    prepared.insert(0, "__provider_index__", frame.index)
    prepared = prepared.reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet.tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        prepared.to_parquet(temporary, index=False)
        if path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    observed = pd.read_parquet(path)
    try:
        pd.testing.assert_frame_equal(prepared, observed, check_dtype=False)
    except AssertionError as error:
        raise KrEtfDailyError("Korean ETF frame Landing read-back differs") from error
    body = path.read_bytes()
    return {
        "path": path.as_posix(),
        "rows": len(frame),
        "columns": [str(value) for value in frame.columns],
        "index_name": frame.index.name,
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
        raise KrEtfDailyError(f"Korean ETF normalized dataset is empty: {path.name}")
    return {"files": files, "sha256": digest.hexdigest()}


def _request_key(
    symbols: tuple[str, ...], start: date, end: date,
    symbol_starts: Mapping[str, date] | None,
) -> str:
    payload: dict[str, object] = {
        "symbols": list(symbols), "start": start.isoformat(), "end": end.isoformat(),
    }
    if symbol_starts is not None:
        payload["symbol_starts"] = {
            symbol: symbol_starts[symbol].isoformat() for symbol in symbols
        }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _normalize_symbol_starts(
    symbols: tuple[str, ...], start: date, end: date,
    supplied: Mapping[str, date] | None,
) -> dict[str, date]:
    starts = {symbol: start for symbol in symbols} if supplied is None else dict(supplied)
    if set(starts) != set(symbols):
        raise ValueError("symbol_starts must define every selected Korean ETF exactly once")
    if any(not isinstance(value, date) or value < start or value > end for value in starts.values()):
        raise ValueError("symbol_starts must stay inside the requested range")
    return starts


def _retained_dates_by_symbol(
    root: Path, symbols: tuple[str, ...],
) -> dict[str, set[date]]:
    observed = {symbol: set() for symbol in symbols}
    price_root = root / PRICE_ROOT
    if not price_root.exists() or not any(price_root.rglob("data.parquet")):
        return observed
    prices = read_dataset(
        price_root, KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily,
    )
    for symbol, group in prices.loc[prices["symbol"].astype(str).isin(symbols)].groupby("symbol"):
        observed[str(symbol)] = set(pd.to_datetime(group["date"], errors="raise").dt.date)
    return observed


def _retained_latest_by_symbol(
    root: Path, symbols: tuple[str, ...],
) -> dict[str, date | None]:
    return {
        symbol: max(values) if values else None
        for symbol, values in _retained_dates_by_symbol(root, symbols).items()
    }


def _scheduler_result(
    *,
    status: str,
    target_session: date,
    latest_before: Mapping[str, date | None],
    latest_after: Mapping[str, date | None],
    api_calls: int,
    symbols: tuple[str, ...],
    provider_gap_dates: Mapping[str, list[str]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "lane": SCHEDULER_LANE,
        "status": status,
        "target_session": target_session.isoformat(),
        "latest_before": {
            symbol: value.isoformat() if value is not None else None
            for symbol, value in latest_before.items()
        },
        "latest_after": {
            symbol: value.isoformat() if value is not None else None
            for symbol, value in latest_after.items()
        },
        "api_calls": api_calls,
        "retry_count": 0,
        "predictive_use": False,
        "symbols": list(symbols),
    }
    if provider_gap_dates:
        result["provider_gap_dates"] = dict(provider_gap_dates)
    return result


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
        raise KrEtfDailyError(f"invalid Korean ETF JSON object: {path.name}")
    return payload


def _safe_relative(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise KrEtfDailyError("Korean ETF retained path is invalid")
    path = (root / value).resolve(strict=True)
    if path == root or root not in path.parents or path.is_symlink():
        raise KrEtfDailyError("Korean ETF retained path is outside the project")
    return path


__all__ = [
    "CHECKPOINT_SCHEMA", "KrEtfDailyError", "KrEtfSymbolWindow",
    "MAX_CALENDAR_DAYS", "MAX_SCHEDULER_SESSIONS", "MAX_SYMBOLS",
    "normalize_master", "normalize_prices", "normalize_symbols",
    "plan_kr_etf_symbol_windows", "resolve_kr_etf_symbols", "run_kr_etf_daily",
    "run_kr_etf_scheduler_lane", "validate_window",
]
