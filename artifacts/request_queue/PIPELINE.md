# Persistent Python Agent Pipeline

updated_at: 2026-08-31
status: Python-only persistent control plane active; exact unattended `pythonw` task installed/read back; f488 terminal failure recovered, current material ledger pending-zero/idle, and Queue Doctor currently passes.

This document defines runtime persistence and restart behavior. Queue business
rules remain in [README.md](README.md); the concise role flow is
[WORKFLOW.md](WORKFLOW.md); live generated Queue state is [BOARD.md](BOARD.md).

## Runtime boundary

The repository-local Python service is the only supported lifecycle runtime.
It composes the Listener, proposal-only Goal–Queue Reconciler, one PM writer,
durable role/session registry, hierarchy contracts, typed mailboxes, direct
session wakes, sanitized event store and read-only GUI projection.

There is no console-dependent control loop and no fallback transport. Orca is
denied legacy migration/history only: inert historical locator columns may be
read during exact schema migration, but no runtime path executes Orca, checks
it for health, resumes through it, or falls back to it. Every new execution or
session receipt has `transport=direct` and `orca_used=false`.

## Persistent topology

```text
Listener -> Goal receipt / inbox intent
         -> Goal–Queue proposals -> typed PM mailbox

one PM session
  +-- Lead A session
  |     +-- Worker A1 session -> preassigned Reviewer A1 session
  |     +-- Worker A2 session -> preassigned Reviewer A2 session
  +-- Lead B session
        +-- disjoint Worker / Reviewer pairs
```

PM may own several disjoint Lead lanes. Each Lead may fan out several Workers
only when their normalized write scopes do not overlap. Every Worker, Lead and
Reviewer has a distinct stored Codex session ID, exact parent role, task and
role generation. Each Worker assignment binds its Lead-preassigned Reviewer;
the task-level Reviewer remains only a backward-compatible default for legacy
contracts. A Worker never selects or changes its Reviewer.

## Storage responsibility

| Surface | Responsibility |
|---|---|
| SQLite | Current machine state: workflow projection, PM generation, exact TaskContracts, role/session registry, parent hierarchy, candidate state, mailboxes, ACKs, wakes, review receipts and Lead checkpoints |
| Sanitized JSONL | Append-only allowlisted workflow events and their digests; no prompt, transcript, credential, direct account identity or arbitrary provider payload |
| Markdown | Human Goal/Queue contracts, decisions, current HANDOFF and accepted receipts; never runtime locking or session truth |

All persistent IDs are bounded and content-addressed where possible. SQLite
transactions use generation/session compare-and-set before durable effects.
JSONL replay verifies canonical payload bytes and event IDs. Markdown is read
as a human contract only after the current user instruction and Status route.

## User intake and reconciliation

`ListenerGateway` commits the intent, checkpoint and pending delivery receipt
before calling a sink. A new chat continues the stored listener chain and
resolves the current PM role, Codex session and generation from SQLite. If the
process stops after the sink accepts but before the Listener records the ACK,
restart replays the same message ID; the PM mailbox returns the prior accepted
message rather than creating another.

Goal reconciliation accepts only explicit user intent and a complete Queue
snapshot. Its seven outputs are:

- `CREATE`: propose a new Queue identity;
- `AMEND`: propose field changes to New/Ready/Waiting work;
- `REPLAN`: propose changed Active work for PM decision;
- `INVALIDATE_REVIEW`: identify a Review generation made stale by Goal change;
- `REOPEN`: propose reconsidering a Done receipt;
- `LINK`: link a matching existing task to the Goal item;
- `NOOP`: record that Queue already reflects the Goal.

The Reconciler never writes Queue files. PM receives a typed proposal envelope,
rechecks the current Queue generation and either performs the structural change
through the canonical Queue manager or records a decision without mutation.

## Contract, mailbox and review state machine

PM seals one exact `TaskContract` per Queue generation. It binds task, PM,
Lead, total write scope, pairwise-disjoint Worker assignments, each assignment's
Lead-preassigned independent Reviewer and launch profiles. Reusing the same
generation with different bytes fails closed.

