result: Scrubbed hidden Net Worth timeline labels, widget/QChart metadata, series, and axes across unavailable, empty, invalid, stale GAP, and privacy transitions while preserving valid restoration.
changed: src/stock_data/gui/main_window.py; tests/unit/gui/test_net_worth_page.py
verified: 36 page/service tests, py_compile, native GUI smoke, independent stale marker probe, and independent reviewer PASS.; independent review by goal_inbox_review: Independent current-tree PASS: all widget/QChart/QChartLegend values and metadata scrubbed across unavailable, empty, corrupt, invalid, stale, and privacy transitions; valid restoration works; 36 tests, py_compile, native smoke, and stale marker probe pass.
completed_at: 2026-08-26T21:28:19+09:00
