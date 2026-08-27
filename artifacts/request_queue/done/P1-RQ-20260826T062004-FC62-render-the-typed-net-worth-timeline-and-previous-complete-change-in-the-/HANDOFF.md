updated_at: 2026-08-26T21:35:19+09:00
phase: completed
summary: Parent timeline contract and P0 scrub are integrated; all exact-int-to-Qt coordinate and axis overflow cases fail chart closed before mutation.
completed: Independent exact-tree PASS including 10**400, 2**53+1 precision loss, and +/-1e308 span-overflow probes with ordinary restoration.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_net_worth_page.py
tests: 37 page/service tests pass; py_compile pass; native GUI smoke pass; independent adversarial coordinate probes pass.
risks: Pre-existing deprecation warnings only.
new_discoveries: C938 write scope conflicts with active 687F; FC62 is the safe independent GUI lane and was claimed instead.
