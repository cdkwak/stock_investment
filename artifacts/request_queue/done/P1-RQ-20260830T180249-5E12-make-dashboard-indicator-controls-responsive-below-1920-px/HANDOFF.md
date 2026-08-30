updated_at: 2026-08-30T23:11:52+09:00
phase: completed
summary: Responsive Dashboard header and indicator controls now reflow as whole labeled controls below 1920 px and under 125 percent font scaling.
completed: Implementation and focused responsive, keyboard, accessibility, cold-smoke, and Qt-warning checks completed.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/integration/gui/test_release_readiness.py; tests/unit/gui/test_gui_health.py
tests: 60 distinct focused tests passed; five viewports at 100 and 125 percent; zero captured Qt warnings; provider-free cold smoke passed.
risks: Qt offscreen validation completed; fresh Reviewer must perform independent behavior and visual-contract review.
new_discoveries: none
