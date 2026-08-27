"""Route-local parser for UR-190's retained official Nasdaq SOXX info body."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


_EASTERN = ZoneInfo("America/New_York")
_KST = ZoneInfo("Asia/Seoul")
_MAX_AGE = timedelta(minutes=60)
IDENTITY = ObservationIdentity("US_ETF_CURRENT", "NASDAQ", "SOXX")
ROUTE_ID = "nasdaq-soxx-info-api:NASDAQ:SOXX"
SOURCE_ROUTE = "NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf"


class NasdaqSoxxInfoObservationError(ValueError):
    """The retained payload lacks one exact SOXX current-observation fact."""


def _price(payload: dict[str, Any]) -> float:
    raw = payload.get("lastSalePrice")
    if not isinstance(raw, str) or re.fullmatch(r"\$\d+(?:\.\d+)?", raw.strip()) is None:
        raise NasdaqSoxxInfoObservationError("lastSalePrice must contain exactly one USD dollar marker")
    try:
        value = float(raw.strip()[1:])
    except ValueError as error:
        raise NasdaqSoxxInfoObservationError("lastSalePrice must be numeric") from error
    if not math.isfinite(value) or value <= 0:
        raise NasdaqSoxxInfoObservationError("lastSalePrice must be finite and positive")
    return value


def _provider_timestamp(value: Any, *, retrieved_at: datetime) -> datetime:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M ET", value) is None:
        raise NasdaqSoxxInfoObservationError("lastTradeTimestamp must be dated provider ET")
    try:
        source = datetime.strptime(value, "%b %d, %Y %I:%M %p ET").replace(tzinfo=_EASTERN)
    except ValueError as error:
        raise NasdaqSoxxInfoObservationError("lastTradeTimestamp must be valid provider ET") from error
    retrieved_utc = retrieved_at.astimezone(timezone.utc)
    source_utc = source.astimezone(timezone.utc)
    if source.astimezone(_KST).date() != retrieved_utc.astimezone(_KST).date():
        raise NasdaqSoxxInfoObservationError("lastTradeTimestamp is not today KST")
    if source_utc > retrieved_utc or retrieved_utc - source_utc > _MAX_AGE:
        raise NasdaqSoxxInfoObservationError("lastTradeTimestamp fails the <=60-minute nonfuture gate")
    return source_utc


def nasdaq_soxx_info_quote(
    payload: dict[str, Any], *, retrieved_at: datetime, request_count: int,
) -> SourceObservation[CurrentObservation]:
    """Accept only UR-190's explicit one-dollar SOXX ETF price representation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise NasdaqSoxxInfoObservationError("payload data object is required")
    data = payload["data"]
    primary = data.get("primaryData")
    if data.get("symbol") != "SOXX" or data.get("assetClass") != "ETF" or data.get("exchange") != "NASDAQ-GM":
        raise NasdaqSoxxInfoObservationError("payload must bind exact SOXX ETF NASDAQ-GM identity")
    if not isinstance(primary, dict):
        raise NasdaqSoxxInfoObservationError("primaryData object is required")
    currency = primary.get("currency")
    if currency not in (None, "", "USD"):
        raise NasdaqSoxxInfoObservationError("non-null currency contradicts the route-local USD marker")
    if data.get("marketStatus") not in {"Pre-Market", "Regular Market"}:
        raise NasdaqSoxxInfoObservationError("marketStatus must be a supported current session")
    if primary.get("isRealTime") is not True:
        raise NasdaqSoxxInfoObservationError("isRealTime must be true")
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise NasdaqSoxxInfoObservationError("retrieved_at must be timezone-aware")
    provider_at = _provider_timestamp(primary.get("lastTradeTimestamp"), retrieved_at=retrieved_at)
    observation = CurrentObservation(
        route_id=ROUTE_ID,
        identity=IDENTITY,
        interval=ObservationInterval.SNAPSHOT,
        value=_price(primary),
        unit="USD per share",
        provider="NASDAQ_OFFICIAL",
        upstream_provider="NASDAQ_OFFICIAL",
        source_route=SOURCE_ROUTE,
        provider_timestamp_utc=provider_at.isoformat(),
        retrieved_at_utc=retrieved_at.astimezone(timezone.utc).isoformat(),
        finality=ObservationFinality.PROVISIONAL,
    )
    observation.validate()
    return SourceObservation(
        observation,
        SourceProvenance(
            provider=observation.provider,
            upstream_provider=observation.upstream_provider,
            source_route=observation.source_route,
            retrieved_at_utc=observation.retrieved_at_utc,
            request_count=request_count,
        ),
    )


def nasdaq_soxx_info_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=ROUTE_ID,
            primary_provider="NASDAQ_OFFICIAL",
            primary_route=SOURCE_ROUTE,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=IDENTITY,
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


__all__ = [
    "NasdaqSoxxInfoObservationError",
    "nasdaq_soxx_info_quote",
    "nasdaq_soxx_info_route",
]
