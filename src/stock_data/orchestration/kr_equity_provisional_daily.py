from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Mapping
from uuid import uuid4

import pandas as pd

from stock_data.contracts.kr_equity import KR_EQUITY_PRICE_DAILY
from stock_data.contracts.kr_equity_provisional import (
    KR_EQUITY_PRICE_PROVISIONAL_DAILY,
    validate_kr_equity_price_provisional_daily,
)
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.providers.pykrx.kr_equity_provisional import (
    MARKETS,
    ProvisionalEquityProvider,
    PykrxProvisionalEquityClient,
    normalize_market_ohlcv,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_equity import validate_equity_price


LANDING_ROOT = Path("data/landing/pykrx/kr_equity_provisional_daily")
PRICE_ROOT = Path("data/normalized/kr_equity_price_provisional_daily")
STATE_PATH = Path("data/state/kr_equity_provisional_daily.json")
SCHEDULER_LANE = "KR_EQUITY_PROVISIONAL_DAILY"
CHECKPOINT_SCHEMA = "stock_data.kr_equity_provisional_daily_checkpoint.v1"
STATE_SCHEMA = "stock_data.kr_equity_provisional_daily_state.v1"
PHYSICAL_RETENTION_SESSIONS = 5


class ProvisionalEquityDailyError(RuntimeError):
    pass


def _has_dataset(path: Path) -> bool:
    return path.exists() and any(path.rglob("data.parquet"))


def _read_optional(root: Path) -> pd.DataFrame | None:
    if not _has_dataset(root):
        return None
    return read_dataset(
        root,
        KR_EQUITY_PRICE_PROVISIONAL_DAILY,
        validate_kr_equity_price_provisional_daily,
    )


def _retained_dates(project_root: Path) -> frozenset[date]:
    frame = _read_optional(project_root / PRICE_ROOT)
    if frame is None:
        return frozenset()
    return frozenset(pd.to_datetime(frame["date"], errors="raise").dt.date)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    body = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
        raise ProvisionalEquityDailyError("provisional equity Landing read-back differs") from error
    body = path.read_bytes()
    return {
        "path": path.as_posix(),
        "rows": len(frame),
        "columns": [str(value) for value in frame.columns],
        "index_name": frame.index.name,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def _merge(existing: pd.DataFrame | None, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing is None:
        combined = incoming.copy()
    else:
        keys = list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.primary_key)
        overlap = set(map(tuple, existing[keys].astype(str).to_numpy())) & set(
            map(tuple, incoming[keys].astype(str).to_numpy())
        )
        if overlap:
            raise ProvisionalEquityDailyError("provisional equity session already exists")
        combined = pd.concat([existing, incoming], ignore_index=True)
    combined = combined[list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.column_names)].sort_values(
        list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_kr_equity_price_provisional_daily(combined)
    return combined


def run_kr_equity_provisional_daily(
    project_root: Path,
    *,
    target_session: date,
    provider_factory: Callable[[], ProvisionalEquityProvider] | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Capture both pykrx equity markets, then atomically append one session."""

    root = project_root.resolve()
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    if tuple(calendar.sessions_in_range(target_session, target_session)) != (target_session,):
        raise ValueError("provisional Korean equity target must be an XKRX session")
    retained = _retained_dates(root)
    latest_before = max(retained) if retained else None
    if target_session in retained:
        return _scheduler_result(
            status="ALREADY_CURRENT",
            target_session=target_session,
            latest_before=latest_before,
            latest_after=latest_before,
            api_calls=0,
            rows=0,
            run_id=None,
        )

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_run_id = run_id or f"provisional-equity-{target_session:%Y%m%d}-{uuid4().hex}"
    if not selected_run_id or any(value not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for value in selected_run_id):
        raise ValueError("run_id contains unsupported characters")
    run_dir = (
        root / LANDING_ROOT / f"date={target_session:%Y%m%d}" / f"run={selected_run_id}"
    )
    if run_dir.exists():
        raise FileExistsError(run_dir)
    run_dir.mkdir(parents=True)
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint: dict[str, object] = {
        "schema": CHECKPOINT_SCHEMA,
        "status": "RUNNING",
        "run_id": selected_run_id,
        "target_session": target_session.isoformat(),
        "max_provider_calls": 2,
        "provider_calls": 0,
        "retry_count": 0,
        "normalized_writes": [],
        "observed_at": observed_at.isoformat(),
    }
    _atomic_json(checkpoint_path, checkpoint)
    provider = (
        provider_factory()
        if provider_factory is not None
        else PykrxProvisionalEquityClient(manual=True, requested_days=1)
    )
    raw_frames: dict[str, pd.DataFrame] = {}
    landing: dict[str, object] = {}
    try:
        for market in MARKETS:
            raw = provider.get_market_ohlcv_by_ticker(target_session, market)
            relative = Path(f"market={market}") / "ohlcv.parquet"
            receipt = _capture_frame_new(run_dir / relative, raw)
            receipt["path"] = (run_dir / relative).relative_to(root).as_posix()
            raw_frames[market] = raw
            landing[market] = receipt
            checkpoint.update(status="CAPTURING", provider_calls=provider.request_count)
            _atomic_json(checkpoint_path, checkpoint)
        if provider.request_count != 2:
            raise ProvisionalEquityDailyError("provisional equity provider call accounting differs")
        empty_markets = [market for market, frame in raw_frames.items() if frame.empty]
        if len(empty_markets) == len(MARKETS):
            checkpoint.update(
                status="EXPECTED_PROVIDER_LAG",
                provider_calls=2,
                landing=landing,
                completed_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            _atomic_json(checkpoint_path, checkpoint)
            return _scheduler_result(
                status="EXPECTED_PROVIDER_LAG",
                target_session=target_session,
                latest_before=latest_before,
                latest_after=latest_before,
                api_calls=2,
                rows=0,
                run_id=selected_run_id,
            )
        if empty_markets:
            raise ProvisionalEquityDailyError(
                f"provisional equity market frames are inconsistently empty: {empty_markets}"
            )

        parts = [
            normalize_market_ohlcv(
                raw_frames[market],
                market=market,
                source_date=target_session,
                observed_at=observed_at,
            )
            for market in MARKETS
        ]
        incoming = pd.concat(parts, ignore_index=True).sort_values(
            list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.sort_key), kind="stable"
        ).reset_index(drop=True)
        combined = _merge(_read_optional(root / PRICE_ROOT), incoming)
        write_dataset_atomic(
            combined,
            root / PRICE_ROOT,
            KR_EQUITY_PRICE_PROVISIONAL_DAILY,
            validate_kr_equity_price_provisional_daily,
        )
        read_back = read_dataset(
            root / PRICE_ROOT,
            KR_EQUITY_PRICE_PROVISIONAL_DAILY,
            validate_kr_equity_price_provisional_daily,
        )
        pd.testing.assert_frame_equal(combined, read_back, check_dtype=False)
        completed = datetime.now(timezone.utc).isoformat()
        checkpoint.update(
            status="SUCCEEDED",
            provider_calls=2,
            incoming_rows=len(incoming),
            retained_rows=len(read_back),
            normalized_writes=[KR_EQUITY_PRICE_PROVISIONAL_DAILY.name],
            landing=landing,
            completed_at_utc=completed,
        )
        _atomic_json(checkpoint_path, checkpoint)
        _atomic_json(
            root / STATE_PATH,
            {
                "schema": STATE_SCHEMA,
                "latest_completed": target_session.isoformat(),
                "last_checkpoint": checkpoint_path.relative_to(root).as_posix(),
                "retained_rows": len(read_back),
                "updated_at_utc": completed,
            },
        )
        return _scheduler_result(
            status="UPDATED",
            target_session=target_session,
            latest_before=latest_before,
            latest_after=target_session,
            api_calls=2,
            rows=len(incoming),
            run_id=selected_run_id,
        )
    except Exception as error:
        checkpoint.update(
            status="STOPPED",
            provider_calls=provider.request_count,
            error_type=type(error).__name__,
            stopped_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(checkpoint_path, checkpoint)
        raise


def cleanup_canonicalized_provisional_rows(
    project_root: Path,
    *,
    reference_session: date,
    keep_sessions: int = PHYSICAL_RETENTION_SESSIONS,
) -> dict[str, object]:
    """Drop canonicalized provisional rows only after a five-session grace period."""

    if keep_sessions < 1:
        raise ValueError("keep_sessions must be positive")
    root = project_root.resolve()
    provisional = _read_optional(root / PRICE_ROOT)
    canonical_root = root / "data/normalized" / KR_EQUITY_PRICE_DAILY.name
    if provisional is None or not _has_dataset(canonical_root):
        return {"status": "NOOP", "removed_rows": 0}
    canonical = read_dataset(canonical_root, KR_EQUITY_PRICE_DAILY, validate_equity_price)
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    cutoff = reference_session
    for _ in range(keep_sessions):
        cutoff = calendar.previous_trading_day(cutoff)
    keys = list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.primary_key)
    canonical_keys = set(map(tuple, canonical[keys].astype(str).to_numpy()))
    provisional_keys = provisional[keys].astype(str).apply(tuple, axis=1)
    dates = pd.to_datetime(provisional["date"], errors="raise").dt.date
    remove = provisional_keys.isin(canonical_keys) & dates.le(cutoff)
    if not remove.any():
        return {"status": "NOOP", "removed_rows": 0, "cutoff": cutoff.isoformat()}
    retained = provisional.loc[~remove].reset_index(drop=True)
    write_dataset_atomic(
        retained,
        root / PRICE_ROOT,
        KR_EQUITY_PRICE_PROVISIONAL_DAILY,
        lambda frame: validate_kr_equity_price_provisional_daily(frame, allow_empty=True),
    )
    return {
        "status": "CLEANED",
        "removed_rows": int(remove.sum()),
        "cutoff": cutoff.isoformat(),
    }


def _scheduler_result(
    *,
    status: str,
    target_session: date,
    latest_before: date | None,
    latest_after: date | None,
    api_calls: int,
    rows: int,
    run_id: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "lane": SCHEDULER_LANE,
        "status": status,
        "target_session": target_session.isoformat(),
        "latest_before": latest_before.isoformat() if latest_before else None,
        "latest_after": latest_after.isoformat() if latest_after else None,
        "api_calls": api_calls,
        "retry_count": 0,
        "predictive_use": False,
        "rows": rows,
        "run_id": run_id,
    }


__all__ = [
    "PHYSICAL_RETENTION_SESSIONS",
    "ProvisionalEquityDailyError",
    "cleanup_canonicalized_provisional_rows",
    "run_kr_equity_provisional_daily",
]
