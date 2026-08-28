---
name: request-queue
description: Operate this repository's file-backed request queue when work must be resumed, claimed, checkpointed, submitted, discovered, reviewed, blocked, or integrity-checked. Use only for work tracked under artifacts/request_queue, not ordinary unqueued tasks.
---

# Request Queue Operator

Read `artifacts/request_queue/README.md` completely; it alone defines states,
ordering, task records, and manager commands. Read `BOARD.md` next. Neither file
overrides repository authority, permissions, or the user's scope.

## Operate

Use `scripts/request_queue.py` for every state or metadata change. Run `doctor`
before recovering questionable state. Resume an owned Active task first;
otherwise claim one dependency-ready task. Never preload unrelated Done,
Review, or Blocked records.

Use a stable owner label unique to this live agent/session. Concurrent agents
must not all claim as `codex_root`; choose distinct labels such as
`codex_data_a` and `codex_data_b`, retain each returned raw claim token only in
that session, and still own at most one Active task per agent.

After claim, route through `repo-router`, then read only the task's named
authority, source, and tests. Respect its exact `write_scope`, writer lane, and
resource locks. Up to the README-defined writer limit may work concurrently
when scopes and locks are disjoint, including multiple tasks in one domain
lane; the `shared` lane remains exclusive. Read-only agents may supply
independent evidence without consuming a writer slot.

Workers and Reviewers report findings to their routed Lead; they never promote
or schedule new Queue work. The Lead may register a reproducible disjoint
candidate in `inbox/new` with the original `--reported-by-role`. MAIN alone
triages it to Ready and assigns priority, dependencies, domain, Lead,
complexity/risk, review policy and model profiles. The Lead uses the model and
effort plan printed by `status --lead-owner` for fresh Orca launches.

Follow the README for checkpoint, submit, discovery, review, and blocked
decisions. Doctor is read-only; use only the README-defined
`doctor --fix-board` exception when its derived BOARD needs regeneration.

Do not block for solvable repository work: implementation/test failures,
semantic/PIT/finality research, provider errors, bounded retry/fallback design,
stale documentation, scheduler preparation, public or existing-credential API
calls, and ordinary tool escalation remain Active. Current user and Status
standing authorization supersedes permission-only clauses in an older task;
checkpoint that fact and continue within its write scope. Use Blocked only when
no safe in-scope action remains and an unavailable required secret/entitlement,
rejected protected-resource escalation, exact future provider
publication/session/cooldown time, or excluded user-only mutation is the exact resume
condition. A timed wait releases its writer lane and never pauses unrelated
Ready work.

Keep ordinary failures inside the Lead lane. Do not escalate repository bugs,
tests, provider behavior, semantics/PIT/finality research, scheduler repair,
safe retries or routine tool approval to the user. Escalate only an exact
non-delegable action such as a real/paper broker mutation, transfer/withdrawal,
purchase or binding agreement, required user Goal/risk choice, or a necessary
unavailable credential/entitlement after every safe independent action is
exhausted. Quarantine that exact gate and continue unrelated work.
