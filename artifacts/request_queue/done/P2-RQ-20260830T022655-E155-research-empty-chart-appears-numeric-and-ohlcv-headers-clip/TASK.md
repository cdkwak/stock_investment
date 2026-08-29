# Research empty chart appears numeric and OHLCV headers clip

## Problem
Research Workspace with no selected symbol shows misleading default numeric chart axes and clips OHLCV headers in a fixed-width panel at 1600x900.

## Evidence
Audit finding F-GUI-20260830-004 in artifacts/gui_audits/20260830_full_user_audit/REPORT.md with HIGH confidence and screenshot 04_Research_Workspace.png; audit P3 maps to canonical lowest priority P2.

## Scope
allow:
- Edit only the exact declared production and owning test paths.
- Run provider-free focused tests, offscreen/native Windows renders, QTest click regressions, Queue Doctor, immutable review, and scoped commit.

deny:
- No broker/order/amend/cancel/transfer/account or secret mutation, provider refresh, live scheduler activation, Backtest run/export, or external publication.
- Never open, inspect, modify, enumerate through, stage, restore, or commit artifacts/analysis/kospi200_option_wall_recent_250.csv; no broad Git enumeration or unrelated files.

## Done When
The no-symbol chart presents an explicit unavailable state rather than numeric-looking data and all OHLCV column meanings are readable without clipped headers at 1600x900.

## Verify
Run focused Research Workspace and candidate-discovery GUI tests, repeat the no-symbol 1600x900 render and affected controls, run Queue Doctor, and obtain fresh independent review.
