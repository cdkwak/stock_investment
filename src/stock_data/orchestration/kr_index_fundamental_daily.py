from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import shutil
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.contracts.kr_index_fundamental_daily import KR_INDEX_FUNDAMENTAL_DAILY
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.pipelines.kr_index_fundamental_promotion import (
    merge_index_fundamental_frames,
    normalize_index_fundamental_response,
    prepare_retained_index_fundamentals,
)
from stock_data.providers.pykrx.kr_index_fundamental_daily import (
    BodyFetcher,
    capture_index_fundamental_range,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_index_fundamental_daily import validate_kr_index_fundamental_daily


DEFAULT_RETAINED_ROOT = Path(
    "data/landing/diagnostics/pykrx_fundamentals_pilot/"
    "20260815T015855Z_5c6ec1d853f445e4aabdb076e2700a73"
)


class IndexFundamentalDailyError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexFundamentalDailyResult:
    status: str
    run_id: str | None
    api_calls: int
    latest_before: str
    latest_after: str
    inserted_rows: int
    reason: str


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _latest_by_market(frame: pd.DataFrame) -> dict[str, date]:
    latest = {
        market: date.fromisoformat(str(group["date"].max()))
        for market, group in frame.groupby("market", sort=True)
    }
    if set(latest) != {"KOSPI", "KOSDAQ"} or len(set(latest.values())) != 1:
        raise IndexFundamentalDailyError(f"retained markets have split latest dates: {latest}")
    return latest


def _read_base(project_root: Path, production_root: Path) -> tuple[pd.DataFrame, bool]:
    if production_root.exists():
        return read_dataset(
            production_root,
            KR_INDEX_FUNDAMENTAL_DAILY,
            validate_kr_index_fundamental_daily,
        ), True
    prepared = prepare_retained_index_fundamentals(project_root / DEFAULT_RETAINED_ROOT)
    return prepared.dataframe, False


def _promote_with_rollback(
    frame: pd.DataFrame,
    *,
    production_root: Path,
    state_path: Path,
    state_payload: dict[str, object],
    run_id: str,
    state_writer: Callable[[Path, object], None],
) -> None:
    transaction_root = (
        production_root.parent / f".{production_root.name}.transactions"
    )
    transaction = transaction_root / run_id
    stage = transaction / "stage"
    backup = transaction / "backup"
    if transaction.exists():
        raise IndexFundamentalDailyError("transaction already exists")
    prior_state = state_path.read_bytes() if state_path.exists() else None
    transaction.mkdir(parents=True)
    try:
        write_dataset_atomic(
            frame, stage, KR_INDEX_FUNDAMENTAL_DAILY,
            validate_kr_index_fundamental_daily,
        )
        production_root.mkdir(parents=True, exist_ok=True)
        for market in ("KOSPI", "KOSDAQ"):
            name = f"market={market}"
            current = production_root / name
            if current.exists():
                backup.mkdir(parents=True, exist_ok=True)
                current.replace(backup / name)
            (stage / name).replace(current)
        state_writer(state_path, state_payload)
        restored = read_dataset(
            production_root,
            KR_INDEX_FUNDAMENTAL_DAILY,
            validate_kr_index_fundamental_daily,
        )
        if not restored.equals(frame):
            raise IndexFundamentalDailyError("production readback differs after promotion")
    except Exception:
        for market in ("KOSPI", "KOSDAQ"):
            name = f"market={market}"
            current = production_root / name
            prior = backup / name
            if current.exists():
                shutil.rmtree(current)
            if prior.exists():
                prior.replace(current)
        if prior_state is None:
            state_path.unlink(missing_ok=True)
        else:
            temporary_state = state_path.with_name(
                f".{state_path.name}.{os.getpid()}.rollback.tmp"
            )
            temporary_state.write_bytes(prior_state)
            temporary_state.replace(state_path)
        raise
    finally:
        shutil.rmtree(transaction, ignore_errors=True)
        try:
            transaction_root.rmdir()
        except OSError:
            pass


def run_index_fundamental_daily(
    project_root: Path,
    *,
    target_date: date,
    now: datetime | None = None,
    body_fetcher: BodyFetcher | None = None,
    state_writer: Callable[[Path, object], None] = _atomic_json,
) -> IndexFundamentalDailyResult:
    root = project_root.resolve()
    observed_now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise IndexFundamentalDailyError("now must be timezone-aware")
    if target_date >= observed_now.astimezone(ZoneInfo("Asia/Seoul")).date():
        raise IndexFundamentalDailyError("target must be a prior completed session date")

    production = root / "data/normalized/kr_index_fundamental_daily"
    state_path = root / "data/state/kr_index_fundamental_daily.json"
    lock_path = root / "data/state/kr_index_fundamental_daily.lock"
    base, production_exists = _read_base(root, production)
    validate_kr_index_fundamental_daily(base)
    latest_map = _latest_by_market(base)
    latest = next(iter(latest_map.values()))
    if latest >= target_date and production_exists and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("last_accepted_market_date") != latest.isoformat():
            raise IndexFundamentalDailyError("production and state latest dates differ")
        return IndexFundamentalDailyResult(
            "NOOP_IDEMPOTENT", None, 0, latest.isoformat(), latest.isoformat(), 0,
            "TARGET_ALREADY_ACCEPTED_BEFORE_PROVIDER_ACCESS",
        )
    if latest >= target_date:
        raise IndexFundamentalDailyError("production/state completion is inconsistent")

    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    start = calendar.next_trading_day(latest)
    expected_sessions = tuple(calendar.sessions_in_range(start, target_date))
    if not expected_sessions or expected_sessions[-1] != target_date:
        raise IndexFundamentalDailyError("target is not an exact XKRX session")
    run_id = f"kr-index-fundamental-{start:%Y%m%d}-{target_date:%Y%m%d}-{uuid4().hex}"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise IndexFundamentalDailyError("index fundamental daily lock is held") from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"run_id": run_id, "pid": os.getpid()}, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        capture = capture_index_fundamental_range(
            start, target_date, run_id=run_id,
            landing_root=root / "data/landing/kr_index_fundamental_daily",
            env_file=root / ".env", body_fetcher=body_fetcher,
        )
        incoming = tuple(
            normalize_index_fundamental_response(
                response.path.read_bytes(),
                index_code=response.index_code,
                market=response.market,
            )
            for response in capture.responses
        )
        expected_dates = {item.isoformat() for item in expected_sessions}
        for response, frame in zip(capture.responses, incoming, strict=True):
            observed_dates = set(frame["date"].astype(str))
            if observed_dates != expected_dates:
                raise IndexFundamentalDailyError(
                    f"{response.market} sessions differ: expected={sorted(expected_dates)} "
                    f"observed={sorted(observed_dates)}"
                )
        merged = merge_index_fundamental_frames(base, incoming)
        inserted = len(merged) - len(base)
        expected_inserted = len(expected_sessions) * 2
        if inserted != expected_inserted:
            raise IndexFundamentalDailyError(
                f"joint insert count differs: {inserted} != {expected_inserted}"
            )
        state_payload = {
            "schema_version": 1,
            "status": "ACCEPTED_DESCRIPTIVE_NON_PREDICTIVE",
            "last_accepted_market_date": target_date.isoformat(),
            "rows": len(merged),
            "run_id": run_id,
            "business_calls": capture.business_calls,
            "retry_count": 0,
            "expected_sessions": sorted(expected_dates),
            "landing": [
                {
                    "market": item.market,
                    "index_code": item.index_code,
                    "path": item.path.relative_to(root).as_posix(),
                    "sha256": item.sha256,
                    "rows": item.rows,
                }
                for item in capture.responses
            ],
            "publication_revision_finality": "UNRESOLVED",
            "predictive_eligibility": "NON_PREDICTIVE",
        }
        _promote_with_rollback(
            merged, production_root=production, state_path=state_path,
            state_payload=state_payload, run_id=run_id, state_writer=state_writer,
        )
        return IndexFundamentalDailyResult(
            "PROMOTED", run_id, 2, latest.isoformat(), target_date.isoformat(),
            inserted, "EXACT_TWO_MARKET_RANGE_ATOMIC_PROMOTION",
        )
    finally:
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("run_id") == run_id:
                lock_path.unlink()
        except (OSError, ValueError):
            pass


__all__ = [
    "IndexFundamentalDailyError", "IndexFundamentalDailyResult",
    "run_index_fundamental_daily",
]
