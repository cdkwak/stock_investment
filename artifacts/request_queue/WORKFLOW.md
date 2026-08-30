# Request Queue Workflow

updated_at: 2026-08-30
snapshot: Queue v2.4 + role bootstrap v1

This is the concise current operating-model snapshot. The canonical protocol is
[README.md](README.md), and the generated live state is [BOARD.md](BOARD.md).
Material changes to this snapshot are recorded in
[WORKFLOW_CHANGELOG.md](WORKFLOW_CHANGELOG.md). Python execution, durable role
reuse, and optional Orca reconciliation are defined in [PIPELINE.md](PIPELINE.md).

## Authority split

- The current user instruction, Project Status, domain Status, contracts, and
  runbooks define what work is authorized and what facts are accepted.
- Queue owns request identity, priority, dependencies, domain and Lead routing,
  exact write reservations, review policy, checkpoint, and business lifecycle.
- The Python workflow-control package owns repository-local execution policy,
  accepted event order, role generations, leases, event-driven wakeup, bounded
  recovery, deterministic replay, and content-addressed lifecycle receipts.
- Orca may transport supervised conversations, Dispatch attempts, questions,
  escalations, and `worker_done` delivery. It is optional transport and does
  not confer policy or production authority.
- One live MAIN/PM controller generation owns Queue mutations and Dispatch
  creation. Listeners and Watchdogs are read-only observers that may issue one
  idempotent wake; they never become a second coordinator.
- Queue Submit does not require a mirrored Orca Dispatch status. `ORCA_STATE.json`
  is a bounded locator; legacy reconciliation remains compatibility telemetry.

## Role flow

Every managed role first reads `.agents/roles/README.md` plus its one role document and
records a digest-matching `queue-role-v1` acknowledgement. Until then it is
read-only and cannot use Queue or lifecycle authority.

1. The conversation intake agent records explicit user intent in the Project
   Goal and its change history; it does not implement or dispatch the work.
2. A Goal Planner compares changed Goal sections with current Status, Queue and
   accepted project evidence, then creates only evidenced, deduplicated New
   candidates.
3. MAIN/PM is the single mutation owner. It triages New, records priority,
   dependencies, exact scope, risk, review policy and topology, then moves only
   executable work to Ready.
4. MAIN/PM reads Ready and routes it to one durable Domain Lead. A Listener or
   Watchdog may detect and wake this owner but cannot create another execution
   path.
5. The Lead claims Ready into Active and chooses the smallest valid topology:
   `FAST`, `SINGLE`, or `PARALLEL`.
6. A Worker, when needed, edits and tests only its dispatched scope and reports
   files, checks, findings and remaining risk to the Lead.
7. The Lead reconciles the report with the actual scoped diff and acceptance
   boundary, then freezes an immutable review generation.
8. A fresh read-only Reviewer returns `PASS`, `FIX`, or a bounded finding to the
   Lead. The Reviewer does not direct a Worker or mutate Queue state.
9. `FIX` returns through the Lead for bounded rework and a new generation;
   `PASS` permits the scoped commit and Done receipt. Out-of-scope findings are
   proposed as New and require MAIN triage.
10. PM releases or safely reuses settled sessions and records bottlenecks,
    retries, current owner and the Goal-to-project reconciliation in the digest.

## Lifecycle

```text
managed intake -> New -> MAIN triage/route -> Ready -> Lead claim -> Active
Active -> Done                         (focused, low-risk acceptance)
Active -> Review -> Done               (independent review required)
Active -> Ready                         (safe release for later work)
New|Ready -> Waiting -> Ready           (dependency, capacity, or timed gate)
Active -> Blocked -> Ready              (true external or user-only gate)
```

Waiting, Review, and Done do not consume an Active writer lane. A commit-pinned
Review releases its live write reservation while remaining bound to the exact
immutable candidate; unpinned legacy Review retains the reservation. A shared lane is exclusive; other lanes allow
up to three pairwise-disjoint writers subject to exact scope and resource-lock
checks. Lead mutations use the current Queue generation, and non-Lead claims use
the one-time raw claim capability, so stale writers fail closed.

