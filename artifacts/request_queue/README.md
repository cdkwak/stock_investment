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
material workflow changes. Use [PIPELINE.md](PIPELINE.md) for the Python control
plane, durable role/session reuse, and optional Orca reconciliation guidance. This README remains the
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

- Exactly one live MAIN/PM controller generation is the Queue mutation and
  Dispatch-creation owner. A Listener or Watchdog may read state, detect a
  material event, and issue one idempotent wake to that owner; it must not
  create a competing Lead, Worker, Reviewer, claim, or lifecycle transition.
  A duplicate wake is a zero-effect receipt, not a second execution path.
- MAIN COORDINATOR accepts unstructured user requests and managed discovery
  proposals, assigns `domain` and `lead_owner`, manages global
  priority/dependencies and cross-domain conflicts, and escalates only true
  user/external gates. It does not manually reproduce every finding, normally
  dispatch workers, or review domain-local changes.
- A Domain Lead reads only its routed work with `status --lead-owner <lead>`,
  claims a package with `--role lead`, decomposes it, manages directly launched
  workers and an independent reviewer, and submits the accepted result. Orca
  may be used as an optional transport, but is not required. Queue files are the
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

### Adaptive execution topology

Choose the smallest topology that can satisfy Done When:

| Mode | Use when | Execution path |
|---|---|---|
| `FAST` | deterministic, low-risk, single-scope work | Lead implements directly, runs focused checks, and uses the normal no-review flow unless policy requires review |
| `SINGLE` | one coherent implementation scope or one consequential writer | one Lead with at most one Worker, followed by a fresh Reviewer when required |
| `PARALLEL` | at least two dependency-ready, pairwise-disjoint scopes with no shared resource lock | one Lead coordinates multiple scoped Workers; each returned scope is reconciled before integration and review |

Do not create a Worker merely to preserve hierarchy, and do not create
multiple Leads or Workers for a sequential task. MAIN records the selected
mode during triage; the Lead may safely collapse `SINGLE` to direct Lead work,
but may expand to `PARALLEL` only after proving disjoint ownership and resource
locks.

### Role bootstrap and acknowledgement

Every Queue-backed Agent follows `.agents/roles/README.md` and exactly one matching role
document. Managed launch and resume packets pin `queue-role-v1`, the common-role
document digest and the selected role-document digest. The Agent records the
matching `rules_ack` before using role authority. Missing, stale or mismatched
acknowledgement permits read-only diagnosis only and must not be repaired by
creating a parallel Agent.

The packet and acknowledgement are part of Task/Dispatch provenance. Reviewer
packets additionally bind the immutable review generation. A ruleset update
applies to later packet generations and never silently changes the contract of
an already-pinned immutable review.

### Role permissions and escalation

- MAIN owns global Queue intake, triage, routing, priority, dependencies and
  cross-domain integration. It does not take over ordinary domain debugging.
- A Domain Lead owns decomposition, model profile selection, worker and
  reviewer lifecycle, in-scope rework, and accepted discovery intake for its
  routed packages.
- Workers may edit and test only their exact dispatched scope. Reviewers are
  read-only by default and return `PASS`, `FIX`, or a finding to the Lead.
  Workers never select or instruct their Reviewer, and Reviewers never assign
  rework directly to a Worker.
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
Production writers may use the current worktree or a managed child worktree with exact
non-overlapping write scopes. Only the routed Lead uses the canonical manager
in the main worktree. Child worktrees may inspect their supplied task packet,
but must not run a worktree-local queue manager or mutate the central Queue.
The Lead collects worker and reviewer results before Submit.

A legacy task in `review` reserves its exact normalized `write_scope`. A Submit
that includes `--snapshot-commit <full-HEAD>` instead pins the reviewed candidate
to a clean, immutable Git commit and releases the live write reservation; a
later writer may continue while the Reviewer reads only that commit. Review
acceptance must repeat the identical commit and current `review_generation`.
Review tasks consume no Active writer lane. Unpinned legacy reviews keep their
reservation until `review-pass` or `review-fail` for migration safety.

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
- `REVIEW.md`: immutable review generation and HANDOFF digest, plus an optional
  full `snapshot_commit`. A pinned commit is the reviewed candidate identity and
  allows later disjoint or overlapping writers to proceed without invalidating it.
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
Reviewer profiles and the Lead maps them to direct-runner launch preferences:

| Profile | Current model / effort | Intended work |
|---|---|---|
| `fast` | `gpt-5.6-luna / medium` | small, deterministic, low-risk work |
| `balanced` | `gpt-5.6-terra / medium` | ordinary single-domain work |
| `strong` | `gpt-5.6-sol / high` | complex or high-risk implementation |
| `critical` | `gpt-5.6-sol / xhigh` | critical work and strongest review |

