"""Fail-closed adapter for Naver's exact 005930 domestic-stock polling row."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import CurrentObservation, CurrentObservationRoute, ObservationFinality, ObservationIdentity, ObservationInterval
from stock_data.orchestration.exchange_calendar import MarketSessionService, MarketVenue, SessionState


KST = ZoneInfo("Asia/Seoul")
IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930")
ROUTE_ID = "naver-domestic-stock-current:XKRX:005930"
SOURCE_ROUTE = "NAVER_FINANCE_WEB:polling.finance.naver.com/api/realtime/domestic/stock/A005930"
_MAX_AGE = timedelta(minutes=60)


class NaverDomesticStockObservationError(ValueError):
    """The undocumented polling row cannot become an in-window current quote."""


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise NaverDomesticStockObservationError("nv must be numeric")
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise NaverDomesticStockObservationError("nv must be numeric") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise NaverDomesticStockObservationError("nv must be positive and finite")
    return parsed


def _timestamp(value: Any, retrieved_at: datetime) -> datetime:
    if not isinstance(value, str) or len(value) != 14 or not value.isdecimal():
        raise NaverDomesticStockObservationError("dt must be exact YYYYMMDDHHMMSS")
    try:
        source_kst = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError as error:
        raise NaverDomesticStockObservationError("dt must be a valid KST timestamp") from error
    retrieved_utc = retrieved_at.astimezone(timezone.utc)
    source_utc = source_kst.astimezone(timezone.utc)
    if source_kst.date() != retrieved_utc.astimezone(KST).date():
        raise NaverDomesticStockObservationError("dt is not today KST")
    if source_utc > retrieved_utc or retrieved_utc - source_utc > _MAX_AGE:
        raise NaverDomesticStockObservationError("dt fails the <=60-minute nonfuture gate")
    if MarketSessionService(MarketVenue.XKRX_CASH).state_at(source_utc) is not SessionState.REGULAR:
        raise NaverDomesticStockObservationError("dt is outside XKRX regular session")
    return source_utc


def naver_domestic_stock_quote(payload: dict[str, Any], *, retrieved_at: datetime) -> SourceObservation[CurrentObservation]:
    """Map only explicit KOSPI regular-market polling evidence to KRW/share.

    The route must expose both ``mks=KOSPI`` and ``ms=OPEN``. Missing venue
    evidence is intentionally numeric-free, which prevents an NXT or blended
    quote from being silently presented as an XKRX regular-session value.
    """
    if not isinstance(payload, dict) or payload.get("cd") != "A005930":
        raise NaverDomesticStockObservationError("cd must be exact A005930")
    if payload.get("mks") != "KOSPI":
        raise NaverDomesticStockObservationError("mks must explicitly be KOSPI")
    if payload.get("ms") != "OPEN":
        raise NaverDomesticStockObservationError("ms must be OPEN")
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise NaverDomesticStockObservationError("retrieved_at must be timezone-aware")
    provider_at = _timestamp(payload.get("dt"), retrieved_at)
    retrieved_utc = retrieved_at.astimezone(timezone.utc)
    observation = CurrentObservation(
        route_id=ROUTE_ID, identity=IDENTITY, interval=ObservationInterval.SNAPSHOT,
        value=_number(payload.get("nv")), unit="KRW per share",
        provider="NAVER_FINANCE_WEB", upstream_provider="NAVER_FINANCE_WEB", source_route=SOURCE_ROUTE,
        provider_timestamp_utc=provider_at.isoformat(), retrieved_at_utc=retrieved_utc.isoformat(),
        finality=ObservationFinality.PROVISIONAL,
    )
    observation.validate()
    return SourceObservation(observation, SourceProvenance(
        provider=observation.provider, upstream_provider=observation.upstream_provider,
        source_route=observation.source_route, retrieved_at_utc=observation.retrieved_at_utc, request_count=1,
    ))


def naver_domestic_stock_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(route_id=ROUTE_ID, primary_provider="NAVER_FINANCE_WEB", primary_route=SOURCE_ROUTE,
                                    fallback_provider="UNAVAILABLE", fallback_upstream_provider="UNAVAILABLE", fallback_route="UNAVAILABLE", fallback_enabled=False),
        identity=IDENTITY, interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


__all__ = ["NaverDomesticStockObservationError", "naver_domestic_stock_quote", "naver_domestic_stock_route"]
