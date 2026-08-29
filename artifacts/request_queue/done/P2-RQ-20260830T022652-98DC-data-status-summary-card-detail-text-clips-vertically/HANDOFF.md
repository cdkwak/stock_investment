updated_at: 2026-08-30T03:39:08+09:00
phase: completed
summary: Data Status summary cards now derive a shared minimum height from native wrapped-text metrics and expose their full summaries to accessibility.
completed: Replaced fixed 102px card height with measured fitting; added 1600x900 viewport regression; native Windows local-reread capture confirms all four cards fit.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_gui_health.py
tests: 18 GUI health passed; 5 focused Data Status integration passed; py_compile 3/3 passed; native Windows 1600x900 local reread 1 click, 4/4 cards fit, horizontal scroll max 0; Queue Doctor OK.
risks: No known functional risk; full project suite not run because change is isolated to Data Status card sizing.
new_discoveries: none
