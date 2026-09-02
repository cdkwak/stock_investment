# Project Status

Read this immediately after `AGENTS.md`. It selects the active domain and the
next status document to read. Only the lead agent may update it. Dataset,
scheduler, GUI, and experiment detail belongs to the owning status, contract,
runbook, checkpoint, or retained evidence.

## Current route

| Field | Current value |
|---|---|
| Selected domain | `DATA_PRIMARY / PARALLEL_ENGINEERING_ACTIVE` |
| Current phase | `RECOVERY_PROMOTED / PYTHON_PM_RETAINED / AUTONOMOUS_DATA_AND_READONLY_ACCOUNT_OPERATIONS / GUI_BACKTEST_FEATURE_ENGINEERING_ACTIVE / FINAL_HOLDOUT_SEALED / FINANCIAL_MUTATIONS_DISABLED` |
| Next domain | No phase handoff required for ordinary in-scope engineering |
| Exact next action | Observe the 2026-09-02 20:30 KR slot naturally and reconcile its exact receipt and managed Health. When an administrator token is available, apply the already approved ACL repair only to the quarantined futures-investor dataset, then hash/audit it and rerun release readiness. |
| Real external blockers | Exact future provider publication/session windows, unavailable secret/entitlement, rejected protected-resource escalation, or a user-only financial/legal/access action |
| Parallel work | GUI, Features, offline Backtest/ML, portfolio simulation, local paper simulation, diagnostics, and read-only account integration may proceed with disjoint scopes |
| Queue role | Hermes coordinates the current direct recovery. The tested repository-local Python-PM/Queue control plane is retained and inactive; the proposed retirement was rejected and preserved only as recovery evidence. Direct user tasks do not require queue registration. |

The user-owned [Project Goal](PROJECT_GOAL.md) is durable planning input. It does
not select a phase or override Status, Contract, checkpoint, or runbook
authority.

## Current cross-project facts

- `master` now contains the verified recovery and the follow-up Windows/Data
  repairs through `6c6f32a`. The pre-Hermes state remains at
  `backup/python-pm-pre-hermes-20260831-224653`; the rejected 73-file retirement
  candidate is recoverable from `backup/python-pm-retirement-candidate-20260902`
  and the named recovery stash. Mutable runtime, resumable ML, local-private,
  and account state remain outside Git.
- Data is the primary operational domain. Current dataset health, source
  semantics, publication/finality gates, account observations, and exact next
  operations live in [Data Status](../data/DATA_STATUS.md).
- Hermes is the conversation-facing Project Manager for this recovery. The
  repository-local Python-PM roles, Queue lifecycle, workflow runtime, scheduler
  entry point, and read-only operations Dashboard remain preserved on `master`
  but are not the current execution authority. Their clean-checkout evidence is
  393 unit, 17 integration, and 10 operations-Dashboard tests passing. The user
  accepted the recommended recovery: the 65 deletions and eight other tracked
  retirement changes were rejected, restored, and preserved only on a backup
  branch plus stash.
- Documentation uses the bounded [Documentation Router](../README.md). Current
  domain Status files contain only routing facts, blockers, support boundaries,
  and exact next actions; pre-compaction snapshots and terminal operation detail
  are non-default archive evidence.
- The Windows scheduler is operational but receipt-degraded: all 13 enabled Data
  definitions exist, are enabled, and have definition mismatch `0`. The 09:10 KR
  task and an idempotent global-futures run ended with result `0`. The natural
  14:10 bundle advanced its three Data lanes through their targets, then ended
  result `1` only because pre-20:30 downstream rows were projected as stale; the
  schedule-aware Health fix is on `master` and native recomputation is `39/39`.
  The 20:30 task remains `Ready` for its natural 2026-09-02 occurrence. The
  separate read-only KB account task failed closed at 07:10 after one supplier
  call and preserved the prior snapshot. A separately keyed manual read-only
  refresh succeeded with one call but did not rewrite that failed receipt.
  Exact inventory remains in [Scheduler Status](SCHEDULER_STATUS.md).
- The scheduler inventory does not duplicate GUI freshness. Per-surface as-of,
  freshness, last accepted success, and next eligibility remain owned by the
  [GUI refresh-status contract](../gui/GUI_REFRESH_STATUS_CONTRACT.md) and
  current GUI behavior remains in [GUI Status](../gui/GUI_STATUS.md).
