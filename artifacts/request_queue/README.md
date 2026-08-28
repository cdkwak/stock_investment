# Request Queue

This file is the canonical request-queue protocol. The queue is a shared current
state control plane, not a knowledge archive. Git holds history; tests hold
behavioral evidence.

## Start and authority

For queue-backed work, read `AGENTS.md`, this file, and `BOARD.md`. Resume an
owned Active task before considering Ready work. Otherwise claim only from
`inbox/ready/` with `scripts/request_queue.py`. Read only that task's `TASK.md`
and `HANDOFF.md`, then follow the normal repository authority route.

Do not normally scan Done, Review, Blocked, old conversations, scheduler
history, or unrelated repository areas. Queue records never override the
current user request, Status, contracts, runbooks, permissions, or frozen-domain
boundaries.

Current user and Status authority also supersede permission-only clauses inside
an older `TASK.md` or `HANDOFF.md`. Keep its write scope, resource locks,
identity/schema, source-rights/entitlement, finality/PIT, secret-handling,
promotion, non-mutation, and Done/Verify invariants,
but do not wait for a fresh phase/Data/Lead/live-call approval already covered
by standing authorization. Checkpoint the supersession and proceed; if a
necessary production edit is outside `write_scope`, register a disjoint follow-up
instead of blocking the work that remains in scope.

## Layout

```text
artifacts/request_queue/
  README.md
  BOARD.md
  inbox/
    new/
    ready/
  waiting/
  active/
  review/
  blocked/
  done/
  templates/
```

Deleted legacy requests are not restored. Done contains only this new batch.

## Roles and writer mode

- MAIN COORDINATOR accepts unstructured user requests, registers discoveries,
  assigns `domain` and `lead_owner`, manages global priority/dependencies and
  cross-domain conflicts, and escalates only true user/external gates. It does
  not normally dispatch workers or review domain-local changes.
- A Domain Lead claims a Queue work package with `--role lead`, retains its raw
  claim capability, performs detailed triage, binds an Orca Run/Task, manages
  Dispatch attempts, workers and an independent reviewer, and submits the
  accepted candidate. The Queue is the Lead's external memory; the session is
  replaceable.
- A worker implements or reproduces only the Dispatch scope. An Orca worker
  never receives a Queue claim token and never mutates the central Queue.
- A verifier reviews independently and is read-only unless explicitly assigned
  as the sole writer. Domain-local review stays with the Domain Lead; MAIN sees
  only cross-domain interfaces, architecture, schema/contracts, breaking or
  destructive changes.
- A non-Lead production worker may own at most one Active task. A Lead may
  supervise up to `lead_wip_limit: 3` Active work packages, still subject to the
  global writer limit, exact scope reservations and resource locks.
- An owner label is not a claimant credential. Every claim returns a one-time
  `claim_token`; retain it in that client only and pass it to every Active-state
  mutation. `META.json` stores only its SHA-256 digest.
- `writer_limit: 3`: up to three non-shared production writers may be Active
  when their exact write scopes and resource locks are pairwise disjoint,
  including multiple writers in the same domain lane.
- A `shared` lane is exclusive: it cannot run beside another production writer.
- Older tasks without `writer_lane` are classified from `write_scope`; mixed or
  unclassified scopes fail closed into `shared`.
- Multiple read-only investigations may run in parallel without consuming a
  writer slot.

Queue metadata mutations remain globally serialized by `.queue-mutation.lock`;
contending commands wait briefly and then fail closed rather than mutating in
parallel.
Do not let two agents edit overlapping files or hold the same resource lock.
Production writers may use an Orca-managed current or child worktree with exact
non-overlapping write scopes. Only the token-owning Lead uses the canonical
manager in the main worktree. Child worktrees may inspect Queue receipts
read-only, but must not run a worktree-local queue manager or mutate the central
Queue. The Lead reconciles the candidate commit and diff digest before Submit.

A task in `review` reserves its exact normalized `write_scope`. A later claim
whose production scope overlaps that reservation fails before either task is
mutated, although Review tasks do not consume an Active writer lane or the
three-writer limit. `review-pass` and `review-fail` release the reservation by
moving the submitted generation out of Review. Disjoint claims remain governed
by the normal lane and resource-lock rules.

## Task identity and files

Directory names use `P0|P1|P2-RQ-YYYYMMDDTHHMMSS-ABCD-slug`. Priority order is
P0, P1, P2; within a priority the coordinator considers dependencies and then
`created_at`.

Each task directory contains:

