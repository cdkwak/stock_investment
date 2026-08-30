# Allow Equity and US ETF result feedback to wrap fully

## Problem
Equity and US ETF long result-feedback banners clip the explanation.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Feedback wraps to full height or exposes a reliable full-text disclosure on both surfaces at all supported viewports and 125% font.

## Verify
Focused Equity/US ETF long-feedback tests plus all five viewport and 125% large-font checks; zero Qt warnings; Queue Doctor.
