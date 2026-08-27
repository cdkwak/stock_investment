"""Future-only, serial manifest composition for UR-199 Naver equity windows."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from stock_data.orchestration.naver_mobile_home_windows import select_due_window, window_id
from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.providers.naver_current_web_observation import NaverCurrentWebObservationError
from stock_data.providers.naver_mobile_basic_000660_observation import naver_mobile_basic_000660_quote, naver_mobile_basic_000660_route
from stock_data.providers.naver_mobile_basic_005930_observation import naver_mobile_basic_005930_quote, naver_mobile_basic_005930_route

KST = ZoneInfo("Asia/Seoul")
OPERATION_ID = "UR-199"
TARGET_DATE_KST = "2026-08-24"
IDENTITIES = ("000660", "005930")
WINDOW_IDS = tuple(f"2026-08-24T{hour:02d}:{minute:02d}:00+09:00" for hour, minute in ((9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (12, 0), (12, 30), (13, 0), (13, 30), (14, 0), (14, 30), (15, 0), (15, 30)))
MANIFEST_PATH = Path("data/state/naver_equity_ur199_activation.json")
STATE_PATH = Path("data/state/naver_equity_ur199_windows.json")
LANDING_ROOT = Path("data/landing/naver_mobile_basic/ur199")

class HttpResponse(Protocol):
    status_code: int
    content: bytes

CONFIG = {
    "000660": (naver_mobile_basic_000660_route, naver_mobile_basic_000660_quote, Path("data/state/current_observations/naver_mobile_basic_000660_ur199.json")),
    "005930": (naver_mobile_basic_005930_route, naver_mobile_basic_005930_quote, Path("data/state/current_observations/naver_mobile_basic_005930_ur199.json")),
}


def manifest_payload() -> dict[str, object]:
    return {"schema_version": 1, "operation_id": OPERATION_ID, "target_date_kst": TARGET_DATE_KST, "identities": list(IDENTITIES), "route_template": "NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/<code>/basic", "allowed_window_ids": list(WINDOW_IDS), "serial_order": list(IDENTITIES), "timeout_seconds": 10, "request_cap_per_identity_window": 1, "retry_count": 0, "redirect_count": 0, "fallback_count": 0, "display_only": True, "pit_safe": False, "state_path": STATE_PATH.as_posix(), "runner_api": "NaverEquityUr199Runner.run(now, response_factories)"}


def ensure_manifest(root: Path) -> Path:
    path = Path(root) / MANIFEST_PATH; path.parent.mkdir(parents=True, exist_ok=True); expected = manifest_payload()
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2).encode()); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError: pass
    if json.loads(path.read_text(encoding="utf-8")) != expected: raise RuntimeError("UR-199 manifest differs from approved scope")
    return path


def read_manifest(root: Path) -> dict[str, object]:
    try: actual = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise RuntimeError("UR-199 manifest unreadable") from error
    if actual != manifest_payload(): raise RuntimeError("UR-199 manifest differs from approved scope")
    return actual


def is_active(root: Path, *, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None: raise ValueError("timezone-aware clock required")
    return select_due_window(allowed_window_ids=read_manifest(root)["allowed_window_ids"], now=now.astimezone(KST)) is not None

def selected_boundary(root: Path, *, now: datetime) -> str | None:
    allowed = read_manifest(root)["allowed_window_ids"]
    return select_due_window(allowed_window_ids=allowed, now=now.astimezone(KST)) if isinstance(allowed, list) else None


def eligible_identities(root: Path, *, now: datetime) -> tuple[str, ...]:
    """Read-only, bounded preflight for one exact UR-199 window."""
    if not is_active(root, now=now): return ()
    current = window_id(now=now.astimezone(KST)); state = NaverEquityUr199Runner(root)._state(); windows = state["windows"]
    claims = windows.get(current, {})
    if not isinstance(claims, dict) or any(identity not in IDENTITIES or not isinstance(value, dict) or not isinstance(value.get("status"), str) for identity, value in claims.items()):
        raise RuntimeError("UR-199 current-window ledger is malformed")
    return tuple(identity for identity in IDENTITIES if identity not in claims)


@dataclass(frozen=True)
class RunnerResult:
    window_id: str
    statuses: Mapping[str, str]
    api_calls: int


class NaverEquityUr199Runner:
    """API-zero before activation; future callers inject one serial adapter per identity."""
    def __init__(self, root: Path) -> None: self.root = Path(root); self.state_path = self.root / STATE_PATH
    def _write_state(self, state: dict[str, object]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(f".{self.state_path.name}.tmp")
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2)
        temporary.write_text(encoded, encoding="utf-8"); os.replace(temporary, self.state_path)
        if self.state_path.read_text(encoding="utf-8") != encoded: raise RuntimeError("UR-199 ledger readback differs")
    def _state(self) -> dict[str, object]:
        if not self.state_path.exists(): return {"schema_version": 1, "operation_id": OPERATION_ID, "windows": {}}
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or set(state) != {"schema_version", "operation_id", "windows"} or state["schema_version"] != 1 or state["operation_id"] != OPERATION_ID or not isinstance(state["windows"], dict): raise RuntimeError("UR-199 durable state differs from contract")
        return state
    def run(self, *, now: datetime, response_factories: Mapping[str, Callable[[], HttpResponse]] | None = None) -> RunnerResult:
        current = window_id(now=now.astimezone(KST))
        if not is_active(self.root, now=now): return RunnerResult(current, {identity: "WINDOW_NOT_MANIFESTED" for identity in IDENTITIES}, 0)
        lock = CurrentObservationProcessLock(self.state_path.with_suffix(".lock"))
        if not lock.acquire(): return RunnerResult(current, {identity: "PROCESS_LOCKED" for identity in IDENTITIES}, 0)
        try:
            if response_factories is None: return RunnerResult(current, {identity: "NO_TRANSPORT_ADAPTER" for identity in IDENTITIES}, 0)
            state = self._state(); windows = dict(state["windows"]); claims = dict(windows.get(current, {})); statuses: dict[str, str] = {}; api_calls = 0
            for identity in IDENTITIES:
                factory = response_factories.get(identity)
                existing = claims.get(identity)
                if existing is not None: statuses[identity] = "ORPHANED_NO_REPEAT" if existing.get("status") == "ATTEMPTING" else "NO_REPEAT"; continue
                if factory is None: statuses[identity] = "NO_TRANSPORT_ADAPTER"; continue
                route_factory, quote, projection = CONFIG[identity]; route = route_factory(); store = CurrentObservationFileStore(self.root / projection); coordinator = CurrentObservationCoordinator(store)
                claims[identity] = {"status": "ATTEMPTING", "raw_gets_reserved": 1, "raw_gets_invoked": 0, "raw_gets_completed": 0, "retry_count": 0, "redirect_count": 0, "fallback_count": 0}; windows[current] = claims; state["windows"] = windows; self._write_state(state)
                claims[identity]["raw_gets_invoked"] = 1; api_calls += 1; self._write_state(state)
                try:
                    response = factory(); claims[identity]["raw_gets_completed"] = 1
                    if response.status_code != 200:
                        statuses[identity] = "COMPLETE_HTTP_FAILURE"; claims[identity].update({"status": statuses[identity], "http_status": response.status_code})
                    else:
                        body = bytes(response.content); digest = hashlib.sha256(body).hexdigest(); landing = self.root / LANDING_ROOT / current.replace(":", "") / identity / digest / "response.json"; landing.parent.mkdir(parents=True, exist_ok=True)
                        with landing.open("xb") as stream: stream.write(body); stream.flush(); os.fsync(stream.fileno())
                        readback = landing.read_bytes()
                        if hashlib.sha256(readback).hexdigest() != digest: raise RuntimeError("Landing hash readback mismatch")
                        candidate = quote(json.loads(readback), retrieved_at=now)
                        refresh = coordinator.refresh(route, primary_attempt=lambda: candidate, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
                        statuses[identity] = "COMPLETE"; claims[identity].update({"status": "COMPLETE", "landing_file": landing.relative_to(self.root).as_posix(), "landing_sha256": digest, "provider_timestamp_utc": candidate.value.provider_timestamp_utc, "replay_api_calls": coordinator.replay(route).api_calls, "observation_written": refresh.observation is not None})
                except (json.JSONDecodeError, NaverCurrentWebObservationError, ValueError) as error:
                    statuses[identity] = "COMPLETE_SEMANTIC_FAILURE"; claims[identity].update({"status": statuses[identity], "failure_type": type(error).__name__, "replay_api_calls": coordinator.replay(route).api_calls})
                except Exception as error:
                    statuses[identity] = "COMPLETE_TRANSPORT_FAILURE"; claims[identity].update({"status": statuses[identity], "failure_type": type(error).__name__, "replay_api_calls": coordinator.replay(route).api_calls})
                self._write_state(state)
            return RunnerResult(current, statuses, api_calls)
        finally: lock.release()


def runner(root: Path) -> NaverEquityUr199Runner: return NaverEquityUr199Runner(root)

__all__ = ["IDENTITIES", "WINDOW_IDS", "eligible_identities", "ensure_manifest", "is_active", "manifest_payload", "read_manifest", "runner", "selected_boundary"]
