from __future__ import annotations

from stock_data.orchestration.automatic_fallback import (
    AutomaticFallbackController,
    ExecutionKind,
    FailureKind,
    RoutePolicy,
)


FRED_VIXCLS_FALLBACK_POLICY = RoutePolicy(
    route_id="fred_vix_daily:VIXCLS",
    primary_provider="fred",
    primary_route="fredgraph_csv:VIXCLS",
    fallback_provider="financedatareader",
    fallback_upstream_provider="FRED",
    fallback_route="FRED:VIXCLS",
    fallback_enabled=True,
    max_primary_requests=1,
    max_fallback_requests=2,
    eligible_primary_failures=frozenset({FailureKind.SCHEMA_ERROR}),
)


def controller(circuit_store) -> AutomaticFallbackController:
    """Return the exact VIXCLS controller; caller owns scheduling and promotion."""
    return AutomaticFallbackController(circuit_store)


def execute_vixcls_fallback(
    *, circuit_store, primary_attempt, primary_validator, fallback_attempt,
    fallback_validator, promotion, prior_valid,
    execution_kind: ExecutionKind = ExecutionKind.NORMAL_SCHEDULE,
):
    """Execute the accepted exact route without changing scheduler cadence.

    The caller remains the owner of its primary adapter and of the atomic
    data+decision+circuit promotion transaction.  This boundary deliberately
    exposes no generic FinanceDataReader dispatch.
    """
    return controller(circuit_store).execute(
        policy=FRED_VIXCLS_FALLBACK_POLICY,
        execution_kind=execution_kind,
        primary_attempt=primary_attempt,
        primary_validator=primary_validator,
        fallback_attempt=fallback_attempt,
        fallback_validator=fallback_validator,
        promotion=promotion,
        prior_valid=prior_valid,
    )
