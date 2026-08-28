# Request Queue

This file is the canonical request-queue protocol. The queue is a shared current
state control plane, not a knowledge archive. Git holds history; tests hold
behavioral evidence.

## Start and authority

For queue-backed work, read `AGENTS.md`, this file, and `BOARD.md`. Resume an
owned Active task before considering Ready work. Otherwise claim only from
`inbox/ready/` with `scripts/request_queue.py`. Read only that task's `TASK.md`
and `HANDOFF.md`, then follow the normal repository authority route.

Use [WORKFLOW.md](WORKFLOW.md) for the concise current operating-model snapshot
and [WORKFLOW_CHANGELOG.md](WORKFLOW_CHANGELOG.md) for the append-only record of
material workflow changes. Use [PIPELINE.md](PIPELINE.md) for durable Orca role,
session-reuse, and restart-reconciliation guidance. This README remains the
canonical protocol and `BOARD.md` remains the generated current-state view.

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
  WORKFLOW.md
  WORKFLOW_CHANGELOG.md
  PIPELINE.md
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

- MAIN COORDINATOR accepts unstructured user requests and managed discovery
  proposals, assigns `domain` and `lead_owner`, manages global
  priority/dependencies and cross-domain conflicts, and escalates only true
  user/external gates. It does not manually reproduce every finding, normally
  dispatch workers, or review domain-local changes.
- A Domain Lead reads only its routed work with `status --lead-owner <lead>`,
  claims a package with `--role lead`, decomposes it, manages Orca workers and
  an independent reviewer, and submits the accepted result. Queue files are the
  Lead's external memory, so a replacement Lead session resumes by reading the
  same routed worklist and HANDOFF rather than reconstructing chat history.
- A worker implements or reproduces only the scope sent by its Lead. Workers
  and reviewers do not scan or mutate the global Queue and never receive a
  Queue claim token.
- A verifier reviews independently and is read-only unless explicitly assigned
  as the sole writer. Domain-local review stays with the Domain Lead; MAIN sees
  only cross-domain interfaces, architecture, schema/contracts, breaking or
  destructive changes.
- Worker and Reviewer findings travel to their Domain Lead. The Lead either
  returns an in-scope defect for rework or registers a disjoint, evidenced
  candidate in `inbox/new` with `--intake-role lead` and the original
  `--reported-by-role`. A finding is not executable until Coordinator triage.
- A Goal Planner may read the user-owned Goal plus current Status and create
  only deduplicated `inbox/new` candidates with `--intake-role goal_planner`.
  It cannot edit the Goal, triage, route, claim, execute, review, or update
  Status.
- A non-Lead production worker may own at most one Active task. A Lead may
  supervise up to `lead_wip_limit: 3` Active work packages, still subject to the
  global writer limit, exact scope reservations and resource locks.
- A routed Lead may resume its own Active packages without a session-local
  token: it reads the current `generation` from `status --lead-owner` and sends
  that value as `--expected-generation`. The Queue manager serializes mutations
  and accepts only the current generation, so a stale duplicate Lead cannot
  overwrite newer Queue state. Non-Lead direct claims retain the one-time
  `claim_token` safeguard.
- `writer_limit: 3`: up to three non-shared production writers may be Active
  when their exact write scopes and resource locks are pairwise disjoint,
  including multiple writers in the same domain lane.
- A `shared` lane is exclusive: it cannot run beside another production writer.
- Older tasks without `writer_lane` are classified from `write_scope`; mixed or
  unclassified scopes fail closed into `shared`.
- Multiple read-only investigations may run in parallel without consuming a
  writer slot.

### Role permissions and escalation

- MAIN owns global Queue intake, triage, routing, priority, dependencies and
  cross-domain integration. It does not take over ordinary domain debugging.
- A Domain Lead owns decomposition, model profile selection, Orca worker and
  reviewer lifecycle, in-scope rework, and accepted discovery intake for its
  routed packages.
- Workers may edit and test only their exact dispatched scope. Reviewers are
  read-only by default and return `PASS`, `FIX`, or a finding to the Lead.
