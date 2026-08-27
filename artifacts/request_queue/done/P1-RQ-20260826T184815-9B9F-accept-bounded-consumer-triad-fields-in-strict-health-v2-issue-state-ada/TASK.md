# Accept bounded consumer-triad fields in strict Health V2 issue-state adapter

## Problem
Strict Health V2 issue-state ingestion rejects the six new bounded consumer triad fields emitted by the parent typed Health projector.

## Evidence
The adapter compares exact row-key sets against _HEALTH_ROW_FIELDS, which lacks display/research/predictive consumer eligibility and reason fields.

## Scope
allow:
- Extend the exact Health V2 row allowlist and typed registry validation; add focused offline tests.

deny:
- No changes to issue aggregation/discovery policy, scheduler/provider behavior, Health schema version, GUI, or external state.

## Done When
The adapter accepts exactly the six typed fields, validates every value and reason against DATASET_UNIVERSE, continues rejecting missing/extra/forged values, and existing issue-state behavior remains unchanged.

## Verify
Run owning issue-state adapter tests plus parent Health reconciliation tests; prove real projected rows pass and forged/missing/cross-dataset triad values fail closed.
