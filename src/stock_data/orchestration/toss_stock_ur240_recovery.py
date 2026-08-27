"""API-zero retained-Landing recovery for the UR-239 Toss 000660 quote."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


KST = ZoneInfo("Asia/Seoul")
UR239_LANDING_SHA256 = "576019ac260bf2e6ce97f6683bb60fb5f1fb39beaaa1785abdd21d806bad78d5"
UR239_LANDING_PATH = Path(
    "data/landing/tossinvest/stock_current_quote_ur239/000660_2026-08-21_20260821T131225785444Z.json"
)
PROJECTION_PATH = Path("data/state/current_observations/toss_000660_nxt_session_close_ur240.json")
ROUTE_ID = "toss-stock-price:000660:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "000660")
_CLOSE_START = time(19, 55)
_CLOSE_END = time(20, 0)


class TossUr240RecoveryError(RuntimeError):
    """The retained quote does not prove the NXT session-close contract."""


@dataclass(frozen=True)
class TossUr240RecoveryResult:
    status: str
    landing_sha256: str
    provider_timestamp_utc: str | None
    venue_inferred: bool
    replay_api_calls: int


def _load_landing(path: Path, expected_sha256: str) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TossUr240RecoveryError("retained Landing is unreadable") from error
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise TossUr240RecoveryError("retained Landing hash mismatch")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise TossUr240RecoveryError("retained Landing JSON is invalid") from error
    if not isinstance(payload, dict) or set(payload) != {
        "captured_at_utc", "endpoint", "expected_market_date", "params", "provider", "raw_response", "raw_sha256",
    }:
        raise TossUr240RecoveryError("retained Landing envelope schema mismatch")
    if payload["provider"] != "tossinvest_open_api" or payload["endpoint"] != "/api/v1/prices":
        raise TossUr240RecoveryError("retained Landing provider route mismatch")
    if payload["params"] != {"symbols": "000660"} or payload["expected_market_date"] != "2026-08-21":
        raise TossUr240RecoveryError("retained Landing identity mismatch")
    return payload


def _row(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    response = payload["raw_response"]
    if not isinstance(response, dict) or set(response) != {"result"}:
        raise TossUr240RecoveryError("retained quote response schema mismatch")
    rows = response["result"]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise TossUr240RecoveryError("retained quote must contain exactly one row")
    row = rows[0]
    if row.get("symbol") != "000660" or row.get("currency") != "KRW":
        raise TossUr240RecoveryError("retained quote symbol or currency mismatch")
    venue, session = row.get("venue"), row.get("session")
    if venue is None and session is None:
        # The current user authorized this one route-local classification only.
        # It remains explicit inference in route provenance, never provider data.
        return row, True
    if venue != "XKRX" or session != "NXT":
        raise TossUr240RecoveryError("retained quote venue-session contradicts the XKRX/NXT contract")
    return row, False


def _provider_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise TossUr240RecoveryError("retained quote timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TossUr240RecoveryError("retained quote timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise TossUr240RecoveryError("retained quote timestamp must be timezone-aware")
    kst = parsed.astimezone(KST)
    if kst.date().isoformat() != "2026-08-21" or not _CLOSE_START <= kst.time() <= _CLOSE_END:
        raise TossUr240RecoveryError("retained quote is outside the exact NXT close window")
    return parsed.astimezone(timezone.utc)


def _value(value: object) -> float:
    if isinstance(value, bool):
        raise TossUr240RecoveryError("retained quote price is invalid")
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as error:
        raise TossUr240RecoveryError("retained quote price is invalid") from error
    if parsed <= 0:
        raise TossUr240RecoveryError("retained quote price must be positive")
    return parsed


def recover_ur239_nxt_session_close(
    project_root: Path,
    *,
    landing_path: Path = UR239_LANDING_PATH,
    expected_sha256: str = UR239_LANDING_SHA256,
    projection_path: Path = PROJECTION_PATH,
) -> TossUr240RecoveryResult:
    """Hash-read a retained quote and atomically project only complete close evidence.

    This function is transport-free.  It never attempts OAuth or a provider GET.
    A rejected Landing does not open, create, or alter the isolated projection.
    """
    root = Path(project_root)
    payload = _load_landing(root / landing_path, expected_sha256)
    row, venue_inferred = _row(payload)
    provider_time = _provider_time(row.get("timestamp"))
    retrieved_at = datetime.fromisoformat(str(payload["captured_at_utc"]).replace("Z", "+00:00"))
    if retrieved_at.tzinfo is None:
        raise TossUr240RecoveryError("retained capture timestamp must be timezone-aware")
    observation = CurrentObservation(
        route_id=ROUTE_ID,
        identity=IDENTITY,
        interval=ObservationInterval.SNAPSHOT,
        value=_value(row.get("lastPrice")),
        unit="KRW per share",
        provider="tossinvest_open_api",
        upstream_provider="tossinvest_open_api",
        source_route="/api/v1/prices",
        provider_timestamp_utc=provider_time.isoformat(),
        retrieved_at_utc=retrieved_at.astimezone(timezone.utc).isoformat(),
        finality=ObservationFinality.POST_CLOSE_SNAPSHOT,
    )
    observation.validate()
    source = SourceObservation(
        observation,
        SourceProvenance(
            provider=observation.provider,
            upstream_provider=observation.upstream_provider,
            source_route=observation.source_route,
            retrieved_at_utc=observation.retrieved_at_utc,
            # The recovery performs no transport, but provenance preserves the
            # one original UR-239 provider request represented by Landing.
            request_count=1,
        ),
    )
    route = CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=ROUTE_ID,
            primary_provider="tossinvest_open_api",
            primary_route="/api/v1/prices",
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=IDENTITY,
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / projection_path))
    result = coordinator.refresh(
        route,
        primary_attempt=lambda: source,
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("UR-240 has no fallback")),
    )
    if result.observation != observation or result.api_calls != 1:
        raise TossUr240RecoveryError("UR-240 atomic projection readback mismatch")
    replay = coordinator.replay(route)
    if replay.observation != observation or replay.api_calls != 0:
        raise TossUr240RecoveryError("UR-240 API-zero replay mismatch")
    return TossUr240RecoveryResult(
        "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW" if venue_inferred else "NXT_SESSION_CLOSE",
        expected_sha256,
        observation.provider_timestamp_utc,
        venue_inferred,
        replay.api_calls,
    )


__all__ = [
    "IDENTITY", "PROJECTION_PATH", "ROUTE_ID", "TossUr240RecoveryError", "TossUr240RecoveryResult",
    "UR239_LANDING_PATH", "UR239_LANDING_SHA256", "recover_ur239_nxt_session_close",
]
