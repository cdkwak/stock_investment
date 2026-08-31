# Reopened QFont regression evidence

- fingerprint: `gui:windows-navigation:qfont-negative-point-size-warning:qt-dpr1`
- severity / confidence: `P2 / HIGH`
- observed_at: `2026-08-30T06:27+09:00`
- exact repro: native Windows, `QT_SCALE_FACTOR=1`, `QT_ENABLE_HIGHDPI_SCALING=0`, run `tests/unit/gui/test_gui_backtest.py::test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning` in a fresh pytest process.
- expected: full page and Account-source traversal emits zero `QFont::setPointSize*` warnings and every chart/font remains positive.
- actual: the isolated test fails reproducibly with repeated `QFont::setPointSizeF: Point size <= 0 (-0.750000)` messages; its explicit positive-font object assertions pass before the warning assertion fails.
- sanitized screenshot: `windows-full-page-navigation.png`
- screenshot sha256: `ca0f25a8110b88cf765c651de1c6996993a0d255a900019f0ee47a08148c14aa`
- contrast evidence: ten retained-state acceptance cycles recorded zero negative QFont warnings, so the failure is specific to the synthetic dual-currency Account traversal path exercised by the focused regression.
- likely OWNS: `src/stock_data/gui/font_policy.py`, `src/stock_data/gui/main_window.py`, `tests/unit/gui/test_gui_backtest.py`
- dedup: Canonical Done `RQ-20260830T022656-F3CF` was a no-code duplicate/satisfaction of `FC40`, backed by commit `b86567464f4dc399a66a03dfdc57e5369d63cefc`; reviewed snapshot `0e59cc8640d96dbf727d66f31866f8ec5c69fbe3` now fails this exact isolated test.
- acceptance effect: cycles 01–10 are discarded for final acceptance and must restart at cycle 01 after adaptive Queue implementation, fresh review, and scoped commit.
