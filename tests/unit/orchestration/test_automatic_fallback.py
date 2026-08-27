from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    AutomaticFallbackController,
    CircuitRecord,
    DecisionOutcome,
    ExecutionKind,
    FailureKind,
    FallbackInvariantError,
    InMemoryCircuitStore,
    ProviderRole,
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
    ValidationReceipt,
)


def _policy(route_id: str = "kr_equity_price_daily:005930") -> RoutePolicy:
    return RoutePolicy(
        route_id=route_id,
        primary_provider="official",
        primary_route="official/exact-daily",
        fallback_provider="financedatareader",
        fallback_upstream_provider="bounded-upstream",
        fallback_route="FDR:BOUNDED",
        fallback_enabled=True,
    )


def _observation(value: str, *, primary: bool) -> SourceObservation[str]:
    return SourceObservation(
        value=value,
        provenance=SourceProvenance(
            provider="official" if primary else "financedatareader",
            upstream_provider="official-upstream" if primary else "bounded-upstream",
            source_route="official/exact-daily" if primary else "FDR:BOUNDED",
            retrieved_at_utc="2026-08-20T12:00:00+00:00",
            request_count=1,
            retry_count=0,
        ),
    )


def _valid(observation: SourceObservation[str]) -> ValidationReceipt:
    assert observation.value
    return ValidationReceipt("2026-08-19", "synthetic-v1")


class _AtomicBoundary:
    def __init__(
        self, current: str | None, *, store=None, fail_commit: bool = False,
        fail_verify: bool = False,
    ):
        self.current = current
        self.decisions = []
        self.store = store
        self.fail_commit = fail_commit
        self.fail_verify = fail_verify
        self.rollback_count = 0

    def snapshot(self):
        circuit = None
        if self.store is not None:
            circuit = self.store.load(_policy().route_id)
        return self.current, tuple(self.decisions), circuit

    def stage(self, observation, decision):
        return observation.value, decision

    def commit(self, staged):
        self.current, decision = staged
        self.decisions.append(decision)
        if self.store is not None and decision.circuit_after != decision.circuit_before:
            self.store.save(decision.route_id, decision.circuit_after)
        if self.fail_commit:
            raise OSError("synthetic mid-commit failure")

    def verify_readback(self, observation, decision):
        if self.fail_verify or self.current != observation.value or self.decisions[-1] != decision:
            raise RuntimeError("synthetic readback failure")

    def rollback(self, snapshot):
        self.current, decisions, circuit = snapshot
        self.decisions = list(decisions)
        if self.store is not None and circuit is not None:
            self.store.save(_policy().route_id, circuit)
        self.rollback_count += 1


def _failure(kind: FailureKind, code: str):
    def fail():
        raise AttemptFailure(kind, safe_code=code, request_count=1, retry_count=0)

    return fail


def test_healthy_primary_has_priority_and_never_calls_fallback():
    fallback_calls = 0

    def fallback():
        nonlocal fallback_calls
        fallback_calls += 1
        return _observation("fallback", primary=False)

    boundary = _AtomicBoundary("prior")
    decision = AutomaticFallbackController[str](InMemoryCircuitStore()).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=lambda: _observation("primary", primary=True),
        primary_validator=_valid,
        fallback_attempt=fallback,
        fallback_validator=_valid,
        promotion=boundary,
        prior_valid="prior",
    )

    assert decision.outcome == DecisionOutcome.PRIMARY_ACCEPTED
    assert decision.selected_role == ProviderRole.PRIMARY
    assert decision.primary_attempts == 1 and decision.fallback_attempts == 0
    assert decision.primary_requests == 1 and decision.fallback_requests == 0
    assert fallback_calls == 0
    assert boundary.current == "primary"
    assert decision.events[0].upstream_provider == "official-upstream"


@pytest.mark.parametrize(
    "primary_kind",
    [
        FailureKind.TIMEOUT,
        FailureKind.HTTP_ERROR,
        FailureKind.SCHEMA_ERROR,
        FailureKind.EMPTY_RESULT,
    ],
)
def test_each_eligible_typed_primary_failure_allows_exactly_one_scoped_fallback(primary_kind):
    fallback_calls = 0

    def fallback():
        nonlocal fallback_calls
        fallback_calls += 1
        return _observation("fallback", primary=False)

    boundary = _AtomicBoundary("prior")
    decision = AutomaticFallbackController[str](InMemoryCircuitStore()).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=_failure(primary_kind, f"PRIMARY_{primary_kind.value}"),
        primary_validator=_valid,
        fallback_attempt=fallback,
        fallback_validator=_valid,
        promotion=boundary,
        prior_valid="prior",
    )

    assert decision.outcome == DecisionOutcome.FALLBACK_ACCEPTED
    assert decision.selected_role == ProviderRole.FALLBACK
    assert decision.primary_attempts == decision.fallback_attempts == 1
    assert decision.primary_requests == decision.fallback_requests == 1
    assert fallback_calls == 1
    assert boundary.current == "fallback"
    assert decision.events[1].upstream_provider == "bounded-upstream"


