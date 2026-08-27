updated_at: 2026-08-26T18:57:18+09:00
phase: completed
summary: Strict Health V2 issue-state adapter now accepts exactly the six consumer-triad fields and validates each against the typed universe.
completed: Exact allowlist/equality gate plus missing, forged, and mismatched field regressions; real 80-row projector-to-adapter replay passes.
next: none
files_touched: src/issue_state/adapters.py; tests/unit/issue_state/test_adapters.py
tests: 102 passed across issue-state adapter, Health reconciliation, release readiness; real projector-adapter replay 80 rows/240 events PASS
risks: No schema bump; exact keys and typed values remain fail-closed.
new_discoveries: none
