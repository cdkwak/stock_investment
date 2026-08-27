"""Bounded live capture for the two Korean daily index contracts.

The live boundary is intentionally Landing-only.  It performs exactly three
retry-zero KRX/pykrx calls for one explicitly finalized trading date, persists
each source response before normalization, and never writes production data.
Promotion is delegated to :mod:`kr_index_daily_incremental`.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import re

import pandas as pd

from stock_data.providers.pykrx.kr_index_daily import (
    _normalize_response as normalize_market_response,
    _stock_module,
)
from stock_data.providers.pykrx.kospi200_index_daily import normalize_response as normalize_kospi200
from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access
from stock_data.validation.kr_index_daily import validate_kr_index_daily
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily


class IndexDailyLiveCaptureError(RuntimeError):
    """Fail-closed live-capture error."""


@dataclass(frozen=True)
class IndexDailyLiveCaptureResult:
    run_id: str
    status: str
    finalized_market_date: str
    business_calls: int
    retry_count: int
    landing_root: Path
    kr_index_landing: Path
    kospi200_landing: Path
    checkpoint_path: Path


SCOPES = (("KOSPI", "1001"), ("KOSDAQ", "2001"), ("KOSPI200", "1028"))


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    body = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_parquet_create_only(frame: pd.DataFrame, path: Path) -> str:
    if path.exists():
        raise IndexDailyLiveCaptureError(f"immutable Landing path already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        body = temporary.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if path.exists():
            raise IndexDailyLiveCaptureError(f"immutable Landing path already exists: {path}")
        temporary.replace(path)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise IndexDailyLiveCaptureError("market_date must use YYYY-MM-DD") from error


def capture_one_finalized_date(
    market_date: str | date,
    *,
    finalized_at: datetime,
    finality_confirmed: bool,
    run_id: str,
    landing_root: Path,
    state_root: Path,
    stock_module=None,
    policy: PykrxRequestPolicy | None = None,
    now: datetime | None = None,
) -> IndexDailyLiveCaptureResult:
    """Capture one reviewed trading date with three calls and retry zero."""
    target = _parse_date(market_date)
    if not finality_confirmed:
        raise IndexDailyLiveCaptureError("explicit source finality confirmation is required")
    if finalized_at.tzinfo is None or finalized_at.utcoffset() is None:
        raise IndexDailyLiveCaptureError("finalized_at must be timezone-aware")
    observed_now = now or datetime.now(finalized_at.tzinfo)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise IndexDailyLiveCaptureError("now must be timezone-aware")
    if finalized_at > observed_now:
        raise IndexDailyLiveCaptureError("source finality timestamp is in the future")
    if finalized_at.date() < target:
        raise IndexDailyLiveCaptureError("finality timestamp precedes the market date")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise IndexDailyLiveCaptureError("run_id is invalid")

    require_manual_live_access(manual=True, requested_days=1)
    job_root = landing_root.resolve() / run_id
    checkpoint = state_root.resolve() / "kr_index_daily_live" / run_id / "checkpoint.json"
    lock = state_root.resolve() / "kr_index_daily_live.lock"
    if job_root.exists() or checkpoint.exists():
        raise IndexDailyLiveCaptureError("run_id already has retained Landing/state")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise IndexDailyLiveCaptureError("another KRX index live capture owns the lock") from error
    with os.fdopen(lock_descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"run_id": run_id, "pid": os.getpid()}, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())

    request_policy = policy or PykrxRequestPolicy(
        min_interval_seconds=2.0,
        max_consecutive_requests=3,
        max_consecutive_failures=1,
    )
    stock = stock_module or _stock_module()
    call_count = 0
    raw_manifest: list[dict[str, object]] = []
    normalized: dict[str, pd.DataFrame] = {}
    date_compact = target.strftime("%Y%m%d")
    base_checkpoint: dict[str, object] = {
        "version": 1,
        "run_id": run_id,
        "status": "RUNNING",
        "finalized_market_date": target.isoformat(),
        "finalized_at": finalized_at.isoformat(),
        "finality_confirmed": True,
        "source": "pykrx/KRX",
        "planned_calls": 3,
        "retry_count": 0,
    }
    _atomic_json(checkpoint, base_checkpoint)
    try:
        for scope, ticker in SCOPES:
            try:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    request_policy.before_request()
                    call_count += 1
                    response = stock.get_index_ohlcv(date_compact, date_compact, ticker)
            except Exception as error:
                # No retry or fallback is permitted at this boundary.
                raise IndexDailyLiveCaptureError(
                    f"{scope} retry-zero capture failed: {type(error).__name__}"
                ) from None
            raw = response.reset_index()
            raw_path = job_root / "source" / f"{scope.lower()}.parquet"
            raw_sha = _write_parquet_create_only(raw, raw_path)
            raw_manifest.append(
                {"scope": scope, "ticker": ticker, "path": str(raw_path), "sha256": raw_sha}
            )
            if scope == "KOSPI200":
                frame = normalize_kospi200(response)
            else:
                frame = normalize_market_response(response, scope)
            if frame["date"].astype(str).tolist() != [target.isoformat()]:
                raise IndexDailyLiveCaptureError(f"{scope} returned a non-target date")
            normalized[scope] = frame
            request_policy.record_success()
            _atomic_json(checkpoint, {**base_checkpoint, "business_calls": call_count, "raw": raw_manifest})

        kr_index = pd.concat([normalized["KOSPI"], normalized["KOSDAQ"]], ignore_index=True)
        kr_index = kr_index.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
        kospi200 = normalized["KOSPI200"].reset_index(drop=True)
        validate_kr_index_daily(kr_index)
        validate_kospi200_index_daily(kospi200)
        kr_path = job_root / "normalized" / "kr_index_daily.parquet"
        k200_path = job_root / "normalized" / "kr_kospi200_index_daily.parquet"
        kr_sha = _write_parquet_create_only(kr_index, kr_path)
        k200_sha = _write_parquet_create_only(kospi200, k200_path)
        completed = {
            **base_checkpoint,
            "status": "COMPLETE",
            "business_calls": call_count,
            "raw": raw_manifest,
            "normalized": [
                {"dataset": "kr_index_daily", "path": str(kr_path), "sha256": kr_sha},
                {"dataset": "kr_kospi200_index_daily", "path": str(k200_path), "sha256": k200_sha},
            ],
        }
        _atomic_json(checkpoint, completed)
        return IndexDailyLiveCaptureResult(
            run_id=run_id,
            status="COMPLETE",
            finalized_market_date=target.isoformat(),
            business_calls=call_count,
            retry_count=0,
            landing_root=job_root,
            kr_index_landing=kr_path,
            kospi200_landing=k200_path,
            checkpoint_path=checkpoint,
        )
    except Exception as error:
        _atomic_json(
            checkpoint,
            {
                **base_checkpoint,
                "status": "STOPPED",
                "business_calls": call_count,
                "raw": raw_manifest,
                "error_type": type(error).__name__,
            },
        )
        raise
    finally:
        try:
            if json.loads(lock.read_text(encoding="utf-8")).get("run_id") == run_id:
                lock.unlink()
        except (OSError, ValueError):
            pass


__all__ = [
    "IndexDailyLiveCaptureError",
    "IndexDailyLiveCaptureResult",
    "capture_one_finalized_date",
]
