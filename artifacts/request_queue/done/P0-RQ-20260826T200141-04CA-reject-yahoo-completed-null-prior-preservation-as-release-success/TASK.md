# Reject Yahoo completed-null prior preservation as release success

## Problem
A newer eligible completed-grid OHLC regression is an occurrence failure even when prior display bytes are preserved; counting it as accepted PASS hides freshness degradation.

## Evidence
Provider-free review preserved prior projection/comparison/session bytes but current NULL_30M_BAR_PRIOR_VALUE_PRESERVED contributes to PASS and release readiness accepts the receipt.

## Scope
allow:
- Adjust Yahoo current outcome aggregation, exact release gate, owning tests, Data Status, and active Yahoo runbook.

deny:
- No live Yahoo call, production projection/Landing/history/canonical/Backtest write, GUI redesign, forward fill, live quote substitution, or unrelated change.

## Done When
Six futures null/partial/nonfinite newest completed-grid outcomes preserve exact prior bytes yet count as typed failures; scheduler status is PARTIAL_FAILURE, release rejects forged PASS, and newer numeric bars still advance.

## Verify
Provider-free six-route prior-present/absent fixtures, byte identities, accepted/preserved/failed exact-int counts, release spoof rejection, owning unit and GUI integration release tests; no live API or production data mutation.
