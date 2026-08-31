# Request Queue Workflow Changelog

This is an append-only record of material operating-model changes. Add new
dated entries at the end; update [WORKFLOW.md](WORKFLOW.md) separately so it
continues to describe only the current workflow.

## 2026-08-28 — Queue v2.1 operating model

- Separated durable Queue request authority from Orca execution authority:
  Queue owns routing, reservations, review policy, checkpoint, and business
  lifecycle; Orca owns supervised conversations, Dispatch attempts, terminals,
  wakeups, and completion delivery.
- Made the Domain Lead the operational owner of decomposition, scoped recovery,
  Worker/Reviewer lifecycle, accepted discovery intake, and Submit. Workers and
  Reviewers report to the Lead; MAIN retains global intake, deduplication,
  triage, priority, dependencies, routing, and cross-domain integration.
- Added managed discovery provenance for Coordinator, Lead, Goal Planner, and
  runtime-monitor intake while preserving the original User, Worker, Reviewer,
  Lead, Goal Planner, or runtime-monitor reporter role.
- Replaced the raw live-item Goal throttle with an operational low-water mark:
  a live P0, six dependency-ready Lead-routed tasks, or six untriaged discoveries
  pauses unsolicited planning, while an explicit user Goal sync may run once.
- Separated urgency from difficulty by adding complexity and risk-based Worker
  and Reviewer profiles, with current Orca model/effort mappings exposed by the
  Lead status view.
- Retained Queue v2/F3C6 migration compatibility: existing tasks may omit new
  provenance/profile fields, legacy Orca reconciliation remains readable
  telemetry, and historical Done receipts are not rewritten.

## 2026-08-29 — Durable Orca role and session recovery

- Added `PIPELINE.md` as the navigable, Doctor-recognized recovery guide for
  durable Manager and Domain Lead Runs, resumable Codex sessions, Task identity,
  per-attempt Dispatch identity, Worker cleanup, and fresh review generations.
- Kept Queue as business authority and Orca as execution authority: recovery
  reuses verified Run/Task/session provenance, reconciles stale Dispatches, and
  never treats `reset --all` or blanket process recreation as routine startup.

## 2026-08-29 — Queue v2.2 offline workflow-policy lifecycle

- Added content-addressed, versioned policy proposals bound to an accepted
  workflow-event snapshot generation, canonical event digest, event IDs, and
  acceptance-receipt digest, with deterministic offline replay and stale-
  generation/event-substitution rejection.
- Required an immutable `PASS` receipt from an identity independent of the
  implementation identity. Any candidate change creates a new proposal
  generation and invalidates prior review evidence.
- Added bounded canary criteria that are disabled by default plus explicit
  refusal, promotion, and rollback receipts. Receipt evaluation is side-effect
  free and cannot activate a scheduler, promote production, or mutate Queue or
  external state.
- Added fail-closed authority tiers: local proposal/replay work is allowed;
  account and standing-authority lifecycle actions require review and standing
  authority; broker/order, transfer/withdrawal, financial, access-control,
  secret, paid-service, and destructive-migration actions remain prohibited.
- Established Python workflow-control as the target policy authority while
  retaining Orca only as optional supervised transport. Live cutover remains a
  separate reviewed operation.

## 2026-08-29 — Queue v2.3 Python operational authority

- Promoted the project-local Python controller from target-only policy code to
  the accepted repository-local workflow authority for explicit runs: SQLite is
  machine truth, JSONL is the event ledger, and Markdown is a projection.
- Added reusable idle PM/Lead role sessions with generation-fenced assignment,
  material-event wakeups, idempotent recovery, and bounded stale-attempt retry.
- Added commit-pinned review snapshots. A fresh Reviewer remains bound to the
  submitted candidate while later writers may proceed; legacy unpinned reviews
  retain their write reservation for migration safety.
- Added ten deterministic end-to-end cycles covering normal completion,
  duplicate claims, heartbeat renewal, lease expiry, stale generation, worker
  failure, question wakeup, review isolation, replay, and Orca absence.
- Kept Orca as an optional adapter/observer. Broken Stop hooks remain disabled;
  unattended scheduler activation, broker/account actions, access-control,
  secrets, paid services, destructive changes, and external publication remain
  outside this accepted local cutover.
- Independent FIX review tightened the evidence: the crash case now retries an
  execution-scoped Worker rather than its Lead, every direct/session boundary
  receipt contributes to the no-Orca/no-production verdict, and Done receipts
  retain the reviewed generation and commit snapshot.

## 2026-08-30 — Queue v2.4 adaptive execution and single-conductor control

- Made one live MAIN/PM controller generation the sole Queue-mutation and
  Dispatch-creation owner. Listener and Watchdog roles remain read-only and may
  issue only idempotent material-event wakes, preventing the observed duplicate
  Lead and Reviewer creation race.
- Added `FAST`, `SINGLE`, and `PARALLEL` topology selection. Hierarchy is no
  longer created for its own sake: deterministic low-risk work stays with the
  Lead, and parallel Workers require at least two pairwise-disjoint ready scopes.
