# Define read-only portfolio-risk and backtest-evidence display contract

## Problem
Define a typed read-only evidence contract for concentration/currency and validated-bundle limitations.

## Evidence
Quant audit REPORT.md; QI-05/QI-06 GENERIC.

## Scope
allow:
- Documentation and read-only presentation-contract work.

deny:
- No risk policy invention, provider/broker call, holdout inspection, backtest execution, protected CSV.

## Done When
Contract specifies only supported inputs and explicit unavailable states, excluding risk-budget calculations, target sizing, VaR/ES claims, holdout access and executable backtests.

## Verify
Contract invariant checks and focused GUI empty-state tests; independent review.
