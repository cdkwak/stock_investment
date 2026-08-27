updated_at: 2026-08-26T02:27:12+09:00
phase: completed
summary: Added fail-closed local KOSPI/KOSDAQ PER-PBR historical comparison views and Dashboard rendering.
completed: Exact kr_index_fundamental_daily/state validation; as-of-only medians and empirical percentiles; independent null suppression; stale/malformed/identity mismatch suppression; dashboard labels/tooltips and density update; GUI status updated.
next: none
files_touched: docs/gui/GUI_STATUS.md; src/stock_data/gui/main_window.py; src/stock_data/gui/services.py; tests/unit/gui/test_gui_backtest.py; tests/unit/gui/test_gui_services.py
tests: Targeted 8 passed; test_gui_services 172 passed; test_gui_backtest 207 passed, 1 skipped; production local read exact; Dashboard no horizontal overflow; native smoke DEGRADED only known offscreen baseline with no clipping and clean worker shutdown.
risks: NON_PREDICTIVE descriptive history only; medians/percentiles describe retained as-retrieved observations; unsupported offscreen Qt baseline remains degraded.
new_discoveries: None
