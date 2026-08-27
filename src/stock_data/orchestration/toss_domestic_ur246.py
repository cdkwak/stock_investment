"""Recurring, calendar-gated Toss domestic display operation for UR-246."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.naver_mobile_home_windows import window_id
from stock_data.orchestration.toss_market_current_observation import market_price_snapshot, stock_price_snapshot

KST = ZoneInfo("Asia/Seoul")
OPERATION_ID = "UR-246"
EQUITIES = ("000660", "005930")
INDICES = ("KOSPI", "KOSDAQ")
MANIFEST_ROOT = Path("data/state/toss_domestic_ur246_manifests")
STATE_ROOT = Path("data/state/toss_domestic_ur246")
LANDING_ROOT = Path("data/landing/tossinvest/domestic_ur246")
MAX_AGE = timedelta(minutes=60)


class TossDomesticTransport(Protocol):
    oauth_calls: int
    business_calls: int

    def stock(self, symbol: str) -> dict[str, Any]: ...
    def index(self, symbol: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RunResult:
    date_kst: str
    window_id: str | None
    statuses: dict[str, str]
    oauth_calls: int
    business_calls: int


def _manifest(date_kst: str) -> dict[str, object]:
    return {
        "schema_version": 1, "operation_id": OPERATION_ID, "date_kst": date_kst,
        "calendar": "ExchangeTradingCalendar/KR", "wake_start_kst": "08:00", "wake_end_kst": "20:00",
        "cadence_minutes": 30, "equities": list(EQUITIES), "indices": list(INDICES), "indices_window_kst": "[09:00,15:30)",
        "oauth_cap": 1, "business_get_cap": 4, "timeout_seconds": 10, "retry_count": 0,
        "redirect_count": 0, "fallback_count": 0, "landing_first": True, "display_only": True, "pit_safe": False,
    }


def _atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != body: raise RuntimeError("UR-246 atomic readback mismatch")
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _calendar_open(date_kst: str) -> bool:
    return ExchangeTradingCalendar(ExchangeMarket.KR).is_trading_day(datetime.fromisoformat(date_kst).date())


def ensure_daily_manifest(root: Path, *, now: datetime) -> Path | None:
    if now.tzinfo is None or now.utcoffset() is None: raise ValueError("timezone-aware clock required")
    date_kst = now.astimezone(KST).date().isoformat()
    if not _calendar_open(date_kst): return None
    path = Path(root) / MANIFEST_ROOT / f"{date_kst}.json"; expected = _manifest(date_kst)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError: pass
    if json.loads(path.read_text(encoding="utf-8")) != expected: raise RuntimeError("UR-246 daily manifest differs from contract")
    return path


def _boundary(now: datetime) -> str | None:
    local = now.astimezone(KST)
    if not time(8, 0) <= local.time() <= time(20, 29, 59): return None
    return window_id(now=local)


def _index_eligible(now: datetime) -> bool:
    local = now.astimezone(KST).time()
    return time(9, 0) <= local < time(15, 30)


def _state_path(root: Path, date_kst: str) -> Path: return Path(root) / STATE_ROOT / f"{date_kst}.json"


def _state(root: Path, date_kst: str) -> dict[str, object]:
    path = _state_path(root, date_kst)
    if not path.exists(): return {"schema_version": 1, "operation_id": OPERATION_ID, "date_kst": date_kst, "windows": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "operation_id", "date_kst", "windows"} or payload["schema_version"] != 1 or payload["operation_id"] != OPERATION_ID or payload["date_kst"] != date_kst or not isinstance(payload["windows"], dict):
        raise RuntimeError("UR-246 durable state differs from contract")
    return payload


def _required(now: datetime) -> tuple[str, ...]:
    return EQUITIES + (INDICES if _index_eligible(now) else ())


def _validate_stock(payload: dict[str, Any], *, symbol: str, now: datetime, date_kst: str, final: bool):
    suffix = ":TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW" if final else ":TOSS_ACTIVE_SESSION_60M"
    candidate = stock_price_snapshot(payload, symbol=symbol, retrieved_at_utc=now.astimezone(timezone.utc).isoformat(), route_suffix=suffix)
    source = datetime.fromisoformat(candidate.observation.provider_timestamp_utc).astimezone(KST)
    rows = [row for row in payload.get("result", []) if isinstance(row, dict) and row.get("symbol") == symbol] if isinstance(payload.get("result"), list) else []
    if candidate.market_date != date_kst or candidate.observation.unit != "KRW per share" or len(rows) != 1 or rows[0].get("currency") != "KRW": raise ValueError("stock identity/date/currency/unit mismatch")
    if final:
        if rows[0].get("venue") not in {None, ""} or rows[0].get("session") not in {None, ""} or not time(19, 55) <= source.time() <= time(20, 0): raise ValueError("final inferred-close contract mismatch")
    elif source > now.astimezone(KST) or now.astimezone(KST) - source > MAX_AGE: raise ValueError("stock source age exceeds 60 minutes")
    return candidate


def _validate_index(payload: dict[str, Any], *, symbol: str, now: datetime, date_kst: str):
    candidate = market_price_snapshot(payload, market=symbol, retrieved_at_utc=now.astimezone(timezone.utc).isoformat())
    source = datetime.fromisoformat(candidate.observation.provider_timestamp_utc)
    if candidate.market_date != date_kst or now.astimezone(timezone.utc) - source > MAX_AGE or source > now.astimezone(timezone.utc): raise ValueError("index source date/age mismatch")
    return candidate


class TossDomesticUr246Runner:
    """One current half-open boundary; transport stays unconstructed until first claim."""
    def __init__(self, root: Path) -> None: self.root = Path(root)

    def run(
        self,
        *,
        now: datetime,
        transport_factory: Callable[[], TossDomesticTransport] | None = None,
        response_clock: Callable[[], datetime] | None = None,
    ) -> RunResult:
        if now.tzinfo is None or now.utcoffset() is None: raise ValueError("timezone-aware clock required")
        date_kst, boundary = now.astimezone(KST).date().isoformat(), _boundary(now)
        if boundary is None or ensure_daily_manifest(self.root, now=now) is None:
            return RunResult(date_kst, None, {"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"}, 0, 0)
        state_path, lock = _state_path(self.root, date_kst), CurrentObservationProcessLock(_state_path(self.root, date_kst).with_suffix(".lock"))
        if not lock.acquire(): return RunResult(date_kst, boundary, {"operation": "PROCESS_LOCKED"}, 0, 0)
        try:
            state, windows = _state(self.root, date_kst), None
            windows = dict(state["windows"]); claims = dict(windows.get(boundary, {})); needed = _required(now); statuses: dict[str, str] = {}
            pending = tuple(item for item in needed if item not in claims)
            if not pending: return RunResult(date_kst, boundary, {item: "ORPHANED_NO_REPEAT" if claims[item].get("status") == "ATTEMPTING" else "NO_REPEAT" for item in needed}, 0, 0)
            if transport_factory is None: return RunResult(date_kst, boundary, {item: "NO_TRANSPORT_ADAPTER" for item in pending}, 0, 0)
            transport: TossDomesticTransport | None = None
            for symbol in needed:
                if symbol in claims:
                    statuses[symbol] = "ORPHANED_NO_REPEAT" if claims[symbol].get("status") == "ATTEMPTING" else "NO_REPEAT"; continue
                claims[symbol] = {"status": "ATTEMPTING", "oauth_cap": 1, "business_get_cap": 1, "retry_count": 0, "redirect_count": 0, "fallback_count": 0}
                windows[boundary] = claims; state["windows"] = windows; _atomic(state_path, state)
                if transport is None: transport = transport_factory()
                before_oauth, before_business = transport.oauth_calls, transport.business_calls
                try:
                    payload = transport.stock(symbol) if symbol in EQUITIES else transport.index(symbol)
                    retrieved_at = (
                        response_clock() if response_clock is not None
                        else datetime.now(timezone.utc)
                    )
                    if (
                        retrieved_at.tzinfo is None
                        or retrieved_at.utcoffset() is None
                    ):
                        raise ValueError("timezone-aware response clock required")
                    oauth_used, business_used = transport.oauth_calls - before_oauth, transport.business_calls - before_business
                    if oauth_used < 0 or business_used != 1 or transport.oauth_calls > 1 or transport.business_calls > 4: raise RuntimeError("global Toss request cap exceeded")
                    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"); digest = hashlib.sha256(raw).hexdigest()
                    landing = self.root / LANDING_ROOT / date_kst / boundary.replace(":", "") / symbol / digest / "response.json"; landing.parent.mkdir(parents=True, exist_ok=True)
                    with landing.open("xb") as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    readback = landing.read_bytes()
                    if hashlib.sha256(readback).hexdigest() != digest: raise RuntimeError("Landing readback hash mismatch")
                    parsed = json.loads(readback)
                    candidate = _validate_stock(parsed, symbol=symbol, now=retrieved_at, date_kst=date_kst, final=boundary.endswith("T20:00:00+09:00")) if symbol in EQUITIES else _validate_index(parsed, symbol=symbol, now=retrieved_at, date_kst=date_kst)
                    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(self.root / "data/state/current_observations" / f"toss_{symbol.lower()}_ur246.json"))
                    refresh = coordinator.refresh(candidate.route(), primary_attempt=lambda candidate=candidate: candidate.source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
                    replay = coordinator.replay(candidate.route())
                    if refresh.observation != candidate.observation or replay.observation != candidate.observation or replay.api_calls != 0: raise RuntimeError("projection replay mismatch")
                    claims[symbol].update({"status": "COMPLETE", "landing_file": landing.relative_to(self.root).as_posix(), "landing_sha256": digest, "provider_timestamp_utc": candidate.observation.provider_timestamp_utc, "route_id": candidate.observation.route_id, "replay_api_calls": 0}); statuses[symbol] = "COMPLETE"
                except (ValueError, json.JSONDecodeError) as error:
                    claims[symbol].update({"status": "COMPLETE_SEMANTIC_FAILURE", "failure_type": type(error).__name__}); statuses[symbol] = "COMPLETE_SEMANTIC_FAILURE"
                except Exception as error:
                    claims[symbol].update({"status": "COMPLETE_TRANSPORT_FAILURE", "failure_type": type(error).__name__}); statuses[symbol] = "COMPLETE_TRANSPORT_FAILURE"
                _atomic(state_path, state)
            assert transport is not None
            return RunResult(date_kst, boundary, statuses, transport.oauth_calls, transport.business_calls)
        finally: lock.release()


def runner(root: Path) -> TossDomesticUr246Runner: return TossDomesticUr246Runner(root)

__all__ = ["EQUITIES", "INDICES", "MANIFEST_ROOT", "OPERATION_ID", "RunResult", "STATE_ROOT", "TossDomesticTransport", "TossDomesticUr246Runner", "ensure_daily_manifest", "runner"]