## Policy proposal lifecycle

```text
accepted workflow-event snapshot -> versioned proposal -> offline replay
-> immutable independent review -> explicitly enabled bounded canary
-> promotion or rollback decision receipt -> separate authorized cutover
```

- Proposal generations are content digests bound to an accepted snapshot
  generation, canonical event digest, event IDs, and acceptance-receipt digest.
- Replay is order-independent, offline, and deterministic. Stale generations
  and event substitution fail before a receipt is issued.
- The Reviewer identity must differ from the implementation identity. Any
  proposal change creates a new generation and invalidates prior review.
- Canary criteria are bounded and disabled by default. Disabled, incomplete,
  over-bound, or failure-limit canaries return explicit refusal receipts.
- Promotion and rollback are explicit, content-addressed decisions with
  `production_mutated=false`; neither automatically changes current policy,
  Queue state, a scheduler, or an external system.
- Authority evaluation allows local replay/proposal work, requires review plus
  standing authority for account reads and lifecycle actions, and always
  refuses broker/order, transfer/withdrawal, financial mutation,
  access-control, secret, paid-service, and destructive-migration actions.
  Unknown and unreviewed standing-authority actions fail closed.

The repository-local control plane is accepted for explicit and canary-driven
operation. An unattended live scheduler, broker/account integration, and any
external production cutover remain disabled and require their own accepted operation.

## Lead execution loop

1. Read `status --lead-owner <lead>` and the exact `TASK.md` / `HANDOFF.md`.
2. Claim or resume with the current generation and preserve the task's scope,
   locks, invariants, Done When, and Verify boundary.
3. Reuse the durable Python role/session identity. Add an Orca locator only when
   optional supervised transport needs durable reopening context.
4. Launch a scoped Worker through the injected direct boundary with the printed model and effort profile, then
   handle ordinary implementation, test, provider, and tooling failures inside
   the Lead lane.
5. Re-read Done When, inspect only the scoped diff, run focused tests plus the
   smallest useful regression and Queue Doctor, and update the current HANDOFF.
6. If review is required, pin a clean full-HEAD commit and submit that exact
   generation to a fresh independent Reviewer; later writers need not wait on it.
7. Submit only after accepted evidence is complete. Escalate only the exact
   unavailable external entitlement, protected-resource action, user Goal/risk
   choice, or prohibited financial/legal mutation that no safe work can avoid.

## Topology, review, and expensive acceptance

- `FAST`: the Lead handles deterministic low-risk work directly. Do not create
  a Worker or Reviewer unless the task policy requires one.
- `SINGLE`: one coherent writer scope uses one Lead and at most one Worker.
- `PARALLEL`: use multiple Workers only for at least two pairwise-disjoint
  dependency-ready scopes. Shared locks or sequential dependencies collapse the
  task back to `SINGLE`.
- Dispatch creation is idempotent by Queue Task, role, attempt and, for review,
  review generation. Before creating anything, reconcile live and historical
  attempts; the first accepted attempt wins and later races are fenced.
- Reviewer `FIX` always returns to the Lead. After two ordinary FIX generations,
  MAIN and the Lead must re-plan root cause, oracle or scope before another
  implementation attempt.
- A costly repeated acceptance run starts only after the decisive preflight,
  focused checks and one complete canary pass. The same decisive preflight runs
  again after the repeated cycles; a failure invalidates the count and reopens
  the same Queue item.

## Discovery and launch policy

- Discovery records the managed intake role and the original reporter role.
- Unsolicited Goal planning pauses for a live P0, six dependency-ready tasks
  already routed to Leads across Ready/Active/Review, or six untriaged New
  discoveries. A direct user Goal sync may always run one bounded explicit pass.
- Priority expresses urgency. Complexity and risk derive provider-independent
  Worker and Reviewer profiles; `status --lead-owner` prints the current
  model/effort mapping used by the selected direct adapter.
- Older tasks may omit v2.1 provenance/profile fields. Safe defaults and derived
  views preserve migration compatibility without rewriting historical receipts.
