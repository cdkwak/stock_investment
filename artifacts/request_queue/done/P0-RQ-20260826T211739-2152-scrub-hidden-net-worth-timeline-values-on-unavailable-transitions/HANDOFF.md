updated_at: 2026-08-26T21:28:19+09:00
phase: completed
summary: All hidden Net Worth timeline value and metadata transitions are scrubbed before hide, with valid restoration preserved.
completed: Independent PASS including a separate stale SNAPSHOT_INCOMPLETE marker probe; no counterexample remains.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_net_worth_page.py
tests: 36 page/service tests pass; py_compile pass; provider-free native GUI smoke 1 pass; independent stale probe pass.
risks: Only pre-existing dependency deprecation warnings.
new_discoveries: none
