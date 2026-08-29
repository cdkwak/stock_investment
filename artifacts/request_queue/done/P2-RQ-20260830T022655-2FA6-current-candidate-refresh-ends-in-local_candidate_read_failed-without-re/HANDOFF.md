updated_at: 2026-08-30T04:43:29+09:00
phase: completed
summary: Retained candidate refresh now preserves READY candidate/valid-empty results and returns privacy-safe typed missing, corrupt, empty-input, or stale reasons with explicit local Data recovery actions.
completed: Provider-free real retained scan now returns typed LOCAL_CANDIDATE_INPUT_CORRUPT recovery instead of terminal LOCAL_CANDIDATE_READ_FAILED; repeat QTest clicks passed; focused GUI/scanner tests 16 passed; py_compile 4/4; diff check clean; Queue Doctor OK.
next: none
files_touched: src/stock_data/gui/main_window.py; src/stock_data/gui/services.py; tests/unit/gui/test_stock_candidate_discovery_gui.py
tests: 16 focused pytest passed; provider-free retained scan typed; py_compile 4/4
risks: Full suite not run; current retained input remains locally unreadable/corrupt and requires the displayed Data-owned recovery action before candidates can load.
new_discoveries: none
