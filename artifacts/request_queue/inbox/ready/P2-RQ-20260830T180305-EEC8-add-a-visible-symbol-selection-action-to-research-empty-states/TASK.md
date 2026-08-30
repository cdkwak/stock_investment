# Add a visible symbol-selection action to Research empty states

## Problem
Research compact empty states offer only Ctrl+K and no pointer-visible symbol action.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Every no-symbol Research preset exposes a visible 종목 선택 action opening the existing exact-identity switcher while retaining Ctrl+K and keyboard focus.

## Verify
Focused Research empty-state/preset tests plus all five viewport and 125% large-font checks; zero Qt warnings; Queue Doctor.
