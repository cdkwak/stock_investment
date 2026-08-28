# Investigate unowned rolling KOSPI200 option-wall artifact mutation

## Problem
artifacts/analysis/kospi200_option_wall_recent_250.csv changed in the canonical worktree without an identified interactive owner.

## Evidence
git diff shows the oldest 2025-08-08 row removed and a 2026-08-27 row added; the user states they did not make this change.

## Impact
Background artifact mutation can contaminate unrelated commits and obscure ownership of retained analytical evidence.

## Scope
allow:
- artifacts/analysis/kospi200_option_wall_recent_250.csv and its owning scheduled derivative artifact publication boundary
deny:
- unrelated files and operations

## Done When
Triage defines the exact acceptance boundary.

## Verify
Inspect git status and the exact CSV diff after the owning scheduled operation while preserving the current bytes.
