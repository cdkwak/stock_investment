# Reconcile stale KR index-daily coverage in Source Registry

## Problem
Source Registry understates retained kr_index_daily coverage as 2026-08-19 while direct accepted state and both 2026 partitions reach 2026-08-25.

## Evidence
Independent 5CE0 review verified KOSPI and KOSDAQ partitions each have 158 rows, max 2026-08-25, duplicate dates 0, and retained state latest 2026-08-25; only SOURCE_REGISTRY line 48 is stale.

## Scope
allow:
- docs/data/SOURCE_REGISTRY.md kr_index_daily row only.

deny:
- No provider/API/data/state/scheduler/code/PIT/finality or unrelated documentation changes.

## Done When
Only the kr_index_daily Source Registry row reflects exact retained 2026-08-25 coverage and 158 rows per KOSPI/KOSDAQ partition without changing PIT/finality/provider claims.

## Verify
Read exact state and both normalized 2026 partitions, assert count/max/duplicate keys, compare Dataset Index and Source Registry row, run link check and queue Doctor.
