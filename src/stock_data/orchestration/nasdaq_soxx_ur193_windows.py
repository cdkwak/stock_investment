"""UR-193's independently manifested, fail-closed Nasdaq SOXX windows."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Collection, Protocol
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.providers.nasdaq_soxx_info_current_observation import nasdaq_soxx_info_quote, nasdaq_soxx_info_route


OPERATION_ID = "UR-193"
URL = "https://api.nasdaq.com/api/quote/SOXX/info?assetclass=etf"
PUBLIC_HEADERS = {"Accept": "application/json, text/plain, */*", "User-Agent": "Mozilla/5.0"}
KST = ZoneInfo("Asia/Seoul")
WINDOW_IDS = tuple(f"2026-08-21T{hour:02d}:{minute:02d}:00+09:00" for hour in range(17, 24) for minute in (0, 30) if (hour, minute) >= (17, 30))
STATE_PATH = Path("data/state/nasdaq_soxx_ur193_windows.json")
LANDING_ROOT = Path("data/landing/nasdaq/soxx_info_ur193")
PROJECTION_PATH = Path("data/state/current_observations/nasdaq_soxx_info_current.json")


class HttpResponse(Protocol):
    status_code: int
    content: bytes


@dataclass(frozen=True)
class NasdaqSoxxWindowResult:
    status: str
    window_id: str
    raw_gets: int
    landing_sha256: str | None
    replay_api_calls: int


def _window_id(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(KST)
    # This boundary remains current for its own half-open 30-minute interval.
    local = local.replace(minute=local.minute - local.minute % 30, second=0, microsecond=0)
    return local.isoformat()


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _state(path: Path) -> dict[str, object]:
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return {"schema_version": 1, "operation_id": OPERATION_ID, "windows": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "operation_id", "windows"} or payload["schema_version"] != 1 or payload["operation_id"] != OPERATION_ID or not isinstance(payload["windows"], dict):
        raise RuntimeError("UR-193 durable ledger schema mismatch")
    return payload


def is_active(root: Path, *, now: datetime) -> bool:
    """Read-only GUI check for an unattempted current half-open UR-193 slot."""
    wid = _window_id(now)
    if wid not in WINDOW_IDS:
        return False
    try:
        state = _state(Path(root) / STATE_PATH)
    except (OSError, RuntimeError, ValueError):
        return False
    windows = state["windows"]
    assert isinstance(windows, dict)
    # Any durable claim or terminal result owns this window; GUI must reread
    # locally rather than construct a duplicate transport attempt.
    return wid not in windows


class NasdaqSoxxWindowedCollector:
    def __init__(self, root: Path, *, state_path: Path = STATE_PATH, landing_root: Path = LANDING_ROOT, lock: CurrentObservationProcessLock | None = None) -> None:
        self.root = Path(root); self.state_path = self.root / state_path; self.landing_root = landing_root
        self.store = CurrentObservationFileStore(self.root / PROJECTION_PATH)
        self.lock = lock or CurrentObservationProcessLock(self.state_path.with_suffix(".lock"))

    def _replay(self) -> int:
        return CurrentObservationCoordinator(self.store).replay(nasdaq_soxx_info_route()).api_calls

    def run(self, *, now: datetime, response_factory: Callable[[], HttpResponse] | None, allowed_window_ids: Collection[str] = WINDOW_IDS) -> NasdaqSoxxWindowResult:
        wid = _window_id(now)
        if wid not in set(allowed_window_ids):
            return NasdaqSoxxWindowResult("WINDOW_NOT_MANIFESTED", wid, 0, None, self._replay())
        if not self.lock.acquire():
            return NasdaqSoxxWindowResult("PROCESS_LOCKED", wid, 0, None, self._replay())
        try:
            try:
                state = _state(self.state_path)
            except (OSError, RuntimeError, ValueError):
                return NasdaqSoxxWindowResult("LEDGER_INVALID", wid, 0, None, self._replay())
            windows = dict(state["windows"])
            existing = windows.get(wid)
            if isinstance(existing, dict):
                status = "ORPHAN_ATTEMPTING_NO_REPEAT" if existing.get("status") == "ATTEMPTING" else "NO_REPEAT"
                return NasdaqSoxxWindowResult(status, wid, 0, None, self._replay())
            if response_factory is None: raise RuntimeError("due UR-193 window requires a transport factory")
            claim: dict[str, object] = {"status": "ATTEMPTING", "attempted_at_utc": now.astimezone(timezone.utc).isoformat(), "raw_gets_reserved": 1, "raw_gets_invoked": 0, "raw_gets_completed": 0, "retry_count": 0, "redirect_count": 0, "fallback_count": 0}
            windows[wid] = claim; state["windows"] = windows; _write(self.state_path, state)
            try:
                claim["raw_gets_invoked"] = 1; windows[wid] = claim; state["windows"] = windows; _write(self.state_path, state)
                response = response_factory(); claim["raw_gets_completed"] = 1
                if response.status_code != 200:
                    claim.update({"status": "COMPLETE_FAILURE", "failure_type": "HTTP_STATUS", "http_status": int(response.status_code), "raw_gets": 1})
                    digest = None
                else:
                    body = bytes(response.content); digest = hashlib.sha256(body).hexdigest()
                    landing = self.root / self.landing_root / wid.replace(":", "") / digest / "body.json"
                    landing.parent.mkdir(parents=True, exist_ok=True)
                    with landing.open("xb") as stream: stream.write(body); stream.flush(); os.fsync(stream.fileno())
                    if hashlib.sha256(landing.read_bytes()).hexdigest() != digest: raise RuntimeError("UR-193 Landing hash readback mismatch")
                    payload = json.loads(landing.read_text(encoding="utf-8"))
                    source = nasdaq_soxx_info_quote(payload, retrieved_at=now, request_count=1)
                    refreshed = CurrentObservationCoordinator(self.store).refresh(nasdaq_soxx_info_route(), primary_attempt=lambda: source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("UR-193 has no fallback")))
                    if refreshed.observation is None: raise RuntimeError("UR-193 strict validation left no observation")
                    claim.update({"status": "COMPLETE_ACCEPTED", "raw_gets": 1, "landing_file": landing.relative_to(self.root).as_posix(), "landing_sha256": digest, "provider_timestamp_utc": refreshed.observation.provider_timestamp_utc, "projection": PROJECTION_PATH.as_posix()})
            except Exception as error:
                digest = None; claim.update({"status": "COMPLETE_FAILURE", "failure_type": type(error).__name__, "raw_gets": int(claim["raw_gets_invoked"])})
            windows[wid] = claim; state["windows"] = windows; _write(self.state_path, state)
            return NasdaqSoxxWindowResult(str(claim["status"]), wid, int(claim["raw_gets"]), digest, self._replay())
        finally: self.lock.release()


def collector(root: Path) -> NasdaqSoxxWindowedCollector: return NasdaqSoxxWindowedCollector(root)


def expire_window_no_backfill(root: Path, *, boundary_id: str, decided_at: datetime) -> NasdaqSoxxWindowResult:
    """Durably prohibit one lead-designated missed UR-193 window without I/O."""
    if boundary_id != "2026-08-21T18:00:00+09:00" or boundary_id not in WINDOW_IDS:
        raise ValueError("only UR-193's explicitly missed 18:00 boundary may expire")
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise ValueError("expiry decision time must be timezone-aware")
    root = Path(root); state_path = root / STATE_PATH; lock = CurrentObservationProcessLock(state_path.with_suffix(".lock"))
    store = CurrentObservationFileStore(root / PROJECTION_PATH)
    if not lock.acquire():
        return NasdaqSoxxWindowResult("PROCESS_LOCKED", boundary_id, 0, None, CurrentObservationCoordinator(store).replay(nasdaq_soxx_info_route()).api_calls)
    try:
        state = _state(state_path); windows = dict(state["windows"])
        if boundary_id in windows:
            return NasdaqSoxxWindowResult("NO_REPEAT", boundary_id, 0, None, CurrentObservationCoordinator(store).replay(nasdaq_soxx_info_route()).api_calls)
        record: dict[str, object] = {
            "status": "EXPIRED_API_ZERO_NO_BACKFILL", "selected_boundary_id": boundary_id,
            "expiry_decision_at_utc": decided_at.astimezone(timezone.utc).isoformat(),
            "raw_gets_reserved": 0, "raw_gets_invoked": 0, "raw_gets_completed": 0,
            "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
        }
        windows[boundary_id] = record; state["windows"] = windows; _write(state_path, state)
        readback = _state(state_path)
        if readback["windows"].get(boundary_id) != record:
            raise RuntimeError("UR-193 expiry journal readback mismatch")
        return NasdaqSoxxWindowResult("EXPIRED_API_ZERO_NO_BACKFILL", boundary_id, 0, None, CurrentObservationCoordinator(store).replay(nasdaq_soxx_info_route()).api_calls)
    finally:
        lock.release()


def window_id(*, now: datetime) -> str:
    """Public selected-boundary helper shared by CLI and GUI composition."""
    return _window_id(now)


__all__ = ["LANDING_ROOT", "OPERATION_ID", "PROJECTION_PATH", "PUBLIC_HEADERS", "STATE_PATH", "URL", "WINDOW_IDS", "NasdaqSoxxWindowResult", "NasdaqSoxxWindowedCollector", "collector", "expire_window_no_backfill", "is_active", "window_id"]