- Goal Planners and Watchdogs are read-mostly: the Planner may only create New
  candidates, while the Watchdog may only observe, wake and notify the routed
  Lead or MAIN. Neither may move Queue lifecycle state.
- Repository bugs, failing tests, semantics/PIT/finality investigation,
  provider errors, scheduler repair, public or existing-credential API work,
  ordinary tool approval, and safe bounded retries stay inside the Lead lane.
  They do not become user escalations or block the whole task.
- Escalate to the user only when the exact next action is genuinely
  non-delegable: real or paper-broker order submission/amendment/cancellation,
  transfer/withdrawal, purchase/subscription or binding agreement, a required
  user Goal/risk-policy choice, or an unavailable credential/entitlement after
  every safe independent action is exhausted. Never ask for permission to
  bypass access controls or disclose secrets; those actions remain prohibited.

Queue metadata mutations remain globally serialized by `.queue-mutation.lock`;
contending commands wait briefly and then fail closed rather than mutating in
parallel.
Do not let two agents edit overlapping files or hold the same resource lock.
Production writers may use an Orca-managed current or child worktree with exact
non-overlapping write scopes. Only the routed Lead uses the canonical manager
in the main worktree. Child worktrees may inspect their supplied task packet,
but must not run a worktree-local queue manager or mutate the central Queue.
The Lead collects worker and reviewer results before Submit.

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
  write scope, review flag, lease, heartbeat, optional discovery provenance,
  complexity and model profiles. The manager owns mutations. Older tasks may
  omit the added profile/provenance fields; routing applies safe defaults.
- `TASK.md`: short Problem, Evidence, allow/deny Scope, Done When, and Verify.
  Keep it stable after triage.
- `HANDOFF.md`: the current checkpoint only, at most about 40 lines. Replace
  stale state; never append a timeline or shell transcript.
- `RESULT.md`: four-line Done receipt when complete.
- `BLOCKED.md`: three-line external gate only while Blocked.
- `WAITING.md`: bounded reason, resume condition, next check and waiting start;
  present only while Waiting.
- `ORCA_STATE.json`: optional Orca Run/Task link. New operation treats it as a
  locator, not as Queue lifecycle authority. Existing v1 dispatch fields remain
  readable for compatibility but are not required for Queue completion. It
  never contains transcripts, prompts, terminal handles or heartbeat logs.

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

`priority` is urgency, not difficulty. Triage records `complexity` separately
as `small`, `standard`, `complex`, or `critical`; `risk` is `low`, `medium`,
`high`, or `critical`. The Queue derives provider-independent Worker and
Reviewer profiles and the Lead maps them to current Orca launch preferences:

| Profile | Current Orca model / effort | Intended work |
|---|---|---|
| `fast` | `gpt-5.6-luna / medium` | small, deterministic, low-risk work |
| `balanced` | `gpt-5.6-terra / medium` | ordinary single-domain work |
| `strong` | `gpt-5.6-sol / high` | complex or high-risk implementation |
| `critical` | `gpt-5.6-sol / xhigh` | critical work and strongest review |

The Worker uses the maximum tier implied by complexity and risk. Required
independent review uses one tier stronger, capped at `critical`, and always a
fresh session. These are launch profiles rather than durable provider
contracts; update the mapping when Orca model availability changes without
rewriting historical tasks.

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
python scripts/request_queue.py status --lead-owner data_lead
python scripts/request_queue.py discover ... --intake-role lead --reported-by-role reviewer
python scripts/request_queue.py triage <TASK_ID> ... --complexity complex --risk high --writer-lane gui --resource-lock qt-process
python scripts/request_queue.py route <TASK_ID> --domain data --lead-owner data_lead --complexity standard --next <action>
python scripts/request_queue.py wait <TASK_ID> --reason <reason> --resume-condition <condition>
python scripts/request_queue.py resume-waiting <TASK_ID> --decision-basis <basis> --next <action>
python scripts/request_queue.py claim <TASK_ID> --owner <lead> --role lead --domain data --next <action>
python scripts/request_queue.py checkpoint <TASK_ID> --owner <lead> --expected-generation <generation> ...
python scripts/request_queue.py release <TASK_ID> --owner <lead> --expected-generation <generation> --reason <reason> --next <action>
python scripts/request_queue.py orca-bind <TASK_ID> --owner <lead> --expected-generation <generation> --run-id <run> --orca-task-id <task> --next-action <action>
python scripts/request_queue.py submit <TASK_ID> --owner <lead> --expected-generation <generation> ...
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

