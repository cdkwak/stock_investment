# Add coordinator-safe recovery for an active task whose raw claim capability is lost

## Problem
An Active task whose client-held raw claim capability is irretrievably unavailable cannot checkpoint, submit, or block even after its implementation and evidence are complete; the manager has no audited coordinator recovery transition.

## Evidence
RQ-20260826T054904-4631 remains Active with a valid stored claim-token digest and verified completed work, but the raw capability is unavailable; all Active mutations correctly reject nonmatching tokens, Doctor is OK, README states expiry never causes automatic takeover and the coordinator decides recovery, yet no coordinator command exists.

## Scope
allow:
- Add one audited expired-Active coordinator recovery transition and its CLI arguments.
- Document the exact recovery preconditions, state changes, and non-use for live leases or legacy adoption.
- Add focused coverage to the existing request-queue owner test module.

deny:
- No recovery of an unexpired lease, caller-selected replacement token, automatic takeover, direct queue-file edits, legacy adoption shortcut, or mutation of unrelated tasks.
- No changes outside the three declared files and no weakening of existing claim-token, review-snapshot, scope-reservation, or Doctor invariants.

## Done When
The canonical manager provides an explicit coordinator recovery command that only after the exact expected Active lease has expired atomically validates the task and expected ownership/lease/digest state, records a bounded decision basis and next action, clears the unavailable claim capability and assignment, moves the task to Ready, and regenerates the Board. It must reject a live lease, wrong expected state, malformed task, stale/concurrent recovery, or non-Active state without mutation; the recovered task must accept one ordinary fresh claim with a new unstored raw capability. No automatic takeover or legacy-claim adoption is introduced.

## Verify
Add owning request-queue tests for live-lease byte identity, exact expired recovery, wrong owner/lease/digest fail-closed behavior, malformed state, concurrent one-winner recovery, Board/Doctor integrity, and fresh post-recovery claim; run the complete owning test module and queue Doctor against the production queue.
