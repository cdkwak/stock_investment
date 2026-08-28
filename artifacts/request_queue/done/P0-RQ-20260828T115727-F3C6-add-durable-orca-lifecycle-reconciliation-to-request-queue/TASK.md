# Add durable Orca lifecycle reconciliation to Request Queue

## Problem
Extend the existing atomic Request Queue with a minimal durable Orca lifecycle companion and deterministic readback reconciliation, without making Agent memory the lifecycle source of truth.

## Evidence
The prior Orca ledger proves dispatch ctx_7eed7fbd190c failed with agent_prompt_stalled while the coordinating Lead could remain waiting; current Queue task records contain no Orca Run/Task/Dispatch pointer or project-level waiting/next-action state.

## Scope
allow:
- Modify only the four declared files; reuse current Queue atomic writers, validation, Doctor, Board derivation, and state transitions; add one compact per-task ORCA_STATE.json companion and bounded CLI operations/reconciliation.

deny:
- Do not store heartbeat streams, transcripts, polling/event history, terminal output, secrets, direct account identifiers, or Agent memory; do not change financial/data semantics, existing task lifecycles, Board format, or mutate unrelated Queue tasks; no master production implementation outside canonical Queue transitions/integration.

## Done When
Existing inbox-active-review-done and active-blocked lifecycles remain compatible; active tasks can durably persist Queue identity plus Orca run/lead/worker/reviewer pointers, phase, waiting_for, next_action, last_transition, and candidate_commit; SSD alone restores waiting_for and next_action; restart-safe reconciliation maps WAITING_FOR_WORKER_DONE plus FAILED worker readback to RECOVERY_REQUIRED and stops waiting; heartbeat/transcript/poll history is never copied; focused tests and Queue Doctor pass; Fresh Reviewer passes; master is clean.

## Verify
.venv/Scripts/python.exe -m pytest tests/unit/orchestration/test_request_queue.py -q -p no:cacheprovider with bounded basetemp; .venv/Scripts/python.exe scripts/request_queue.py doctor; git diff --check; Fresh Orca Reviewer PASS.
