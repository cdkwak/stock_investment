from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    DecisionOutcome,
    FailureKind,
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationOutcome,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
    ObservationTimestampBasis,
)


IDENTITY = ObservationIdentity("GLOBAL_FUTURES", "XCBT", "ZT=F")


def _route(*, fallback_enabled: bool = True, precedence=(ObservationInterval.MINUTES_15, ObservationInterval.MINUTES_30, ObservationInterval.MINUTES_60, ObservationInterval.DAILY, ObservationInterval.SNAPSHOT)) -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id="dashboard:global-futures:ZT=F",
            primary_provider="official-broker",
            primary_route="broker:ZT=F",
            fallback_provider="display-provider",
            fallback_upstream_provider="display-upstream",
            fallback_route="display:ZT=F",
            fallback_enabled=fallback_enabled,
        ),
        identity=IDENTITY,
        interval_precedence=precedence,
    )


def _source(
    value: float,
    *,
    primary: bool = True,
    interval: ObservationInterval = ObservationInterval.MINUTES_60,
    identity: ObservationIdentity = IDENTITY,
    retrieved_at: str = "2026-08-21T12:00:00+00:00",
) -> SourceObservation[CurrentObservation]:
    provider = "official-broker" if primary else "display-provider"
    upstream = "broker-upstream" if primary else "display-upstream"
    source_route = "broker:ZT=F" if primary else "display:ZT=F"
    return SourceObservation(
        CurrentObservation(
            route_id="dashboard:global-futures:ZT=F",
            identity=identity,
            interval=interval,
            value=value,
            unit="USD",
            provider=provider,
            upstream_provider=upstream,
            source_route=source_route,
            provider_timestamp_utc="2026-08-21T11:00:00+00:00",
            retrieved_at_utc=retrieved_at,
            finality=ObservationFinality.AS_RETRIEVED,
        ),
        SourceProvenance(
            provider=provider,
            upstream_provider=upstream,
            source_route=source_route,
            retrieved_at_utc=retrieved_at,
            request_count=1,
        ),
    )


def _failure(kind: FailureKind, code: str):
    def attempt():
        raise AttemptFailure(kind, safe_code=code, request_count=1)
    return attempt


@pytest.mark.parametrize(
    "failure_kind",
    [FailureKind.TIMEOUT, FailureKind.HTTP_ERROR, FailureKind.SCHEMA_ERROR, FailureKind.EMPTY_RESULT],
)
def test_typed_primary_failures_use_one_exact_fallback_and_persist_readable_observation(tmp_path: Path, failure_kind: FailureKind) -> None:
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    fallback_calls = 0

    def fallback():
        nonlocal fallback_calls
        fallback_calls += 1
        return _source(109.25, primary=False)

    result = coordinator.refresh(
        _route(), primary_attempt=_failure(failure_kind, f"PRIMARY_{failure_kind.value}"), fallback_attempt=fallback,
    )

    assert result.decision is not None
    assert result.decision.outcome is DecisionOutcome.FALLBACK_ACCEPTED
    assert result.api_calls == 2 and fallback_calls == 1
    assert store.select(_route()) == result.observation
    assert result.observation is not None and result.observation.display_only and not result.observation.pit_safe


def test_route_scoped_circuit_blocks_second_fallback_and_primary_recovery_closes_it(tmp_path: Path) -> None:
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    route = _route()
    fallback_calls = 0

    def bad_fallback():
        nonlocal fallback_calls
        fallback_calls += 1
        raise AttemptFailure(FailureKind.EMPTY_RESULT, safe_code="FALLBACK_EMPTY", request_count=1)

    first = coordinator.refresh(route, primary_attempt=_failure(FailureKind.HTTP_ERROR, "PRIMARY_HTTP"), fallback_attempt=bad_fallback)
    assert first.decision is not None and first.decision.outcome is DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
    assert store.load(route.route_id).is_open and fallback_calls == 1

    second = coordinator.refresh(route, primary_attempt=_failure(FailureKind.TIMEOUT, "PRIMARY_TIMEOUT"), fallback_attempt=bad_fallback)
    assert second.decision is not None and second.decision.fallback_attempts == 0
    assert fallback_calls == 1

    recovered = coordinator.refresh(route, primary_attempt=lambda: _source(109.5), fallback_attempt=bad_fallback)
    assert recovered.decision is not None and recovered.decision.outcome is DecisionOutcome.PRIMARY_RECOVERED
    assert not store.load(route.route_id).is_open


