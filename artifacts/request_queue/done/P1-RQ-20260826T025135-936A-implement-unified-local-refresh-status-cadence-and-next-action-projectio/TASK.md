# Implement unified local refresh status, cadence, and next-action projection in the GUI

## Problem
The future refresh-status contract needs a provider-free implementation so Dashboard/Data Status users can understand and act on freshness without reading logs or scheduler internals.

## Evidence
B2ED is documentation-only; current GUI uses separate Health, watcher and current-observation states and has no unified lifecycle projection.

## Scope
allow:
- Add a strict local GUI model/service; read accepted local Health/receipt/current-projection files; render compact lifecycle status and allowlisted provider-free/local retry actions; update focused tests and GUI Status.

deny:
- No provider/API call from a read, invented next-run timestamp, direct Task Scheduler mutation, Data/account write, stale numeric promotion, source averaging/substitution, layout redesign outside the compact status surface, order/trading action, or weakening of existing typed suppression.

## Done When
Each supported GUI surface renders a compact typed status derived only from local Health, immutable scheduler receipts/state and accepted current projections: cadence kind, source as-of, last success, in-progress/partial/failure, retained-value staleness, next eligible local refresh when evidenced, and allowlisted retry capability; unsupported/unknown timestamps stay explicit; safe local retry never starts an unauthorized provider route; watcher updates coalesce; no numeric stale value is promoted.

## Verify
Run owning GUI Health and MainWindow tests offscreen with temp roots under .tmp/agents/<agent-id>; cover scheduled/manual/current-observation/unsupported surfaces, DST/session boundaries, missing/malformed/partial receipts, stale retained values, next-run unknown, in-progress and recovered states, one coalesced file update, provider-call-zero render, retry allowlist/no-op denial, responsive 1600x900 layout and clean shutdown.
