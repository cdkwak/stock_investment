# Stock Investment Rev1 recovery report — 2026-09-02

## Outcome

The project is operationally recoverable and the verified repair work is isolated on
`hermes/retire-python-pm-20260831-224653`. The pre-retirement state is preserved by
commit `b0647b4` and branch `backup/python-pm-pre-hermes-20260831-224653`. The target
`master` branch has not been changed. The clean safety branch
`codex/recovery-verified-20260902` contains verified recovery baseline `7f8261b`
and does not include the 65 uncommitted retirement deletions.

Verified repair commits:

- `b66dfe7` hardens current-data recovery.
- `3a8b587` isolates the Dashboard from provider transport.
- `59cdcc5` delivers the consumer-first ten-page Dashboard.
- `d7e848d` aligns release scheduler definitions with installed actions.
- `d9f1871` aligns the release gate with the exact ten-page contract.
- `8423944` fixes secondary-text contrast and reveals expanded detail in-view.
- `2cb5cf8` records the current Data recovery state.
- `818f9b8` retains the verified wide and narrow Dashboard renders.
- `6122723` pins contrast on both white and unavailable-state backgrounds.
- `ec079aa` pins canonical LF for the byte-bound Phase-1 replay artifacts.
- `003b021` pins canonical LF for the exact Phase-1 code-identity source families.

## Measured runtime state

- Managed Data Health is `39/39`.
- All 13 enabled Data scheduler definitions are present and enabled; definition
  mismatch is `0`.
- The 09:10 KR task and the idempotently re-run global-futures task ended with
  result `0`.
- The natural KB account task failed closed after one supplier call, preserved
  the prior valid snapshot, and retained its immutable failed occurrence receipt.
  A separately keyed manual read-only refresh then succeeded after one supplier
  call. No broker mutation was performed and no account identifier was emitted.
- One of 56 immediate Normalized dataset directories is inaccessible:
  `kr_kospi200_futures_investor_net_purchase_daily`. Its 2026 Parquet exists, but
  Windows denies content and ACL reads. That dataset remains quarantined.

## Verification evidence

- Integrated GUI focus: 326 passed, 1 environment-dependent skip, 0 failed.
- Release-readiness unit module: 121 passed.
- Dashboard preferences after accessibility repair: 24 passed.
- Historical suite excluding the ACL-blocked audit: 103 passed.
- Backtest integration: 5 passed.
- Derivatives daily, incremental, and live route checks: 35 passed.
- Independent Data/scheduler review: PASS, including 128 provider-free tests.
- Independent GUI accessibility review: PASS; no financial-semantic or privacy
  boundary change. The new secondary color has at least 6.30:1 contrast on white
  and about 5.7:1 on the unavailable background.
- Native 1366x768 and 900x640 renders show correct Korean glyphs, zero horizontal
  scroll, no overlap, and a visible analysis entry point.
- `git diff --check`: clean apart from informational line-ending warnings.
- Status-link check: 91 checked, 0 missing.
- Remaining unit/integration suite collection after the retirement candidate:
  3,487 tests collected successfully.
- A detached clean checkout of `codex/recovery-verified-20260902` passed 341
  unique focused GUI/release/scheduler tests. Its first run passed 335 and failed
  six because ignored local inputs (`.venv`, Health, and `data/`) do not exist in
  a fresh worktree; after linking the same read-only runtime inputs, the five
  scheduler dry-runs and one native GUI smoke all passed. This confirms the
  recovery commits do not depend on the 65 uncommitted deletions.
- The clean-checkout sweep exposed and repaired a separate Windows Git
  reproducibility defect: CRLF conversion changed both the five-file replay
  generation and its code-tree digest, so the strict GUI consumer failed closed.
  In a new post-fix worktree, all five artifact hashes matched the retained
  generation, code digest `bb85d67f...` matched `experiments.json`, and the two
  checkout/GUI acceptance regressions passed. PIT clocks, feature/label
  isolation, and the sealed 1,222-observation holdout were not inspected or
  changed. The owning GUI Backtest unit file then passed 256 tests with one
  pre-existing skip in that same fresh checkout.

The fresh 2026-09-02 09:50 KST offline read-only release gate returned `FAIL`.
It made zero external calls and zero Data/scheduler mutations. Data-root, schema,
backtest bundle, freshness suppression, local cache/chart, required scheduler
result, native GUI, and user-byte identity checks passed. Exact blockers were
`HEALTH_RECEIPT_RECONCILIATION`, `SCHEDULER_READ_ONLY_STATUS` with three retained
nonzero results, and `DUE_OCCURRENCE_OUTCOMES` with 9/10 groups complete. The
failed KB receipt owns the one due-group failure; the 14:10/20:30 KR slots have
not yet run naturally. The inaccessible Normalized dataset is a separate
retained-audit gate that the release script does not enumerate. A broad pytest
retry was also interrupted by the execution environment; its partial JUnit
record contains 151 tests and zero failures, but it is not represented as a
complete suite result.

## Protected decisions

1. ACL repair: resetting ownership and inherited access on only
   `data/normalized/kr_kospi200_futures_investor_net_purchase_daily` requires
   explicit user approval. After approval, read/hash the existing Parquet, run
   the retained-data historical audit, and then rerun release readiness. Do not
   infer or replace its content before that validation. Read-only comparison
   confirmed that `data/normalized` and a healthy sibling have the same fully
   inherited access descriptor, including the current user and sandbox-worker
   groups; only the target rejects ACL and child enumeration. The proposed repair
   therefore remains narrowly scoped to taking ownership and restoring inherited
   access on that one directory tree, not changing the parent or other datasets.
2. Retirement integration: the working tree contains 65 deletions and 8 modified
   tracked files, totalling 73 tracked files with 34,781 deleted lines. This is
   the broad repository-local Python PM retirement set. It is recoverable from
   the backup branch, but it must not be committed or promoted without explicit
   user approval. `PROJECT_GOAL.md` is inside this mixed set and therefore needs
   the user's direct decision.

Unrelated/generated items remain deliberately excluded: the modified option-wall
analysis CSV, untracked `uv.lock`, untracked release JSON, and the old operations
Dashboard screenshot deletion.

## Exact resume route

Read `AGENTS.md`, `docs/project/PROJECT_STATUS.md`, then
`docs/data/DATA_STATUS.md`. Obtain the exact protected decision before either ACL
mutation or broad retirement integration. Once the ACL is repaired and audited,
run fresh release readiness. Once the retirement decision is made, stage only the
approved set, recheck references and tests, and decide whether to promote the
recovery branch to `master`.
