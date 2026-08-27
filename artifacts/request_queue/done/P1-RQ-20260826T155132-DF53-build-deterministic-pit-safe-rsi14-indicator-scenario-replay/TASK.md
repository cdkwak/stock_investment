# Build deterministic PIT-safe RSI14 indicator scenario replay

## Problem
The accepted generic indicator/execution engines cannot produce a real oversold/overbought scenario because no production RSI feature, fixed 30/70 replay, deterministic bundle or runner exists.

## Evidence
Read-only audit confirmed no RSI module in market_features and no production call sites for indicator_study, indicator_strategy or execution outside tests; current Backtest GUI is close-proxy-only.

## Scope
allow:
- New versioned RSI14 feature, fixed 30/70 development-only replay contract/runner/CLI, atomic result bundle and owning tests/docs over existing frozen input.

deny:
- No threshold search, optimization, winner/recommendation, per-stock claim, final holdout inspection, provider/account call, Data mutation, accepted close-proxy rewrite, current production root substitution, broker/paper order, dividend/tax/capacity or live-performance claim.

## Done When
A versioned Wilder RSI14 builder defines exact seed, missing/session and T+1 usable-clock semantics over the accepted frozen KOSPI200 development rows. indicator-scenario-replay/v1 rejects any requested or observed row reaching the untouched holdout before numeric feature/outcome inspection, runs only fixed LOW30/HIGH70 study plus 30-entry/70-exit next-open scenario and exact matched-hold comparator, selects/ranks no winner, and atomically publishes a strict content-bound bundle. Two clean offline runs from the same frozen identity are byte-identical; CLI is provider-free and preserves the accepted Phase-1 artifact/manifest bytes.

## Verify
Unit tests cover Wilder seed/recurrence, gaps, insufficient warmup, T+1 clock, threshold equality, no-lookahead, holdout rejection-before-value-access, accounting/cost reconciliation, no-entry/insufficient states, deterministic serialization, atomic rollback/readback and exact identities. Integration runs twice in isolated temp, compares all bytes, proves network/provider/account functions unreachable, verifies Phase-1 and sealed holdout identities unchanged, runs py_compile and Backtest regression.
