# Make the UR246 occurrence last pointer concurrency-safe and non-rewinding

## Problem
Concurrent UR246 occurrences can pass separate read/compare checks and publish an older receipt after a newer one, rewinding the verified last pointer.

## Evidence
A provider-free two-thread barrier probe reproduced newer-then-older publication with non_rewind_preserved=false; production has atomic replace but no shared compare-and-swap lock.

## Scope
allow:
- Implement the smallest cross-process-safe pointer publication primitive and owning provider-free regressions; update only scoped Data docs.

deny:
- No provider call, scheduler registration, production data/state mutation, ACL change, GUI/backtest change, or unrelated cleanup.

## Done When
All last-pointer read/validate/compare/write work is serialized across threads/processes; a newer scheduled_for can never be replaced by older/equal-conflicting evidence; deterministic barrier regression preserves the newer exact receipt and interruption leaves prior pointer valid.

## Verify
Run the owning UR246 CLI suite including two-worker newer-first publication, stale/equal/conflicting pointer cases, atomic failure preservation, and provider/API-zero assertions; update Data Status/runbook without live calls.