@pytest.mark.parametrize("kind", [FailureKind.SCHEMA_ERROR, FailureKind.EMPTY_RESULT])
def test_malformed_or_empty_fallback_opens_scope_and_preserves_prior(kind):
    store = InMemoryCircuitStore()
    boundary = _AtomicBoundary("prior", store=store)
    decision = AutomaticFallbackController[str](store).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=_failure(FailureKind.HTTP_ERROR, "PRIMARY_HTTP"),
        primary_validator=_valid,
        fallback_attempt=_failure(kind, f"FALLBACK_{kind.value}"),
        fallback_validator=_valid,
        promotion=boundary,
        prior_valid="prior",
    )

    assert decision.outcome == DecisionOutcome.PRIOR_VALID_PRESERVED
    assert decision.active_value == "prior" and decision.preserved_prior
    assert boundary.current == "prior"
    assert decision.circuit_after.is_open
    assert store.load(_policy().route_id).failure_kind == kind


def test_open_circuit_prevents_fallback_cascade_but_normal_primary_recovery_closes_it():
    store = InMemoryCircuitStore()
    route = _policy().route_id
    store.save(route, CircuitRecord(True, FailureKind.RATE_LIMITED, "RATE_LIMIT", 7))
    fallback_calls = 0

    def fallback():
        nonlocal fallback_calls
        fallback_calls += 1
        return _observation("fallback", primary=False)

    controller = AutomaticFallbackController[str](store)
    first = controller.execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=_failure(FailureKind.TIMEOUT, "PRIMARY_TIMEOUT"),
        primary_validator=_valid,
        fallback_attempt=fallback,
        fallback_validator=_valid,
        promotion=_AtomicBoundary("prior"),
        prior_valid="prior",
    )
    assert first.outcome == DecisionOutcome.PRIOR_VALID_PRESERVED
    assert first.fallback_attempts == 0 and fallback_calls == 0
    assert store.load(route).is_open

    boundary = _AtomicBoundary("prior", store=store)
    recovered = controller.execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=lambda: _observation("recovered-primary", primary=True),
        primary_validator=_valid,
        fallback_attempt=fallback,
        fallback_validator=_valid,
        promotion=boundary,
        prior_valid="prior",
    )
    assert recovered.outcome == DecisionOutcome.PRIMARY_RECOVERED
    assert fallback_calls == 0 and boundary.current == "recovered-primary"
    assert not store.load(route).is_open
    assert store.load(route).generation == 8


def test_circuit_is_route_scoped():
    store = InMemoryCircuitStore()
    store.save("route-a", CircuitRecord(True, FailureKind.HTTP_ERROR, "A_HTTP", 1))
    decision = AutomaticFallbackController[str](store).execute(
        policy=_policy("route-b"),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=_failure(FailureKind.HTTP_ERROR, "B_HTTP"),
        primary_validator=_valid,
        fallback_attempt=lambda: _observation("route-b-fallback", primary=False),
        fallback_validator=_valid,
        promotion=_AtomicBoundary(None),
        prior_valid=None,
    )
    assert decision.outcome == DecisionOutcome.FALLBACK_ACCEPTED
    assert store.load("route-a").is_open
    assert not store.load("route-b").is_open


def test_atomic_fallback_promotion_rolls_back_and_opens_only_that_circuit():
    store = InMemoryCircuitStore()
    boundary = _AtomicBoundary("prior", fail_commit=True)
    decision = AutomaticFallbackController[str](store).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=_failure(FailureKind.EMPTY_RESULT, "PRIMARY_EMPTY"),
        primary_validator=_valid,
        fallback_attempt=lambda: _observation("candidate", primary=False),
        fallback_validator=_valid,
        promotion=boundary,
        prior_valid="prior",
    )
    assert decision.outcome == DecisionOutcome.PRIOR_VALID_PRESERVED
    assert boundary.current == "prior" and boundary.decisions == []
    assert boundary.rollback_count == 1
    assert decision.circuit_after.failure_kind == FailureKind.PROMOTION_ERROR


