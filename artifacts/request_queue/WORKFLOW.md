# Request Queue Workflow

updated_at: 2026-08-29
snapshot: Queue v2.2

This is the concise current operating-model snapshot. The canonical protocol is
[README.md](README.md), and the generated live state is [BOARD.md](BOARD.md).
Material changes to this snapshot are recorded in
[WORKFLOW_CHANGELOG.md](WORKFLOW_CHANGELOG.md). Durable Orca role reuse and
restart reconciliation are defined in [PIPELINE.md](PIPELINE.md).

## Authority split

- The current user instruction, Project Status, domain Status, contracts, and
  runbooks define what work is authorized and what facts are accepted.
- Queue owns request identity, priority, dependencies, domain and Lead routing,
  exact write reservations, review policy, checkpoint, and business lifecycle.
- The Python workflow-control package owns deterministic offline policy replay,
  authority evaluation, and content-addressed lifecycle receipts.
- Orca may transport supervised conversations, Dispatch attempts, questions,
  escalations, and `worker_done` delivery. It is optional transport and does
  not confer policy or production authority.
- Queue Submit does not require a mirrored Orca Dispatch status. `ORCA_STATE.json`
  is a bounded locator; legacy reconciliation remains compatibility telemetry.

## Role flow

1. MAIN accepts direct user intake and managed discovery proposals, performs
   global deduplication and triage, and routes executable work to a Domain Lead.
2. A Goal Planner may create only evidenced, deduplicated New candidates. A
   runtime monitor may observe and discover within its contract. A Watchdog may
   only observe, wake, and notify the routed owner; none of these roles executes
   work or moves Queue lifecycle.
3. The routed Domain Lead claims Ready work, owns decomposition and in-scope
   recovery, selects the printed Worker/Reviewer launch profiles, supervises
   Orca workers, arranges fresh independent review when required, and submits
   the accepted result.
4. A Worker edits and tests only its dispatched scope and reports findings to
   its Lead. A Reviewer is read-only by default and returns `PASS`, `FIX`, or a
   bounded finding to the Lead.
5. Worker or Reviewer findings do not become executable work directly. The Lead
   may register a reproducible disjoint New candidate while preserving the
   original reporter role; MAIN alone triages it to Ready.

## Lifecycle

```text
managed intake -> New -> MAIN triage/route -> Ready -> Lead claim -> Active
Active -> Done                         (focused, low-risk acceptance)
Active -> Review -> Done               (independent review required)
Active -> Ready                         (safe release for later work)
New|Ready -> Waiting -> Ready           (dependency, capacity, or timed gate)
Active -> Blocked -> Ready              (true external or user-only gate)
```

Waiting, Review, and Done do not consume an Active writer lane. Review retains
the submitted write reservation. A shared lane is exclusive; other lanes allow
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

This snapshot defines the offline policy bootstrap only. Live scheduler
activation and production control-plane cutover remain outside this lifecycle
and require their own accepted operation.

## Lead execution loop

1. Read `status --lead-owner <lead>` and the exact `TASK.md` / `HANDOFF.md`.
2. Claim or resume with the current generation and preserve the task's scope,
   locks, invariants, Done When, and Verify boundary.
3. Bind the Queue task to the minimum Orca Run/Task locator when supervised
   execution needs durable reopening context.
4. Launch a scoped Worker with the printed model and effort profile, then
   handle ordinary implementation, test, provider, and tooling failures inside
   the Lead lane.
5. Re-read Done When, inspect only the scoped diff, run focused tests plus the
   smallest useful regression and Queue Doctor, and update the current HANDOFF.
6. If review is required, submit the exact generation for a fresh independent
   Reviewer; otherwise complete the normal no-review flow.
7. Submit only after accepted evidence is complete. Escalate only the exact
   unavailable external entitlement, protected-resource action, user Goal/risk
   choice, or prohibited financial/legal mutation that no safe work can avoid.

## Discovery and launch policy

- Discovery records the managed intake role and the original reporter role.
- Unsolicited Goal planning pauses for a live P0, six dependency-ready tasks
  already routed to Leads across Ready/Active/Review, or six untriaged New
  discoveries. A direct user Goal sync may always run one bounded explicit pass.
- Priority expresses urgency. Complexity and risk derive provider-independent
  Worker and Reviewer profiles; `status --lead-owner` prints the current Orca
  model/effort mapping used for fresh launches.
- Older tasks may omit v2.1 provenance/profile fields. Safe defaults and derived
  views preserve migration compatibility without rewriting historical receipts.
