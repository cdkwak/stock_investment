# Add end-to-end continuous freshness release gate for scheduler and GUI

## Problem
The final release boundary does not yet assert the whole operational promise: correct installed triggers, outcome-complete scheduler receipts, managed dataset freshness, reconciled Health, and bounded GUI visibility. A fail-closed, read-only acceptance must connect all five layers.

## Evidence
The retained system currently has 19 automation-enabled datasets with 14 acceptable and five stale canonical rows; the KR tasks have no real execution history; the retained KR last log is green with no outcomes; cold GUI evidence showed Health at zero rows after 30 seconds while local enumeration blocked startup.

## Scope
allow:
- Read scheduler definitions/state, immutable receipts, retained Health and local GUI artifacts; extend the existing read-only smoke and owning tests; write only its report artifact.

deny:
- No provider calls, no schedule mutation, no data repair or canonical promotion, no accepting process exit without lane evidence, no hiding stale managed rows, and no relaxing GUI timeout by removing content.

## Done When
One supported read-only smoke produces a machine-readable report and fails unless: every required installed task definition matches policy; each due occurrence resolves to outcome-complete receipts; all automation-enabled rows are CURRENT or explicitly accepted EXPECTED_LAG; Health generation/readback is later than the governing successful receipts and internally consistent; and cold GUI Health renders non-empty rows plus managed SLO counts within an agreed bounded timeout. Full 80-row inventory may include MANUAL_ONLY, BLOCKED, NOT_APPLICABLE, STALE, or UNKNOWN only when outside automation_enabled, with reasons preserved.

## Verify
Run unit and integration tests with synthetic scheduler/task/Health fixtures, then run the supported read-only release readiness smoke and a provider-free cold GUI acceptance. Archive only the resulting report under artifacts/release_readiness; report exact failing gates.
