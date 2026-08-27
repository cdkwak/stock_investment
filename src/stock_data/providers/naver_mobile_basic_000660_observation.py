"""UR-199 route-local 000660 adapter, reusing the accepted strict Naver contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import CurrentObservation, CurrentObservationRoute, ObservationFinality, ObservationIdentity, ObservationInterval
from stock_data.providers.naver_current_web_observation import NaverCurrentWebObservationError, _price, _provider_time, _utc, _validate_domestic_krw_contract

IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "000660")
ROUTE_ID = "naver-mobile-basic-current:XKRX:000660"
SOURCE_ROUTE = "NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/000660/basic"


def naver_mobile_basic_000660_quote(payload: dict[str, Any], *, retrieved_at: datetime) -> SourceObservation[CurrentObservation]:
    if not isinstance(payload, dict) or payload.get("itemCode") != IDENTITY.symbol:
        raise NaverCurrentWebObservationError("itemCode differs from the exact route")
    retrieved_at_utc = _utc(retrieved_at, "retrieved_at")
    _validate_domestic_krw_contract(payload)
    provider_at = _provider_time(payload.get("localTradedAt"), retrieved_at_utc=retrieved_at_utc)
    observation = CurrentObservation(ROUTE_ID, IDENTITY, ObservationInterval.SNAPSHOT, _price(payload.get("closePrice")), "KRW per share", "NAVER_FINANCE_WEB", "NAVER_FINANCE_WEB", SOURCE_ROUTE, provider_at.isoformat(), retrieved_at_utc.isoformat(), ObservationFinality.PROVISIONAL)
    observation.validate()
    return SourceObservation(observation, SourceProvenance(observation.provider, observation.upstream_provider, observation.source_route, observation.retrieved_at_utc, 1))


def naver_mobile_basic_000660_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(RoutePolicy(ROUTE_ID, "NAVER_FINANCE_WEB", SOURCE_ROUTE, "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE", False), IDENTITY, (ObservationInterval.SNAPSHOT,))
