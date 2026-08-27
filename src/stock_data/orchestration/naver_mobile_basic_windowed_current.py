"""Durable 30-minute-window collector for the accepted Naver 000660 route.

The module is deliberately transport-injected.  It has no scheduler, GUI,
credential or environment dependency; an authorized runner supplies exactly
one response factory only after a durable per-window claim is recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Collection, Protocol
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import AttemptFailure, CircuitRecord, FailureKind
from stock_data.orchestration.current_observation import CurrentObservation, CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.providers.naver_current_web_observation import NaverCurrentWebObservationError, naver_web_current_quote, naver_web_current_route


KST = ZoneInfo("Asia/Seoul")
CADENCE = timedelta(minutes=30)
INITIAL_PILOT_WINDOW = "2026-08-21T13:00:00+09:00"
STATE_PATH = Path("data/state/naver_mobile_basic_000660_30m_ur153.json")
PROJECTION_PATH = Path("data/state/current_observations/naver_web_000660_current.json")
LANDING_ROOT = Path("data/landing/naver_mobile_basic/ur153_000660_30m")


class NaverWindowedCurrentError(RuntimeError):
    """The window collector cannot safely make or resume an attempt."""


class HttpResponse(Protocol):
    status_code: int
    content: bytes


@dataclass(frozen=True)
class NaverWindowedCurrentResult:
    status: str
    window_id: str
    raw_gets: int
    observation: CurrentObservation | None
    replay_api_calls: int


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _read_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "initial_pilot_window": INITIAL_PILOT_WINDOW, "windows": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "initial_pilot_window", "windows"}:
        raise NaverWindowedCurrentError("window state schema mismatch")
    if payload["schema_version"] != 1 or payload["initial_pilot_window"] != INITIAL_PILOT_WINDOW or not isinstance(payload["windows"], dict):
        raise NaverWindowedCurrentError("window state is invalid")
    return payload


def _window(now: datetime) -> tuple[str, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise NaverWindowedCurrentError("clock must be timezone-aware")
    local = now.astimezone(KST).replace(second=0, microsecond=0)
    floor = local.minute - (local.minute % 30)
    start = local.replace(minute=floor)
    return start.isoformat(), start.astimezone(timezone.utc)


def _terminal_circuit(
    coordinator: CurrentObservationCoordinator, store: CurrentObservationFileStore, *, failure: FailureKind, code: str,
) -> CurrentObservation | None:
    route = naver_web_current_route()
    result = coordinator.refresh(
        route,
        primary_attempt=lambda: (_ for _ in ()).throw(AttemptFailure(failure, safe_code=code, request_count=1)),
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("Naver route has no fallback")),
    )
    # The generic coordinator keeps a no-fallback route numeric-free, but its
    # circuit remains closed when it has valid prior state.  This operation's
    # explicit recovery contract requires a route-scoped open circuit until a
    # fully validated later-window success closes it.
    previous = store.load(route.route_id)
    store.save(route.route_id, CircuitRecord(True, failure, code, previous.generation + 1))
    return result.observation


class NaverMobileBasicWindowedCollector:
    """Run at most one durable attempt in one later 30-minute KST window."""

    def __init__(self, root: Path, *, lock: CurrentObservationProcessLock | None = None) -> None:
        self.root = Path(root)
        self.state_path = self.root / STATE_PATH
        self.store = CurrentObservationFileStore(self.root / PROJECTION_PATH)
        self.lock = lock or CurrentObservationProcessLock(self.state_path.with_suffix(".lock"))

    def replay(self) -> CurrentObservation | None:
        return CurrentObservationCoordinator(self.store).replay(naver_web_current_route()).observation

    def run(
        self,
        *,
        now: datetime,
        response_factory: Callable[[], HttpResponse] | None,
        allowed_window_ids: Collection[str] | None = None,
    ) -> NaverWindowedCurrentResult:
        window_id, _ = _window(now)
        if not self.lock.acquire():
            return NaverWindowedCurrentResult("PROCESS_LOCKED", window_id, 0, self.replay(), 0)
        try:
            state = _read_state(self.state_path)
            windows = dict(state["windows"])
            existing = windows.get(window_id)
            if existing is not None:
                if not isinstance(existing, dict) or existing.get("status") == "ATTEMPTING":
                    return NaverWindowedCurrentResult("ORPHANED_NO_REPEAT", window_id, 0, self.replay(), 0)
                return NaverWindowedCurrentResult("NO_REPEAT", window_id, 0, self.replay(), 0)
            if allowed_window_ids is not None and window_id not in set(allowed_window_ids):
                return NaverWindowedCurrentResult("WINDOW_NOT_MANIFESTED", window_id, 0, self.replay(), 0)
            if window_id <= INITIAL_PILOT_WINDOW:
                return NaverWindowedCurrentResult("INITIAL_WINDOW_CONSUMED", window_id, 0, self.replay(), 0)
            if response_factory is None:
                raise NaverWindowedCurrentError("response factory is required for a new due window")

            # The claim is durable before factory construction/invocation.  Any
            # interruption from here on is fail-closed for this exact window.
            claim: dict[str, object] = {
                "status": "ATTEMPTING", "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
                "raw_gets_reserved": 1, "raw_gets_invoked": 0, "raw_gets_completed": 0,
                "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
            }
            windows[window_id] = claim; state["windows"] = windows; _atomic_json(self.state_path, state)
            coordinator = CurrentObservationCoordinator(self.store)
            try:
                claim["raw_gets_invoked"] = 1; claim["transport_started_at_utc"] = datetime.now(timezone.utc).isoformat()
                windows[window_id] = claim; state["windows"] = windows; _atomic_json(self.state_path, state)
                response = response_factory()
                claim["raw_gets_completed"] = 1
                if response.status_code != 200:
                    observation = _terminal_circuit(coordinator, self.store, failure=FailureKind.HTTP_ERROR, code="NAVER_HTTP_STATUS")
                    claim.update({"status": "COMPLETE_FAILURE", "failure_type": "HTTPStatusError", "raw_gets": 1})
                else:
                    body = bytes(response.content); digest = hashlib.sha256(body).hexdigest()
                    landing = self.root / LANDING_ROOT / window_id.replace(":", "") / digest / "response.json"
                    landing.parent.mkdir(parents=True, exist_ok=True)
                    with landing.open("xb") as stream:
                        stream.write(body); stream.flush(); os.fsync(stream.fileno())
                    if hashlib.sha256(landing.read_bytes()).hexdigest() != digest:
                        raise NaverWindowedCurrentError("Landing hash readback mismatch")
                    try:
                        payload = json.loads(body)
                        candidate = naver_web_current_quote(payload, retrieved_at=now.astimezone(timezone.utc))
                        refreshed = coordinator.refresh(
                            naver_web_current_route(), primary_attempt=lambda: candidate,
                            fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("Naver route has no fallback")),
                        )
                        observation = refreshed.observation
                        claim.update({"status": "COMPLETE", "landing_file": landing.relative_to(self.root).as_posix(), "landing_sha256": digest, "raw_gets": 1, "provider_timestamp_utc": candidate.value.provider_timestamp_utc})
                    except (json.JSONDecodeError, NaverCurrentWebObservationError, ValueError) as error:
                        observation = _terminal_circuit(coordinator, self.store, failure=FailureKind.SCHEMA_ERROR, code="NAVER_SCHEMA_OR_FRESHNESS")
                        claim.update({"status": "COMPLETE_FAILURE", "failure_type": type(error).__name__, "landing_file": landing.relative_to(self.root).as_posix(), "landing_sha256": digest, "raw_gets": 1})
            except Exception as error:
                observation = _terminal_circuit(coordinator, self.store, failure=FailureKind.UNEXPECTED_ERROR, code="NAVER_TRANSPORT_ERROR")
                claim.update({"status": "COMPLETE_FAILURE", "failure_type": type(error).__name__, "raw_gets": int(claim["raw_gets_invoked"])})
            windows[window_id] = claim; state["windows"] = windows; _atomic_json(self.state_path, state)
            replay = coordinator.replay(naver_web_current_route())
            return NaverWindowedCurrentResult(str(claim["status"]), window_id, int(claim["raw_gets"]), observation, replay.api_calls)
        finally:
            self.lock.release()


__all__ = ["NaverMobileBasicWindowedCollector", "NaverWindowedCurrentError", "NaverWindowedCurrentResult"]
