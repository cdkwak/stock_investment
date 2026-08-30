# Repair corrupt retained inputs for current stock candidate discovery

## Problem
The retained local price or canonical-universe input is corrupt, so the provider-free current-stock candidate scan fails closed.

## Evidence
The accepted 2FA6 GUI receipt and real provider-free scan returned LOCAL_CANDIDATE_INPUT_CORRUPT with recovery requiring validation or regeneration of kr_equity_price_daily and kr_equity_canonical_universe_daily.

## Scope
allow:
- Only the two named retained dataset roots, the local exploratory scanner, its owning test module, and a current Data Status routing update when facts materially change.

deny:
- No provider refresh or external call; no GUI change; no scheduler activation; no broker or account mutation; no unrelated data; no protected option-wall CSV access.

## Done When
Both retained daily datasets validate under their contracts, only the corrupt partition is atomically repaired or regenerated from already-retained local evidence, valid-empty remains distinct, and the provider-free candidate scan no longer returns CORRUPT.

## Verify
Run the retained scanner before and after repair with provider calls disabled; identify the exact corrupt input; run focused exploratory-scanner and owning data validation regressions; run Queue Doctor and exact-path manifest reconciliation.
