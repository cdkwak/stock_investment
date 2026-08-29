# Primary chart lower-panel y-axis labels clip at 1600x900

## Problem
At native Windows logical 1600x900, primary chart lower-panel y-axis titles and tick labels clip on Dashboard, Index Graph, selected equity, and US ETF surfaces.

## Evidence
Audit finding F-GUI-20260830-001 in artifacts/gui_audits/20260830_full_user_audit/REPORT.md with HIGH confidence and screenshots 00,01,02,03.

## Scope
allow:
- Edit only the exact declared production and owning test paths.
- Run provider-free focused tests, offscreen/native Windows renders, QTest click regressions, Queue Doctor, immutable review, and scoped commit.

deny:
- No broker/order/amend/cancel/transfer/account or secret mutation, provider refresh, live scheduler activation, Backtest run/export, or external publication.
- Never open, inspect, modify, enumerate through, stage, restore, or commit artifacts/analysis/kospi200_option_wall_recent_250.csv; no broad Git enumeration or unrelated files.

## Done When
All lower-panel rotated y-axis titles and tick labels are fully readable inside the viewport on the four affected primary chart surfaces at 1600x900, with no regression at supported render sizes.

## Verify
Add or strengthen provider-free 1600x900 layout/pixel-bound regression in tests/unit/gui/test_gui_backtest.py, run the focused owning tests, repeat the four affected clicks/renders, run Queue Doctor, and obtain fresh independent review.
