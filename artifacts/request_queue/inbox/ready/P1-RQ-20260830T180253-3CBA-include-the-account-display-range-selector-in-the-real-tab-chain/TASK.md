# Include the Account display-range selector in the real Tab chain

## Problem
The Account display-range selector is absent from real Tab traversal.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
The real Account Tab chain reaches all eight page-local controls in logical visual order with a visible focus indicator at every supported viewport and 125% large font.

## Verify
Focused Account keyboard regression reproduces 8/8 Tab reachability; provider-free viewport/large-font run and zero Qt warnings; Queue Doctor.
