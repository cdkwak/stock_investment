# Define sovereign yield-curve equity-linkage and bond-ETF duration semantics

## Problem
Separate rate and Treasury-futures observations lack one verified semantic boundary for sovereign yield/price identity, compatible curve timing, optional component decomposition, regime-dependent equity linkage, and bond-ETF duration behavior.

## Evidence
PROJECT_GOAL requires tenor/curve alignment, price-yield inverse semantics, nominal/real/breakeven/term-premium distinctions, equity/sector interpretation, regime-varying stock-bond correlation, and TLT duration/distribution/fee/tracking treatment. Current displays use heterogeneous observations and no equivalent contract exists.

## Scope
allow:
- Create the future Project-owned semantic documentation contract and update PROJECT_STATUS routing only; name required exact source identities without selecting providers or inventing data.

deny:
- No provider selection/call, undocumented formula, Data contract/promotion, predictive claim, GUI/runtime implementation, TLT portfolio model, optimization, recommendation/order, or wider phase selection.

## Done When
A documentation-only sovereign-yield-semantics/v1 contract defines exact country/instrument/tenor identities, yield-versus-price units/direction, compatible as-of/cadence/finality rules, curve measures, independently verified nominal/real/breakeven/term-premium components with UNSUPPORTED states, non-causal historical stock-bond regime comparison, evidence-bounded equity/sector interpretation, and separate bond-ETF duration/distribution/expense/tracking metadata; PROJECT_STATUS links it without Data, Backtest, or GUI implementation authority.

## Verify
Map every Project Goal sovereign-rate and TLT requirement to one field/invariant; prove yield levels are not inferred from futures prices, incompatible timestamps are not combined, missing decomposition stays unsupported, regime linkage is not predictive/causal, and ETF semantics remain distinct from individual bonds; verify links and queue doctor.