Claim uses a same-volume atomic move and one claimant wins. A routed package can
be claimed only by its exact Lead using the retained domain. Lead mutations
authorize against that stable owner/route plus the current Queue generation,
so a replacement Lead session can resume immediately while stale sessions fail
closed. Non-Lead claims still return an
unpredictable raw `claim_token`; only its SHA-256 digest is stored. Active
leases default to 60 minutes and checkpoint renews the heartbeat. Expiry is
reported by Doctor but never causes automatic takeover by a different owner.

If a non-Lead token-backed Active task loses its raw client capability, no
different-owner recovery may occur while its exact lease is live. After expiry,
the coordinator may use
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

## Orca execution and Watchdog

Queue and Orca do not duplicate authority:

- Queue owns the work request: identity, priority, domain/Lead routing,
  dependencies, write reservations, review policy, current HANDOFF and business
  lifecycle.
- Orca owns execution: Lead/worker/reviewer conversations, Dispatch attempts,
  terminal/process state and wakeups. Queue Submit does not require a mirrored
  Orca Dispatch status.
- `status --lead-owner` exposes the derived Worker and Reviewer model/effort
  plan. The Lead supplies those values to `orca orchestration worker-start`
  when launching fresh agents; a model launch failure is ordinary Lead-owned
  recovery, not a user escalation.
- `orca-bind` stores only the Orca Run/Task locator needed to reopen execution
  context. The older `orca-reconcile` command remains a compatibility telemetry
  path; normal Lead operation does not depend on it and its phase is not a
  Queue completion or review gate.
- A Python Watchdog reads Active Queue routing plus live Orca state, then wakes
  the routed Lead or alerts MAIN when work is stalled. It does not edit Queue
  files, move lifecycle directories, infer completion from a vanished terminal,
  or become another source of truth.
- Before `release`, `wait`, or `block`, the Lead stops or settles workers in
  Orca. The Queue manager removes the stale Orca link while moving the package;
  it does not mirror every worker transition to prove that settlement.
- `REVIEW.md` binds the submitted Queue generation and HANDOFF snapshot. The
  Domain Reviewer checks the supplied candidate; MAIN receives only important
  cross-domain or architectural escalations.

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

Discovery is two-stage. A Worker or Reviewer reports a finding to its Lead; it
does not decide priority, routing, model, or execution. The Lead validates the
evidence and registers only a disjoint candidate in `inbox/new`, preserving the
original reporter role. MAIN then performs global deduplication, priority,
dependencies, write reservations, domain/Lead routing, complexity/risk and
review policy before `new -> ready`. Urgent findings use Orca escalation to the
Lead, but only MAIN may promote them into executable Queue work.

Fingerprint equality is only the first duplicate check. Before triage and again
before claim, compare the task's Done When and exact write scope with Done
receipts completed after the task was created. If newer work already satisfies
the request, validate that result instead of repeating implementation or review;
record the duplicate/satisfaction evidence on the existing task. Unsolicited
Goal planning uses an operational low-water mark, not a raw Queue count. Pause
it when a P0 is Ready/Active/Review, when six dependency-ready tasks are already
routed to Leads across Ready/Active/Review, or when six untriaged discoveries
already await Coordinator work. Unassigned Ready tasks, unresolved dependencies
and timed Waiting work do not pretend to be runnable Lead buffer. An explicit
user-triggered Goal update may run one bounded planning pass with
`--explicit-user-trigger`; explicit user intake and task-derived defects always
remain allowed.

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
