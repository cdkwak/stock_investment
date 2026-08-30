updated_at: 2026-08-30T23:26:41+09:00
phase: pm_lead_replanned_rework
summary: After repeated FIX generations, PM+Lead re-planned root cause, oracle, and scope: the defect is source-transcription versus interpretation conflation, not the unsupported decision.
completed: Root cause: candidate normalized a published asterisk into an inferred plus and borrowed adjacent trailing-EPS denominator language. New oracle: preserve the literal published expression, label any weighted-sum reading as inference, keep the inconsistency an unresolved semantic gate, and use exact provider label EPS(Fwd.12M, 지배). Scope remains the two research docs; DATA_STATUS unchanged.
next: Lead performs bounded transcription correction; automated checks must assert literal asterisk disclosure, interpretation separation, exact field label, and unchanged unsupported/numeric prohibition before fresh gen4 review.
files_touched: docs/data/research/active/HISTORICAL_FREE_SOURCE_DISCOVERY.md; docs/data/research/active/KR_FORWARD_EARNINGS_PIT_CONTRACT.md
tests: Planned: exact published-expression and exact-label text assertions; four-field unsupported/numeric prohibition; table shape; git diff --check.
risks: Official Help may contain an apparent typo; documentation must not silently repair it. No inference may be presented as provider fact.
new_discoveries: Review-oracle gap: source transcription and interpretation were not previously separate acceptance dimensions.
