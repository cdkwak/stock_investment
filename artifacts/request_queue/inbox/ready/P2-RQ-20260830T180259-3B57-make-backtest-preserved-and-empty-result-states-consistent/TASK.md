# Make Backtest preserved and empty result states consistent

## Problem
Backtest preserved/no-result copy conflicts and empty plots look like rendering failures.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Backtest has one consistent plain-language result state, one safe primary recovery action, and friendly non-numeric empty plot overlays at all supported sizes and 125% font.

## Verify
Focused Backtest GUI tests plus provider-free viewport/large-font and zero-QFont regression; no Backtest run/export; Queue Doctor.
