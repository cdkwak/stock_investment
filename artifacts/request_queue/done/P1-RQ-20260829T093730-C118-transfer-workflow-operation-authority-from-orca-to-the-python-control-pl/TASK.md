# Transfer workflow operation authority from Orca to the Python Control Plane

## Problem
Orca PM and terminal delivery remain the effective lifecycle and wakeup authority, so a stale PM can halt reviews, routing, recovery, and Queue progression even though the Python foundation exists.

## Evidence
The Queue had active=0 while three Orca Dispatches were live; six lifecycle messages waited behind an idle PM; terminal-send and computer-use wake paths returned runtime_unavailable; only manual structured-message intervention resumed work.

## Scope
allow:
- Project-local Python control-loop implementation, fake/injected direct agent boundary, Queue lifecycle tests, replay, disabled-Orca canary, documentation, fresh review, and scoped commit.

deny:
- No broker order, trade, transfer, account mutation, secret or credential access, paid service, privilege change, destructive migration, external publication, protected option-wall CSV access, or unreviewed production scheduler activation.

## Done When
An idempotent event-driven Python controller consumes Canonical Queue and workflow events, selects routes, launches or resumes agents through an injected direct runner, settles lifecycle receipts, performs bounded watchdog recovery, preserves role provenance for Lead-validated New candidates, and passes an offline replay plus Orca-disabled canary before promotion; Orca is optional transport only.

## Verify
Run focused controller/discovery/policy/recovery/routing tests, full request-queue tests, Queue Doctor, deterministic replay, duplicate-event and stale-generation regressions, disabled-Orca canary, authority-boundary fixtures, immutable manifest reconciliation, and fresh independent review.
