---
name: orca-runtime-recovery
description: Verify and recover this project's Orca runtime and existing orchestration after a reboot, app repair, open timeout, or conflicting status reports. Use when resuming Manager, Lead, Worker, or Reviewer sessions, or when the user supplies `orca status --json`.
---

# Orca Runtime Recovery

Load Orca's version-matched `orchestration` or `orca-cli` guide before using
mutation commands. Recovery must preserve the existing Run, Task, Dispatch,
terminal, worktree, and Queue provenance whenever they remain live.

## Decide What Is Actually Running

Treat the newest same-host `orca status --json` observation as runtime truth.
A status result supplied by the user after an earlier agent probe supersedes
that older probe. Compare `app.running`, `runtime.state`, `runtime.reachable`,
and `runtimeId`; do not reuse an earlier runtime ID after a restart.

`orca open --json` timing out is not proof that Orca stayed off. It can be an
app-start race or a desktop-window timeout. After an open timeout, do not
relaunch repeatedly or switch to another Orca executable. Allow one bounded
startup grace period and run `orca status --json` again, with at most two
status checks over 30 seconds. If the user supplies a newer status during that
window, use it immediately instead of defending the older observation.

A child agent reporting `Could not connect to the running Orca app` may have a
sandbox or process-context connection failure while the host runtime is still
ready. Cross-check once from the controlling session or use the user's newer
same-host status before declaring a global outage.

Classify the result precisely:

- `running=true`, `state=ready`, and `reachable=true`: proceed with recovery.
- Starting or temporarily unreachable: wait only within the bounded checks.
- Still not running after the bounded checks: ask the user to start Orca once;
  do not fabricate orchestration state or create substitute agents.

## Resume Existing Orchestration

Once ready, inspect before mutating:

1. `orca terminal list --json`
2. `orca orchestration run-list --json`
3. `orca orchestration task-list --run <known_run_id> --brief --json`
4. `orca orchestration dispatch-show --task <active_task_id> --json`

Reuse compatible connected Manager, Lead, Worker, and Reviewer terminals. Do
not create a replacement merely because Windows or Orca restarted. If a prior
worker is missing, inspect its Dispatch or `worker-show` result and follow the
exact recovery action returned by Orca. Take over a Run only when the original
coordinator is demonstrably unavailable; never take over a still-live Run.

For a Queue review checkpoint, preserve the submitted review generation. A
read-only Reviewer that stopped before tests or PASS/FIX can resume or be
replaced according to the Dispatch recovery result without altering candidate
files or inventing a completion report.

Report the observed runtime ID, reused or missing roles, recovered Queue state,
and exact next action. Distinguish a verified runtime outage from a stale probe,
startup race, or child-agent-only connection failure.
