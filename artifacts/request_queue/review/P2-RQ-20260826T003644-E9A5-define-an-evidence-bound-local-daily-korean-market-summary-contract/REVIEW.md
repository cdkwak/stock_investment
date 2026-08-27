result: Fixed the daily summary contract's binding-state and idempotency contradictions; documentation remains fail-closed and runtime-neutral.
changed: docs/gui/DAILY_MARKET_SUMMARY_CONTRACT.md: summary_id now excludes composition occurrence time; all nine bindings are mandatory envelopes; missing/duplicate/unexpected bindings are INVALID; delivery dedupe uses the stable ID.
verified: Contract invariants 9/7/4 OK; scoped links 4 files OK; Telegram bridge 17 passed; queue doctor OK.
review_generation: e399aca0233e93119959496b6c723102
handoff_sha256: 419ea5861acd6297226d6be2798c0e920ece93f547eddfed12531890267f1a90
submitted_at: 2026-08-27T02:30:23+09:00
