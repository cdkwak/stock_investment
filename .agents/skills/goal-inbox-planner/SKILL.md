---
name: goal-inbox-planner
description: Compare the user-owned Project Goal with current Project and domain Status, then register bounded, evidenced, non-duplicate discoveries in this repository's request-queue Inbox. Use for goal-driven planning passes, not implementation, triage, or execution.
---
# Goal Inbox Planner

Run one idempotent planning pass. Repeated invocations provide ongoing Inbox
maintenance; do not create a long-running loop inside one agent turn.

## Route

1. Read `AGENTS.md` and apply its temporary-workspace and authority rules.
2. Read `docs/project/PROJECT_GOAL.md`.
   - If its status is `AWAITING_USER_DEFINITION`, the Goal is blank, or its
     meaning is ambiguous, stop without changing the queue and report that the
     user must define or clarify it.
   - Never invent, broaden, optimize, or edit the Goal.
3. Read `docs/project/PROJECT_STATUS.md`, then exactly its selected domain
   Status. Treat these Status documents as current truth and execution
   boundaries. Do not reproduce permission-only phase, activation, retry-zero,
   manual-only, or fresh-approval gates that standing authority supersedes.
4. Read `.agents/skills/request-queue/SKILL.md`,
   `artifacts/request_queue/README.md`, and `artifacts/request_queue/BOARD.md`.
   Resume no task and claim nothing; this skill is planning-only.
5. Compare the Goal with current facts, real external blockers, prohibited
   mutations, next useful action, and existing queue coverage. Open only the exact
   Status-linked evidence needed to prove one candidate gap. Do not scan
   archives, Done records, the full documentation tree, or the full Data tree.
6. For each genuinely new gap, call `scripts/request_queue.py discover` with a
   stable fingerprint, reproducible evidence, bounded suspected scope, and an
   honest priority hint. Use `PROJECT_GOAL` as `--source-task` when no existing
   request is the direct source.

## Discovery gate

Create an Inbox discovery only when all are true:

- closing the gap materially advances the exact user-owned Goal;
- current Status does not already mark it completed or superseded;
- the issue is concrete enough to state a symptom, evidence, impact, suspected
  scope, and reproducible verification;
- no Active, Review, Ready, New, Blocked, Done, or completed-index entry already
  represents the same work; and
- the discovery preserves missing semantic, credential, entitlement, rights,
  or user-policy evidence without turning researchable uncertainty or a
  permission-only phase label into a work stop.

The non-delegable exclusions in `AGENTS.md` and explicit external
rights/entitlement/user-policy requirements justify an authorization-based
deferment. Ordinary cross-domain work, public or existing-credential API calls,
semantic/PIT/finality research, and implementation are standing-authorized;
describe the concrete missing work instead of `DEFERRED_NOT_AUTHORIZED` or a
fresh-approval request.

When provider meaning, licensing, credentials, entitlement, or user policy is
unknown, the discovery may ask to establish that evidence or decision. It must
not assert an undocumented value, select a paid service, request a secret, or
bundle later implementation behind the unresolved gate.

Use the same stable fingerprint for the same underlying gap across planning
passes. A duplicate rejection is a safe no-op; do not vary wording to bypass
deduplication. Prefer a small number of well-supported discoveries over filling
the Inbox with roadmap aspirations or splitting one problem into artificial
subtasks.

## Boundary

Goal alignment is not execution authorization. Do not triage New to Ready,
claim or implement tasks, edit Status, call providers, mutate Data or account
state, change schedulers, or take external actions. Report discoveries created,
duplicates skipped, deferred gates recorded, and the exact reason for any no-op
planning pass.
