from datetime import datetime, timezone

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    CircuitRecord,
    DecisionOutcome,
    FailureKind,
    InMemoryCircuitStore,
    SourceObservation,
    SourceProvenance,
    ValidationReceipt,
)
from stock_data.orchestration.fred_vix_fallback import (
    FRED_VIXCLS_FALLBACK_POLICY,
    execute_vixcls_fallback,
)


def test_vixcls_fallback_is_exact_and_only_primary_schema_failure_is_eligible():
    policy = FRED_VIXCLS_FALLBACK_POLICY
    assert policy.route_id == "fred_vix_daily:VIXCLS"
    assert policy.fallback_upstream_provider == "FRED"
    assert policy.fallback_route == "FRED:VIXCLS"
    assert policy.max_primary_requests == 1
    assert policy.max_fallback_requests == 2
    assert policy.eligible_primary_failures == frozenset({FailureKind.SCHEMA_ERROR})


class _Promotion:
    def __init__(self, store):
        self.store = store
        self.value = None
        self.decision = None

    def snapshot(self):
        return self.value, self.decision, self.store.load("fred_vix_daily:VIXCLS")

    def stage(self, observation, decision):
        return observation.value, decision

    def commit(self, staged):
        self.value, self.decision = staged
        self.store.save(self.decision.route_id, self.decision.circuit_after)

    def verify_readback(self, observation, decision):
        assert self.value == observation.value
        assert self.decision == decision

    def rollback(self, snapshot):
        self.value, self.decision, circuit = snapshot
        self.store.save("fred_vix_daily:VIXCLS", circuit)


def test_exact_entrypoint_automatically_selects_fdr_only_after_primary_schema_failure():
    store = InMemoryCircuitStore()
    promotion = _Promotion(store)

    def primary():
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code="FRED_PRIMARY_SCHEMA",
            request_count=1,
        )

    def fallback():
        return SourceObservation(
            "validated-vix",
            SourceProvenance(
                provider="financedatareader",
                upstream_provider="FRED",
                source_route="FRED:VIXCLS",
                retrieved_at_utc=datetime(2026, 8, 20, tzinfo=timezone.utc).isoformat(),
                request_count=2,
            ),
        )

    decision = execute_vixcls_fallback(
        circuit_store=store,
        primary_attempt=primary,
        primary_validator=lambda observation: ValidationReceipt(
            "2026-08-12", "fred_vix_daily/v1"
        ),
        fallback_attempt=fallback,
        fallback_validator=lambda observation: ValidationReceipt(
            "2026-08-12", "fred_vix_daily/v1"
        ),
        promotion=promotion,
        prior_valid="prior-vix",
    )
    assert decision.outcome is DecisionOutcome.FALLBACK_ACCEPTED
    assert decision.primary_requests == 1
    assert decision.fallback_requests == 2
    assert promotion.value == "validated-vix"
    assert store.load(decision.route_id) == CircuitRecord()
