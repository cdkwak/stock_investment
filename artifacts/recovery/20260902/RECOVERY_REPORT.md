# Stock Investment Rev1 recovery report — 2026-09-02

## Outcome

The verified recovery is now promoted to `master`, which is at `6c6f32a`. The
pre-Hermes state remains preserved by branch
`backup/python-pm-pre-hermes-20260831-224653`. The user accepted the recommended
recovery, so the 65 retirement deletions and eight other tracked changes were
rejected and restored. Their exact candidate remains recoverable from
`backup/python-pm-retirement-candidate-20260902` at `438a66b` and the named stash
`approved recovery backup before master promotion 20260902`; it is not part of
the operational tree. `codex/recovery-verified-20260902` remains a safety ref at
the promoted recovery point `b240cd6`.

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
- `637b4d3` records Backtest checkout reproducibility without changing the sealed
  holdout.
- `977a7fe` reconciles the partitioned recovery-test evidence.
- `dc3b4f9` reconciles Project and GUI current-state routing with the verified
  runtime and protected-decision boundary.
- `b240cd6` finalizes the pre-promotion recovery report.
- `a262375` replaces the unsafe Windows PID probe in the FDR future collector.
- `5a9ba23` preserves parent or prior-target Windows security identity across
  canonical atomic replacement.
- `3e6d3e3` and `6c6f32a` make Health publication-time aware through the exact
  Korean 20:30 dataset set without weakening the normal stale gate.

## Measured runtime state

- Managed Data Health is `39/39`.
- All 13 enabled Data scheduler definitions are present and enabled; definition
  mismatch is `0`.
- The 09:10 KR task and the idempotently re-run global-futures task ended with
  result `0`. The natural 14:10 bundle made five calls and advanced all eligible
  Data lanes through 2026-09-01, then returned 1 solely because the old Health
  projection labeled pre-20:30 dependent rows stale. Native post-fix Health is
  `39/39` with 2 `CURRENT`, 37 `EXPECTED_LAG`, 0 invalid, and 0 runtime failures.
- The 20:30 task is enabled, `Ready`, and scheduled for its natural 2026-09-02
  20:30 KST occurrence; it has not been run early.
- The natural KB account task failed closed after one supplier call, preserved
  the prior valid snapshot, and retained its immutable failed occurrence receipt.
  A separately keyed manual read-only refresh then succeeded after one supplier
  call. No broker mutation was performed and no account identifier was emitted.
- One of 56 immediate Normalized dataset directories is inaccessible:
  `kr_kospi200_futures_investor_net_purchase_daily`. Its 2026 Parquet exists, but
  Windows denies content and ACL reads. The user approved the exact ACL repair,
  but the current token is not an administrator and Windows did not apply it.
  That dataset remains quarantined. A separate state-file ACL was repaired with
  no elevation and its SHA-256 remained unchanged.

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
- Independent current-state review: PASS. Only Project and GUI Status changed;
  `PROJECT_GOAL.md` did not. The release remains explicitly `FAIL`, Hermes is the
  conversation-facing PM while the retained Python PM is inactive, and the
  privacy, PIT, sealed-holdout, and no-broker-mutation boundaries remain intact.
- The configured suite collects 4,389 tests. A single-process native sweep is
  not represented as PASS: its first two failures are the two historical tests
  that read the quarantined ACL target, and later long-process runs can hit an
  intermittent Windows temporary-file replacement denial. The same affected
  modules pass in fresh per-file processes, so evidence is partitioned rather
  than masking either condition.
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
- Partitioned fresh-checkout validation also passed the remaining GUI unit scope
  as 444 plus two Health-bound reruns, Providers/Storage/Validation as 350,
  account/privacy as 339 with two skips, and Backtest/Contracts/Derived/Features
  as 724 with one skip plus one isolated rerun. Two different Phase-1 journal
  replacement tests encountered an intermittent Windows `PermissionError` only
  inside broader runs and each passed immediately in a new temporary directory;
  this environment-sensitive file-lock flake is recorded rather than hidden.
- The FDR future-display integration file now passes all 10 tests after replacing
  Windows `os.kill(pid, 0)` probing with a non-signaling process-handle check;
  its related units pass 11 tests. Canonical incremental security handling passes
  8 integration and 50 related unit tests. Post-close Health scheduling passes
  84 related tests, and release-readiness units pass 121 tests natively.
- The current recovery partition also passed historical 103 (excluding only the
  two ACL-bound cases), Backtest integration 5, daily-operations integration 179,
  GUI integration 22, all 39 pipeline files as 301 passed plus one skip, and
  regression as 368 passed plus five skips. Contracts 128, Derived 74, Features
  54, and the completed Issue-State slices 10 also pass. The remaining broad
  unit sweep is not claimed complete.

The fresh 2026-09-02 18:14 KST offline read-only release gate returned `FAIL`.
It made zero external calls and zero Data/scheduler mutations. Data-root, schema,
backtest bundle, freshness suppression, local cache/chart, required scheduler
result, native GUI, and user-byte identity checks passed. Exact blockers were
`HEALTH_RECEIPT_RECONCILIATION`, `SCHEDULER_READ_ONLY_STATUS` with three retained
nonzero results, `SCHEDULER_RESULT_STATUS` for the KB task, and
`DUE_OCCURRENCE_OUTCOMES` with 8/10 groups complete. The failed KB and 14:10
receipts own the two failed due groups. The inaccessible Normalized dataset is a
separate retained-audit gate that the release script does not enumerate.

## Decisions and remaining protected boundary

1. ACL repair: the user explicitly approved resetting ownership and inherited
   access on only
   `data/normalized/kr_kospi200_futures_investor_net_purchase_daily`. The retained
   script validates that exact root before acting. The current token is not an
   administrator; `takeown`/ACL reset did not change the target and attempted UAC
   launch did not yield an elevated child. No broader or alternate dataset ACL
   mutation is authorized. When an administrator token is available, run the
   exact repair, read/hash the existing Parquet, run the retained-data historical
   audit, and only then admit it back into evidence.
2. Retirement integration: resolved in favor of the recommended recovery. The
   73 tracked retirement changes were not integrated; the tested Python-PM/Queue
   control plane is restored on `master`. The rejected candidate remains
   recoverable on its backup branch and stash, so this decision did not destroy
   evidence.

Unrelated/generated items remain deliberately excluded: the modified option-wall
analysis CSV, untracked `uv.lock`, and untracked release-readiness JSON reports.

## Exact resume route

Read `AGENTS.md`, `docs/project/PROJECT_STATUS.md`, then
`docs/data/DATA_STATUS.md`. Observe the natural 20:30 KR task and reconcile its
exact receipt plus managed Health. With an administrator token, run only the
already approved ACL repair, hash and audit the retained Parquet, then rerun the
two ACL-bound historical tests and release readiness. Preserve immutable failed
receipts and the unrelated working-tree items listed above.