- The Yahoo current-only task's natural 2026-08-27 01:02 and 01:32 KST
  occurrences both passed the accepted 17-route set with zero history writes.
  The latter accepted Yahoo `^VIX` 15-minute value `15.61` for the 01:30 KST
  completed provider bar and the same Dashboard instance hot-read it without a
  restart. The market-temperature VIX row now uses that accepted completed
  Yahoo observation as the labelled current value and ranks it against the
  retained FRED completed-daily distribution without changing either history
  or Backtest inputs. The 03:02 occurrence later returned 1 because only
  `SP500_CURRENT_60M` raised a fallback invariant; all 17 Landing responses are
  retained, the prior S&P 500 value was preserved, and replaying that response
  against a copied state passed. The natural 03:32 recovery then passed all 17
  routes with failures 0, retries 0, and history writes 0. BOK 17:10 remains
  evidence-only and must stop after its three-batch finality review gate.
- Toss account has a daily 07:00 read-only task; its natural 2026-08-27
  occurrence completed strict `TERMINAL_SUCCESS / SUCCEEDED` with one token call
  and three account calls. The scheduler last-result receipt is byte-identical
  to the date-keyed occurrence receipt, and its `normalized_sha256` matches the
  current Normalized snapshot. A prior bounded CLI attempt failed at OAuth and
  preserved the accepted prior snapshot. A separately keyed 02:52 KST OAuth
  preflight then passed with one token call and zero account calls; it does not
  claim account-refresh success. KB `SSQM2952` has a configured runtime, a
  retained identifier-free 2026-08-26 live success, and an installed 07:10 task.
- The fresh 18:14 KST offline release gate made zero external calls and zero
  Data/scheduler mutations. Data-root/schema/backtest/cache/freshness/required-
  result/native-GUI/user-byte checks passed. It remains `FAIL` on Health-receipt
  reconciliation, three retained nonzero task results, and two of ten due groups:
  the immutable failed KB receipt and the 14:10 terminal receipt. Exactly one of
  56 immediate Normalized datasets is separately inaccessible. The user approved
  its exact owner/inheritance repair, but the current token is not an
  administrator, so that dataset remains quarantined until an elevated token is
  available; no broader ACL change is authorized.
- The sanitized local [Issue-State Contract](ISSUE_STATE_CONTRACT.md) is active.
  Its scheduled component aggregates local evidence and may discover only the
  explicit thresholded Inbox case; it performs no provider retry or Data action.
  The 06:45 task's action, trigger, overlap, time limit, and battery/wake policy
  were repaired to the exact registration contract; first natural execution is pending.
- The typed close-proxy publication remains accepted evidence. The existing
  1,222-observation final holdout is intentionally sealed and
  `results_reviewed=false`; separate development-only experiments remain allowed
  under [Backtest Status](../backtest/BACKTEST_STATUS.md).
- The new provider-free `market-regime-validation/v1` engine fixes 63/126/252
  session return and true path-drawdown outcomes behind a 252-session purged
  development-only boundary. It cannot run on production market context until
  all three PIT-safe axes exist; the missing Forward EPS/revision/ROE axis is a
  typed dependency, never a zero or neutral substitute. The accepted five-file
  generation and sealed holdout remain untouched.
- P4 now separates practical daily discovery from strict historical validation.
  `stock-exploratory-scanner/v1` asynchronously scans the exact current dated
  Korean universe with retained original-price RSI14 and MA60 disparity and
  shows an extreme technical candidate list at RSI14 <= 30 or close/SMA60 <=
  80%. The actual 2026-08-25 local scan evaluated 2,633 instruments and found
  186 observation candidates, displaying the first 80. A one-call retry-zero
  exact-date KRX current observation retained 2,719 unique rows with zero
  duplicates; 78/80 displayed candidates have at least one PER/PBR value (PER
  29, PBR 78). These descriptive values do not change inclusion/order, while
  Forward EPS and strict relative-value judgment remain explicitly `N/A`.
  The valuation child was introduced in KR bundle contract v4 and is retained
  unchanged in active contract v5 as a retry-zero, one-session 09:10 child;
  its 2026-09-02 natural execution completed with Windows result `0` while
  managed Health remained `39/39`.
  The separate `stock-candidate-research/v1` strict three-axis boundary remains
  future Backtest/research validation only and never blocks the practical
  single-axis list.
