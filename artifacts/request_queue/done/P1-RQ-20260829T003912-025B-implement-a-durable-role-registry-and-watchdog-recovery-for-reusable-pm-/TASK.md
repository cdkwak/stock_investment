# Implement a durable role registry and watchdog recovery for reusable PM and Lead sessions

## Problem
Durable PM and Lead identity, lease, heartbeat, and stale-session recovery remain manual guidance with no executable fail-closed owner.

## Evidence
PIPELINE and the recovered D1E1 receipts document terminal_missing, operator_close, stale Dispatch, session reuse, and exact retry provenance; Steward found no role-registry or watchdog implementation.

## Scope
allow:
- Only the listed role-registry, watchdog, simulator, and offline test files.

deny:
- No process killing outside the fake simulator, no reset --all, no automatic Queue completion or dispatch, no transcripts, secrets, account or provider actions, scheduler changes, or protected option-wall CSV access.

## Done When
An identifier-only registry and read-only watchdog use the accepted control-plane state to record PM and Lead leases and heartbeats, detect stalled or missing work, mark recovery_required, and propose exact same-Task retry without moving Queue state; a fake-agent simulator proves atomic double-claim exclusion, kill/restart recovery, stale heartbeat handling, and idempotent retry provenance.

## Verify
Run tests/unit/orchestration/test_workflow_recovery.py plus owning state tests and Queue Doctor; replay kill, restart, double-claim, terminal loss, and stale-generation fixtures offline.
