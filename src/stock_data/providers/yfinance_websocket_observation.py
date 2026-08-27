"""Offline adapter for yfinance 1.6.0 decoded ``PricingData`` messages.

This module has no yfinance, HTTP, WebSocket, credential, cookie, environment,
or scheduler dependency.  A future authorized caller must decode the public
``PricingData`` protobuf and inject its dictionary plus a caller-owned arrival
sequence.  Adapter readiness is deliberately distinct from numeric availability:
synthetic acceptance proves only parsing/promotion behavior, never entitlement,
live delivery, or a displayable production price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation, CurrentObservationCoordinator, CurrentObservationFileStore,
    CurrentObservationRoute, ObservationFinality, ObservationIdentity, ObservationInterval,
)


YFINANCE_PROVIDER = "YFINANCE_1_6_0"
YFINANCE_UPSTREAM = "YAHOO_FINANCE_WEBSOCKET"


class YFWebSocketObservationError(ValueError):
    pass


class YFWebSocketAdapterStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    THROTTLED = "THROTTLED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    MALFORMED = "MALFORMED"
    EMPTY = "EMPTY"
    DISCONNECTED = "DISCONNECTED"
    RATE_LIMITED = "RATE_LIMITED"
    API_ZERO_REPLAY = "API_ZERO_REPLAY"


class YFWebSocketNumericAvailability(StrEnum):
    """Production availability intentionally cannot be inferred from an adapter."""

    UNAVAILABLE_UNTIL_ACCEPTED_LIVE_PILOT = "UNAVAILABLE_UNTIL_ACCEPTED_LIVE_PILOT"


class YFWebSocketTimeUnit(StrEnum):
    """Explicit contract for PricingData ``time``; never infer by magnitude."""

    SECONDS = "SECONDS"
    MILLISECONDS = "MILLISECONDS"


@dataclass(frozen=True)
class YFWebSocketActivationManifest:
    """Exact identity contract supplied by a future approved activation."""

    identity: ObservationIdentity
    exchange: str
    exchange_timezone: str
    currency: str
    unit: str
    event_time_unit: YFWebSocketTimeUnit

    def __post_init__(self) -> None:
        self.identity.validate()
        if not all(isinstance(value, str) and value and not any(char.isspace() for char in value)
                   for value in (self.exchange, self.currency)):
            raise YFWebSocketObservationError("exchange and currency must be nonempty tokens")
        try:
            ZoneInfo(self.exchange_timezone)
        except ZoneInfoNotFoundError as error:
            raise YFWebSocketObservationError("exchange timezone is invalid") from error
        if not self.unit:
            raise YFWebSocketObservationError("unit is required")
        if not isinstance(self.event_time_unit, YFWebSocketTimeUnit):
            raise YFWebSocketObservationError("PricingData event time unit must be explicit and supported")


@dataclass(frozen=True)
class YFWebSocketInjectedMessage:
    """Already-decoded yfinance PricingData payload and caller arrival sequence."""

    sequence: int
    payload: Mapping[str, object]


@dataclass(frozen=True)
class YFWebSocketIngestResult:
    status: YFWebSocketAdapterStatus
    observation: CurrentObservation | None
    provider_calls: int
    numeric_availability: YFWebSocketNumericAvailability


class YFWebSocketObservationAdapter:
    """Validate and atomically retain injected messages without a transport path."""

    def __init__(self, *, manifest: YFWebSocketActivationManifest,
                 store: CurrentObservationFileStore, throttle: timedelta = timedelta(seconds=1)) -> None:
        if throttle.total_seconds() < 0:
            raise ValueError("throttle must not be negative")
        self._manifest = manifest
        self._store = store
        self._coordinator = CurrentObservationCoordinator(store)
        self._throttle = throttle
        self._last_sequence: int | None = None
        self._last_provider_time: datetime | None = None
        self._last_stored_at: datetime | None = None

    @property
    def route(self) -> CurrentObservationRoute:
        route_id = f"yfinance-ws:{self._manifest.identity.dataset_id}:{self._manifest.identity.market}:{self._manifest.identity.symbol}"
        return CurrentObservationRoute(
            fallback_policy=RoutePolicy(
                route_id=route_id, primary_provider=YFINANCE_PROVIDER,
                primary_route="YFINANCE:WEBSOCKET:PricingData", fallback_provider="NO_FALLBACK",
                fallback_upstream_provider="NO_FALLBACK", fallback_route="NO_FALLBACK", fallback_enabled=False,
                # The shared policy type requires positive declared budgets even
                # when fallback is disabled; no fallback attempt is callable.
                max_primary_requests=1, max_fallback_requests=1,
            ),
            identity=self._manifest.identity, interval_precedence=(ObservationInterval.SNAPSHOT,),
        )

    def replay(self) -> YFWebSocketIngestResult:
        replay = self._coordinator.replay(self.route)
        return YFWebSocketIngestResult(YFWebSocketAdapterStatus.API_ZERO_REPLAY, replay.observation, 0,
                                       YFWebSocketNumericAvailability.UNAVAILABLE_UNTIL_ACCEPTED_LIVE_PILOT)

    def disconnected(self) -> YFWebSocketIngestResult:
        return self._preserved(YFWebSocketAdapterStatus.DISCONNECTED)

    def rate_limited(self) -> YFWebSocketIngestResult:
        return self._preserved(YFWebSocketAdapterStatus.RATE_LIMITED)

    def ingest(self, message: YFWebSocketInjectedMessage | None, *, retrieved_at_utc: str) -> YFWebSocketIngestResult:
        if message is None or not message.payload:
            return self._preserved(YFWebSocketAdapterStatus.EMPTY)
        if type(message.sequence) is not int or message.sequence < 1:
            return self._preserved(YFWebSocketAdapterStatus.MALFORMED)
        if self._last_sequence is not None:
            if message.sequence == self._last_sequence:
                return self._preserved(YFWebSocketAdapterStatus.DUPLICATE)
            if message.sequence < self._last_sequence:
                return self._preserved(YFWebSocketAdapterStatus.OUT_OF_ORDER)
        try:
            observation, provider_time, retrieved = self._decode(message.payload, retrieved_at_utc)
        except YFWebSocketObservationError:
            return self._preserved(YFWebSocketAdapterStatus.MALFORMED)
        if self._last_provider_time is not None:
            if provider_time == self._last_provider_time:
                return self._preserved(YFWebSocketAdapterStatus.DUPLICATE)
            if provider_time < self._last_provider_time:
                return self._preserved(YFWebSocketAdapterStatus.OUT_OF_ORDER)
        if self._last_stored_at is not None and retrieved - self._last_stored_at < self._throttle:
            self._last_sequence, self._last_provider_time = message.sequence, provider_time
            return self._preserved(YFWebSocketAdapterStatus.THROTTLED)
        source = SourceObservation(
            observation,
            # One injected provider event is an accepted observation attempt;
            # ``provider_calls`` on the public result remains zero because this
            # module never owns or performs a transport operation.
            SourceProvenance(YFINANCE_PROVIDER, YFINANCE_UPSTREAM, "YFINANCE:WEBSOCKET:PricingData", retrieved_at_utc, 1, 0),
        )
        self._coordinator.refresh(
            self.route, primary_attempt=lambda: source,
            fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")),
        )
        self._last_sequence, self._last_provider_time, self._last_stored_at = message.sequence, provider_time, retrieved
        return YFWebSocketIngestResult(YFWebSocketAdapterStatus.ACCEPTED, self._store.select(self.route), 0,
                                       YFWebSocketNumericAvailability.UNAVAILABLE_UNTIL_ACCEPTED_LIVE_PILOT)

    def _preserved(self, status: YFWebSocketAdapterStatus) -> YFWebSocketIngestResult:
        return YFWebSocketIngestResult(status, self._store.select(self.route), 0,
                                       YFWebSocketNumericAvailability.UNAVAILABLE_UNTIL_ACCEPTED_LIVE_PILOT)

    def _decode(self, payload: Mapping[str, object], retrieved_at_utc: str) -> tuple[CurrentObservation, datetime, datetime]:
        required = {"id", "price", "time", "currency", "exchange"}
        if not required.issubset(payload) or str(payload["id"]) != self._manifest.identity.symbol:
            raise YFWebSocketObservationError("PricingData identity is not allowlisted")
        if str(payload["exchange"]) != self._manifest.exchange or str(payload["currency"]) != self._manifest.currency:
            raise YFWebSocketObservationError("PricingData exchange or currency differs from manifest")
        try:
            price = float(payload["price"])
            event_value = int(str(payload["time"]))
            divisor = 1 if self._manifest.event_time_unit is YFWebSocketTimeUnit.SECONDS else 1_000
            event_seconds = event_value / divisor
            provider_time = datetime.fromtimestamp(event_seconds, tz=timezone.utc)
            retrieved = datetime.fromisoformat(retrieved_at_utc)
        except (TypeError, ValueError, OverflowError, OSError) as error:
            raise YFWebSocketObservationError("PricingData price or time is invalid") from error
        if not math.isfinite(price) or price <= 0:
            raise YFWebSocketObservationError("PricingData price must be finite and positive")
        if retrieved.tzinfo is None or retrieved.utcoffset() != timezone.utc.utcoffset(retrieved) or provider_time > retrieved:
            raise YFWebSocketObservationError("PricingData timestamp is invalid")
        # Resolve the supplied exchange zone so a future manifest cannot claim an
        # unchecked timezone; PricingData itself carries epoch UTC, not a zone.
        provider_time.astimezone(ZoneInfo(self._manifest.exchange_timezone))
        observation = CurrentObservation(
            route_id=self.route.route_id, identity=self._manifest.identity, interval=ObservationInterval.SNAPSHOT,
            value=price, unit=self._manifest.unit, provider=YFINANCE_PROVIDER,
            upstream_provider=YFINANCE_UPSTREAM, source_route="YFINANCE:WEBSOCKET:PricingData",
            provider_timestamp_utc=provider_time.isoformat(), retrieved_at_utc=retrieved_at_utc,
            finality=ObservationFinality.PROVISIONAL,
        )
        return observation, provider_time, retrieved


__all__ = [
    "YFWebSocketActivationManifest", "YFWebSocketAdapterStatus", "YFWebSocketInjectedMessage",
    "YFWebSocketIngestResult", "YFWebSocketNumericAvailability", "YFWebSocketObservationAdapter", "YFWebSocketTimeUnit",
    "YFWebSocketObservationError",
]
