"""Strict after-hours NXT close acceptance for the isolated UR-241 pilot."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.toss_market_current_observation import TossCurrentObservation, stock_price_snapshot
from stock_data.orchestration.toss_stock_current_live import _atomic_json

KST = ZoneInfo("Asia/Seoul")
RETAINED_LANDING_SHA256 = "876eb70453142829b0eb7a02ebef89fc94492ac1ed8da9f737b63ce4ea1c691c"


def validate_nxt_session_close(candidate: TossCurrentObservation, payload: dict[str, Any], retrieved_at: datetime, expected_date: str) -> None:
    """Accept an explicit NXT 19:55–20:00 KST close, never a live tick."""
    if candidate.market_date != expected_date or candidate.observation.unit != "KRW per share":
        raise RuntimeError("Toss NXT close identity/date/unit mismatch")
    result = payload.get("result")
    rows = [row for row in result if isinstance(row, dict) and row.get("symbol") == "005930"] if isinstance(result, list) else []
    if len(rows) != 1 or rows[0].get("venue") not in {None, ""}:
        raise RuntimeError("Toss NXT-close inference requires an absent venue field")
    source = datetime.fromisoformat(candidate.observation.provider_timestamp_utc).astimezone(KST)
    if source.date().isoformat() != expected_date or not time(19, 55) <= source.timetz().replace(tzinfo=None) <= time(20, 0):
        raise RuntimeError("Toss NXT close timestamp is outside the exact session-close interval")
    if source > retrieved_at.astimezone(KST):
        raise RuntimeError("Toss NXT close timestamp is in the future")


def recover_retained_inferred_close(
    root: Path, *, expected_date: str, expected_landing_sha256: str = RETAINED_LANDING_SHA256,
) -> dict[str, object]:
    """Project UR-241's terminal retained Landing without a provider call."""
    root = Path(root)
    state = json.loads((root / "data/state/toss_stock_nxt_close_ur241.json").read_text(encoding="utf-8"))
    attempt = state.get("attempts", {}).get(expected_date)
    if not isinstance(attempt, dict) or attempt.get("status") != "FAILED" or attempt.get("token_calls") != 1 or attempt.get("business_calls") != 1 or not isinstance(attempt.get("landing_file"), str):
        raise RuntimeError("UR-241 retained recovery requires the exact terminal 1/1 attempt")
    landing_bytes = (root / str(attempt["landing_file"])).read_bytes()
    if hashlib.sha256(landing_bytes).hexdigest() != expected_landing_sha256:
        raise RuntimeError("UR-241 retained Landing file hash mismatch")
    landing = json.loads(landing_bytes)
    payload = landing.get("raw_response")
    captured = landing.get("captured_at_utc")
    if not isinstance(payload, dict) or not isinstance(captured, str):
        raise RuntimeError("UR-241 retained Landing schema is invalid")
    retrieved = datetime.fromisoformat(captured)
    candidate = stock_price_snapshot(payload, symbol="005930", retrieved_at_utc=captured, route_suffix=":TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW")
    validate_nxt_session_close(candidate, payload, retrieved, expected_date)
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / "data/state/current_observations/toss_005930_nxt_close_ur241.json"))
    decision = coordinator.refresh(candidate.route(), primary_attempt=lambda: candidate.source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
    replay = coordinator.replay(candidate.route())
    if decision.observation != candidate.observation or replay.observation != candidate.observation or replay.api_calls != 0:
        raise RuntimeError("UR-241 retained recovery readback mismatch")
    attempt["retained_api_zero_recovery"] = {
        "classification": "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        "venue_inferred": True,
        "not_live": True,
        "currency": "KRW",
        "projection_path": "data/state/current_observations/toss_005930_nxt_close_ur241.json",
        "route_id": candidate.observation.route_id,
        "source_route": candidate.observation.source_route,
        "provider_timestamp_utc": candidate.observation.provider_timestamp_utc,
        "external_api_calls": 0,
    }
    _atomic_json(root / "data/state/toss_stock_nxt_close_ur241.json", state)
    return {"status": "RETAINED_API_ZERO_INFERRED_NXT_CLOSE", "route_id": candidate.observation.route_id, "provider_timestamp_utc": candidate.observation.provider_timestamp_utc, "replay_api_calls": replay.api_calls}


__all__ = ["recover_retained_inferred_close", "validate_nxt_session_close"]
