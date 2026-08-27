result: Typed Net Worth timeline renders deterministic revisions, exact visible points, explicit gaps, safe deltas and privacy transitions; unrepresentable Qt coordinates fail chart closed without losing exact non-chart semantics.
changed: src/stock_data/gui/main_window.py; tests/unit/gui/test_net_worth_page.py
verified: 37 page/service tests, py_compile, native GUI smoke, P0 scrub probes, adversarial coordinate/axis probes, and independent exact-tree PASS.; independent review by goal_inbox_review: Independent exact-tree PASS after P0 integration: deterministic timeline, visible broken points, deltas/privacy/scrub, and overflow/precision/span fail-closed boundaries all validated with normal restoration; 37 tests, py_compile, native smoke pass.
completed_at: 2026-08-26T21:35:19+09:00
