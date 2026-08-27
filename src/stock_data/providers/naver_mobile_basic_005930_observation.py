"""UR-152 exact 005930 use of the retained Naver mobile-basic contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import CurrentObservation, CurrentObservationRoute, ObservationFinality, ObservationIdentity, ObservationInterval
from stock_data.providers.naver_current_web_observation import (
    NaverCurrentWebObservationError,
    _price,
    _provider_time,
    _utc,
    _validate_domestic_krw_contract,
)


IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930")
ROUTE_ID = "naver-mobile-basic-current:XKRX:005930"
SOURCE_ROUTE = "NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/005930/basic"


def naver_mobile_basic_005930_quote(payload: dict[str, Any], *, retrieved_at: datetime) -> SourceObservation[CurrentObservation]:
    """Reuse the strict UR-145 KS/KOR/domestic/zero-delay contract unchanged."""
    if not isinstance(payload, dict) or payload.get("itemCode") != IDENTITY.symbol:
        raise NaverCurrentWebObservationError("itemCode differs from the exact route")
    retrieved_at_utc = _utc(retrieved_at, "retrieved_at")
    _validate_domestic_krw_contract(payload)
    provider_at_utc = _provider_time(payload.get("localTradedAt"), retrieved_at_utc=retrieved_at_utc)
    observation = CurrentObservation(
        route_id=ROUTE_ID, identity=IDENTITY, interval=ObservationInterval.SNAPSHOT,
        value=_price(payload.get("closePrice")), unit="KRW per share",
        provider="NAVER_FINANCE_WEB", upstream_provider="NAVER_FINANCE_WEB", source_route=SOURCE_ROUTE,
        provider_timestamp_utc=provider_at_utc.isoformat(), retrieved_at_utc=retrieved_at_utc.isoformat(),
        finality=ObservationFinality.PROVISIONAL,
    )
    observation.validate()
    return SourceObservation(observation, SourceProvenance(
        provider=observation.provider, upstream_provider=observation.upstream_provider,
        source_route=observation.source_route, retrieved_at_utc=observation.retrieved_at_utc, request_count=1,
    ))


def naver_mobile_basic_005930_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(route_id=ROUTE_ID, primary_provider="NAVER_FINANCE_WEB", primary_route=SOURCE_ROUTE,
                                    fallback_provider="UNAVAILABLE", fallback_upstream_provider="UNAVAILABLE", fallback_route="UNAVAILABLE", fallback_enabled=False),
        identity=IDENTITY, interval_precedence=(ObservationInterval.SNAPSHOT,),
    )
