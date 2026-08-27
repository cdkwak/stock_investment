# Define multi-factor crash-risk states and point-in-time stress validation

## Problem
Descriptive valuation, breadth, trend, volatility, derivatives, and macro surfaces do not form a verified multi-factor crash-risk state with account loss impact and PIT-safe defensive-candidate validation.

## Evidence
PROJECT_GOAL requires four distinct non-certain risk states, concentration/loss impact, defensive candidates, and PIT-safe stress evidence including false alarms, early exits, rebound opportunity cost, drawdown, and costs. Current Backtest permits only Price and Volatility families, keeps other families unavailable, and preserves the final holdout; no equivalent contract task exists.

## Scope
allow:
- Create the future Backtest-owned documentation contract and update only BACKTEST_STATUS routing after the investment-policy contract is accepted.

deny:
- No provider call, feature substitution, model training, strategy or defensive implementation, live account access, optimization, holdout inspection, orders, Data mutation, or wider phase selection.

## Done When
A documentation-only crash-risk-validation/v1 contract defines the four observable states without a bubble/prediction claim, required factor identities and PIT/freshness availability, UNKNOWN behavior for blocked inputs, account concentration/loss-impact input boundary, defensive candidate output boundary, versioned stress windows, false-alarm/early-exit/rebound-opportunity/drawdown/cost metrics, purge/split/final-holdout invariants, and binding to an accepted investment-policy revision; BACKTEST_STATUS links it without implementation authority.

## Verify
Map every Project Goal crash-defense requirement to a field/invariant; prove blocked feature families remain unavailable; distinguish historical validation from prediction and candidate from order; preserve the untouched holdout; verify links and queue doctor.