The Lead fans out typed `WORKER_ASSIGNMENT` messages. A Worker freezes one
candidate digest and performs `submit_worker_candidate`; the controller
atomically writes the Reviewer `CANDIDATE` and linked Lead visibility. The
Worker then calls `wake_role_session` with that candidate message ID. Its
durable outbox precedes the direct resume, and routing is complete only after
the stable wake receipt returns. Wake replay resumes the same stored Reviewer
session and does not create another session or message.

```text
candidate round 1 -> FIX to same Worker + Lead visibility
candidate round 2 -> FIX to same Worker + Lead visibility
candidate round 3 -> REPLAN_REQUIRED to Lead and PM; no Worker patch message
fresh PM Queue/TaskContract generation -> new candidate -> PASS to Lead
Lead integration + checkpoint -> PM LEAD_CHECKPOINT mailbox
PM -> canonical Queue Done transition
```

Reviewer independence remains strict. The candidate envelope contains only the
pinned generation, digest, TaskContract reference and accepted evidence. The
Reviewer does not read Worker chat, scratch state or self-assessment and cannot
edit code, select a Worker or mutate Queue. `FIX` is a bounded typed decision
to the already assigned Worker, not an untracked conversation. `PASS` goes only
to Lead. Third `FIX` cannot be replayed as an ordinary patch request.

## Idempotency and fences

- Mailbox identity binds sender, recipient, type, task, Queue generation,
  parent and body digest. The row additionally binds the recipient's stored
  role generation and Codex session.
- ACK binds exact message, recipient, acknowledgement reference and generation.
  An exact duplicate returns the original settlement; a different reference or
  recipient fails. Checkpoint delivery additionally records a durable ACK
  settlement, so deleting or losing its mailbox row cannot resurrect it as
  pending.
- A Lead checkpoint delivery is keyed by checkpoint and the exact PM
  generation/session. PM rotation supersedes an older pending delivery and
  creates exactly one current-lifecycle envelope; acknowledged delivery remains
  settled.
- Candidate submission, review decision, Reviewer wake, Lead checkpoint and
  lifecycle transition use stable operation IDs. Exact replay does not add
  rows or repeat the direct boundary call. A completed wake stores and verifies
  an outbox integrity digest covering its runner receipt and bound lifecycle.
- Stale PM, Lead, Worker or Reviewer generation; changed recipient session;
  wrong Reviewer; stale Queue generation; tampered envelope; tampered receipt;
  or rebound immutable contract fails before state mutation.
- A newer role generation cannot inherit an old pending envelope silently.
  Restart resolves the current stored identity and either resumes the exact
  route or requires PM replan.

## Restart procedure

1. Acquire the one Python PM writer lease and read SQLite current state.
2. Reconcile append-only JSONL facts with the workflow projection.
3. Resolve the stored PM Codex session and resume it with its current role
   generation.
4. Resume each stored Lead, then its Workers and Reviewers, in deterministic
   hierarchy order. Do not create a replacement because a chat/process ended.
5. Replay pending typed mail, ACKs, wakes and exact controller operations by
   stable ID. Duplicates return prior receipts.
6. Fail closed on stale generation/session or changed bytes. PM decides whether
   a fresh Queue/contract generation is required.

Historical Orca Run/Task/Dispatch/terminal fields are denied migration/history
evidence only. They are never liveness proof and never participate in these
steps. Historical reset or deletion is a separate destructive operation.

## Unattended material-event runner

`scripts/maintenance/workflow_controller.py event-run-once` is the only
supported no-console runner entrypoint. The exact Windows definition invokes
that subcommand through the project `.venv\Scripts\pythonw.exe` once per minute,
uses `IgnoreNew`, a fifteen-minute task limit around its ten-minute direct wake
boundary, current-user limited privileges, and an exact ownership marker. Each
invocation wakes at most one stored role, so process timeout, process-tree reap,
and durable settlement fit inside the parent task limit. A PM+Lead generation
therefore settles across at most two successful ticks; `IgnoreNew` prevents
overlap and unchanged generations do not acquire another PM writer.

