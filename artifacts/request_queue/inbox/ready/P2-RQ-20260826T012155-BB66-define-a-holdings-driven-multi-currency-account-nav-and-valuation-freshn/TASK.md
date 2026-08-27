# Define a holdings-driven multi-currency account NAV and valuation-freshness contract

## Problem
No typed read-only contract maps verified broker holdings to exact quote identities and combines independently timestamped account, price and FX evidence into a fail-closed multi-currency estimated NAV.

## Evidence
PROJECT_GOAL requires holdings-driven quote targets, separate account/price/FX timestamps, honest 30-minute semantics and multi-currency NAV. F363 is currency-section-only and F2B9 domestic KRW; neither owns cross-currency NAV, symbol mapping or freshness joins, and wider account/realtime work remains unauthorized.

## Scope
allow:
- After B2ED acceptance, create the future Project-owned documentation contract and update Project Status routing only.

deny:
- No provider/account call, symbol guess, scheduler/Data/state write, credential/private value, runtime/GUI implementation, currency averaging, stale/current silent join, order/transfer/trading, or wider account/realtime phase.

## Done When
A documentation-only account-nav-valuation/v1 contract defines exact broker/account/snapshot and holding identities; explicit broker-instrument-to-quote mapping with unsupported state; provider-labelled quote identity/currency and no merge; per-currency cash/position valuation; user-selected base currency; exact FX pair/direction/unit and conversion formula; separate account/quote/FX as-of, last-success and freshness/cadence states; 30-minute versus close semantics; generation-consistent joins, prior-valid retention and partial-component visibility; total NAV is numeric only when every included holding/cash/quote/FX input passes identity/freshness; estimated NAV remains distinct from broker valuation; sanitized reason codes and privacy boundary; PROJECT_STATUS link without implementation authority.

## Verify
Map every Project Goal account-valuation requirement to a field/invariant; test the written rules with worked schema examples for KRW/USD cash and holdings, inverse-FX rejection, missing/stale quote or FX, duplicate mapping, mixed generation, close-only and prior-retained cases; prove one invalid required component suppresses total but not independent valid sections, no symbol/provider guess is allowed, and B2ED freshness fields are reused; verify links and queue doctor.
