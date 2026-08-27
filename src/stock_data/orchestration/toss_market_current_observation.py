"""Static Toss market adapters for the display-only current-observation layer.

The adapter accepts already-received, allowlisted market payloads.  It never
constructs a Toss client, reads runtime configuration, or invokes an endpoint.
Account, holdings, and order paths are intentionally absent.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import (
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
    ObservationTimestampBasis,
)


KST = ZoneInfo("Asia/Seoul")
_MARKETS = frozenset({"KOSPI", "KOSDAQ"})
_INDEX_UNIT = "index points"
_KRW_UNIT = "KRW"
_KRW_PER_SHARE_UNIT = "KRW per share"
_STOCK_SYMBOL_PATTERN = re.compile(r"[0-9]{6}")


class TossCurrentObservationError(ValueError):
    """A payload cannot become a precise, display-only Toss observation."""


class TossProviderBoundary(StrEnum):
    MARKET_INDICATOR = "TOSS_MARKET_INDICATOR"
    KRX_ONLY_PROVIDER_EOD = "KRX_ONLY_PROVIDER_EOD"


@dataclass(frozen=True)
class TossCurrentObservation:
    """Base observation plus Toss-specific serving facts retained by the caller."""

    source: SourceObservation[CurrentObservation]
    market_date: str
    provider_boundary: TossProviderBoundary
    is_provisional: bool

    @property
    def observation(self) -> CurrentObservation:
        return self.source.value

    def route(self) -> CurrentObservationRoute:
        """Return a no-fallback route with this exact provider identity/interval."""
        observation = self.observation
        return CurrentObservationRoute(
            fallback_policy=RoutePolicy(
                route_id=observation.route_id,
                primary_provider=observation.provider,
                primary_route=observation.source_route,
                fallback_provider="UNAVAILABLE",
                fallback_upstream_provider="UNAVAILABLE",
                fallback_route="UNAVAILABLE",
                fallback_enabled=False,
            ),
            identity=observation.identity,
            interval_precedence=(observation.interval,),
        )


def _market(market: str) -> str:
    if market not in _MARKETS:
        raise TossCurrentObservationError("Toss current observation supports KOSPI or KOSDAQ only")
    return market


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise TossCurrentObservationError(f"Toss {field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TossCurrentObservationError(f"Toss {field} must be an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise TossCurrentObservationError(f"Toss {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: Any, field: str) -> str:
    return _timestamp(value, field).isoformat()


def _market_date(timestamp_utc: str) -> str:
    return _timestamp(timestamp_utc, "provider timestamp").astimezone(KST).date().isoformat()


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TossCurrentObservationError(f"Toss {field} must be numeric")
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as error:
        raise TossCurrentObservationError(f"Toss {field} must be numeric") from error
    if not math.isfinite(parsed):
        raise TossCurrentObservationError(f"Toss {field} must be finite")
    return parsed


def _single_result_row(payload: dict[str, Any], key: str) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TossCurrentObservationError("Toss result must be an object")
    rows = result.get(key)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise TossCurrentObservationError(f"Toss result.{key} must contain exactly one object")
    return rows[0]


def _source(
    *, route_id: str, identity: ObservationIdentity, interval: ObservationInterval,
    value: float, unit: str, source_route: str, provider_timestamp_utc: str,
    retrieved_at_utc: str, finality: ObservationFinality,
    timestamp_basis: ObservationTimestampBasis = ObservationTimestampBasis.PROVIDER_TIMESTAMP,
) -> SourceObservation[CurrentObservation]:
    observation = CurrentObservation(
        route_id=route_id,
        identity=identity,
        interval=interval,
        value=value,
        unit=unit,
        provider="tossinvest_open_api",
        upstream_provider="tossinvest_open_api",
        source_route=source_route,
        provider_timestamp_utc=provider_timestamp_utc,
        retrieved_at_utc=retrieved_at_utc,
        finality=finality,
        timestamp_basis=timestamp_basis,
    )
    observation.validate()
    return SourceObservation(
        observation,
        SourceProvenance(
            provider=observation.provider,
            upstream_provider=observation.upstream_provider,
            source_route=source_route,
            retrieved_at_utc=retrieved_at_utc,
            request_count=1,
        ),
    )


def market_price_snapshot(
    payload: dict[str, Any], *, market: str, retrieved_at_utc: str,
) -> TossCurrentObservation:
    """Adapt one exact KOSPI/KOSDAQ price snapshot; never select another symbol."""
    market = _market(market)
    result = payload.get("result")
    if not isinstance(result, list):
        raise TossCurrentObservationError("Toss price result must be an array")
    rows = [row for row in result if isinstance(row, dict) and row.get("symbol") == market]
    if len(rows) != 1:
        raise TossCurrentObservationError("Toss price response must contain exactly one requested market")
    row = rows[0]
    retrieved = _utc_text(retrieved_at_utc, "retrieved_at_utc")
    provider_timestamp = row.get("timestamp")
    if provider_timestamp is None:
        timestamp = retrieved
        timestamp_basis = ObservationTimestampBasis.RETRIEVAL_TIMESTAMP
    else:
        timestamp = _utc_text(provider_timestamp, "price timestamp")
        timestamp_basis = ObservationTimestampBasis.PROVIDER_TIMESTAMP
    source = _source(
        route_id=f"toss-market-price:{market}:snapshot:PROVISIONAL",
        identity=ObservationIdentity("TOSS_MARKET_PRICE_SNAPSHOT", "XKRX", market),
        interval=ObservationInterval.SNAPSHOT,
        value=_number(row.get("lastPrice"), "lastPrice"),
        unit=_INDEX_UNIT,
        source_route="/api/v1/market-indicators/prices",
        provider_timestamp_utc=timestamp,
        retrieved_at_utc=retrieved,
        finality=ObservationFinality.PROVISIONAL,
        timestamp_basis=timestamp_basis,
    )
    return TossCurrentObservation(source, _market_date(timestamp), TossProviderBoundary.MARKET_INDICATOR, True)


def stock_price_snapshot(
    payload: dict[str, Any], *, symbol: str, retrieved_at_utc: str,
    route_suffix: str = "",
) -> TossCurrentObservation:
    """Adapt one exact current stock price with its provider timestamp and currency."""
    if not _STOCK_SYMBOL_PATTERN.fullmatch(symbol):
        raise TossCurrentObservationError("Toss stock symbol must be six digits")
    result = payload.get("result")
    if not isinstance(result, list):
        raise TossCurrentObservationError("Toss stock price result must be an array")
    rows = [row for row in result if isinstance(row, dict) and row.get("symbol") == symbol]
    if len(rows) != 1:
        raise TossCurrentObservationError("Toss stock price response must contain exactly one requested symbol")
    row = rows[0]
    if row.get("currency") != "KRW":
        raise TossCurrentObservationError("Toss stock price currency must be KRW")
    timestamp = _utc_text(row.get("timestamp"), "stock price timestamp")
    retrieved = _utc_text(retrieved_at_utc, "retrieved_at_utc")
    source = _source(
        route_id=f"toss-stock-price:{symbol}:snapshot:PROVISIONAL{route_suffix}",
        identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", symbol),
        interval=ObservationInterval.SNAPSHOT,
        value=_number(row.get("lastPrice"), "lastPrice"),
        unit=_KRW_PER_SHARE_UNIT,
        source_route=f"/api/v1/prices{route_suffix}",
        provider_timestamp_utc=timestamp,
        retrieved_at_utc=retrieved,
        finality=ObservationFinality.PROVISIONAL,
    )
    return TossCurrentObservation(
        source, _market_date(timestamp), TossProviderBoundary.MARKET_INDICATOR, True,
    )


def market_candle(
    payload: dict[str, Any], *, market: str, interval: ObservationInterval,
    retrieved_at_utc: str, finality: ObservationFinality = ObservationFinality.PROVISIONAL,
) -> TossCurrentObservation:
    """Adapt one retained-evidence daily market candle without resampling."""
    market = _market(market)
    if interval is not ObservationInterval.DAILY:
        raise TossCurrentObservationError("Toss retained candle evidence supports interval=1d only")
    row = _single_result_row(payload, "candles")
    timestamp = _utc_text(row.get("timestamp"), "candle timestamp")
    retrieved = _utc_text(retrieved_at_utc, "retrieved_at_utc")
    source = _source(
        route_id=f"toss-market-candle:{market}:{interval.value}:{finality.value}",
        identity=ObservationIdentity("TOSS_MARKET_CANDLE", "XKRX", market),
        interval=interval,
        value=_number(row.get("closePrice"), "closePrice"),
        unit=_INDEX_UNIT,
        source_route=f"/api/v1/market-indicators/{market}/candles",
        provider_timestamp_utc=timestamp,
        retrieved_at_utc=retrieved,
        finality=finality,
    )
    return TossCurrentObservation(
        source, _market_date(timestamp), TossProviderBoundary.MARKET_INDICATOR,
        finality is not ObservationFinality.FINAL,
    )


_INVESTOR_METRICS = {
    "individual_buy_amount": ("individual", "buyAmount"),
    "individual_sell_amount": ("individual", "sellAmount"),
    "foreigner_buy_amount": ("foreigner", "buyAmount"),
    "foreigner_sell_amount": ("foreigner", "sellAmount"),
    "institution_buy_amount": ("institution", "buyAmount"),
    "institution_sell_amount": ("institution", "sellAmount"),
    "other_corporation_buy_amount": ("otherCorporation", "buyAmount"),
    "other_corporation_sell_amount": ("otherCorporation", "sellAmount"),
}


def market_investor_observation(
    payload: dict[str, Any], *, market: str, metric: str, retrieved_at_utc: str,
    finality: ObservationFinality = ObservationFinality.AS_RETRIEVED,
) -> TossCurrentObservation:
    """Adapt one provider-bound KRX-only investor amount; no official merge occurs."""
    market = _market(market)
    if metric not in _INVESTOR_METRICS:
        raise TossCurrentObservationError("unsupported Toss investor metric")
    row = _single_result_row(payload, "records")
    source_date = row.get("date")
    if not isinstance(source_date, str):
        raise TossCurrentObservationError("Toss investor date is required")
    try:
        market_date = datetime.fromisoformat(source_date).date().isoformat()
    except ValueError as error:
        raise TossCurrentObservationError("Toss investor date must be ISO") from error
    section, field = _INVESTOR_METRICS[metric]
    amounts = row.get(section)
    if not isinstance(amounts, dict):
        raise TossCurrentObservationError(f"Toss investor {section} must be an object")
    timestamp = _utc_text(row.get("updatedAt"), "investor updatedAt")
    retrieved = _utc_text(retrieved_at_utc, "retrieved_at_utc")
    source = _source(
        route_id=f"toss-market-investor:{market}:{metric}:KRX_ONLY_PROVIDER_EOD:{finality.value}",
        identity=ObservationIdentity("TOSS_MARKET_INVESTOR_KRX_ONLY", "XKRX", market),
        interval=ObservationInterval.DAILY,
        value=_number(amounts.get(field), f"{section}.{field}"),
        unit=_KRW_UNIT,
        source_route=f"/api/v1/market-indicators/{market}/investor-trading",
        provider_timestamp_utc=timestamp,
        retrieved_at_utc=retrieved,
        finality=finality,
    )
    return TossCurrentObservation(
        source, market_date, TossProviderBoundary.KRX_ONLY_PROVIDER_EOD,
        finality is not ObservationFinality.FINAL,
    )


__all__ = [
    "TossCurrentObservation", "TossCurrentObservationError", "TossProviderBoundary",
    "market_candle", "market_investor_observation", "market_price_snapshot",
    "stock_price_snapshot",
]
