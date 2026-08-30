# Render shared provenance failures in plain language with a direct recovery route

## Problem
Shared provenance surfaces expose internal contract tokens and Research lacks an operable recovery route.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Research, Data Status, Index detail, Account, and Backtest show consistent plain-language summary/effect/next step with technical identifiers behind disclosure; Research provides a direct safe Data Status or recovery action.

## Verify
Focused GUI service, candidate, health, and Backtest tests; all five viewport and 125% large-font checks; provider/API call count zero; zero Qt warnings; Queue Doctor.
