updated_at: 2026-08-27T02:30:23+09:00
phase: review
summary: Resolved both independent-review contradictions in daily-market-summary/v1.
completed: Made summary_id a stable evidence/content digest that excludes composition_time_utc; made all missing, duplicate, or unexpected registry bindings INVALID with complete suppression; clarified same-content recomposition dedupe.
next: Independent review
files_touched: docs/gui/DAILY_MARKET_SUMMARY_CONTRACT.md
tests: Contract invariants OK (9 roles, 7 sections, 4 classes); scoped local links OK (4 files); Telegram bridge 17 passed; queue doctor OK.
risks: Documentation only; registry revision 1 remains NO_OUTPUT because MARKET_STATE is not bound. No runtime, provider, Data, account, scheduler, Telegram, or trading change.
new_discoveries: none
