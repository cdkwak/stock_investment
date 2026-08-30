# Preserve Account chart legend identity and date meaning

## Problem
Populated Account charts lose legend identity, crowd pie labels, and obscure date meaning.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
Distinct holdings remain identifiable through unique labels/tooltips, pie labels do not overlap materially, and sparse formatted dates remain meaningful at supported viewports and 125% font.

## Verify
Focused Account chart/value-history tests using sanitized synthetic 0/1/100/1000 fixtures plus viewport/large-font checks; Queue Doctor.
