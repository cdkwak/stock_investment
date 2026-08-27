result: CommonMark indented H1/H2 recovery bypass closed fail-closed.
changed: Normalize/reject 0-3-space ATX H1/H2 before exact TASK section validation; added regressions.
verified: 72 tests PASS; py_compile PASS; Queue Doctor OK.; independent review by goal_inbox_review: PASS: direct 1-3-space H1/H2 matrix rejected before mutation with 6/6 exact byte identity; 72 tests, py_compile and Doctor pass; pinned recovery contract preserved.
completed_at: 2026-08-26T14:10:30+09:00