def test_atomic_promotion_failure_rolls_back_and_preserves_prior_valid_observation(tmp_path: Path) -> None:
    class _FailOnceStore(CurrentObservationFileStore):
        fail_next_write = False

        def _write_state(self, state):
            if self.fail_next_write:
                self.fail_next_write = False
                raise OSError("synthetic atomic write failure")
            super()._write_state(state)

    store = _FailOnceStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    route = _route()
    initial = coordinator.refresh(route, primary_attempt=lambda: _source(109.0), fallback_attempt=lambda: _source(0.0, primary=False))
    assert initial.observation is not None

    store.fail_next_write = True
    failed = coordinator.refresh(route, primary_attempt=lambda: _source(110.0, retrieved_at="2026-08-21T12:30:00+00:00"), fallback_attempt=lambda: _source(0.0, primary=False))

    assert failed.decision is not None and failed.decision.outcome is DecisionOutcome.PRIOR_VALID_PRESERVED
    assert store.select(route) == initial.observation
    # A local primary-promotion error does not itself justify a provider circuit;
    # the existing fallback controller preserves the prior circuit state.
    assert not store.load(route.route_id).is_open


def test_exact_identity_mismatch_is_numeric_free_when_no_fallback_is_authorized(tmp_path: Path) -> None:
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    route = _route(fallback_enabled=False)
    wrong_identity = ObservationIdentity("GLOBAL_FUTURES", "XCBT", "ZN=F")

    result = coordinator.refresh(
        route, primary_attempt=lambda: _source(109.0, identity=wrong_identity),
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    assert result.observation is None
    assert result.decision is not None and result.decision.outcome is DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
    assert result.decision.events[0].failure_kind is FailureKind.SCHEMA_ERROR


def test_interval_precedence_retains_multiple_native_intervals_and_api_zero_replay(tmp_path: Path) -> None:
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    route = _route()
    coordinator.refresh(route, primary_attempt=lambda: _source(109.0, interval=ObservationInterval.MINUTES_60), fallback_attempt=lambda: _source(0.0, primary=False))
    current = coordinator.refresh(
        route,
        primary_attempt=lambda: _source(108.5, interval=ObservationInterval.MINUTES_15, retrieved_at="2026-08-21T12:15:00+00:00"),
        fallback_attempt=lambda: _source(0.0, primary=False),
    )

    assert len(store.observations(route)) == 2
    assert current.observation is not None and current.observation.interval is ObservationInterval.MINUTES_15
    replay = coordinator.replay(route)
    assert replay.outcome is CurrentObservationOutcome.API_ZERO_REPLAY
    assert replay.api_calls == 0 and replay.observation == current.observation


def test_same_route_work_coalesces_without_a_second_provider_attempt(tmp_path: Path) -> None:
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    coordinator = CurrentObservationCoordinator(store)
    entered, release = Event(), Event()

    def primary():
        entered.set()
        assert release.wait(timeout=2)
        return _source(109.0)

    thread = Thread(target=lambda: coordinator.refresh(_route(), primary_attempt=primary, fallback_attempt=lambda: _source(0.0, primary=False)))
    thread.start()
    assert entered.wait(timeout=2)
    coalesced = coordinator.refresh(_route(), primary_attempt=lambda: (_ for _ in ()).throw(AssertionError("must not run")), fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert coalesced.outcome is CurrentObservationOutcome.COALESCED and coalesced.api_calls == 0


def test_retrieval_timestamp_basis_persists_without_claiming_provider_event_time(tmp_path: Path) -> None:
    store = CurrentObservationFileStore(tmp_path / "current-observations.json")
    route = _route(fallback_enabled=False)
    retrieved = "2026-08-21T12:00:00+00:00"
    base = _source(109.0, retrieved_at=retrieved).value
    observation = CurrentObservation(
        route_id=base.route_id,
        identity=base.identity,
        interval=base.interval,
        value=base.value,
        unit=base.unit,
        provider=base.provider,
        upstream_provider=base.upstream_provider,
        source_route=base.source_route,
        provider_timestamp_utc=retrieved,
        retrieved_at_utc=retrieved,
        finality=base.finality,
        timestamp_basis=ObservationTimestampBasis.RETRIEVAL_TIMESTAMP,
    )
    source = SourceObservation(
        observation,
        SourceProvenance(
            observation.provider, observation.upstream_provider,
            observation.source_route, retrieved, 1,
        ),
    )

    result = CurrentObservationCoordinator(store).refresh(
        route,
        primary_attempt=lambda: source,
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    reloaded = CurrentObservationFileStore(store.path).select(route)

    assert result.observation == reloaded
    assert reloaded is not None
    assert reloaded.timestamp_basis is ObservationTimestampBasis.RETRIEVAL_TIMESTAMP
    assert reloaded.provider_timestamp_utc == reloaded.retrieved_at_utc


def test_retrieval_timestamp_basis_rejects_a_distinct_provider_timestamp() -> None:
    observation = _source(109.0).value
    invalid = CurrentObservation(
        route_id=observation.route_id,
        identity=observation.identity,
        interval=observation.interval,
        value=observation.value,
        unit=observation.unit,
        provider=observation.provider,
        upstream_provider=observation.upstream_provider,
        source_route=observation.source_route,
        provider_timestamp_utc=observation.provider_timestamp_utc,
        retrieved_at_utc=observation.retrieved_at_utc,
        finality=observation.finality,
        timestamp_basis=ObservationTimestampBasis.RETRIEVAL_TIMESTAMP,
    )

    with pytest.raises(ValueError, match="retrieval instant"):
        invalid.validate()
