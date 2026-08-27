# Bound canonical equity catch-up by call and time budget instead of one date

## Problem
The accepted canonical lane catches up only one XKRX session per eligible occurrence, so missed occurrences keep five critical Dashboard datasets stale for multiple days after the scheduler returns.

## Evidence
Health V2 has five automation-enabled STALE canonical rows latest 2026-08-19 versus expected 2026-08-24; the current runbook explicitly says a multi-day backlog requires several occurrences and the accepted one-date advancement used two calls.

## Scope
allow:
- Change only the canonical selection/loop, typed scheduler aggregation, owning status/runbook and existing owning tests; use current accepted provider endpoints and atomic transaction boundaries.

deny:
- No date skipping, parallel provider calls, unbounded loop, new provider/fallback, retry weakening, schema/PIT change, Backtest/GUI/account/order mutation, manual live trigger, or scheduler-definition change.

## Done When
An explicit per-occurrence call/time/date budget advances consecutive oldest eligible sessions without skipping, stops before the budget, preserves per-date immutable Landing and atomic five-family acceptance, fails closed on the first non-success, reports all attempted/accepted dates and calls, and API-zero replay is idempotent; defaults remain safely bounded and current source finality/PIT limits are unchanged.

## Verify
Add deterministic tests for zero backlog, one date, multi-date success, budget exhaustion, second-date provider/validation failure preserving the first accepted date, retry/replay, ordered receipts and scheduler aggregation; run owning canonical, provider-scheduler and CLI suites with no live calls.
