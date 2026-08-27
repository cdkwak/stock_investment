# Dashboard Core Daily Incremental

This operation governs already-accepted Dashboard core daily datasets. Current
execution authority comes from Data Status and the standing autonomous Data
runbook; this document does not add a separate user-approval or phase gate. A
new source, wider refresh, scheduler, or Raw-to-Normalized promotion still needs
its own bounded scope, contract, evidence, and acceptance checks.

## Universal gate

Any plan that promotes or schedules daily data must bind all of the following
before authentication. Semantics/PIT research, official-document review, and
bounded Landing/Raw evidence collection may proceed while a promotion-only gate
is unresolved:

1. the artifact and state-derived `last_accepted_market_date`;
2. a source-specific, evidence-backed `latest_finalized_market_date`;
3. explicit accepted trading dates between those bounds;
4. exact request scopes and expected business-call count;
5. a dataset-specific Landing-first adapter, schema/date validator, atomic
   promotion transaction, checkpoint, lock, and provider-aware bounded retry
   policy;
6. only the affected derived dates.

Never infer trading dates from weekdays, fill absent dates with zero, or treat a
current snapshot as final daily history. A readiness label below limits the
dependent promotion or schedule represented by this historical matrix; it does
not prohibit independent research or a newly contracted Landing/Raw operation.

## Historical UR-111 incident record — 2026-08-21

This section records the conditions observed during UR-111. Its permission and
ACL stops are superseded by current Data Status, which records the ACL blocker
closed and the canonical and short-selling routes active. Preserve the incident
evidence and exact historical receipts, but do not use this section to require a
new user or Lead approval for a new bounded occurrence.

- The 21-row local-first matrix is
  `artifacts/agent_runs/ur111_phase1_local_first_matrix_20260821T022500+0900.md`.
  Health was regenerated from contract-valid production readback with provider
  calls 0. KR index/KOSPI200/VKOSPI/market-investor now read through
  2026-08-20; lending/global index/SOXX/global futures through 2026-08-19;
  FRED yields/VIX through 2026-08-18 and H.10 FX through 2026-08-14, each at
  its own expected publication boundary.
- At the time of UR-111, exact 2026 canonical equity price, cap,
  provider/canonical universe, breadth, and three state files could not be read
  by that process identity. Current Data Status supersedes that historical ACL
  observation. Never infer a retained date or alter ACL inheritance; use current
  contract-valid readback and the active canonical operation.
- The historical 2026-08-20 short-selling plan passed preflight but did not start,
  so its calls and writes were zero. That rejected execution escalation is not a
  current permission gate. A new eligible occurrence may run under standing
  authorization with its own idempotency key, current finalized-date evidence,
  Landing-first capture, atomic promotion, and provider-aware bounded retry.
- The scheduler wrapper now records lane advancement independently from a
  later Health projection failure. A retained process or task result is not
  advancement evidence.
- `dashboard_refresh.py` provides a transport-free allowlisted coordinator and
  metadata-only local poller. Final EOD and provisional native-15m lanes are
  distinct; concurrent GUI/scheduled requests coalesce and only `UPDATED`
  invalidates exact changed datasets. Current route activation belongs to Data
  Status and the selected active runbook, not this historical incident record.

## Historical readiness snapshot

The labels below preserve the review state at the time this matrix was written.
They are navigation evidence, not current permission authority; use Data Status
and the selected dataset runbook for current execution and automation state.

| Dataset | Readiness | Current reason |
|---|---|---|
| Equity price, cap, provider/canonical universe | `DAILY_INCREMENTAL_READY` | Official D+1 business-day 13:00 KST window plus exact-date dual-stream validation governs ingest. 2026-08-13 is accepted; revision remains unresolved. |
| `kr_index_daily` | `HISTORICAL_MANUAL_DAILY_INCREMENTAL_READY_WITH_LIMITS` | One explicitly finalized date used immutable source-first Landing, bounded pykrx calls for KOSPI/KOSDAQ, contract validation, staged atomic promotion, and a recoverable journal/checkpoint. First-date replay passed and retained coverage then ended 2026-08-14. New occurrences use current Data Status, explicit trading-date/finality evidence, and a provider-aware bounded retry policy. |
| `kr_kospi200_index_daily` | `HISTORICAL_MANUAL_DAILY_INCREMENTAL_READY_WITH_LIMITS` | The same capture job added ticker 1028 and a separate contract-shaped Landing/promotion. First-date replay passed and retained coverage then ended 2026-08-14. Current scheduler eligibility is an acceptance decision under the standing runbook, not a user-permission gate. |
| `kr_market_breadth_daily` | `ADAPTER_REUSE_POSSIBLE` | Deterministic builder exists; run only after a successful canonical equity increment and only for affected dates. |
| Short-selling trading/balance/investor | `ADAPTER_REUSE_POSSIBLE` | The historical collector was Landing-first, bounded, checkpointed, and retry-zero; canonical dates then extended through 2026-08-13. A new occurrence may use a provider-aware bounded retry policy while preserving exact-date atomicity and the independent finality gates. |
| Stock lending detail/market/participant | `NEW_INCREMENTAL_WRAPPER_NEEDED` | Historical page/resume collector exists, but this snapshot had no evidence-backed missing-finalized-date append transaction. Implementing and validating one is ordinary standing-authorized engineering work. |
| KOSPI200 option source/bridge/PCR/Wall | `OFFLINE_TRANSACTION_READY / PROMOTION_FINALITY_BLOCKED` | The five-stage atomic transaction exists. Its exact candidate is the two named data.go.kr KOSPI200 regular-session `basDt` operations. `T` affects Source/Bridge/Basis/PCR at `T` and Wall at `T` plus the immediate next option observation only. Bounded Landing/Raw research is authorized; publication/revision finality must be evidenced before dependent promotion, automation, or predictive use. |
| Accepted market-investor bridge | `SOURCE_FINALITY_BLOCKED` | Provider segments are not equivalent and predictive/PIT use remains blocked; current snapshot cannot extend it. |
| LS t1633 program trading | `LIVE_SOURCE_HTTP_500_FAIL_CLOSED` | The authorized 2026-08-19 run retained KOSPI amount successfully, then stopped on the second KOSPI quantity HTTP 500 with retry zero. KOSDAQ was not called; no Normalized/checkpoint promotion, replay, Dashboard number, or scheduler change occurred. See [the provider runbook](LS_T1633_PROGRAM_TRADING_DAILY.md); the same attempt must not be retried. |

## Dependency order

- Canonical equity increment -> affected-date market breadth.
- KOSPI200 option source increment at `T` -> Bridge/Basis/PCR at `T` -> Wall at
  `T` plus the immediate next option observation -> explicit same-date KOSPI200
  joins for those Wall dates. No later Wall date is affected because the change
  fields are one-period differences, not cumulative state.
- Short source increment -> affected-date market short summary.
- Lending source increment -> affected-date market/security lending summary.

No dependent build runs when its source increment is empty, stopped, blocked,
or unpublished.
