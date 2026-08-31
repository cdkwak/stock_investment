# Final GUI acceptance blocked — F-GUI-20260830-006 recurrence

status: `BLOCKED_PRODUCT_DEFECT`
observed_at: `2026-08-30T06:29+09:00`
base_head: `16f8b7d41d0985ce945b37d35b106ad9b53eab96`
acceptance_generation: `NOT_FROZEN`

## Decision

Ten native Windows 1600x900 cycles numbered 01 through 10 each completed all
10 page contracts, reconciled 138 current stable control IDs, exercised 108
safe controls, recorded 16 disabled prerequisites and 14 hard safety skips,
performed two provider-free local candidate-refresh clicks, emitted zero
negative QFont warnings in their retained local-data states, drained managed
workers, and closed cleanly. Those cycle ledgers remain valid observations,
but the final acceptance count is invalidated because the required focused
Windows navigation regression reproducibly fails on the current HEAD.

## Defect

- Fingerprint: `gui:windows-navigation:qfont-negative-point-size-warning:qt-dpr1`
- Existing audit ID: `F-GUI-20260830-006`
- Severity: `P2`
- Confidence: `HIGH`
- Exact reproduction:
  `QT_SCALE_FACTOR=1 QT_ENABLE_HIGHDPI_SCALING=0 .venv\Scripts\python.exe -m pytest -q tests/unit/gui/test_gui_backtest.py::test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning`
- Expected: full native Windows page/account-source navigation, nested account
  tabs, dual-currency account charts, and screenshot capture produce no
  `QFont::setPointSize*` warning with a non-positive point size.
- Actual: the exact isolated test fails at
  `tests/unit/gui/test_gui_backtest.py:9401` with repeated
  `QFont::setPointSizeF: Point size <= 0 (-0.750000)` messages.
- Combined focused receipt: `6 passed, 1 failed` in `6.51s`; the same test was
  the only failure.
- Isolated receipt: `1 failed` in `5.39s` in a fresh native Windows process.
- Product files were not edited by this acceptance worker; exact-path status
  for `font_policy.py`, `main_window.py`, and `test_gui_backtest.py` was clean.

## Deduplication clues

- Canonical Done task:
  `RQ-20260830T022656-F3CF` / fingerprint above.
- Its receipt classified the finding as a no-code duplicate/satisfaction of
  `FC40`, with reviewed snapshot commit
  `0e59cc8640d96dbf727d66f31866f8ec5c69fbe3`.
- Earlier backing fix commit visible on the exact GUI path history:
  `b86567464f4dc399a66a03dfdc57e5369d63cefc` (`fix(gui): eliminate negative QFont point size warning`).
- The current reproduction indicates that satisfaction is no longer valid for
  the synthetic dual-currency account navigation path.

## Likely exact remediation scope

- Production OWNS candidates:
  `src/stock_data/gui/font_policy.py`,
  `src/stock_data/gui/main_window.py`.
- Test owner:
  `tests/unit/gui/test_gui_backtest.py::test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning`.
- Inspect account chart title/legend/axis/slice font propagation during source
  changes and nested account-tab navigation; preserve the current positive
  public font values while eliminating the warning-producing intermediate
  assignment.

## Resume condition

PM must reopen/deduplicate the exact Queue fingerprint, route a bounded SINGLE
implementation, require the exact isolated regression plus focused native GUI
checks and fresh independent review, and land a scoped commit. After explicit
resume, discard this final-pass count and rerun the complete ten accepted
cycles from cycle 01 before freezing any acceptance generation.

No broker/order/transfer/account mutation, provider refresh, live scheduler,
Backtest run/export, Queue mutation, or protected CSV access occurred.
