# Current candidate refresh ends in LOCAL_CANDIDATE_READ_FAILED without recovery detail

## Problem
Current candidate refresh terminates with LOCAL_CANDIDATE_READ_FAILED and zero rows without identifying the retained input failure or a recovery action.

## Evidence
Audit finding F-GUI-20260830-005 in artifacts/gui_audits/20260830_full_user_audit/REPORT.md with HIGH confidence and screenshot 04_Research_Workspace.png.

## Scope
allow:
- Edit only the exact declared production and owning test paths.
- Run provider-free focused tests, offscreen/native Windows renders, QTest click regressions, Queue Doctor, immutable review, and scoped commit.

deny:
- No broker/order/amend/cancel/transfer/account or secret mutation, provider refresh, live scheduler activation, Backtest run/export, or external publication.
- Never open, inspect, modify, enumerate through, stage, restore, or commit artifacts/analysis/kospi200_option_wall_recent_250.csv; no broad Git enumeration or unrelated files.

## Done When
Current candidate refresh loads valid retained candidates or returns a typed, privacy-safe missing/corrupt/stale input reason with a precise recovery action; valid empty remains distinct from failure.

## Verify
Add provider-free valid, missing, corrupt, stale, and valid-empty scanner/GUI regressions; repeat the refresh control; run owning tests and Queue Doctor; obtain fresh strong independent review.
