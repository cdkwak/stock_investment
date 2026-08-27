"""Strict retained-payload adapter for one Naver public-web current quote.

The route is undocumented public-web data.  This module deliberately accepts
only the observed Korean domestic contract; it does not make HTTP requests,
infer a currency from a symbol, or authorize polling.
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


KST = ZoneInfo("Asia/Seoul")
IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "000660")
ROUTE_ID = "naver-web-current:XKRX:000660"
SOURCE_ROUTE = "NAVER_WEB:/api/stock/000660/basic"
_MAX_AGE = timedelta(minutes=60)


class NaverCurrentWebObservationError(ValueError):
    """The undocumented public-web payload cannot become a current number."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaverCurrentWebObservationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _price(value: Any) -> float:
    if isinstance(value, bool):
        raise NaverCurrentWebObservationError("closePrice must be numeric")
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise NaverCurrentWebObservationError("closePrice must be numeric") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise NaverCurrentWebObservationError("closePrice must be positive and finite")
    return parsed


def _provider_time(value: Any, *, retrieved_at_utc: datetime) -> datetime:
    if not isinstance(value, str):
        raise NaverCurrentWebObservationError("localTradedAt is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise NaverCurrentWebObservationError("localTradedAt must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() != KST.utcoffset(parsed):
        raise NaverCurrentWebObservationError("localTradedAt must carry Asia/Seoul offset")
    provider_at_utc = parsed.astimezone(timezone.utc)
    if provider_at_utc > retrieved_at_utc:
        raise NaverCurrentWebObservationError("localTradedAt is after retrieval")
    if retrieved_at_utc - provider_at_utc > _MAX_AGE:
        raise NaverCurrentWebObservationError("localTradedAt exceeds the 60-minute age gate")
    if parsed.astimezone(KST).date() != retrieved_at_utc.astimezone(KST).date():
        raise NaverCurrentWebObservationError("localTradedAt is not today KST")
    return provider_at_utc


def _validate_domestic_krw_contract(payload: dict[str, Any]) -> None:
    exchange = payload.get("stockExchangeType")
    if not isinstance(exchange, dict):
        raise NaverCurrentWebObservationError("stockExchangeType is required")
    exact = {
        "code": "KS",
        "zoneId": "Asia/Seoul",
        "nationType": "KOR",
        "stockType": "domestic",
        "delayTime": 0,
        "startTime": "0900",
        "endTime": "1530",
    }
    if any(exchange.get(key) != value for key, value in exact.items()):
        raise NaverCurrentWebObservationError("payload is outside the exact Korean domestic KRW contract")
    if payload.get("marketStatus") != "OPEN":
        raise NaverCurrentWebObservationError("marketStatus must be OPEN")
    if payload.get("delayTime") != 0:
        raise NaverCurrentWebObservationError("top-level delayTime must be zero")


def naver_web_current_quote(
    payload: dict[str, Any], *, retrieved_at: datetime,
) -> SourceObservation[CurrentObservation]:
    """Map only the observed XKRX/KOSPI web contract to KRW per share.

    ``KRW per share`` is a route-local identity/unit mapping, not an inference
    from a missing top-level currency field.  Any different exchange, nation,
    stock type, zone, delay or session fails closed.
    """
    if not isinstance(payload, dict) or payload.get("itemCode") != IDENTITY.symbol:
        raise NaverCurrentWebObservationError("itemCode differs from the exact route")
    retrieved_at_utc = _utc(retrieved_at, "retrieved_at")
    _validate_domestic_krw_contract(payload)
    provider_at_utc = _provider_time(payload.get("localTradedAt"), retrieved_at_utc=retrieved_at_utc)
    observation = CurrentObservation(
        route_id=ROUTE_ID,
        identity=IDENTITY,
        interval=ObservationInterval.SNAPSHOT,
        value=_price(payload.get("closePrice")),
        unit="KRW per share",
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


def naver_web_current_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=ROUTE_ID,
            primary_provider="NAVER_FINANCE_WEB",
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
    "IDENTITY", "NaverCurrentWebObservationError", "ROUTE_ID",
    "naver_web_current_quote", "naver_web_current_route",
]
