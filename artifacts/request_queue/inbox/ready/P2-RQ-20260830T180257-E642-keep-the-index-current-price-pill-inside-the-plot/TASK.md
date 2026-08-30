# Keep the Index current-price pill inside the plot

## Problem
Index current-price pill clips beyond the plot at 1600 px and below.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
The current-price pill remains fully inside the plot at 2560/1920/1600/1366/1280 and 125% large font without obscuring the series.

## Verify
Focused Index/current-display GUI tests plus provider-free viewport geometry assertions and zero Qt warnings; Queue Doctor.