- `META.json`: identity, state, assignment, fingerprint, dependencies,
  write scope, review flag, lease, and heartbeat. The manager owns mutations.
- `TASK.md`: short Problem, Evidence, allow/deny Scope, Done When, and Verify.
  Keep it stable after triage.
- `HANDOFF.md`: the current checkpoint only, at most about 40 lines. Replace
  stale state; never append a timeline or shell transcript.
- `RESULT.md`: four-line Done receipt when complete.
- `BLOCKED.md`: three-line external gate only while Blocked.
- `WAITING.md`: bounded reason, resume condition, next check and waiting start;
  present only while Waiting.
- `ORCA_STATE.json`: optional bounded current execution projection. It contains
  only Queue/Run/Task/Dispatch identity, attempt, phase, `waiting_for`,
  `next_action`, candidate commit/diff binding and reconciliation timestamps.
  It never contains transcripts, prompts, terminal handles or heartbeat logs.

Queue v2 keeps the existing stable directory name
`P0|P1|P2-RQ-...-slug`. Domain routing lives in `META.json` as `domain` and
`lead_owner`; renaming live directories merely to expose a Lead prefix is not
part of the protocol.

`write_scope` uses exact repository-relative POSIX paths: no absolute paths,
parent traversal, or globs.

`writer_lane` is `gui`, `data`, `backtest`, or `shared`. Up to
`writer_limit: 3` Active writers may share a non-shared lane when their exact
write scopes and resource locks are pairwise disjoint; `shared` remains
exclusive. `resource_locks` are normalized
tokens for non-file resources such as `qt-process`, `live-data-root`, a shared
registry, or a status document. Any matching token blocks a concurrent claim
even when file scopes differ. Scope comparison also treats a declared directory
and a contained path as overlapping.

## States and transitions

- `inbox/new`: evidenced, untriaged discoveries. Not executable work.
- `waiting`: dependency, capacity, cooldown, scheduled observation or other
  non-human condition is not ready yet. Waiting holds no writer lane.
- `inbox/ready`: deduplicated, reproduced, scoped, prioritized work. Workers
  or Leads claim only here.
- `active`: currently owned work with lease, heartbeat, and exact next action.
- `review`: implementation and automated checks are complete. `agent_review`
  is for independent technical review; `user_review` is only for a genuinely
  user-only product judgment or excluded/risky external mutation, never generic
  permission to continue ordinary work.
- `blocked`: no safe in-scope action remains and completion requires an
  unavailable required secret/entitlement, a rejected
  administrator/protected-resource escalation, or a user-only action outside
  standing authority.
- `done`: accepted result for this batch; keep the receipt short.

Normal low-risk flow: `new -> ready -> active -> done`.

Scheduling flow: `new|ready -> waiting -> ready`. A Lead may also safely return
owned work with `active -> ready` via `release`; this is not a failure or a
human gate.

Reviewed flow: `ready -> active -> review -> done`; failed review returns to
Ready, never Blocked.

Require independent review for high-risk work, GUI-visible financial semantics,
account/privacy boundaries, scheduler definitions, canonical promotion, and
shared contracts. Do not set `review_required` for low-risk documentation
alignment, compact Status/view maintenance, or other deterministic changes when
focused automated validation fully covers the Done When.

External-gate flow: `active -> blocked -> ready` after the resume condition.

A task whose next required observation is tied to a future publication date,
market session, cooldown, capacity or dependency must not occupy an Active
writer lane while merely waiting. Checkpoint completed evidence, move it to
Waiting with the exact resume condition and optional next-check timestamp,
release the lane, and claim a different dependency-ready task.

Code bugs, implementation errors, fixture problems, failing tests, semantic or
PIT uncertainty, provider failures, retry design, stale documentation, disabled
schedulers, public/existing-credential calls, and ordinary escalation requests
remain Active work. Before blocking, exhaust safe in-scope research,
implementation, bounded retry/fallback, offline fixtures, and independent task
branches; record the precise attempted alternatives and external resume
condition. A subclaim or promotion gate never blocks unrelated task work.
Review or Blocked never stops unrelated Ready work.

## Queue manager

All state changes use:

