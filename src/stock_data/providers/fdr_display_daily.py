"""Injected, daily-only FinanceDataReader current-display refresher.

No FinanceDataReader import or network call occurs here.  A future authorized
caller supplies one counted transport response for one allowlisted identity.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Callable

import pandas as pd

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure, FailureKind, RoutePolicy, SourceObservation, SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation, CurrentObservationCoordinator, CurrentObservationFileStore,
    CurrentObservationOutcome, CurrentObservationRoute, ObservationFinality,
    ObservationIdentity, ObservationInterval,
)

FDR_PROVIDER = "FinanceDataReader"
TIMEOUT_SECONDS = 10
COOLDOWN = timedelta(minutes=30)


class FDRDisplayDailyError(ValueError):
    pass


@dataclass(frozen=True)
class FDRDisplayDailySpec:
    identity: ObservationIdentity
    route: str
    upstream_provider: str
    unit: str
    frame_columns: frozenset[str]
    required_numeric_columns: tuple[str, ...]


@dataclass(frozen=True)
class FDRDisplayDailyResponse:
    status_code: int
    body: bytes
    frame: pd.DataFrame | None
    request_count: int = 1
    retry_count: int = 0
    frame_reader: Callable[[], pd.DataFrame] | None = None


class FDRDisplayDailyOutcome(StrEnum):
    DECIDED = "DECIDED"
    COOLDOWN = "COOLDOWN"
    COALESCED = "COALESCED"
    API_ZERO_REPLAY = "API_ZERO_REPLAY"


@dataclass(frozen=True)
class FDRDisplayDailyRefreshResult:
    outcome: FDRDisplayDailyOutcome
    observation: CurrentObservation | None
    api_calls: int
    primary_safe_code: str | None = None


_NAVER_COLUMNS = frozenset(("Open", "High", "Low", "Close", "Volume", "Change"))
_YAHOO_COLUMNS = frozenset(("Open", "High", "Low", "Close", "Adj Close", "Volume"))


def _spec(dataset: str, market: str, symbol: str, route: str, upstream: str, unit: str) -> FDRDisplayDailySpec:
    columns = _NAVER_COLUMNS if upstream == "NAVER" else _YAHOO_COLUMNS
    numeric = ("Close",) if upstream == "NAVER" else ("Close", "Adj Close")
    return FDRDisplayDailySpec(ObservationIdentity(dataset, market, symbol), route, upstream, unit, columns, numeric)


# Contracted bounded current-display routes. VIX remains FRED-owned; failed
# KOSPI, KOSDAQ, and USD/KRW routes are intentionally absent and cannot be
# retried. NAVER:005930 is the consumed UR-129 exact-date operation whose live
# observation was not accepted; its presence permits API-zero replay/testing,
# not another provider request or a numeric availability claim.
FDR_DAILY_ALLOWLIST = (
    _spec("KR_EQUITY_CURRENT", "XKRX", "000660", "NAVER:000660", "NAVER", "KRW"),
    _spec("KR_EQUITY_CURRENT", "XKRX", "035420", "NAVER:035420", "NAVER", "KRW"),
    _spec("KR_EQUITY_CURRENT", "XKRX", "005930", "NAVER:005930", "NAVER", "KRW"),
    _spec("DASHBOARD_CURRENT", "XUS", "^GSPC", "YAHOO:^GSPC", "YAHOO", "index points"),
    _spec("DASHBOARD_CURRENT", "XUS", "^IXIC", "YAHOO:^IXIC", "YAHOO", "index points"),
    _spec("DASHBOARD_CURRENT", "XUS", "SOXX", "YAHOO:SOXX", "YAHOO", "USD"),
    _spec("DASHBOARD_CURRENT", "XCME", "NQ=F", "YAHOO:NQ=F", "YAHOO", "index points"),
    _spec("DASHBOARD_CURRENT", "XCOM", "GC=F", "YAHOO:GC=F", "YAHOO", "USD"),
    _spec("DASHBOARD_CURRENT", "XNYM", "CL=F", "YAHOO:CL=F", "YAHOO", "USD"),
)
_SPECS = {spec.identity: spec for spec in FDR_DAILY_ALLOWLIST}


class FDRDisplayDailyLandingStore:
    """Append-only successful-body retention; response headers are never stored."""
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def retain(self, spec: FDRDisplayDailySpec, body: bytes) -> Path:
        if not body:
            raise FDRDisplayDailyError("successful FDR response body is empty")
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / spec.route.replace(":", "_").replace("^", "IDX") / digest / "response.bin"
        if path.exists():
            if path.read_bytes() != body:
                raise FDRDisplayDailyError("Landing hash path collision")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path


def _route(spec: FDRDisplayDailySpec) -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=f"fdr-display-daily:{spec.route.replace('^', 'IDX')}", primary_provider=FDR_PROVIDER,
            primary_route=spec.route, fallback_provider="LOCAL_CIRCUIT",
            fallback_upstream_provider="LOCAL_CIRCUIT", fallback_route="LOCAL_CIRCUIT",
            fallback_enabled=True, max_primary_requests=1, max_fallback_requests=1,
        ), identity=spec.identity, interval_precedence=(ObservationInterval.DAILY,),
    )


def _failure(kind: FailureKind, code: str, requests: int) -> AttemptFailure:
    return AttemptFailure(kind, safe_code=code, request_count=requests, retry_count=0)


class FDRDisplayDailyRefresher:
    """One-request daily adapter with local cooldown/coalescing and UR-118 storage."""
    def __init__(self, *, store: CurrentObservationFileStore, landing: FDRDisplayDailyLandingStore, now: Callable[[], datetime]) -> None:
        self._store, self._landing, self._now = store, landing, now
        self._coordinator = CurrentObservationCoordinator(store)
        self._lock = Lock()
        self._active: set[str] = set()
        self._last_attempt: dict[str, datetime] = {}

    @staticmethod
    def spec_for(identity: ObservationIdentity) -> FDRDisplayDailySpec:
        try:
            return _SPECS[identity]
        except KeyError as error:
            raise FDRDisplayDailyError("identity is not in the accepted FDR daily allowlist") from error

    def replay(self, identity: ObservationIdentity) -> FDRDisplayDailyRefreshResult:
        replay = self._coordinator.replay(_route(self.spec_for(identity)))
        return FDRDisplayDailyRefreshResult(FDRDisplayDailyOutcome.API_ZERO_REPLAY, replay.observation, 0)

    def refresh(self, *, identity: ObservationIdentity, start: date, end: date,
                transport: Callable[[str, date, date, int, int], FDRDisplayDailyResponse]) -> FDRDisplayDailyRefreshResult:
        if end < start:
            raise ValueError("end precedes start")
        spec, route = self.spec_for(identity), _route(self.spec_for(identity))
        now = self._now().astimezone(timezone.utc)
        with self._lock:
            if route.route_id in self._active:
                return FDRDisplayDailyRefreshResult(FDRDisplayDailyOutcome.COALESCED, self._store.select(route), 0)
            prior = self._last_attempt.get(route.route_id)
            if prior is not None and now - prior < COOLDOWN:
                return FDRDisplayDailyRefreshResult(FDRDisplayDailyOutcome.COOLDOWN, self._store.select(route), 0)
            self._active.add(route.route_id)
            self._last_attempt[route.route_id] = now
        try:
            def primary() -> SourceObservation[CurrentObservation]:
                try:
                    response = transport(spec.route, start, end, TIMEOUT_SECONDS, 0)
                except TimeoutError as error:
                    raise _failure(FailureKind.TIMEOUT, "FDR_DISPLAY_TIMEOUT", 1) from error
                except Exception as error:
                    raise _failure(FailureKind.HTTP_ERROR, "FDR_DISPLAY_TRANSPORT", 1) from error
                if response.request_count != 1 or response.retry_count != 0:
                    raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_CALL_ACCOUNTING", response.request_count)
                if response.status_code == 429:
                    raise _failure(FailureKind.RATE_LIMITED, "FDR_DISPLAY_HTTP_429", 1)
                if response.status_code != 200:
                    raise _failure(FailureKind.HTTP_ERROR, f"FDR_DISPLAY_HTTP_{response.status_code}", 1)
                self._landing.retain(spec, response.body)  # Landing before frame validation.
                try:
                    frame = response.frame if response.frame is not None else (
                        response.frame_reader() if response.frame_reader is not None else None
                    )
                except Exception as error:
                    raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_PARSE", 1) from error
                if frame is None or frame.empty or set(frame.columns) != spec.frame_columns:
                    raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_SCHEMA", 1)
                dates = pd.to_datetime(frame.index, errors="coerce")
                if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
                    raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_DATE", 1)
                if any(item.date() < start or item.date() > end for item in dates):
                    raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_DATE_RANGE", 1)
                numeric = {column: pd.to_numeric(frame[column], errors="coerce") for column in spec.required_numeric_columns}
                if any(values.isna().any() or not all(math.isfinite(float(value)) and float(value) > 0 for value in values) for values in numeric.values()):
                    raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_PRICE", 1)
                if spec.upstream_provider == "NAVER":
                    ohlc = {column: pd.to_numeric(frame[column], errors="coerce") for column in ("Open", "High", "Low", "Close")}
                    if any(values.isna().any() or not all(math.isfinite(float(value)) and float(value) > 0 for value in values) for values in ohlc.values()):
                        raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_OHLC", 1)
                    volumes = pd.to_numeric(frame["Volume"], errors="coerce")
                    if volumes.isna().any() or not all(math.isfinite(float(value)) and float(value) >= 0 and float(value).is_integer() for value in volumes):
                        raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_DAILY_VOLUME", 1)
                closes = numeric["Close"]
                source_day = dates[-1].date().isoformat()
                retrieved = now.isoformat()
                # The frame index is a daily source-date label.  Midnight UTC
                # represents that date for the foundation key, never source
                # availability or an intraday bar/publication timestamp.
                observation = CurrentObservation(route.route_id, spec.identity, ObservationInterval.DAILY,
                    float(closes.iloc[-1]), spec.unit, FDR_PROVIDER, spec.upstream_provider, spec.route,
                    f"{source_day}T00:00:00+00:00", retrieved, ObservationFinality.AS_RETRIEVED)
                return SourceObservation(observation, SourceProvenance(FDR_PROVIDER, spec.upstream_provider, spec.route, retrieved, 1, 0))

            def local_circuit() -> SourceObservation[CurrentObservation]:
                raise _failure(FailureKind.SCHEMA_ERROR, "FDR_DISPLAY_NO_ALTERNATE_ROUTE", 0)

            result = self._coordinator.refresh(route, primary_attempt=primary, fallback_attempt=local_circuit)
            primary_safe_code = next(
                (event.safe_code for event in result.decision.events if event.event == "PRIMARY_FAILED"), None,
            )
            return FDRDisplayDailyRefreshResult(
                FDRDisplayDailyOutcome.DECIDED, result.observation, result.api_calls, primary_safe_code,
            )
        finally:
            with self._lock:
                self._active.remove(route.route_id)


__all__ = ["COOLDOWN", "FDR_DAILY_ALLOWLIST", "FDRDisplayDailyError", "FDRDisplayDailyLandingStore", "FDRDisplayDailyOutcome", "FDRDisplayDailyRefreshResult", "FDRDisplayDailyResponse", "FDRDisplayDailyRefresher", "TIMEOUT_SECONDS"]
