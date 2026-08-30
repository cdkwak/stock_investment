# Separate Account safe reads from local maintenance and destructive actions

## Problem
Account safe reread, local maintenance, and deletion actions share an unsafe neutral visual hierarchy.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Viewing and safe reread are visibly separated from local maintenance; destructive actions have danger treatment and consequence text while default-No confirmation remains.

## Verify
Focused Account GUI hierarchy tests and provider-free viewport/large-font render checks; no destructive action executed; Queue Doctor.
