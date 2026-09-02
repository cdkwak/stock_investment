# Stock Investment Rev1 recovery report — 2026-09-02

## Outcome

The project is operationally recoverable and the verified repair work is isolated on
`hermes/retire-python-pm-20260831-224653`. The pre-retirement state is preserved by
commit `b0647b4` and branch `backup/python-pm-pre-hermes-20260831-224653`. The target
`master` branch has not been changed.

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

The configured full release gate is not claimed as PASS. It correctly remains
pending because the retained KB scheduled receipt is failed and the one
Normalized dataset cannot be audited. A broad pytest retry was also interrupted
by the execution environment; its partial JUnit record contains 151 tests and
zero failures, but it is not represented as a complete suite result.

## Protected decisions

1. ACL repair: resetting ownership and inherited access on only
   `data/normalized/kr_kospi200_futures_investor_net_purchase_daily` requires
   explicit user approval. After approval, read/hash the existing Parquet, run
   the retained-data historical audit, and then rerun release readiness. Do not
   infer or replace its content before that validation.
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
