# Reconcile stale KR index daily current state in Data Status

## Problem
Current Data Status still couples kr_index_daily to the older 2026-08-19 KOSPI200 advancement even though the index lane has independently advanced through 2026-08-25.

## Evidence
kr_index_daily lane state is SUCCEEDED/finalized_market_date 2026-08-25; KOSPI/KOSDAQ 2026 partitions each have 158 unique dates through 2026-08-25; KOSPI200 Source Registry coverage remains 2026-08-19.

## Scope
allow:
- docs/data/DATA_STATUS.md KR index daily paragraph only.

deny:
- No provider/API/data/state/scheduler/code/other status/PIT/finality changes.

## Done When
The KR index daily paragraph states the current independent kr_index_daily coverage through 2026-08-25 and preserves the separate KOSPI200 2026-08-19 fact, API-zero replay, contract limits, and scheduler route.

## Verify
Compare exact lane state and both index partitions with Data Status, Dataset Index, and Source Registry; ensure only the KR index paragraph changes; run queue Doctor.
