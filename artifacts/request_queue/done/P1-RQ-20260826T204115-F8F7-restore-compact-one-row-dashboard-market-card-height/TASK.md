# Restore compact one-row dashboard market-card height

## Problem
The accepted ten-card one-row dashboard density regressed to a 120px top strip instead of the tested 112px compact target.

## Evidence
Provider-free focused run passed 8 checks and failed only the exact density height assertion: observed 120, expected 112 at 1600x840; cards still occupy row 0.

## Scope
allow:
- Modify exact Dashboard main_window and owning GUI test only; provider/API/data zero.

deny:
- No data/provider/scheduler/account/backtest behavior change; no hiding status text or reducing readable font/sparkline minima; no unrelated GUI edits.

## Done When
Ten visible market cards remain one row at common logical widths, title/body/status and >=18px sparklines stay visible/unclipped, no horizontal scroll appears, and the accepted top strip height is restored to 112px at 1600x840.

## Verify
Run density, populated common-width 1280/1365/1440, current/stale visibility, valuation, Korean/current sparkline and typed-gap tests offscreen; py_compile and independent review.
