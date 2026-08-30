# Make Equity and US ETF guided examples catalog-aware

## Problem
Built-in 005930 and SOXX guided examples immediately fail against the current local catalog.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Each guided example is selected from an actually available local catalog item or the UI presents an explicit local-catalog unavailable/recovery state without provider calls.

## Verify
Focused Equity/US ETF identity tests plus provider-free 005930/SPY/SOXX context checks across supported viewports; Queue Doctor.
