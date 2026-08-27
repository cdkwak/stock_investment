"""Typed LS t1101 current-quote time-label composition for one display route."""

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
from stock_data.orchestration.exchange_calendar import MarketSessionService, MarketVenue, SessionState


KST = ZoneInfo("Asia/Seoul")
_HOTIME = re.compile(r"^(?:[01]\d|2[0-3])[0-5]\d[0-5]\d\d{2}$")
_MAX_AGE = timedelta(minutes=60)


class LST1101CurrentObservationError(ValueError):
    """A time-only t1101 response cannot establish the requested current timestamp."""


def _retrieval(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LST1101CurrentObservationError("retrieval time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _price(value: Any) -> float:
    if isinstance(value, bool):
        raise LST1101CurrentObservationError("t1101 price must be numeric")
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as error:
        raise LST1101CurrentObservationError("t1101 price must be numeric") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise LST1101CurrentObservationError("t1101 price must be positive and finite")
    return parsed


def _compose_timestamp(*, hotime: Any, retrieved_at_utc: datetime) -> datetime:
    """Compose only a regular-session label and an LS source time-of-day.

    The source does not provide a date.  The composition is intentionally
    unavailable outside a current XKRX regular session, which prevents a prior
    session's time label from being relabelled as the retrieval day.
    """
    if not isinstance(hotime, str) or not _HOTIME.fullmatch(hotime):
        raise LST1101CurrentObservationError("t1101 hotime must be HHMMSScc")
    service = MarketSessionService(MarketVenue.XKRX_CASH)
    if service.state_at(retrieved_at_utc) is not SessionState.REGULAR:
        raise LST1101CurrentObservationError("t1101 retrieval is outside an XKRX regular session")
    session_date = service.trade_date_at(retrieved_at_utc)
    local = retrieved_at_utc.astimezone(KST)
    if local.date() != session_date:
        raise LST1101CurrentObservationError("t1101 retrieval date does not equal the XKRX session label")
    hour, minute, second, centiseconds = (
        int(hotime[:2]), int(hotime[2:4]), int(hotime[4:6]), int(hotime[6:]),
    )
    provider_local = datetime(
        session_date.year, session_date.month, session_date.day, hour, minute, second,
        centiseconds * 10_000, tzinfo=KST,
    )
    provider_utc = provider_local.astimezone(timezone.utc)
    if provider_utc > retrieved_at_utc:
        raise LST1101CurrentObservationError("t1101 hotime is in the future relative to retrieval")
    if retrieved_at_utc - provider_utc > _MAX_AGE:
        raise LST1101CurrentObservationError("t1101 hotime exceeds the 60-minute age gate")
    return provider_utc


def t1101_current_quote(payload: dict[str, Any], *, retrieved_at: datetime) -> SourceObservation[CurrentObservation]:
    """Validate one LS t1101 response as a provisional source-native snapshot."""
    if not isinstance(payload, dict) or str(payload.get("rsp_cd")) != "00000":
        raise LST1101CurrentObservationError("t1101 response is not successful")
    block = payload.get("t1101OutBlock")
    if not isinstance(block, dict) or str(block.get("shcode", "")).strip() != "005930":
        raise LST1101CurrentObservationError("t1101 response symbol differs from the exact route")
    retrieved_at_utc = _retrieval(retrieved_at)
    provider_at_utc = _compose_timestamp(hotime=block.get("hotime"), retrieved_at_utc=retrieved_at_utc)
    observation = CurrentObservation(
        route_id="ls-t1101-current:XKRX:005930",
        identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930"),
        interval=ObservationInterval.SNAPSHOT,
        value=_price(block.get("price")),
        unit="provider_native_price",
        provider="LS_OPENAPI",
        upstream_provider="LS_OPENAPI",
        source_route="LS_OPENAPI:/stock/market-data:t1101",
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


def t1101_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id="ls-t1101-current:XKRX:005930",
            primary_provider="LS_OPENAPI",
            primary_route="LS_OPENAPI:/stock/market-data:t1101",
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930"),
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


__all__ = ["LST1101CurrentObservationError", "t1101_current_quote", "t1101_route"]
