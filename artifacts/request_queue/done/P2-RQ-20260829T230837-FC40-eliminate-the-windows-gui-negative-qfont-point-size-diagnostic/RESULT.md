result: Native Windows negative QFont point-size diagnostic eliminated with a DPI-equivalent positive point-unit root font policy; independent review requested.
changed: src/stock_data/gui/main_window.py: root QWidget font unit 13px -> 9.75pt; tests/unit/gui/test_font_policy.py: positive application point-size invariant; tests/unit/gui/test_gui_backtest.py: Windows native nine-page, Account-source, privacy-subview, chart-font, screenshot, and diagnostic regression.
verified: Exact native reproduction .tmp/agents/RQ-20260829T230837-FC40-gui_lead/locate_font_warning.json is []; 6 focused native Windows tests passed; py_compile passed; exact-scope diff-check passed; Queue Doctor OK.; independent review by gui_runtime_reviewer: Fresh Windows reviewer PASS: legacy account source-selector click reproduced one negative QFont point-size warning; candidate produced zero across nine top pages, two Account subviews, four source choices; six focused tests, py_compile, exact diff checks, and Queue Doctor passed.
completed_at: 2026-08-29T23:50:19+09:00
review_generation: 112d2bd0968829be8d64ceb5faceacef
reviewed_by: gui_runtime_reviewer
