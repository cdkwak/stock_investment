# Reconcile stale kr_index_daily coverage in Source Registry

## Problem
Source Registry reports kr_index_daily only through 2026-08-19 although exact state and both retained partitions reach 2026-08-25.

## Evidence
State retained_latest=2026-08-25 and both KOSPI/KOSDAQ 2026 partitions have 158 unique rows through that date; Dataset Index is already truthful.

## Scope
allow:
- Read retained normalized/state evidence and update SOURCE_REGISTRY.md only.

deny:
- No provider/API call, data/state/schema/scheduler/GUI/backtest mutation, or unrelated registry change.

## Done When
Only the stale kr_index_daily coverage fact is replaced with exact date/count evidence consistent with retained state and Dataset Index.

## Verify
Contract-read both partitions, verify unique date/market keys and maxima/counts, validate state and local links, and prove docs-only diff with API zero.
