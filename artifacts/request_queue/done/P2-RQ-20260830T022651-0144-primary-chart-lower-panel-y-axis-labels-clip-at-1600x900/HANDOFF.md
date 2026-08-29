updated_at: 2026-08-30T03:15:07+09:00
phase: completed
summary: Lower primary-chart panels reserve a 6px viewport gutter, aligned 80px axes, and 144px height on Dashboard, Index Graph, 005930, and SPY.
completed: Implemented exact two-file layout fix; captured four native 1600x900 click renders with label bounds; 11 focused tests and py_compile pass.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_gui_backtest.py
tests: 11 focused GUI tests passed; native 4/4 at 1600x900: label x=2, height 81-105 within 144px panels; py_compile PASS; Queue Doctor OK.
risks: Isolated venv lacks deployed Qt glyph fonts, so native captures show tofu glyphs; exact logical label bounds and real audited font policy are unchanged. Full 255-test file was stopped during a long unrelated later case; all owning focused/layout/link regressions pass.
new_discoveries: none