```powershell
python scripts/request_queue.py status --compact
python scripts/request_queue.py discover ...
python scripts/request_queue.py triage <TASK_ID> ... --writer-lane gui --resource-lock qt-process
python scripts/request_queue.py route <TASK_ID> --domain data --lead-owner data_lead --next <action>
python scripts/request_queue.py wait <TASK_ID> --reason <reason> --resume-condition <condition>
python scripts/request_queue.py resume-waiting <TASK_ID> --decision-basis <basis> --next <action>
python scripts/request_queue.py claim <TASK_ID> --owner <lead> --role lead --domain data --next <action>
python scripts/request_queue.py checkpoint <TASK_ID> --owner <agent> --claim-token <token> ...
python scripts/request_queue.py release <TASK_ID> --owner <lead> --claim-token <token> --reason <reason> --next <action>
python scripts/request_queue.py orca-bind <TASK_ID> --owner <lead> --claim-token <token> --run-id <run> --orca-task-id <task> --next-action <action>
python scripts/request_queue.py orca-reconcile <TASK_ID> --owner <lead> --claim-token <token> --dispatch-id <dispatch> --attempt 1 --observed-status running --next-action <action>
python scripts/request_queue.py submit <TASK_ID> --owner <agent> --claim-token <token> ...
python scripts/request_queue.py review-pass <TASK_ID> --reviewer <agent> --review-generation <token> --decision-basis <basis>
python scripts/request_queue.py review-fail <TASK_ID> --reviewer <agent> --review-generation <token> --decision-basis <basis> --next <action>
python scripts/request_queue.py reopen <TASK_ID> --reason <evidence> --next <action>
python scripts/request_queue.py block <TASK_ID> ...
python scripts/request_queue.py unblock <TASK_ID> --next <action>
python scripts/request_queue.py compact-done <TASK_ID> --dry-run
python scripts/request_queue.py compact-done <TASK_ID>
python scripts/request_queue.py prune-done --keep 20 --dry-run
python scripts/request_queue.py prune-done --keep 20
python scripts/request_queue.py doctor
```

Claim uses a same-volume atomic move and one claimant wins. The manager always
creates the capability with its cryptographically secure random generator;
callers cannot provide or select it. Output includes the task ID and the
unpredictable raw `claim_token`; the token is never written
to queue records. `checkpoint`, `submit`, and `block` require both the owner and
the exact token, so two clients that reuse an owner cannot mutate the same
Active task. Active leases default to 60 minutes; checkpoint renews the
heartbeat. Expiry is reported by Doctor but never causes automatic takeover.
The coordinator decides recovery.

If a token-backed Active task loses its raw client capability, no recovery may
occur while its exact lease is live. After expiry, the coordinator may use
  `recover-expired-active` only by pinning the retained owner, `updated_at`
  generation, exact `lease_until`, and stored claim digest, and by recording a
  bounded decision basis and next action. Before considering those pins, the
  command validates the complete canonical Active receipt: exact task files,
  META identity/types/assignment/scope/timestamp ordering, owner-assignee
  equality, canonical HANDOFF generation and a complete TASK contract. A
  missing assignment or heartbeat, mismatched HANDOFF generation, noncanonical
  receipt, or other malformed Active state is rejected byte-identically. The
  command clears the unavailable
  capability and assignment, moves the exact task to Ready, and regenerates the
Board. It does not generate or accept a replacement token. The recovered task
must win an ordinary fresh `claim`, whose manager-generated raw capability is
again printed once and never stored. A live lease, mismatched pin, malformed
state, concurrent/stale recovery, or non-Active task fails before mutation.
This is audited exceptional recovery, not automatic takeover and not legacy
claim adoption.

An Active task created before the token protocol fails closed. Its original
claimant may perform exactly one mutation with `--claim-token <new-token>` and
`--adopt-legacy-claim`; after all command validation succeeds, the globally
serialized mutation stores only the digest and every later contender must
present that winning token. A rejected adoption attempt leaves the legacy
receipt unchanged. Never use adoption
to take over an expired or uncertain task; the coordinator must first resolve
ownership and precondition evidence.

Always invoke the canonical manager from the main worktree. The manager rejects
execution when its own path differs from the central queue's main-worktree
`scripts/request_queue.py`. This is a defense for current clients, not a way to
control historical scripts that predate the check. Therefore linked worktrees
are not authorized queue clients: before enabling domain-parallel writers, the
coordinator must verify that no linked worktree contains a local
`scripts/request_queue.py`. Claim and Doctor enforce that operational gate for
the canonical manager. If one exists, parallel claims remain disabled until that
stale client is removed or the queue is cut over to a separately versioned
central control plane.

Doctor is read-only by default. It checks task/fingerprint uniqueness, required
files, directory/META state, assignments, leases, the global writer limit plus
scope/resource-lock conflicts,
dependencies/cycles, Review/Blocked/Done fields, and BOARD digest. Only
`doctor --fix-board` may safely regenerate the derived board; it does not move or
rewrite tasks.