- Fixed the review authority path as Worker to Lead reconciliation to immutable
  generation to fresh Reviewer. `FIX` returns through the Lead, reviewers never
  assign Workers directly, and a third ordinary rework requires root-cause and
  topology re-planning.
- Ordered expensive acceptance behind decisive platform/contract preflight,
  focused tests and one complete canary. The decisive preflight repeats after
  the requested cycles; a late failure invalidates the count and reopens the
  same Queue identity at cycle 01.
- Expanded the operational digest to reconcile Goal, Queue and accepted project
  state and to report duplicate wakes, fenced attempts, bottlenecks, retries and
  the accepted rather than merely executed cycle count.

## 2026-08-30 — Queue role bootstrap v1

- Added a common role bootstrap and separate Intake, Planner, PM, Lead, Worker,
  Reviewer and Listener contracts under `.agents/roles/`.
- Managed packets now pin `queue-role-v1` plus common/role document SHA-256
  digests. Agents acknowledge the exact rules before using mutation,
  implementation, review or lifecycle authority; mismatch is read-only.
- Reviewer context is limited to the immutable generation, task contract and
  evidence rather than Worker conversation, preserving independent review.
- Durable sessions remain reusable because each new packet reloads current
  role rules instead of trusting stale conversation memory.

## 2026-08-31 — Unattended Python PM material-event runner contract

- Added a one-shot `workflow_controller.py event-run-once` entrypoint and an
  exact no-console Windows `pythonw` task definition with one-minute material
  observation, overlap exclusion, a fifteen-minute task bound around the
  ten-minute direct wake boundary, and strict readback.
- Kept app-created Codex tasks as coordination identities only. An exact
  generation/fingerprint migration launches separate CLI-owned PM/Lead
  sessions whose turns exit, proves their direct-launch ownership, and rebinds
  unsettled runner targets without settling the material generation.
- Bounded bootstrap attempts 1 through 9 to the initialization-only prompt and
  rejected malformed or out-of-range attempt events before process launch.
- Added an idempotent local runner ledger, all-target ownership preflight,
  restart/duplicate/stale fences and sanitized receipts. Unknown ownership,
  active-writer conflict and migration mismatch remain zero-effect failures;
  Orca, provider, account, broker and direct Queue-file paths remain absent.
- Live activation is still pending: the PM and routed Lead must both produce
  accepted direct wake receipts, pending generations must reach zero, and the
  installed scheduler definition must pass exact readback before Status may
  call the runner operational.

## 2026-08-31 — Uncertain-wake recovery revision

- Rejected the revision-3 live attempt: its two-minute parent task limit was
  shorter than the boundary wake and left one live writer, one uncertain
  boundary operation and one pending material generation. Those receipts stay
  preserved and are not counted as a wake.
- Limited each runner invocation to one ten-minute Codex wake and aligned the
  exact Windows definition to `IgnoreNew` plus a fifteen-minute task limit.
- Added exact hashed operation pins, OS-mutex liveness preflight, and public
  idempotent recovery for the dead writer/uncertain boundary pair. Recovery
  never terminates a process and refuses while the mutex is held.
- Added a proof-bound event-generation recovery epoch. It preserves the failed
  generation and prior attempts, requires an idle reconciled controller, and
  alone permits a fresh PM+Lead material generation.
- Operational acceptance remains pending until the old live process exits,
  exact recovery completes, a fresh generation settles PM then Lead with zero
  pending counts, and the installed definition is read back again.

## 2026-08-31 — Natural-terminal reconciliation revision

- Recorded that the rejected wake exited naturally: the prior generation is
  terminally released, the writer is idle, the exact session operation is
  failed, boundary pending is zero, and the scheduler task was removed for
  containment. This remains failure evidence, not activation evidence.
- Added public exact-pinned `reconcile-terminal` preflight and receipt creation
  for that state. It requires the latest generation-history release, failed
  session operation/error, workspace profile, idle writer, zero pending
  boundary operations and available OS mutex; no source operation or history
  row is rewritten.
- Allowed proof-bound event rotation to accept either the unchanged stranded
  recovery receipt or the new terminal-reconciliation receipt. The original
  failed attempt and generation remain immutable, and all identity, state,
  liveness, profile and replay mismatches fail before a fresh material epoch.
- Installation and live activation remain pending until recovery, a fresh
  direct PM then routed-Lead wake with pending zero, and revision-4 exact task
  Install/Check readback are independently retained.

## 2026-08-31 — App-owned Lead replacement boundary

- Added read-only exact failed-generation reconciliation status without
  constructing a writer or changing runner state.
- Added a PM-only CAS boundary for one stale app-owned active Lead. It pins the
  PM and Lead generations, current/replacement sessions, runtime and worktree,
  advances only the Lead generation, and retains hierarchy, Queue assignment,
  Dispatch and retry history.
- CLI-owned, stopped, taskless, duplicate-session, changed-generation or
  non-PM attempts fail before replacement. The Lead never replaces itself.
