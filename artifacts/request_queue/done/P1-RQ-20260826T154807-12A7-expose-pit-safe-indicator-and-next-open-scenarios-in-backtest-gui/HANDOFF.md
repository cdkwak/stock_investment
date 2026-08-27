updated_at: 2026-08-26T19:49:17+09:00
phase: completed
summary: Fixed RSI14 development scenario adapter, immutable views and responsive Backtest GUI panel are complete and explicitly validated.
completed: Exact typed input/version/schema gate; holdout date-key rejection before all other row values; fixed LOW30/HIGH70 study and 30/70 next-open/matched-hold; typed insufficient/no-entry; background Page flow and failure preservation; 1600x900 capture; status truth.
next: none
files_touched: docs/backtest/BACKTEST_STATUS.md; docs/gui/GUI_STATUS.md; src/stock_data/gui/backtest_scenario_service.py; src/stock_data/gui/main_window.py; tests/unit/gui/test_backtest_scenario_service.py; tests/unit/gui/test_gui_backtest.py
tests: Service 9 passed; explicit service/Page/worker/accepted-close-proxy slice 17 passed; owning execution/indicator regressions 63 passed; py_compile passed; 1600x900 offscreen hscroll=0, four cards visible, zero QThreads; provider socket test API0.
risks: An unbounded full GUI attempt was stopped after 30 percent when its orphaned process exceeded 2 GB; no result from it is claimed and no other process was touched. Exact 12A7 and owning regressions all pass.
new_discoveries: none
