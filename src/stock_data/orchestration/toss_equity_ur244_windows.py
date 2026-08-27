"""Future-only, serial Toss Korean-equity windows for UR-244.

The collector owns only its isolated state, Landing, and display-only
observations.  It is intentionally transport-injected: an inactive or malformed
manifest cannot construct a runtime client, and every identity is durably
claimed before its one bounded transport invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.orchestration.naver_mobile_home_windows import select_due_window, window_id
from stock_data.orchestration.toss_market_current_observation import TossCurrentObservation, stock_price_snapshot

KST = ZoneInfo("Asia/Seoul")
OPERATION_ID = "UR-244"
TARGET_DATE_KST = "2026-08-24"
IDENTITIES = ("000660", "005930")
WINDOW_IDS = tuple(f"{TARGET_DATE_KST}T{hour:02d}:{minute:02d}:00+09:00" for hour, minute in (
    *((hour, minute) for hour in range(8, 20) for minute in (0, 30)), (20, 0),
))
MANIFEST_PATH = Path("data/state/toss_equity_ur244_activation.json")
STATE_PATH = Path("data/state/toss_equity_ur244_windows.json")
LANDING_ROOT = Path("data/landing/tossinvest/ur244_equity_30m")
PROJECTIONS = {
    "000660": Path("data/state/current_observations/toss_000660_ur244_30m.json"),
    "005930": Path("data/state/current_observations/toss_005930_ur244_30m.json"),
}
MAX_AGE = timedelta(minutes=60)


@dataclass(frozen=True)
class TossQuoteTransportResult:
    """Sanitized result supplied by one exact runtime market invocation."""

    payload: dict[str, Any]
    oauth_calls: int
    business_calls: int


@dataclass(frozen=True)
class RunnerResult:
    window_id: str
    statuses: Mapping[str, str]
    business_api_calls: int


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": OPERATION_ID,
        "target_date_kst": TARGET_DATE_KST,
        "korean_trading_day_verification": "XKRX_2026-08-24_NEXT_SESSION_LITERAL",
        "identities": list(IDENTITIES),
        "endpoint": "/api/v1/prices",
        "query_template": {"symbols": "<six_digit_code>"},
        "allowed_window_ids": list(WINDOW_IDS),
        "serial_order": list(IDENTITIES),
        "timeout_seconds": 10,
        "oauth_cap_per_identity_window": 1,
        "business_get_cap_per_identity_window": 1,
        "retry_count": 0,
        "redirect_count": 0,
        "fallback_count": 0,
        "landing_first": True,
        "display_only": True,
        "pit_safe": False,
        "state_path": STATE_PATH.as_posix(),
        "runner_api": "TossEquityUr244Runner.run(now, transport_factories)",
    }


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != encoded:
            raise RuntimeError("UR-244 atomic state readback mismatch")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def ensure_manifest(root: Path) -> Path:
    path = Path(root) / MANIFEST_PATH
    expected = manifest_payload()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    if json.loads(path.read_text(encoding="utf-8")) != expected:
        raise RuntimeError("UR-244 activation manifest differs from the approved scope")
    return path


def read_manifest(root: Path) -> dict[str, object]:
    try:
        payload = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-244 activation manifest is unreadable") from error
    if payload != manifest_payload():
        raise RuntimeError("UR-244 activation manifest differs from the approved scope")
    return payload


def selected_boundary(root: Path, *, now: datetime) -> str | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    allowed = read_manifest(root)["allowed_window_ids"]
    return select_due_window(allowed_window_ids=allowed, now=now.astimezone(KST)) if isinstance(allowed, list) else None


def is_active(root: Path, *, now: datetime) -> bool:
    return selected_boundary(root, now=now) is not None


def _state_path(root: Path) -> Path:
    return Path(root) / STATE_PATH


def _read_state(root: Path) -> dict[str, object]:
    path = _state_path(root)
    if not path.exists():
        return {"schema_version": 1, "operation_id": OPERATION_ID, "windows": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-244 durable ledger is unreadable") from error
    if not isinstance(state, dict) or set(state) != {"schema_version", "operation_id", "windows"} or state["schema_version"] != 1 or state["operation_id"] != OPERATION_ID or not isinstance(state["windows"], dict):
        raise RuntimeError("UR-244 durable ledger differs from contract")
    return state


def eligible_identities(root: Path, *, now: datetime) -> tuple[str, ...]:
    """Read-only preflight; an inactive, malformed, or claimed window is API-zero."""
    current = selected_boundary(root, now=now)
    if current is None:
        return ()
    claims = _read_state(root)["windows"].get(current, {})
    if not isinstance(claims, dict) or any(key not in IDENTITIES or not isinstance(value, dict) for key, value in claims.items()):
        raise RuntimeError("UR-244 current-window claims are malformed")
    return tuple(identity for identity in IDENTITIES if identity not in claims)


def _classification(candidate: TossCurrentObservation, payload: dict[str, Any], *, identity: str, now: datetime, current: str) -> str:
    source = datetime.fromisoformat(candidate.observation.provider_timestamp_utc).astimezone(KST)
    if candidate.market_date != TARGET_DATE_KST or candidate.observation.unit != "KRW per share":
        raise ValueError("Toss equity identity/date/unit contract mismatch")
    rows = [row for row in payload.get("result", []) if isinstance(row, dict) and row.get("symbol") == identity] if isinstance(payload.get("result"), list) else []
    if len(rows) != 1 or rows[0].get("currency") != "KRW":
        raise ValueError("Toss equity payload must have exactly one KRW identity row")
    if current == WINDOW_IDS[-1]:
        if rows[0].get("venue") not in {None, ""} or rows[0].get("session") not in {None, ""}:
            raise ValueError("Toss NXT-close inference requires absent provider venue/session")
        local_time = source.timetz().replace(tzinfo=None)
        if source.date().isoformat() != TARGET_DATE_KST or not time(19, 55) <= local_time <= time(20, 0):
            raise ValueError("Toss NXT-close provider time is outside the exclusive close interval")
        return "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
    if source > now.astimezone(KST) or now.astimezone(KST) - source > MAX_AGE:
        raise ValueError("Toss active-session provider time fails the 60-minute age gate")
    return "TOSS_ACTIVE_SESSION_60M"


class TossEquityUr244Runner:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.state_path = _state_path(root)

    def run(
        self,
        *,
        now: datetime,
        transport_factories: Mapping[str, Callable[[], TossQuoteTransportResult]] | None = None,
    ) -> RunnerResult:
        current = window_id(now=now.astimezone(KST))
        if selected_boundary(self.root, now=now) is None:
            return RunnerResult(current, {identity: "WINDOW_NOT_MANIFESTED" for identity in IDENTITIES}, 0)
        lock = CurrentObservationProcessLock(self.state_path.with_suffix(".lock"))
        if not lock.acquire():
            return RunnerResult(current, {identity: "PROCESS_LOCKED" for identity in IDENTITIES}, 0)
        try:
            if transport_factories is None:
                return RunnerResult(current, {identity: "NO_TRANSPORT_ADAPTER" for identity in IDENTITIES}, 0)
            state = _read_state(self.root)
            windows = dict(state["windows"])
            claims = dict(windows.get(current, {}))
            statuses: dict[str, str] = {}
            business_api_calls = 0
            for identity in IDENTITIES:
                existing = claims.get(identity)
                if existing is not None:
                    statuses[identity] = "ORPHANED_NO_REPEAT" if existing.get("status") == "ATTEMPTING" else "NO_REPEAT"
                    continue
                factory = transport_factories.get(identity)
                if factory is None:
                    statuses[identity] = "NO_TRANSPORT_ADAPTER"
                    continue
                claims[identity] = {
                    "status": "ATTEMPTING", "oauth_reserved": 1, "business_get_reserved": 1,
                    "oauth_invoked": 0, "business_get_invoked": 0, "business_get_completed": 0,
                    "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
                }
                windows[current] = claims
                state["windows"] = windows
                _atomic_json(self.state_path, state)
                claims[identity]["oauth_invoked"] = 1
                claims[identity]["business_get_invoked"] = 1
                _atomic_json(self.state_path, state)
                business_api_calls += 1
                coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(self.root / PROJECTIONS[identity]))
                try:
                    transport = factory()
                    if not 0 <= transport.oauth_calls <= 1 or transport.business_calls != 1:
                        raise RuntimeError("UR-244 transport exceeded or missed its fixed request budget")
                    claims[identity].update({"oauth_calls": transport.oauth_calls, "business_get_completed": transport.business_calls})
                    raw = json.dumps(transport.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    digest = hashlib.sha256(raw).hexdigest()
                    landing = self.root / LANDING_ROOT / current.replace(":", "") / identity / digest / "response.json"
                    landing.parent.mkdir(parents=True, exist_ok=True)
                    with landing.open("xb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    readback = landing.read_bytes()
                    if hashlib.sha256(readback).hexdigest() != digest:
                        raise RuntimeError("UR-244 Landing hash readback mismatch")
                    landing_payload = json.loads(readback)
                    base = stock_price_snapshot(landing_payload, symbol=identity, retrieved_at_utc=now.astimezone(timezone.utc).isoformat())
                    classification = _classification(base, landing_payload, identity=identity, now=now, current=current)
                    candidate = stock_price_snapshot(landing_payload, symbol=identity, retrieved_at_utc=now.astimezone(timezone.utc).isoformat(), route_suffix=f":{classification}")
                    refresh = coordinator.refresh(candidate.route(), primary_attempt=lambda: candidate.source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
                    replay = coordinator.replay(candidate.route())
                    if refresh.observation != candidate.observation or replay.observation != candidate.observation or replay.api_calls != 0:
                        raise RuntimeError("UR-244 projection/API-zero replay readback mismatch")
                    statuses[identity] = "COMPLETE"
                    claims[identity].update({
                        "status": "COMPLETE", "landing_file": landing.relative_to(self.root).as_posix(), "landing_sha256": digest,
                        "provider_timestamp_utc": candidate.observation.provider_timestamp_utc, "route_id": candidate.observation.route_id,
                        "classification": classification, "replay_api_calls": 0,
                    })
                except (json.JSONDecodeError, ValueError) as error:
                    statuses[identity] = "COMPLETE_SEMANTIC_FAILURE"
                    claims[identity].update({"status": statuses[identity], "failure_type": type(error).__name__, "replay_api_calls": 0})
                except Exception as error:
                    statuses[identity] = "COMPLETE_TRANSPORT_FAILURE"
                    claims[identity].update({"status": statuses[identity], "failure_type": type(error).__name__, "replay_api_calls": 0})
                _atomic_json(self.state_path, state)
            return RunnerResult(current, statuses, business_api_calls)
        finally:
            lock.release()


def runner(root: Path) -> TossEquityUr244Runner:
    return TossEquityUr244Runner(root)


__all__ = [
    "IDENTITIES", "LANDING_ROOT", "MANIFEST_PATH", "PROJECTIONS", "STATE_PATH", "TARGET_DATE_KST", "TossEquityUr244Runner",
    "TossQuoteTransportResult", "WINDOW_IDS", "eligible_identities", "ensure_manifest", "is_active", "manifest_payload", "read_manifest", "runner", "selected_boundary",
]
