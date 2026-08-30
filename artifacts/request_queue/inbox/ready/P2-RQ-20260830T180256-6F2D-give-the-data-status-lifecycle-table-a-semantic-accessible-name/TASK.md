# Give the Data Status lifecycle table a semantic accessible name

## Problem
The Data Status lifecycle table lacks a semantic accessible name.

## Evidence
artifacts/gui_audits/20260830_qt_ux_exhaustive/REPORT.md and CRITIQUE.md; 154 retained images and machine evidence.

## Scope
allow:
- Provider-free local GUI implementation and tests; sanitized read-only fixtures; exact viewport, keyboard, large-font, and accessibility validation; Queue lifecycle and exact-path scoped commit.

deny:
- No provider refresh or network call; no live scheduler or production activation; no broker order, amend, cancel, transfer, withdrawal, account mutation, secret/access change, Backtest run/export, protected option-wall CSV access, destructive action, broad Git, or external publication.

## Done When
The lifecycle QTableWidget exposes the exact user-facing accessible name and remains keyboard reachable at all supported viewports and 125% font.

## Verify
Focused Data Status accessibility test plus provider-free viewport/large-font check and zero Qt warnings; Queue Doctor.
