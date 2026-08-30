# Keep Net Worth reload and maintenance actions visible at laptop widths

## Problem
Net Worth fixed header row hides reload and maintenance controls at laptop widths.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Safe local reread remains visible without horizontal discovery at every supported viewport; edit/delete are clearly grouped and 125% large-font layout does not clip.

## Verify
Focused Net Worth page tests plus provider-free viewport/QFont regression at all five sizes and 125% large font; zero Qt warnings; Queue Doctor.
