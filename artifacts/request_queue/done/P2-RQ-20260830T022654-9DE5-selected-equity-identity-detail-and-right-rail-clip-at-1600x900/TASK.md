# Selected-equity identity detail and right rail clip at 1600x900

## Problem
Selected-equity identity/source detail and the bottom right-rail control caption clip at 1600x900 for 005930 and SPY workflows.

## Evidence
Audit finding F-GUI-20260830-003 in artifacts/gui_audits/20260830_full_user_audit/REPORT.md with HIGH confidence and screenshots 02 and 03.

## Scope
allow:
- Edit only the exact declared production and owning test paths.
- Run provider-free focused tests, offscreen/native Windows renders, QTest click regressions, Queue Doctor, immutable review, and scoped commit.

deny:
- No broker/order/amend/cancel/transfer/account or secret mutation, provider refresh, live scheduler activation, Backtest run/export, or external publication.
- Never open, inspect, modify, enumerate through, stage, restore, or commit artifacts/analysis/kospi200_option_wall_recent_250.csv; no broad Git enumeration or unrelated files.

## Done When
Selected instrument identity, source/date context, error state, and every right-rail control caption remain fully readable at 1600x900 for Korean equity and US ETF workflows.

## Verify
Add provider-free selected-identity 1600x900 regressions, run focused owning tests, repeat 005930 and SPY search/select/chart clicks, run Queue Doctor, and obtain fresh strong independent review.
