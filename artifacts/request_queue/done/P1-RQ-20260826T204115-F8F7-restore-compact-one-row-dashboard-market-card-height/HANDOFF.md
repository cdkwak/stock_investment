updated_at: 2026-08-29T09:55:05+09:00
phase: completed
summary: Restored the accepted compact Dashboard strip constant to 112px and pinned literal density, card, and sparkline acceptance invariants.
completed: Exact two-file implementation and change-driven provider-free validation are complete; dependency C938 is Done and no newer Done receipt satisfies F8F7.
next: none
files_touched: src/stock_data/gui/main_window.py,tests/unit/gui/test_gui_backtest.py
tests: Density 1 passed; owning GUI regression set 7 passed; py_compile PASS; exact-path diff --check PASS; Queue Doctor OK.
risks: Residual risk is limited to untested platform-specific font metrics outside the accepted offscreen logical-width matrix; no semantic/provider/data/account/backtest behavior changed.
new_discoveries: none
