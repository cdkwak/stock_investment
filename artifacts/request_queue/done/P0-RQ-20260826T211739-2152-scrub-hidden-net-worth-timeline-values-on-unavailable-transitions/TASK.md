# Scrub hidden net-worth timeline values on unavailable transitions

## Problem
Unavailable/corrupt/privacy transitions hide the net-worth timeline without scrubbing prior delta/date/chart numeric state.

## Evidence
Offscreen populated-to-empty transition retained +50,000 KRW in timeline_delta and one numeric QLineSeries in hidden widget state.

## Scope
allow:
- Modify exact NetWorthPage main_window and owning test only; provider/account refresh/API zero.

deny:
- No provider/account refresh, persistence, scheduler, other GUI page, financial mutation, or hidden numeric retention.

## Done When
Every unavailable, empty, corrupt, invalid, stale-suppressed, and privacy transition clears timeline date/delta labels, chart series/axes, tooltips/accessibility strings and all amount-bearing widget state before hiding; valid display remains intact.

## Verify
Add permanent populated-to-empty/corrupt/privacy transitions and scan complete widget state plus chart series/axes; run accepted timeline service and NetWorthPage suites, py_compile, native close/no-side-effect checks, independent review.
