# Windows full-page navigation emits negative QFont point-size warnings

## Problem
Native Windows full-page navigation emits repeated QFont point-size <=0 (-0.75) warnings and fails the focused positive-font regression.

## Evidence
Audit finding F-GUI-20260830-006 in artifacts/gui_audits/20260830_full_user_audit/REPORT.md with HIGH confidence; six sibling focused tests pass and the negative-font regression fails reproducibly.

## Scope
allow:
- Edit only the exact declared production and owning test paths.
- Run provider-free focused tests, offscreen/native Windows renders, QTest click regressions, Queue Doctor, immutable review, and scoped commit.

deny:
- No broker/order/amend/cancel/transfer/account or secret mutation, provider refresh, live scheduler activation, Backtest run/export, or external publication.
- Never open, inspect, modify, enumerate through, stage, restore, or commit artifacts/analysis/kospi200_option_wall_recent_250.csv; no broad Git enumeration or unrelated files.

## Done When
Every navigated page and account chart retains a positive effective font size under native Windows DPR1, with no QFont negative-point-size warnings.

## Verify
Run test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning under QT_SCALE_FACTOR=1 and QT_ENABLE_HIGHDPI_SCALING=0 plus the smallest owning GUI regressions, repeat affected navigation, run Queue Doctor, and obtain fresh strong independent review.
