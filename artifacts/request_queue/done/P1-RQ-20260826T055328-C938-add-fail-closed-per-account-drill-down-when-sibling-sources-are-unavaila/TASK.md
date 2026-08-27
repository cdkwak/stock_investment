# Add fail-closed per-account drill-down when sibling sources are unavailable

## Problem
A globally unavailable sibling account source correctly suppresses combined totals but also prevents an independently validated source-local subtotal, allocation, history, and holdings drill-down.

## Evidence
Current presentation computes all_sources_displayable globally and intentionally nulls combined currency totals/allocations/history; AccountPage has no explicit source selector, so a valid Toss projection cannot be inspected independently when KB/family sources are unavailable.

## Scope
allow:
- After F363 and F2B9, modify only the account presentation model, AccountPage rendering/selection, and their existing GUI tests to expose independently verified source-local views.

deny:
- No weakening of combined-total suppression, no defaulting silently to an available source, no account identifiers, no values from unavailable/stale sources, no cross-currency sum or FX inference, no provider/account call, no Data write, and no order/transfer action.

## Done When
The default combined view remains fail-closed and numeric-free whenever any configured source is unavailable; an explicit identifier-free source selector lists every configured source, and selecting one independently displayable source shows only that source's currency-separated subtotal, allocation, history, and holdings with exact ownership/provider/as-of/freshness labels. Selecting an unavailable/stale source stays numeric-free with its typed reason. Privacy mode masks every monetary value and holding identity in combined and source-local modes; no currency conversion, cross-source inference, provider call, or persistence occurs.

## Verify
Add service regressions for valid Toss plus unavailable KB/family, unavailable-source selection, mixed currencies, and combined fail-closed behavior; add AccountPage tests for default combined state, explicit source switching, privacy masking, refresh preservation, and clean worker shutdown; run owning GUI service/widget suites and a provider-free masked native smoke.