The Worker uses the maximum tier implied by complexity and risk. Required
independent review uses one tier stronger, capped at `critical`, and always a
fresh session. These are launch profiles rather than durable provider
contracts; update the adapter mapping when model availability changes without
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
python scripts/request_queue.py submit <TASK_ID> --owner <lead> --expected-generation <generation> --review --reviewer <agent> --snapshot-commit <full-HEAD> ...
python scripts/request_queue.py review-pass <TASK_ID> --reviewer <agent> --review-generation <token> --snapshot-commit <same-full-HEAD> --decision-basis <basis>
python scripts/request_queue.py review-fail <TASK_ID> --reviewer <agent> --review-generation <token> --snapshot-commit <same-full-HEAD> --decision-basis <basis> --next <action>
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

## Python execution, event wakeup, and optional Orca transport

The Python control plane is the repository-local workflow authority:

- Queue owns the work request: identity, priority, domain/Lead routing,
  dependencies, write reservations, review policy, current HANDOFF and business
  lifecycle.
- Python owns accepted lifecycle events, idempotent direct runner receipts,
  durable role generations, leases, heartbeat recovery, and material-event
  wakeups. Queue Submit does not require a mirrored Orca Dispatch status.
- `status --lead-owner` exposes the derived Worker and Reviewer model/effort
  plan. The Lead supplies those values to the selected direct adapter when
  launching fresh agents; a model launch failure is ordinary Lead-owned recovery.
- `orca-bind` stores only the Orca Run/Task locator needed to reopen execution
  context. The older `orca-reconcile` command remains a compatibility telemetry
  path; normal Lead operation does not depend on it and its phase is not a
  Queue completion or review gate.
- The Python Watchdog reads Active Queue routing plus the injected direct health
  boundary. It wakes only on `worker_done`, question, escalation, material Queue
  transition, or a proved stale lease. It does not require a Stop hook, poll a
  terminal every minute, or treat a spinner as health evidence.
- Orca can still carry supervised messages and expose terminal telemetry. Its
  IDs are compatibility locators only and it never becomes required for claim,
  wakeup, review, completion, or recovery.
- `REVIEW.md` binds the submitted Queue generation, HANDOFF snapshot, and when
  supplied the exact immutable Git commit. The
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

For a task that requires independent review, the authority path is fixed:

```text
Worker -> Lead reconciliation -> immutable generation -> fresh Reviewer
Reviewer FIX -> Lead -> bounded Worker/Lead rework -> new generation
Reviewer PASS -> Lead/MAIN -> scoped commit and Done receipt
Reviewer finding outside scope -> Lead evidence check -> Queue New candidate
```

The same defect family may receive at most two ordinary `FIX` generations.
Before a third implementation retry, the Lead must stop patching symptoms,
restate the root cause and acceptance oracle, and ask MAIN to re-plan topology
or scope. This remains an internal project decision unless the next action is a
true user-only gate.

Before Submit, re-read Done When, inspect the scoped diff, run the exact focused
test and the smallest useful regression, and update HANDOFF. Expensive repeated
acceptance follows this order:

1. reproduce the failure or establish a positive control;
2. run the decisive platform/contract preflight that could invalidate the run;
3. run focused owning tests;
4. run one complete canary cycle;
5. run the requested repeated cycles;
6. repeat the decisive preflight and reconcile every retained receipt.

A late decisive failure invalidates the repeated-cycle acceptance count. Reopen
the same Queue identity, preserve the failed evidence, fix and review it, then
restart the count at cycle 01; never create a duplicate defect or pretend the
earlier cycles remain accepted. A Done receipt is:

```text
result:
changed:
verified:
completed_at:
```

Never store long reasoning, complete diffs, full logs, or conversation history
in the queue.

Each reviewed Submit creates a new `review_generation` in `REVIEW.md`. Prefer a
clean full-HEAD `--snapshot-commit`; the independent reviewer must inspect that
exact commit and repeat it when deciding. Stale generations or substituted
commits are rejected without mutation. Use `reopen` only when newer concrete evidence proves
a Done receipt invalid. It removes the stale receipt, records the reason, and
returns the task to Ready for a fresh claim and review generation.

On PASS, `RESULT.md` retains `review_generation`, `snapshot_commit` (when
pinned), and `reviewed_by` after the transient `REVIEW.md` is removed. This
keeps the Done receipt independently auditable instead of discarding the
identity of the reviewed candidate.
