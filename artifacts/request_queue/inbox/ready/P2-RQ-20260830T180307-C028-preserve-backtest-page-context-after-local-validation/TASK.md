# Preserve Backtest page context after local validation

## Problem
Backtest loses page title context after safe local validation or reload.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Safe interaction preserves page identity and returns focus/scroll to a meaningful in-view result without unexpected title loss at every supported viewport.

## Verify
Focused Backtest scroll/focus tests plus all five viewport and 125% large-font checks; no Backtest run/export; Queue Doctor.
