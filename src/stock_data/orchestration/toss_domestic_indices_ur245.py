"""Future-only, transport-injected 30-minute Toss KOSPI/KOSDAQ collector."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.toss_market_current_observation import market_price_snapshot

KST = ZoneInfo("Asia/Seoul")
OPERATION_ID = "UR-245"
TARGET_DATE = "2026-08-24"
SYMBOLS = ("KOSPI", "KOSDAQ")
MANIFEST_PATH = Path("data/state/toss_domestic_indices_ur245_activation.json")
LEDGER_PATH = Path("data/state/toss_domestic_indices_ur245_windows.json")
LANDING_ROOT = Path("data/landing/tossinvest/domestic_indices_ur245")


class TossDomesticIndicesUr245Error(RuntimeError):
    pass


def _atomic(path: Path, payload: dict[str, Any]) -> None:
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


def _read(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return default
    except (OSError, json.JSONDecodeError) as error: raise TossDomesticIndicesUr245Error("UR-245 state unreadable") from error
    if not isinstance(payload, dict): raise TossDomesticIndicesUr245Error("UR-245 state schema mismatch")
    return payload


def eligible_boundary(now: datetime, manifest: dict[str, Any], ledger: dict[str, Any]) -> str | None:
    """Return one current half-open boundary; never backfill or construct transport."""
    if manifest != {"schema_version": 1, "operation_id": OPERATION_ID, "target_date_kst": TARGET_DATE, "symbols": list(SYMBOLS), "wake_start_kst": "08:00", "wake_end_kst": "20:00", "provider_start_kst": "09:00", "provider_end_kst": "15:30", "cadence_minutes": 30, "timeout_seconds": 10, "retry_count": 0, "redirect_count": 0, "fallback_count": 0, "display_only": True, "pit_safe": False}:
        raise TossDomesticIndicesUr245Error("UR-245 manifest mismatch")
    if not isinstance(ledger, dict) or set(ledger) != {"schema_version", "operation_id", "windows"} or ledger["schema_version"] != 1 or ledger["operation_id"] != OPERATION_ID or not isinstance(ledger["windows"], dict):
        raise TossDomesticIndicesUr245Error("UR-245 ledger mismatch")
    local = now.astimezone(KST)
    if local.date().isoformat() != TARGET_DATE or not time(9, 0) <= local.time() < time(15, 30): return None
    minute = local.minute - local.minute % 30
    boundary = local.replace(minute=minute, second=0, microsecond=0)
    key = boundary.isoformat()
    if key in ledger["windows"]: return None
    return key


def run_injected(root: Path, *, now: datetime, response_factory: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """Run exactly KOSPI then KOSDAQ after a durable no-backfill claim."""
    root = Path(root); manifest = _read(root / MANIFEST_PATH, {})
    ledger_path = root / LEDGER_PATH; ledger = _read(ledger_path, {"schema_version": 1, "operation_id": OPERATION_ID, "windows": {}})
    boundary = eligible_boundary(now, manifest, ledger)
    if boundary is None: return {"status": "API_ZERO_INELIGIBLE", "boundary": None, "calls": 0}
    ledger["windows"][boundary] = {"status": "ATTEMPTING", "symbols": list(SYMBOLS)}; _atomic(ledger_path, ledger)
    results: dict[str, Any] = {}
    try:
        for symbol in SYMBOLS:
            payload = response_factory(symbol)
            if not isinstance(payload, dict): raise TossDomesticIndicesUr245Error("UR-245 injected payload must be object")
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            landing = LANDING_ROOT / f"window={boundary.replace(':', '').replace('+09:00', '+0900')}/{symbol}.json"
            _atomic(root / landing, {"provider": "tossinvest_open_api", "source_route": "/api/v1/market-indicators/prices", "symbol": symbol, "body_sha256": hashlib.sha256(encoded).hexdigest(), "raw_response": payload})
            saved = _read(root / landing, {})
            if saved.get("body_sha256") != hashlib.sha256(json.dumps(saved.get("raw_response"), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest(): raise TossDomesticIndicesUr245Error("UR-245 Landing readback mismatch")
            candidate = market_price_snapshot(saved["raw_response"], market=symbol, retrieved_at_utc=now.astimezone(timezone.utc).isoformat())
            source_time = datetime.fromisoformat(candidate.observation.provider_timestamp_utc)
            if candidate.market_date != TARGET_DATE or now.astimezone(timezone.utc) - source_time > timedelta(minutes=60): raise TossDomesticIndicesUr245Error("UR-245 provider timestamp is stale or wrong-date")
            projection = root / Path(f"data/state/current_observations/toss_{symbol.lower()}_price_snapshot_ur245.json")
            coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(projection))
            decision = coordinator.refresh(candidate.route(), primary_attempt=lambda candidate=candidate: candidate.source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
            if decision.observation != candidate.observation or coordinator.replay(candidate.route()).api_calls != 0: raise TossDomesticIndicesUr245Error("UR-245 projection replay mismatch")
            results[symbol] = {"landing": landing.as_posix(), "provider_timestamp_utc": candidate.observation.provider_timestamp_utc}
        ledger["windows"][boundary] = {"status": "COMPLETE", "symbols": list(SYMBOLS), "results": results}; _atomic(ledger_path, ledger)
        return {"status": "COMPLETE", "boundary": boundary, "calls": 2, "results": results}
    except Exception as error:
        ledger["windows"][boundary] = {"status": "FAILED", "symbols": list(SYMBOLS), "failure_type": type(error).__name__}; _atomic(ledger_path, ledger)
        raise


__all__ = ["MANIFEST_PATH", "LEDGER_PATH", "OPERATION_ID", "SYMBOLS", "eligible_boundary", "run_injected"]
