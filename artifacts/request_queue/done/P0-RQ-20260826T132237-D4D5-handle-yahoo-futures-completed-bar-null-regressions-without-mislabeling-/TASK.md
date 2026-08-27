# Handle Yahoo futures completed-bar null regressions without mislabeling live quote rows

## Problem
Six Yahoo futures routes can return null OHLC at the newest completed grid timestamp while an irregular live quote row is populated, leaving prior current projections stale after a nominally successful occurrence.

## Evidence
Retained 12:32 versus 13:02 Landing metadata shows prior completed bars followed by null completed-grid OHLC; regularMarketTime quote rows are not contracted completed bars and cannot substitute.

## Scope
allow:
- Change current-bar selection/outcome classification, owning tests/release gate, and current Data runbook/status only.

deny:
- No live Yahoo call, history/canonical/backtest write, forward fill, live quote substitution, provider averaging, GUI implementation, or unrelated route change.

## Done When
Null/partial newest completed-bar rows never erase or mislabel prior values; live quote rows never become completed 30m bars; each route emits an explicit preserved or failed terminal outcome, release readiness reconciles it, and newer fully numeric completed bars still advance.

## Verify
Use retained-shape/provider-free fixtures to cover six futures routes, null/partial OHLC, irregular quote timestamp, prior absent/present, typed outcome counts, last-pointer preservation, API zero replay, and release-readiness acceptance without numeric logging.
