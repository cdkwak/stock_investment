# Define a sanitized local issue-state aggregator and Inbox discovery gate

## Problem
Sanitized runtime diagnostics, Data update events, Health rows, and scheduler receipts remain fragmented; no shared issue state aggregates repeated stable causes, preserves recovery, applies explicit escalation policy, and deduplicates before inbox/new-only discovery.

## Evidence
PROJECT_GOAL issue criterion requires stable code/fingerprint, target, first/latest occurrence, count, last success, severity, retryability, sanitized evidence, recovery, shared consumption, thresholded escalation, and all-state Queue deduplication. BB29 and 626E provide safe source events; Health/receipt tasks and request_queue fingerprinting cover separate pieces, not a cross-source issue contract.

## Scope
allow:
- Create the Project-owned documentation contract and update PROJECT_STATUS routing/current facts only; reference existing sanitized local evidence and canonical queue discovery semantics.

deny:
- No production code/tests/store, raw exception/traceback/payload/secret/account data, provider call, automatic retry/recollection, Data or scheduler mutation, queue triage/Ready/Active/Done transition, or broader application/operations phase.

## Done When
A documentation-only issue-state/v1 and escalation-policy/v1 contract defines allowlisted adapters, stable sanitized fingerprint identity, bounded occurrence aggregation, first/latest/count/last-success, severity/retryability/freshness context, recovery epochs, relative evidence links, explicit no-default thresholds, all-state Queue deduplication, and request_queue.py discover as the only allowed inbox/new bridge; PROJECT_STATUS links the future boundary without authorizing implementation.

## Verify
Map every Project Goal issue requirement to a field/invariant; prove separation from BB29 runtime events, 626E update events, Health, receipts, and queue state; verify no raw/private material or automatic triage/execution path is allowed; verify links and queue doctor.
