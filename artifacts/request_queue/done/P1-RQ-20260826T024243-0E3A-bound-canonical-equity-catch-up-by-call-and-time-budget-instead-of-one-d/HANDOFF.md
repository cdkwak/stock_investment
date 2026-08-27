updated_at: 2026-08-26T13:35:30+09:00
phase: completed
summary: Canonical catch-up now reports pre-first-date budget exhaustion and accepted-but-breadth-pending state from retained facts.
completed: Time budget is checked before every date; false current NOOP removed; exception path rereads accepted and strict breadth states; consecutive per-occurrence limits documented and stale one-date status corrected.
next: none
files_touched: src/stock_data/orchestration/canonical_equity_daily.py,tests/unit/orchestration/test_canonical_equity_daily.py,docs/data/operations/CANONICAL_EQUITY_DAILY.md,docs/data/DATA_STATUS.md
tests: 13 canonical tests plus 63 provider scheduler/CLI tests pass; no live calls.
risks: No scheduler definition, provider route, retry, schema, PIT or atomic per-date boundary changed.
new_discoveries: none
