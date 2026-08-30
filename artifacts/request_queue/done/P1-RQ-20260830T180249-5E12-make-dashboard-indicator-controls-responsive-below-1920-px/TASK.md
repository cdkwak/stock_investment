# Make Dashboard indicator controls responsive below 1920 px

## Problem
Dashboard fixed single-row indicator layouts collapse below 1920 px.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Dashboard chart title, market selector, and every indicator control remain recognizable and operable at 2560/1920/1600/1366/1280 and 125% large font without overlap or ambiguous fragments.

## Verify
Focused Dashboard GUI tests plus provider-free viewport/QFont regression at all five sizes and 125% large font; zero Qt warnings; Queue Doctor.
