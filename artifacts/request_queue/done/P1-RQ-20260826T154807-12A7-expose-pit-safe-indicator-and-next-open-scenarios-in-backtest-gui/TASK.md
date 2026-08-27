# Expose PIT-safe indicator and next-open scenarios in Backtest GUI

## Problem
Accepted PIT-safe indicator, threshold-band, matched-hold and next-open engines exist only as Python boundaries; the Backtest GUI remains close-proxy-only, so the user's core indicator-discovery workflow is not usable.

## Evidence
BACKTEST_STATUS records predefined-indicator-study/v1, predefined-threshold-band/v1, threshold-band-matched-hold/v1 and historical-next-open/v1 as implemented and tested. Current BacktestPage/BacktestReplayService contain no consumer for those contract versions.

## Scope
allow:
- One versioned read-only scenario adapter and Backtest GUI panel over already accepted pure offline engines; explicit predefined scenarios only; documentation truth.

deny:
- No threshold optimization, winner ranking, recommendation, final holdout inspection, provider/account call, Data mutation, accepted close-proxy rewrite, broker/paper order, or feature/label calculation in GUI widgets.

## Done When
A new provider-free service accepts only exact typed development inputs and one predefined indicator/band scenario, rejects any row reaching the untouched holdout before inspecting values, invokes the accepted engines without parameter search, and returns immutable result views. Backtest GUI exposes a clearly labelled development-only scenario panel with conditional summaries, signal coverage, next-open ledger metrics and matched-hold differences; it never selects/ranks a winner, presents a recommendation, calls a provider, reads account data, or replaces the accepted close-proxy bundle.

## Verify
Owning service tests cover exact identity, threshold order, no-lookahead usable clocks, holdout rejection-before-value-access, typed insufficient/no-entry states, matched clock/cost reconciliation, immutability and API0. GUI tests cover responsive background execution, failure preservation, labels/no winner or recommendation, 1600x900 layout and clean shutdown. Run Backtest unit regression, focused GUI suite, py_compile and provider-call-zero smoke without opening the holdout.
