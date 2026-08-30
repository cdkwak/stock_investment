# Preserve complete Research source and status content at 1280x720

## Problem
Research all-open loses source/status content at 1280x720 with no vertical recovery.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
All source/status content stays reachable at every supported viewport, pane preferences survive responsive recovery, and 125% large-font content is not clipped.

## Verify
Focused Research preferences/GUI tests plus all five viewport and 125% large-font provider-free render checks; zero Qt warnings; Queue Doctor.
