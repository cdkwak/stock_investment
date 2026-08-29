updated_at: 2026-08-30T07:03:30+09:00
phase: completed
summary: Fresh native DPR1 reproduction confirmed three deferred QFont warnings; tracing isolated them to Account source popup delegate style options, while chart title/legend/axis/slice public fonts stayed positive.
completed: Minimal positive-point item delegate and reusable font materialization helper implemented; exact native test now passes once.
next: none
files_touched: src/stock_data/gui/font_policy.py,src/stock_data/gui/main_window.py,tests/unit/gui/test_gui_backtest.py
tests: Exact native DPR1 baseline 1 failed at line 9401; delegate-only candidate exact native DPR1 1 passed.
risks: Must still verify repeated fresh processes, focused 1600x900/account regressions, portability, and independent review.
new_discoveries: none
