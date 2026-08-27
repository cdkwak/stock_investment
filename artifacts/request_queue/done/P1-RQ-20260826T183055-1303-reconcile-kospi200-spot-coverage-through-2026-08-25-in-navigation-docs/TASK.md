# Reconcile KOSPI200 spot coverage through 2026-08-25 in navigation docs

## Problem
Dataset Index and Source Registry understate the atomically promoted kr_kospi200_index_daily coverage as 2026-08-19/9,449 rows while exact state and normalized data reach 2026-08-25/9,453 rows.

## Evidence
Exact lane is SUCCEEDED/finalized 2026-08-25/api_calls3; nested KOSPI200 retained_latest 2026-08-25/total_rows9453; all 37 normalized partitions read 9,453 rows, max 2026-08-25, duplicate date 0.

## Scope
allow:
- docs/data/DATASET_INDEX.md and docs/data/SOURCE_REGISTRY.md KOSPI200 spot rows only.

deny:
- No provider/API/data/state/scheduler/code/other row/PIT/finality/Option Wall changes.

## Done When
Only the KOSPI200 spot rows in Dataset Index and Source Registry state 1990-01-03..2026-08-25 and 9,453 rows, while preserving ticker/source, PIT_SAFE_EOD_T_PLUS_1, Option Wall join limit, anomaly, and fallback semantics.

## Verify
Read every KOSPI200 normalized partition and exact nested lane state, assert row count/max/duplicates; compare both changed rows and Data Status; run queue Doctor.