- P5 now has an explicit [Account Detail Todo](../gui/ACCOUNT_DETAIL_TODO.md).
  The first detail pass exposes already-validated average/current prices,
  ordinary/after-cost/daily P/L, returns, weights, source and reference time at
  exact holding/currency grain. It adds no provider call, account identifier,
  cross-currency total, realized P/L, or order capability. Toss decimal-ratio
  returns are converted once to GUI percentage points. Per-source cards now
  show last accepted time, allowlisted outcome, refresh capability and next
  eligibility. The user explicitly requested account-size change tracking on
  2026-08-27; the resulting [local history contract](../data/operations/ACCOUNT_VALUE_HISTORY_LOCAL.md)
  atomically retains value-only observations with accepted snapshots. KB exact
  total assets, Toss securities-plus-cash-buying-power components, and legacy
  securities-only values remain differently labelled and source/currency
  scoped. External cash flows remain unseparated, so no series is called a
  return. A real line requires two natural observations; none is fabricated.
- Sovereign-yield, curve, equity-linkage, and bond-ETF distinctions are owned by
  the [Data semantic contract](../data/SOVEREIGN_YIELD_BOND_ETF_SEMANTICS.md).
  The documentation-only Korean daily summary is owned by the
  [GUI summary contract](../gui/DAILY_MARKET_SUMMARY_CONTRACT.md). Its compact
  Telegram projection is normally 3–4 lines and hard-limited to 6 lines/480
  code points, but registry revision 1 intentionally yields `NO_OUTPUT` until
  an accepted local `MARKET_STATE` result is selected. It authorizes no runtime,
  numeric promotion, provider call, account mutation, or trading.

## Current gates

| Area | Current state | Required handling |
|---|---|---|
| Data operations | `AUTONOMOUS_PUBLIC_AND_ENV_AUTHENTICATED_API_OPERATIONS` | Use Landing-first capture, source contracts, validation, atomic writes, and prior-valid preservation |
| Scheduler | `OPERATIONAL / DEFINITIONS_MATCH / CURRENT_RECEIPTS_DEGRADED` | Preserve immutable failed receipts and observe the 20:30 KR slot naturally |
| Data integrity | `ONE_NORMALIZED_DATASET_ACL_BLOCKED / REPAIR_APPROVED / ADMIN_TOKEN_UNAVAILABLE` | With an administrator token, repair only that dataset, then hash and audit before use |
| GUI | `AUTONOMOUS_READONLY_ENGINEERING` | Preserve typed freshness, numeric suppression, privacy, and source identity; keep provider transport and canonical promotion in Data |
| Workflow control | `HERMES_CONVERSATION_PM / PYTHON_PM_RETAINED_INACTIVE` | Do not run two lifecycle writers; the retirement candidate is rejected and preserved only as backup evidence |
| Account | Existing-credential read-only access authorized | Keep projections identifier-free; no account discovery in scheduled paths and no broker mutation |
| Backtest / ML | `AUTONOMOUS_OFFLINE_ENGINEERING` | Keep the existing final holdout sealed and enforce PIT/leakage controls |
| Semantics / PIT | `AUTONOMOUS_EVIDENCE_GATHERING` | Quarantine only unsupported claims and dependent promotion/use while continuing independent investigation |
| Realtime / simulation | Read-only realtime data and unmistakably local simulation allowed | Never reach a real or paper-broker order endpoint |

## Standing boundaries

- Public and existing `.env`-authenticated API research/calls, bounded retries,
  read-only account refreshes, contract-valid promotion, and scheduler
  management are authorized. Never disclose secrets, authorization material,
  or direct account identifiers.
- No order submission, amendment, cancellation, transfer, withdrawal, purchase,
  subscription, binding agreement, or broker-side financial mutation.
- Do not silently reinterpret identity, dates, units, session meaning,
  revisions, finality, or PIT. Preserve valid zero, missing, and source values.
- Roadmaps, goals, archives, provider guides, audits, queues, and manual scripts
  are reference or evidence, never current execution authority by themselves.

## Resume route

```text
AGENTS.md
  -> docs/project/PROJECT_STATUS.md
  -> docs/data/DATA_STATUS.md
```

For a specifically selected Backtest or GUI task, replace the final line with
exactly one of:

```text
docs/backtest/BACKTEST_STATUS.md
docs/gui/GUI_STATUS.md
```

Follow only the Contract, checkpoint/state, evidence, or runbook selected by
that domain status. Use [Repository Map](REPOSITORY_MAP.md) only for location and
ownership, [Scheduler Status](SCHEDULER_STATUS.md) only for scheduler inventory
and consolidation, and [Project Roadmap](PROJECT_ROADMAP.md) only for
architecture or long-term sequencing.
