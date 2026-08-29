updated_at: 2026-08-29T23:50:19+09:00
phase: completed
summary: Reproduced the Windows native QFont warning at Account source-selector visibility, replaced the DPI-equivalent root font unit 13px with positive 9.75pt, and added full-page real-click diagnostics.
completed: Exact native harness now records no QFont point-size warning across nine top-level pages, all Account sources, privacy hide, and both Account subviews.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_font_policy.py; tests/unit/gui/test_gui_backtest.py
tests: 6 focused native Windows tests passed; py_compile passed; exact reproduction JSON is []; git diff --check passed; Queue Doctor OK.
risks: Fresh independent native Windows review remains; pytest reported only existing dependency deprecations and cache write warning.
new_discoveries: none
