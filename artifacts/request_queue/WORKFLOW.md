# Request Queue Workflow

updated_at: 2026-08-31
snapshot: Queue v2.4 + persistent Python control plane

This is the concise current operating model. The canonical Queue protocol is
[README.md](README.md), live generated state is [BOARD.md](BOARD.md), and the
persistent runtime contract is [PIPELINE.md](PIPELINE.md).

## Authority and durable flow

```text
User chat
  -> Listener (durable Goal/inbox intent only)
  -> Goal–Queue Reconciler (proposal only)
  -> PM typed mailbox
  -> PM Queue decision + immutable TaskContract
  -> one of several disjoint Leads
  -> several disjoint Workers, each with a Lead-preassigned Reviewer
  -> Worker candidate -> Reviewer + idempotent Reviewer wake
  -> Reviewer FIX -> same Worker (ordinary rounds 1 and 2; Lead visible)
  -> third FIX -> REPLAN_REQUIRED to Lead + PM
  -> Reviewer PASS -> Lead integration/checkpoint -> PM
  -> PM-only Queue final lifecycle transition
```

- Listener is the durable user entry point. It may persist the explicit Goal
  receipt and inbox intent and send one typed, generation/session-bound PM
  envelope. It never changes Queue structure or creates agents.
- `GoalQueueReconciler` compares an explicit Goal revision with a complete
  Queue snapshot and emits only `CREATE`, `AMEND`, `REPLAN`,
  `INVALIDATE_REVIEW`, `REOPEN`, `LINK`, or `NOOP` proposals. A proposal is not
  a Queue mutation; PM revalidates the current generation before deciding it.
- PM alone owns Queue structure, `new -> ready -> active -> review -> done`
  lifecycle changes, Lead routing and final settlement. Every exact
  `TaskContract` is immutable for its Queue generation.
- PM may route multiple pairwise-disjoint Leads. Each Lead may fan out multiple
  pairwise-disjoint Workers and preassigns each independent Reviewer before
  candidate work begins. Each Worker-to-Reviewer pair is sealed in the
  immutable TaskContract generation, and different Worker pairs have distinct
  Reviewer Codex sessions.
- Worker owns only its declared candidate scope. Reviewer owns only `PASS` or
  `FIX` for the pinned candidate generation. Lead owns fan-out, visibility,
  checkpoints and integration. No lower role mutates Queue state.

## Review loop

Review independence is content-based and identity-based. Reviewer reads the
immutable candidate digest, exact contract and accepted verification evidence;
it does not read Worker chat, private scratch state or self-assessment.

1. Worker freezes a candidate and sends it directly to the preassigned
   Reviewer. Candidate submission atomically records the Reviewer envelope and
   linked Lead visibility. The Worker then calls the public, message-bound
   Reviewer wake for the stored Codex session before reporting routing
   complete.
2. Reviewer `FIX` goes directly to that same Worker. The Lead receives linked
   visibility and may checkpoint. The Worker may submit a new immutable
   candidate for ordinary rounds one and two.
3. A third `FIX` is not a patch request. The controller records
   `REPLAN_REQUIRED` for both Lead and PM and fences further candidate work in
   that generation.
4. PM may seal a fresh Queue/contract generation after re-planning. Reviewer
   `PASS` goes to the Lead; the Lead integrates, records an idempotent
   checkpoint and informs PM. PM alone changes final Queue lifecycle.

Workers never select or replace Reviewers. Reviewers never edit code, choose a
Worker, alter Queue state, or reuse a prior decision after candidate bytes or
generation change.

## Persistence, replay and restart

- SQLite is current machine state for workflow facts, role/session identity,
  hierarchy contracts, mailboxes, acknowledgements, review receipts and
  control generations.
- Sanitized JSONL is append-only event evidence. It contains allowlisted
  workflow facts and digests, never prompts, transcripts, credentials, direct
  account identifiers or arbitrary payloads.
- Markdown holds human contracts, decisions, current handoffs and Queue
  receipts. It is not a competing runtime database.
- Every role stores its Codex session ID. A new chat or Python process resumes
  the same PM, then its Leads, Workers and Reviewers, when role key, parent,
  task, session and generation still match.
- Mailbox IDs, ACK references, candidate/review receipts, wakes and checkpoints
  are content-addressed. Exact replay returns the same result; a rebound body,
  recipient, session, generation, candidate or ACK fails closed.
- Lead checkpoint delivery is bound to the exact PM role lifecycle. PM
  generation/session rotation supersedes the older pending delivery and
  redelivers exactly once to the current PM. The durable ACK settlement remains
  authoritative if a mailbox row is lost, so an acknowledged checkpoint cannot
  reappear as pending.
- Completed wake outbox rows carry an integrity digest over role, generation,
  session, message, provenance, state and direct-runner receipt. A forged wake
  receipt fails closed instead of becoming a replay result.
- A generation fence covers PM, Lead, Worker and Reviewer actions. Duplicate
  wake, delivery, ACK or restart replay does not create another session,
  message, task or transition.

## Lifecycle and topology

```text
New -> Ready -> Active -> Review -> Done
                 |          |
                 +-> Ready <-+   safe release/replan only by PM
New|Ready -> Waiting -> Ready
Active -> Blocked -> Ready       true external or user-only gate
```

`FAST` is direct Lead work for deterministic low-risk scope. `SINGLE` uses one
coherent Worker scope. `PARALLEL` requires at least two pairwise-disjoint Worker
scopes; overlap collapses to `SINGLE`. PM may keep up to three pairwise-disjoint
Lead lanes. Waiting, Review and Done do not consume an Active writer lane.

Lead and PM actions use the exact current Queue and role generations. Stale
sessions, substituted Reviewers, overlapping Worker scopes and changed
`TaskContract` bytes fail before a durable side effect.

## Python-only runtime

The supported runtime is Python only. It uses the repository-local workflow
controller, injected direct Codex boundary, durable session runner and the
canonical Queue manager. There is no console-dependent scheduler path and no
transport fallback.

Orca appears only in denied legacy migration/history: historical locator fields
may be retained as inert identifiers while old receipts are migrated or read,
but they are never executed, queried for liveness, used for wake/recovery, or
accepted as fallback authority. New runtime receipts require
`transport=direct` and `orca_used=false`.

The standalone Korean dark operations dashboard is a read-only Qt projection
of the same SQLite/JSONL/Queue facts. It shows PM, Leads, Workers, Reviewers,
Queue counts, freshness, warnings and recent events without inventing roles or
tasks. Its only button refreshes the projection; it has no Queue, lifecycle,
provider, broker or filesystem mutation control.

## Discovery and escalation

Task-derived findings remain two-stage. Worker or Reviewer reports bounded
evidence to the Lead; Lead validates a disjoint New candidate; PM performs
global deduplication and triage. No discovery is executable until PM moves it
to Ready.

Ordinary repository failures remain within the Lead lane. Escalate only an
unavailable external entitlement, rejected protected-resource action, required
user Goal/risk choice, or prohibited financial/legal mutation for which no safe
independent work remains.
