# Data Status summary-card detail text clips vertically

## Problem
Data Status summary-card second and third lines are vertically clipped at native Windows logical 1600x900.

## Evidence
Audit finding F-GUI-20260830-002 in artifacts/gui_audits/20260830_full_user_audit/REPORT.md with HIGH confidence and screenshot 06_Data_Status.png.

## Scope
allow:
- Edit only the exact declared production and owning test paths.
- Run provider-free focused tests, offscreen/native Windows renders, QTest click regressions, Queue Doctor, immutable review, and scoped commit.

deny:
- No broker/order/amend/cancel/transfer/account or secret mutation, provider refresh, live scheduler activation, Backtest run/export, or external publication.
- Never open, inspect, modify, enumerate through, stage, restore, or commit artifacts/analysis/kospi200_option_wall_recent_250.csv; no broad Git enumeration or unrelated files.

## Done When
All four Data Status summary cards show complete status text at 1600x900 or provide an explicit accessible expansion/ellipsis affordance without hiding operational counts.

## Verify
Run focused GUI health and 1600x900 layout regressions, repeat the Data Status local-reread render, run Queue Doctor, and obtain fresh independent review.
