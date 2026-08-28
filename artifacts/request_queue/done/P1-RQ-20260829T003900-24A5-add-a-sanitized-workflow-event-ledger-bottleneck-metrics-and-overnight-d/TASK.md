# Add a sanitized workflow event ledger, bottleneck metrics, and overnight digest

## Problem
No executable Python control-plane state contract records Queue and orchestration facts as sanitized machine truth or projects a reproducible overnight digest.

## Evidence
PROJECT_GOAL and PIPELINE require measured workflow events and a morning digest; D1E1 records executable state and digesting as future work; Steward dedup found no owning implementation.

## Scope
allow:
- Only the listed workflow-control core files and offline tests; standard-library dependencies preferred.

deny:
- No live Queue lifecycle movement, Orca authority replacement, provider or scheduler action, production data or account mutation, secrets, transcripts, direct identifiers, or protected option-wall CSV access.

## Done When
A versioned offline Python core uses SQLite for machine state, append-only sanitized JSONL events, deterministic Markdown projections, and a read-only adapter around the existing request_queue.py contract; atomic transactions, schema migration, idempotent replay, privacy exclusions, and deterministic digest metrics are tested.

## Verify
Run tests/unit/orchestration/test_workflow_control_state.py, the owning request-queue suite, Queue Doctor, and deterministic replay against sanitized fixtures.