## Orca execution and Watchdog reconciliation

Queue and Orca do not duplicate authority:

- Queue owns request identity, domain/Lead routing, priority, dependencies,
  claim capability, write reservations, review policy and business lifecycle.
- Orca owns Run/Task/Dispatch execution, terminal/process state and individual
  attempts.
- `ORCA_STATE.json` is only the bounded join between them. `orca-bind` records
  Run and Task identity. `orca-reconcile` records one observed Dispatch status
  and maps it deterministically to `DISPATCHED`,
  `WAITING_FOR_WORKER_DONE`, `SUCCEEDED`, or `RECOVERY_REQUIRED`.

The same reconciliation observation is byte-idempotent. A replacement Dispatch
is accepted only from `RECOVERY_REQUIRED` with exactly one attempt increment;
stale or identity-changing observations fail before mutation. `completed`
requires both `candidate_commit` and a SHA-256 `diff_digest`.

A Python Watchdog or scheduler may watch a durable Orca-event outbox and invoke
the canonical `orca-reconcile` command as an edge trigger. It may retry the same
event freely, but it must never edit Queue files, infer completion from a
terminal disappearing, move state directories, or become a second source of
truth. On restart, the Lead reads Orca Dispatch state and replays reconciliation;
Queue state remains recoverable without Watchdog memory.

For an Orca-backed reviewed Submit, `REVIEW.md` binds
`dispatch_id + candidate_commit + diff_digest + review_generation`. Review pass
or fail rejects any mismatch. A failed review returns the Queue task to Ready
and marks the Orca projection `RECOVERY_REQUIRED`; accepted Done receipts remove
the live projection because Git and the short result receipt retain completed
history.

`compact-done` is an explicit, one-task operation. It first requires a clean
Doctor result, an unreferenced valid Done receipt, and proof that every file in
that Done directory is tracked and clean in Git. It atomically adds the exact
task ID, legacy ID, fingerprint, completion timestamp, bounded result summary,
directory name, and receipt digest to `COMPLETED_INDEX.json`, then removes only
that exact Done directory. The index is digest-protected and continues to reserve
IDs/fingerprints and satisfy later dependencies. Malformed, live-referenced,
untracked, dirty, partially indexed, or deletion-failed records are rejected
without losing the original Done bytes. `--dry-run` performs all eligibility
checks but writes neither the index, Done directory, nor BOARD.
without a durable partial mutation. Never delete Done directories directly.

`prune-done --keep 20` applies the same checks and atomic index preservation to
all but the 20 newest Done receipts. Done tasks still referenced by a live task
are retained in addition to that newest set, so the on-disk count can
temporarily exceed the requested retention count. The command preflights every
candidate before writing, writes the completed index once, and restores every
selected receipt plus the prior index and BOARD if any deletion fails. Use
`--dry-run` first. The default retention count is 20; never bypass this command
with direct directory deletion.

`--allow-untracked` is a destructive bootstrap exception that requires explicit
user approval. It accepts only wholly untracked Done directories containing the
exact four regular receipt files, preserves their bounded identity/result
summary and full receipt digest in `COMPLETED_INDEX.json`, and then applies the
same atomic rollback boundary. It must not bypass dirty or partially tracked
records. Omit it during normal operation so Git remains the detailed history.

## Discovery and completion

Register a separate discovery only with reproducible evidence and a unique
fingerprint. Do not silently expand Active scope. Duplicate evidence belongs on
the existing task; a non-P0 discovery does not interrupt current work.

Fingerprint equality is only the first duplicate check. Before triage and again
before claim, compare the task's Done When and exact write scope with Done
receipts completed after the task was created. If newer work already satisfies
the request, validate that result instead of repeating implementation or review;
record the duplicate/satisfaction evidence on the existing task. When any P0 is
live, or `Ready + Active + Review >= 6`, suspend unsolicited Goal/Inbox
discovery passes while continuing explicit user intake and execution of the
existing backlog.

Before Submit, re-read Done When, inspect the scoped diff, run the exact focused
test and the smallest useful regression, and update HANDOFF. A Done receipt is:

```text
result:
changed:
verified:
completed_at:
```

Never store long reasoning, complete diffs, full logs, or conversation history
in the queue.

Each reviewed Submit creates a new `review_generation` in `REVIEW.md`. The
independent reviewer must inspect that exact generation; stale generations are
rejected without mutation. Use `reopen` only when newer concrete evidence proves
a Done receipt invalid. It removes the stale receipt, records the reason, and
returns the task to Ready for a fresh claim and review generation.