def test_retry_nonzero_and_multiple_internal_requests_fail_the_controller_boundary():
    with pytest.raises(FallbackInvariantError, match="retry_count=0"):
        AttemptFailure(FailureKind.TIMEOUT, safe_code="BAD_RETRY", retry_count=1)

    multiple = SourceObservation(
        "multiple",
        SourceProvenance(
            provider="official",
            upstream_provider="official-upstream",
            source_route="official/exact-daily",
            retrieved_at_utc="2026-08-20T12:00:00+00:00",
            request_count=2,
        ),
    )
    with pytest.raises(FallbackInvariantError, match="exceeded policy request budget"):
        AutomaticFallbackController[str](InMemoryCircuitStore()).execute(
            policy=_policy(),
            execution_kind=ExecutionKind.NORMAL_SCHEDULE,
            primary_attempt=lambda: multiple,
            primary_validator=_valid,
            fallback_attempt=lambda: _observation("fallback", primary=False),
            fallback_validator=_valid,
            promotion=_AtomicBoundary(None),
            prior_valid=None,
        )


def test_policy_specific_internal_request_budget_is_adapter_reported_not_inferred():
    policy = RoutePolicy(
        route_id="fred_vix_daily:VIXCLS",
        primary_provider="official",
        primary_route="official/exact-daily",
        fallback_provider="financedatareader",
        fallback_upstream_provider="FRED",
        fallback_route="FRED:VIXCLS",
        fallback_enabled=True,
        max_fallback_requests=2,
    )
    two_request_fallback = SourceObservation(
        "fallback",
        SourceProvenance(
            provider="financedatareader",
            upstream_provider="FRED",
            source_route="FRED:VIXCLS",
            retrieved_at_utc="2026-08-20T12:00:00+00:00",
            request_count=2,
        ),
    )
    decision = AutomaticFallbackController[str](InMemoryCircuitStore()).execute(
        policy=policy,
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=_failure(FailureKind.SCHEMA_ERROR, "PRIMARY_SCHEMA"),
        primary_validator=_valid,
        fallback_attempt=lambda: two_request_fallback,
        fallback_validator=_valid,
        promotion=_AtomicBoundary(None),
        prior_valid=None,
    )
    assert decision.fallback_requests == 2
    assert decision.outcome == DecisionOutcome.FALLBACK_ACCEPTED


def test_unexpected_provider_exception_reports_unknown_zero_requests():
    def unexpected():
        raise RuntimeError("transport adapter did not type this failure")

    decision = AutomaticFallbackController[str](InMemoryCircuitStore()).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=unexpected,
        primary_validator=_valid,
        fallback_attempt=lambda: _observation("must-not-run", primary=False),
        fallback_validator=_valid,
        promotion=_AtomicBoundary("prior"),
        prior_valid="prior",
    )
    assert decision.primary_requests == 0
    assert decision.events[0].failure_kind == FailureKind.UNEXPECTED_ERROR
    assert decision.fallback_attempts == 0


class _FailingCircuitStore(InMemoryCircuitStore):
    def __init__(self, initial):
        super().__init__()
        self._records[_policy().route_id] = initial

    def save(self, route_id, record):
        if not record.is_open:
            raise OSError("synthetic circuit persistence failure")
        super().save(route_id, record)


def test_primary_recovery_cannot_promote_when_atomic_circuit_close_fails():
    open_record = CircuitRecord(True, FailureKind.HTTP_ERROR, "OPEN", 2)
    store = _FailingCircuitStore(open_record)
    boundary = _AtomicBoundary("prior", store=store)
    decision = AutomaticFallbackController[str](store).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
        primary_attempt=lambda: _observation("recovered", primary=True),
        primary_validator=_valid,
        fallback_attempt=lambda: _observation("must-not-run", primary=False),
        fallback_validator=_valid,
        promotion=boundary,
        prior_valid="prior",
    )
    assert decision.outcome == DecisionOutcome.PRIOR_VALID_PRESERVED
    assert boundary.current == "prior" and boundary.rollback_count == 1
    assert store.load(_policy().route_id) == open_record


def test_decision_and_provenance_are_immutable_and_secret_free_by_construction():
    decision = AutomaticFallbackController[str](InMemoryCircuitStore()).execute(
        policy=_policy(),
        execution_kind=ExecutionKind.AUTHORIZED_HEALTH_CHECK,
        primary_attempt=lambda: _observation("primary", primary=True),
        primary_validator=_valid,
        fallback_attempt=lambda: _observation("fallback", primary=False),
        fallback_validator=_valid,
        promotion=_AtomicBoundary(None),
        prior_valid=None,
    )
    assert isinstance(decision.events, tuple)
    assert not hasattr(decision.events[0], "headers")
    assert not hasattr(decision.events[0], "response")
    with pytest.raises(FrozenInstanceError):
        decision.events[0].safe_code = "MUTATED"
