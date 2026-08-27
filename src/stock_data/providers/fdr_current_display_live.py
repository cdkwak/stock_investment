"""One-shot FinanceDataReader/Naver current-display operation.

This runner has no credential, environment, scheduler, GUI, canonical, or
Backtest dependency. It implements the installed FinanceDataReader 0.9.202
Naver daily request contract with one timeout-bounded raw GET, retains a
successful body before parsing, and delegates typed display-only promotion to
the UR-128 refresher.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Callable, Protocol

import pandas as pd
import requests

from stock_data.orchestration.current_observation import CurrentObservationFileStore, ObservationIdentity
from stock_data.providers.fdr_display_daily import (
    FDRDisplayDailyLandingStore,
    FDRDisplayDailyRefreshResult,
    FDRDisplayDailyResponse,
    FDRDisplayDailyRefresher,
)


FDR_VERSION = "0.9.202"
ROUTE = "NAVER:005930"
SOURCE_DATE = date(2026, 8, 21)
TIMEOUT_SECONDS = 10
RETRY_COUNT = 0
RAW_REQUEST_CAP = 1
LANDING_ROOT = Path("data/landing/fdr_display_daily")
PROJECTION_PATH = Path("data/state/current_observations/fdr_display_daily.json")
CHECKPOINT_PATH = Path("data/state/fdr_current_display_operation.json")
IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930")
_NAVER_URL = "https://fchart.stock.naver.com/sise.nhn?timeframe=day&count=6000&requestType=0&symbol=005930"


class _HTTPResponse(Protocol):
    status_code: int
    content: bytes


HTTPGet = Callable[[str], _HTTPResponse]


@dataclass(frozen=True)
class FDRCurrentDisplayOperationResult:
    status: str
    source_date: str
    raw_get_count: int
    api_zero_replay_calls: int
    landing_file: str | None
    landing_sha256: str | None
    landing_bytes: int | None
    safe_code: str | None


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_checkpoint(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "attempts": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "attempts"}:
        raise RuntimeError("FDR current-display checkpoint schema mismatch")
    if payload["schema_version"] != 1 or not isinstance(payload["attempts"], dict):
        raise RuntimeError("FDR current-display checkpoint is invalid")
    return payload


def _parse_fdr_naver_body(body: bytes, start: date, end: date) -> pd.DataFrame:
    """Parse the installed FDR 0.9.202 Naver `fchart` item format, offline."""
    items = re.findall(rb'<item data="(.*?)" />', body, re.DOTALL)
    if not items:
        return pd.DataFrame()
    raw = b"\n".join(items).decode("utf-8")
    frame = pd.read_csv(StringIO(raw), delimiter="|", header=None, dtype={0: str})
    frame.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    frame["Date"] = pd.to_datetime(frame["Date"], format="%Y%m%d")
    frame.set_index("Date", inplace=True)
    frame.sort_index(inplace=True)
    frame["Change"] = frame["Close"].pct_change()
    return frame.loc[start:end]


class FDRNaverSingleGetTransport:
    """Direct, audited implementation of FDR 0.9.202's single Naver fchart GET."""
    def __init__(self, http_get: Callable[..., _HTTPResponse]) -> None:
        self._http_get = http_get
        self.raw_get_count = 0

    def __call__(self, route: str, start: date, end: date, timeout: int, retry: int) -> FDRDisplayDailyResponse:
        if route != ROUTE or start != SOURCE_DATE or end != SOURCE_DATE:
            raise ValueError("FDR current-display route/date boundary mismatch")
        if timeout != TIMEOUT_SECONDS or retry != RETRY_COUNT:
            raise ValueError("FDR current-display timeout/retry boundary mismatch")
        if self.raw_get_count >= RAW_REQUEST_CAP:
            raise RuntimeError("FDR current-display raw GET cap exhausted")
        self.raw_get_count += 1
        response = self._http_get(_NAVER_URL, timeout=TIMEOUT_SECONDS)
        status_code = int(response.status_code)
        if status_code != 200:
            # A failed body is deliberately neither returned nor retained.
            return FDRDisplayDailyResponse(status_code, b"", None, request_count=1, retry_count=0)
        body = bytes(response.content)
        return FDRDisplayDailyResponse(
            status_code, body, None, request_count=1, retry_count=0,
            frame_reader=lambda: _parse_fdr_naver_body(body, start, end),
        )