App-created Codex task IDs remain coordination identities only. They are never
passed to `codex exec resume`. `bootstrap-role` launches a separate
Python/CLI-owned persistent session whose bootstrap turn exits, then replaces
only an exact fingerprint- and generation-bound coordination registry row.
Bootstrap attempts are bounded to 1 through 9, and every allowed attempt uses
the same initialization-only prompt. A malformed or out-of-range bootstrap
event is rejected before process launch or control-plane construction.
The Codex boundary proves the session came from a completed direct CLI launch
before the event runner may target it. PM migrates before Lead. Pending runner
targets are rebound from the exact old generation/fingerprint to the new
CLI-owned identity without settling the material generation.

Before CLI migration, an exact stale app-owned active Lead may be replaced only
through the PM-owned `replace-app-coordination-lead` boundary. It CAS-pins the
current PM generation plus the Lead generation, current and replacement session
identities, app runtime and worktree; then it increments only the Lead generation
while retaining its parent, Queue task, Dispatch and retry history. CLI-owned,
stopped, taskless, duplicate-session or changed rows fail before mutation. A Lead
never invokes its own replacement.

Unknown ownership, an active writer, changed generation/session, mismatched
migration receipt, missing parent, or incomplete pending ledger fails closed
before a wake. The runner validates every PM/Lead target before the first
resume, retains only session fingerprints and receipt digests in its local
SQLite ledger, and keeps interrupted generations pending for exact replay.
`status` publishes only hashed boundary and generation evidence. If a dead
writer still owns one pending operation, `recover-stranded --preflight-only`
uses the OS mutex as its liveness oracle and exact public recovery fences that
pending operation and writer without terminating a process. If the original
one-shot instead exits naturally and already leaves writer idle, generation
history terminal, the exact session operation failed, and boundary pending
zero, `reconcile-terminal --preflight-only` verifies the prior owner,
generation, operation, request, workspace profile, error code, release reason,
latest history row and available OS mutex. Its mutation form writes only a
sanitized reconciliation receipt; it does not rewrite the failed operation or
generation. `event-recover-generation` accepts exactly one of those durable
public proofs plus the exact failed-attempt digest, requires the controller to
remain idle with boundary pending zero, preserves the old generation as
recovered, and rotates a fresh material epoch. Pin, terminal-state, profile,
liveness or replay mismatch has zero wake, session and Queue effects.
Live activation is incomplete until the exact PM and routed Lead have both
produced direct, `orca_used=false` wake receipts, the pending count is zero,
and the installed task passes exact readback.

## Queue lifecycle and recovery

Canonical business lifecycle is `new -> ready -> active -> review -> done`.
Waiting and Blocked are explicit non-writer states. PM alone invokes structural
and final transitions; Listener, Reconciler, Lead, Worker and Reviewer output
typed evidence or proposals only.

Recovery is event-driven and bounded. A material completion, question,
escalation, stale lease or Queue generation change may enqueue one stable wake.
No healthy role is continuously polled. Missing or unreadable Queue state,
unknown session identity and transport failure fail closed; none authorizes a
replacement agent or second writer.

The failed revision-3 PT2M generation remains immutable evidence. Its exact
public terminal-reconciliation proof recovered material generation `f4885fa...`;
the installed task then produced durable direct PM and Lead wake receipts for
`a2f370...` and later material generations. Queue heartbeat timestamps are not
material inputs, so a lease-only metadata refresh cannot create another wake
generation. New Queue discovery or triage count changes are material and may
start a fresh bounded PM/Lead wake; public runner/controller status, rather
than a stale document pin, is authoritative for its live state.

## Read-only Korean Qt projection

`python -m stock_data.gui.operations_dashboard` opens the Korean dark
operations view. `MonitoringSnapshotAdapter` reads SQLite, sanitized JSONL and
the canonical Queue projection without write access. It displays only observed
PM/Lead/Worker/Reviewer roles, tasks, Queue counts, freshness, warnings and
recent events. Missing sources become explicit warnings; the adapter does not
invent owners, workers, decisions or numeric state.

The GUI exposes one refresh action. It has no controls for Queue mutation,
agent creation, lifecycle settlement, provider access, broker actions or file
edits.

## Operator evidence

The provider-free integration contract is
`tests/integration/pipelines/test_persistent_agent_control_plane.py`. Focused
Queue and cutover regressions are
`tests/unit/orchestration/test_request_queue.py` and
`tests/integration/pipelines/test_workflow_controller_cutover.py`. Queue Doctor
must return `OK` after documentation or protocol changes.
