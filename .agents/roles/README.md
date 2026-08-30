# Queue Role Bootstrap Contract

ruleset_version: queue-role-v1
updated_at: 2026-08-31

Every Queue-backed Agent reads only the smallest authoritative bundle needed
for its role. The fixed order is:

1. root `AGENTS.md`;
2. this common contract;
3. exactly one role document from this directory;
4. the exact Queue `TASK.md` and `HANDOFF.md`, when the role has a Task;
5. only the Status, contract, code and tests selected by that packet.

Do not preload every role document, every Queue record, all Status history, or
another Agent's transcript. Reviewer independence specifically requires review
from the immutable generation, task contract and accepted evidence rather than
the Worker's conversation or self-assessment.

## Required packet fields

Every managed launch, resume or tracked follow-up carries:

```yaml
ruleset_version: queue-role-v1
role: intake | planner | pm | lead | worker | reviewer | listener
common_role_doc: .agents/roles/README.md
role_doc: .agents/roles/<ROLE>.md
common_role_doc_sha256: <digest>
role_doc_sha256: <digest>
queue_id: <RQ-id-or-null>
task_id: <task-id-or-null>
attempt: <positive-integer-or-role-generation>
review_generation: <digest-or-null>
```

The receiving Agent records this acknowledgement before using role authority:

```yaml
rules_ack:
  ruleset_version: queue-role-v1
  role: <role>
  common_role_doc_sha256: <matching digest>
  role_doc_sha256: <matching digest>
  read_at: <timestamp>
```

A missing, mismatched or stale acknowledgement fails closed for Queue mutation,
Dispatch creation, implementation, review settlement and lifecycle decisions.
The Agent may remain read-only, report the mismatch to its owner and request a
single corrected packet. A role-document change creates a new packet generation
for later work; it does not silently rewrite the authority of an immutable
review generation already in progress.

## Shared rules

- Listener persists user intent and may update only the Goal/inbox-intent
  boundary. The Goal–Queue Reconciler emits proposals only; it never mutates
  Queue state.
- MAIN/PM is the sole Queue structure, lifecycle, and Dispatch-creation
  authority. It seals the exact immutable `TaskContract` and may route several
  pairwise-disjoint Leads.
- A Lead owns one claimed scope, checkpoints integration, fans out several
  pairwise-disjoint Workers, and preassigns each independent Reviewer. The Lead
  stays visibly informed on every review round.
- A Worker never chooses a Reviewer, changes Queue state or expands scope. It
  submits only a scoped immutable candidate through the typed mailbox to the
  preassigned Reviewer.
- A Reviewer reads the pinned candidate generation, not Worker chat or
  self-assessment. It returns typed `FIX` directly to that same Worker for at
  most two ordinary rounds while copying visibility to the Lead; `PASS` goes to
  the Lead.
- A third `FIX` is not another patch. It emits `REPLAN_REQUIRED` to both Lead
  and PM. The Lead integrates only `PASS`, checkpoints the result and informs
  PM; PM alone performs final Queue lifecycle changes.
- Planner and task-derived findings create `New` candidates only; MAIN triages.
- Listener/Watchdog is read-only outside Goal/inbox intent and may issue one
  idempotent generation/session-bound wake for one material event. It never
  creates a replacement Agent or execution path.
- Creation keys are Queue ID, role, attempt and, for review, immutable review
  generation. The first accepted attempt wins; racing duplicates are fenced.
- Ordinary repository failures remain inside the Lead lane. Escalate only the
  non-delegable boundaries in root `AGENTS.md`.

## Role map

| Role | Required role document | May mutate Queue? | Primary output |
|---|---|---:|---|
| Conversation intake | `INTAKE.md` | no | explicit Goal delta |
| Goal Planner | `PLANNER.md` | New discovery only | deduplicated candidate |
| MAIN/PM | `PM.md` | yes | triage, route and digest |
| Domain Lead | `LEAD.md` | no structural/final Queue mutation | integrated generation and PM checkpoint |
| Worker | `WORKER.md` | no | scoped candidate to preassigned Reviewer |
| Reviewer | `REVIEWER.md` | no | typed independent PASS/FIX decision |
| Listener/Watchdog | `LISTENER.md` | no | evidence-bound wake or digest |
