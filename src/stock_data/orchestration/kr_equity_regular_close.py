"""Display-only Korean regular-session close with a bounded FDR fallback.

The route intentionally remains separate from canonical daily history.  A
caller injects retry-zero pykrx and FinanceDataReader fetches; this module owns
exact-date validation, fallback eligibility, and atomic current-observation
promotion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    FailureKind,
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRefreshResult,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


SUPPORTED_SYMBOLS = frozenset({"000660", "005930"})
TECHNICAL_FALLBACK_FAILURES = frozenset({
    FailureKind.TIMEOUT,
    FailureKind.HTTP_ERROR,
    FailureKind.RATE_LIMITED,
})


@dataclass(frozen=True)
class RegularCloseQuote:
    symbol: str
    source_date: date
    close: float
    retrieved_at_utc: str


QuoteFetch = Callable[[str, date], RegularCloseQuote]


def regular_close_route(symbol: str) -> CurrentObservationRoute:
    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError("symbol is not in the regular-close allowlist")
    identity = ObservationIdentity("KR_EQUITY_REGULAR_CLOSE", "XKRX", symbol)
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=f"kr-equity-regular-close:{symbol}",
            primary_provider="pykrx",
            primary_route=f"KRX_OHLCV:{symbol}",
            fallback_provider="FinanceDataReader",
            fallback_upstream_provider="NAVER",
            fallback_route=f"NAVER:{symbol}",
            fallback_enabled=True,
            max_primary_requests=1,
            max_fallback_requests=1,
            eligible_primary_failures=TECHNICAL_FALLBACK_FAILURES,
        ),
        identity=identity,
        interval_precedence=(ObservationInterval.DAILY,),
    )


def _attempt(
    *, route: CurrentObservationRoute, expected_date: date, provider: str,
    upstream: str, source_route: str, fetch: QuoteFetch,
) -> SourceObservation[CurrentObservation]:
    try:
        quote = fetch(route.identity.symbol, expected_date)
    except AttemptFailure:
        raise
    except TimeoutError as error:
        raise AttemptFailure(
            FailureKind.TIMEOUT, safe_code=f"{provider.upper()}_TIMEOUT",
            request_count=1,
        ) from error
    except ConnectionError as error:
        raise AttemptFailure(
            FailureKind.HTTP_ERROR, safe_code=f"{provider.upper()}_HTTP_ERROR",
            request_count=1,
        ) from error
    except Exception as error:
        raise AttemptFailure(
            FailureKind.UNEXPECTED_ERROR,
            safe_code=f"{provider.upper()}_UNEXPECTED_ERROR", request_count=1,
        ) from error

    if quote.symbol != route.identity.symbol or quote.source_date != expected_date:
        raise AttemptFailure(
            FailureKind.AMBIGUOUS_SEMANTICS,
            safe_code=f"{provider.upper()}_DATE_OR_IDENTITY_MISMATCH", request_count=1,
        )
    if not math.isfinite(float(quote.close)) or float(quote.close) <= 0:
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code=f"{provider.upper()}_INVALID_CLOSE", request_count=1,
        )
    retrieved = datetime.fromisoformat(quote.retrieved_at_utc)
    if retrieved.tzinfo is None or retrieved.utcoffset() != timezone.utc.utcoffset(retrieved):
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code=f"{provider.upper()}_INVALID_RETRIEVED_AT", request_count=1,
        )
    observation = CurrentObservation(
        route_id=route.route_id,
        identity=route.identity,
        interval=ObservationInterval.DAILY,
        value=float(quote.close),
        unit="KRW",
        provider=provider,
        upstream_provider=upstream,
        source_route=source_route,
        provider_timestamp_utc=f"{expected_date.isoformat()}T00:00:00+00:00",
        retrieved_at_utc=quote.retrieved_at_utc,
        finality=ObservationFinality.AS_RETRIEVED,
    )
    return SourceObservation(
        observation,
        SourceProvenance(
            provider, upstream, source_route, quote.retrieved_at_utc, 1, 0,
        ),
    )


def refresh_regular_close(
    *, store: CurrentObservationFileStore, symbol: str, expected_date: date,
    pykrx_fetch: QuoteFetch, fdr_fetch: QuoteFetch,
) -> CurrentObservationRefreshResult:
    """Try pykrx once, then FDR once only for a typed technical failure."""
    route = regular_close_route(symbol)
    return CurrentObservationCoordinator(store).refresh(
        route,
        primary_attempt=lambda: _attempt(
            route=route, expected_date=expected_date, provider="pykrx",
            upstream="KRX", source_route=f"KRX_OHLCV:{symbol}", fetch=pykrx_fetch,
        ),
        fallback_attempt=lambda: _attempt(
            route=route, expected_date=expected_date,
            provider="FinanceDataReader", upstream="NAVER",
            source_route=f"NAVER:{symbol}", fetch=fdr_fetch,
        ),
    )


__all__ = [
    "RegularCloseQuote", "SUPPORTED_SYMBOLS", "TECHNICAL_FALLBACK_FAILURES",
    "refresh_regular_close", "regular_close_route",
]