def _landing_evidence(root: Path) -> tuple[str | None, str | None, int | None]:
    spec = FDRDisplayDailyRefresher.spec_for(IDENTITY)
    directory = root / LANDING_ROOT / spec.route.replace(":", "_").replace("^", "IDX")
    files = sorted(directory.glob("*/response.bin"))
    if not files:
        return None, None, None
    if len(files) != 1:
        raise RuntimeError("FDR current-display Landing is not singular")
    raw = files[0].read_bytes()
    import hashlib
    return files[0].relative_to(root).as_posix(), hashlib.sha256(raw).hexdigest(), len(raw)


def execute_fdr_current_display_operation(
    project_root: Path,
    *,
    expected_source_date: date = SOURCE_DATE,
    http_get: Callable[..., _HTTPResponse] = requests.get,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FDRCurrentDisplayOperationResult:
    """Run once or read back a completed/failed exact-date checkpoint with API=0."""
    if expected_source_date != SOURCE_DATE:
        raise ValueError("UR-129 permits 2026-08-21 only")
    root = Path(project_root)
    checkpoint_path = root / CHECKPOINT_PATH
    checkpoint = _load_checkpoint(checkpoint_path)
    attempted = checkpoint["attempts"].get(SOURCE_DATE.isoformat())
    if attempted is not None:
        if not isinstance(attempted, dict):
            raise RuntimeError("FDR current-display attempt record is invalid")
        return FDRCurrentDisplayOperationResult(
            "API_ZERO_REPLAY", SOURCE_DATE.isoformat(), 0, 0,
            attempted.get("landing_file"), attempted.get("landing_sha256"), attempted.get("landing_bytes"), attempted.get("safe_code"),
        )

    store = CurrentObservationFileStore(root / PROJECTION_PATH)
    refresher = FDRDisplayDailyRefresher(
        store=store, landing=FDRDisplayDailyLandingStore(root / LANDING_ROOT), now=clock,
    )
    transport = FDRNaverSingleGetTransport(http_get)
    result: FDRDisplayDailyRefreshResult = refresher.refresh(
        identity=IDENTITY, start=SOURCE_DATE, end=SOURCE_DATE, transport=transport,
    )
    landing_file, landing_sha256, landing_bytes = _landing_evidence(root)
    circuit = store.load("fdr-display-daily:NAVER:005930")
    accepted = result.observation is not None and result.observation.provider_timestamp_utc[:10] == SOURCE_DATE.isoformat()
    replay = refresher.replay(IDENTITY)
    if replay.api_calls != 0 or replay.observation != result.observation:
        raise RuntimeError("FDR current-display API-zero replay mismatch")
    status = "COMPLETE_VALIDATED" if accepted else "FAILED_BOUNDED"
    # The circuit records the final local no-alternate stop. Preserve the
    # original primary typed cause in the sanitized operation checkpoint.
    safe_code = None if accepted else (result.primary_safe_code or circuit.safe_code)
    attempt = {
        "status": status,
        "route": ROUTE,
        "source_date": SOURCE_DATE.isoformat(),
        "raw_get_count": transport.raw_get_count,
        "timeout_seconds": TIMEOUT_SECONDS,
        "retry_count": RETRY_COUNT,
        "landing_file": landing_file,
        "landing_sha256": landing_sha256,
        "landing_bytes": landing_bytes,
        "safe_code": safe_code,
        "api_zero_replay_calls": replay.api_calls,
    }
    checkpoint["attempts"][SOURCE_DATE.isoformat()] = attempt
    _atomic_json(checkpoint_path, checkpoint)
    return FDRCurrentDisplayOperationResult(
        status, SOURCE_DATE.isoformat(), transport.raw_get_count, replay.api_calls,
        landing_file, landing_sha256, landing_bytes, safe_code,
    )


__all__ = [
    "CHECKPOINT_PATH", "FDRCurrentDisplayOperationResult", "FDRNaverSingleGetTransport", "IDENTITY", "LANDING_ROOT",
    "PROJECTION_PATH", "RAW_REQUEST_CAP", "RETRY_COUNT", "ROUTE", "SOURCE_DATE", "TIMEOUT_SECONDS",
    "execute_fdr_current_display_operation",
]
