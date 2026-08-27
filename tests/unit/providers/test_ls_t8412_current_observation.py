from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_data.orchestration.automatic_fallback import (
    DecisionOutcome,
    RoutePolicy,
)
from stock_data.orchestration.current_observation import (
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationIdentity,
    ObservationInterval,
)
from stock_data.providers.ls_t8412 import (
    FINALITY_STATUS,
    PIT_STATUS,
    PROVIDER,
    SOURCE_OPERATION,
)
from stock_data.providers.ls_t8412_current_observation import (
    LST8412CurrentObservationError,
    adapt_retained_t8412_raw,
    retained_t8412_current_attempt,
)
from stock_data.contracts.kospi200_intraday_pilot import (
    RAW_BAR_TIME_POLICY,
    RAW_REVISION_POLICY,
)


MARKET_DATE = date(2026, 8, 12)
IDENTITY = ObservationIdentity("LS_T8412_CURRENT", "KOSPI", "005930")


def _route(*, fallback_enabled: bool = False) -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id="dashboard:ls-t8412:KOSPI:005930",
            primary_provider=PROVIDER,
            primary_route=SOURCE_OPERATION,
            fallback_provider="none",
            fallback_upstream_provider="none",
            fallback_route="none",
            fallback_enabled=fallback_enabled,
        ),
        identity=IDENTITY,
        interval_precedence=(ObservationInterval.MINUTES_15,),
    )


def _raw(*, symbol: str = "005930", close: int = 101, provider_time: str = "093000") -> pd.DataFrame:
    return pd.DataFrame([
        {
            "market_date": MARKET_DATE,
            "membership_observation_date": MARKET_DATE,
            "market": "KOSPI",
            "symbol": symbol,
            "provider_symbol": symbol,
            "provider_time": provider_time,
            "bar_time_policy": RAW_BAR_TIME_POLICY,
            "interval_minutes": 15,
            "source_session_start": "090000",
            "source_session_end": "153000",
            "open": 100,
            "high": 102,
            "low": 99,
            "close": close,
            "volume": 1000,
            "adjustment_code": 0,
            "adjustment_rate": 0.0,
            "provider": PROVIDER,
            "source_operation": SOURCE_OPERATION,
            "captured_at": pd.Timestamp("2026-08-13T00:00:00+00:00"),
            "source_sha256": "a" * 64,
            "revision_policy": RAW_REVISION_POLICY,
            "finality_status": FINALITY_STATUS,
            "pit_status": PIT_STATUS,
        },
    ])


def test_retained_t8412_adapter_preserves_exact_identity_native_interval_and_raw_limits() -> None:
    raw = pd.concat([_raw(close=101, provider_time="091500"), _raw(close=102, provider_time="093000")])

    source = adapt_retained_t8412_raw(raw, route=_route(), market_date=MARKET_DATE)

    observation = source.value
    assert observation.identity == IDENTITY
    assert observation.interval is ObservationInterval.MINUTES_15
    assert observation.value == 102.0
    assert observation.unit == "provider_native_price"
    assert observation.finality.value == "AS_RETRIEVED"
    assert observation.display_only and not observation.pit_safe
    assert observation.provider_timestamp_utc == "2026-08-12T00:30:00+00:00"
    assert source.provenance.request_count == 1


@pytest.mark.parametrize(
    "raw, message",
    [
        (_raw(symbol="000660"), "no retained"),
        (_raw().assign(bar_time_policy="BAR_END"), "bar-time policy"),
        (_raw().assign(interval_minutes=30), "not native 15m"),
    ],
)
def test_retained_t8412_adapter_rejects_non_exact_or_reinterpreted_raw(
    raw: pd.DataFrame, message: str,
) -> None:
    with pytest.raises(LST8412CurrentObservationError, match=message):
        adapt_retained_t8412_raw(raw, route=_route(), market_date=MARKET_DATE)


def test_invalid_retained_raw_is_numeric_free_and_preserves_prior_valid_observation(tmp_path) -> None:
    route = _route()
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    valid = coordinator.refresh(
        route,
        primary_attempt=retained_t8412_current_attempt(_raw(), route=route, market_date=MARKET_DATE),
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback is disabled")),
    )
    assert valid.observation is not None

    failed = coordinator.refresh(
        route,
        primary_attempt=retained_t8412_current_attempt(
            _raw().assign(revision_policy="REINTERPRETED"), route=route, market_date=MARKET_DATE,
        ),
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback is disabled")),
    )

    assert failed.decision is not None
    assert failed.decision.outcome is DecisionOutcome.PRIOR_VALID_PRESERVED
    assert failed.observation == valid.observation
    assert failed.api_calls == 0


def test_retained_t8412_current_replay_is_api_zero(tmp_path) -> None:
    route = _route()
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(tmp_path / "current-observations.json"))
    coordinator.refresh(
        route,
        primary_attempt=retained_t8412_current_attempt(_raw(), route=route, market_date=MARKET_DATE),
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback is disabled")),
    )

    replay = coordinator.replay(route)

    assert replay.api_calls == 0
    assert replay.observation is not None
