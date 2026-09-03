from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping
from uuid import uuid4

import pandas as pd

from stock_data.contracts.kr_etf import (
    KR_ETF_MASTER,
    KR_ETF_PRICE_DAILY,
    infer_kr_etf_leverage_multiple,
)
from stock_data.providers.pykrx.kr_etf import KrEtfProvider
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
MARKET = "KRX"

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


def normalize_symbols(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    symbols = tuple(str(value).strip() for value in values)
    if not symbols or len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"symbols must contain between 1 and {MAX_SYMBOLS} values")
    if any(not re.fullmatch(r"\d{6}", value) for value in symbols):
        raise ValueError("every Korean ETF symbol must be a six-digit KRX code")
    if len(symbols) != len(set(symbols)):
        raise ValueError("Korean ETF symbols must be unique")
    return symbols


def validate_window(start: date, end: date) -> int:
    days = (end - start).days + 1
    if days < 1 or days > MAX_CALENDAR_DAYS:
        raise ValueError(
            f"Korean ETF live range must contain 1..{MAX_CALENDAR_DAYS} calendar days"
        )
    return days


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
) -> dict[str, object]:
    """Capture, read back, validate, and atomically promote one bounded ETF run."""

    root = project_root.resolve()
    selected = normalize_symbols(symbols)
    days = validate_window(start, end)
    request_key = _request_key(selected, start, end)
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
            raw = provider.get_etf_ohlcv_by_date(start, end, symbol)
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
            normalize_prices(raw_prices[symbol], symbol=symbol, start=start, end=end)
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
    if not isinstance(record, dict) or not str(record.get("status", "")).startswith("SUCCEEDED"):
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
            "name", "market", "symbol", "security_type", "listing_status",
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
    incoming_keys = set(map(tuple, incoming[keys].to_numpy()))
    keep = ~existing[keys].apply(tuple, axis=1).isin(incoming_keys)
    combined = pd.concat([existing.loc[keep], incoming], ignore_index=True)
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


def _request_key(symbols: tuple[str, ...], start: date, end: date) -> str:
    body = json.dumps({
        "symbols": list(symbols), "start": start.isoformat(), "end": end.isoformat(),
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
    "CHECKPOINT_SCHEMA", "KrEtfDailyError", "MAX_CALENDAR_DAYS", "MAX_SYMBOLS",
    "normalize_master", "normalize_prices", "normalize_symbols", "run_kr_etf_daily",
    "validate_window",
]
