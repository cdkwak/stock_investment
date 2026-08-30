# Allow Dashboard cards to reflow long and large-font content

## Problem
Dashboard fixed-height cards clip long or large-font content.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Dashboard card copy reflows, expands, or discloses details without information loss at all supported viewports and 125% large font while preserving the compact default hierarchy.

## Verify
Focused Dashboard long-copy tests plus all five viewport and 125% large-font geometry checks; zero Qt warnings; Queue Doctor.
