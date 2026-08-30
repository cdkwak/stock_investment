# Localize Watchlist create and rename dialog actions

## Problem
Korean Watchlist create/rename prompts show English OK and Cancel buttons.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Create and rename dialogs display Korean actions and preserve pointer, Enter, Escape, default, and cancel semantics.

## Verify
Focused Watchlist dialog localization/keyboard tests at supported viewports and 125% font; Queue Doctor.
