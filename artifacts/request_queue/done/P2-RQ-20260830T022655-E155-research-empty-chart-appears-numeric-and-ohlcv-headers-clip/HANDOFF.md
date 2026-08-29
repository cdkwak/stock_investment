updated_at: 2026-08-30T05:11:48+09:00
phase: completed
summary: No-symbol Research chart now shows a dedicated unavailable state instead of numeric axes; compact Korean OHLCV meanings fit native/offscreen 1600x900.
completed: Provider-free implementation, repeated native/offscreen render measurements, 8 Research tests, 14 candidate-discovery tests, 2 native layout tests, py_compile 3/3, exact diff-check.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_gui_backtest.py
tests: Research 8/8; candidate discovery 14/14; native layout 2/2; py_compile 3/3; Windows sections 64-65px vs max label 54px.
risks: No provider calls or financial semantics changed; narrower non-OHLCV panels remain within existing 1600x900 bounds.
new_discoveries: none
