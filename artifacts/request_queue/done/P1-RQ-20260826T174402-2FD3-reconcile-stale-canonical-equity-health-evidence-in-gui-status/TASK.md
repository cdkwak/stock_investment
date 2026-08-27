# Reconcile stale canonical-equity Health evidence in GUI Status

## Problem
GUI Status cites superseded canonical-equity Health hash, date boundary, and managed-status counts.

## Evidence
Exact retained Health and accepted/breadth state now bind all five canonical rows to latest=expected=2026-08-25, while GUI Status retains older facts.

## Scope
allow:
- Read retained evidence and update GUI_STATUS current facts only.

deny:
- No provider/API call, data/state/scheduler/code/test/GUI runtime mutation, or PROJECT_STATUS change.

## Done When
GUI Status replaces only stale current facts with exact recomputed hash/date/count evidence and keeps provider/data/runtime behavior unchanged.

## Verify
Hash/read the cited Health and accepted/breadth states, recompute managed counts, validate all local links, and prove docs-only diff.
