"""Strict retained-payload adapter for Naver's domestic-index polling response.

This is an undocumented public-web route.  It does not perform HTTP or infer a
clock from retrieval time: every accepted index row carries its own ``dt``
KST source timestamp.  The route is deliberately a local personal-display
candidate only and remains PIT-blocked and redistribution-unverified.
"""

from __future__ import annotations

import math
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
from stock_data.orchestration.exchange_calendar import MarketSessionService, MarketVenue, SessionState


KST = ZoneInfo("Asia/Seoul")
SOURCE_ROUTE = "NAVER_FINANCE_WEB:polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ,KPI200"
_MAX_AGE = timedelta(minutes=60)
_IDENTITIES = {
    "KOSPI": ObservationIdentity("KR_INDEX_CURRENT", "XKRX", "KOSPI"),
    "KOSDAQ": ObservationIdentity("KR_INDEX_CURRENT", "XKRX", "KOSDAQ"),
    "KPI200": ObservationIdentity("KR_INDEX_CURRENT", "XKRX", "KPI200"),
}


class NaverDomesticIndexObservationError(ValueError):
    """The public-web row cannot become a current index number."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaverDomesticIndexObservationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NaverDomesticIndexObservationError(f"{field} must be numeric")
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise NaverDomesticIndexObservationError(f"{field} must be numeric") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise NaverDomesticIndexObservationError(f"{field} must be positive and finite")
    return parsed


def _provider_time(value: Any, *, retrieved_at_utc: datetime) -> datetime:
    if not isinstance(value, str) or len(value) != 14 or not value.isdecimal():
        raise NaverDomesticIndexObservationError("dt must be exact YYYYMMDDHHMMSS")
    try:
        source_kst = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError as error:
        raise NaverDomesticIndexObservationError("dt must be a valid KST timestamp") from error
    provider_at_utc = source_kst.astimezone(timezone.utc)
    if source_kst.date() != retrieved_at_utc.astimezone(KST).date():
        raise NaverDomesticIndexObservationError("dt is not today KST")
    if provider_at_utc > retrieved_at_utc:
        raise NaverDomesticIndexObservationError("dt is after retrieval")
    if retrieved_at_utc - provider_at_utc > _MAX_AGE:
        raise NaverDomesticIndexObservationError("dt exceeds the 60-minute age gate")
    if MarketSessionService(MarketVenue.XKRX_CASH).state_at(provider_at_utc) is not SessionState.REGULAR:
        raise NaverDomesticIndexObservationError("dt is outside the XKRX regular session")
    return provider_at_utc


def _identity(code: Any) -> ObservationIdentity:
    if not isinstance(code, str) or code not in _IDENTITIES:
        raise NaverDomesticIndexObservationError("cd is outside the exact domestic-index allowlist")
    return _IDENTITIES[code]


def naver_domestic_index_row(
    payload: dict[str, Any], *, retrieved_at: datetime,
) -> SourceObservation[CurrentObservation]:
    """Map one exact Naver polling row to native index points.

    ``cd`` selects only KOSPI, KOSDAQ or KPI200.  Their ``nv`` is retained as
    source-native index points; no price, base-100, percentage or multiplier
    conversion is inferred.  ``ms=OPEN`` and the provider timestamp's calendar
    session are both required so a close/non-session snapshot cannot appear as
    an in-window current observation.
    """
    if not isinstance(payload, dict):
        raise NaverDomesticIndexObservationError("index row must be an object")
    retrieved_at_utc = _utc(retrieved_at, "retrieved_at")
    identity = _identity(payload.get("cd"))
    if payload.get("ms") != "OPEN":
        raise NaverDomesticIndexObservationError("ms must be OPEN")
    provider_at_utc = _provider_time(payload.get("dt"), retrieved_at_utc=retrieved_at_utc)
    observation = CurrentObservation(
        route_id=f"naver-domestic-index-current:XKRX:{identity.symbol}",
        identity=identity,
        interval=ObservationInterval.SNAPSHOT,
        value=_number(payload.get("nv"), "nv"),
        unit="index points",
        provider="NAVER_FINANCE_WEB",
        upstream_provider="NAVER_FINANCE_WEB",
        source_route=SOURCE_ROUTE,
        provider_timestamp_utc=provider_at_utc.isoformat(),
        retrieved_at_utc=retrieved_at_utc.isoformat(),
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
            request_count=1,
        ),
    )


def naver_domestic_index_route(code: str) -> CurrentObservationRoute:
    identity = _identity(code)
    route_id = f"naver-domestic-index-current:XKRX:{identity.symbol}"
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=route_id,
            primary_provider="NAVER_FINANCE_WEB",
            primary_route=SOURCE_ROUTE,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=identity,
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


__all__ = [
    "NaverDomesticIndexObservationError", "SOURCE_ROUTE", "naver_domestic_index_row",
    "naver_domestic_index_route",
]
