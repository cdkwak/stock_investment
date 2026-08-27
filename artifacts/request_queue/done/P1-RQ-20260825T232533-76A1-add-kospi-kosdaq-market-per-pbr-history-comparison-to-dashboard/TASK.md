# Add KOSPI KOSDAQ market PER PBR history comparison to Dashboard

## Problem
Dashboard has no typed, read-only KOSPI/KOSDAQ market valuation comparison despite an accepted local KRX index-fundamental series.

## Evidence
B1EB provides exact 1001/2001 KRX close, weighted PER, weighted PBR and dividend-yield history through 2026-08-25 under descriptive NON_PREDICTIVE semantics; current Dashboard service and widgets expose no market valuation view.

## Scope
allow:
- Read-only consumption of the accepted local kr_index_fundamental_daily contract; descriptive as-of historical comparison; focused service, widget, tests and GUI Status updates.

deny:
- No provider calls, Data/history/state writes, contract or scheduler changes, predictive/PIT-safe/Backtest feature claims, security-level aggregation, fallback or value imputation, and no unrelated Dashboard redesign.

## Done When
After B1EB and the active GUI release-smoke task are accepted, a thin local service reads only kr_index_fundamental_daily, validates exact market identities and dates, and exposes KOSPI/KOSDAQ PER and PBR with clearly dated descriptive historical median/percentile context; the Dashboard renders both without clipping and suppresses malformed, stale, or identity-mismatched input.

## Verify
Focused service tests cover identity, duplicate/date mismatch, null/nonfinite values, as-of-only baseline computation, stale/missing fail-closed behavior and no writes; GUI tests verify both markets, labels, resize/close behavior and zero workers; run an isolated-temp offscreen smoke.
