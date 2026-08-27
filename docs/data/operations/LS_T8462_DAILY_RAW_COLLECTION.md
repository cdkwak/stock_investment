# LS t8462 daily Raw collection

Status: `SCHEDULED_RAW_ONLY_V5 / CURRENT_THROUGH_20260826 / FINALITY_GATE`
Pilot/audit status: `CLOSED`

The first authorized post-full-session market date `20260814` completed all 18
scopes and passed the immediate same-date pre-network no-call replay.
This authorization covers all six `K2I/MKI × F/C/P` products across
`D=Day`, `N=Night`, and `U=All`. It permits Raw responses, provenance,
checkpoint/state, immediate same-date no-call replay, and descriptive GUI use.
It does not permit Normalized/Canonical promotion or predictive/PIT use.

This procedure captures one append-only post-close Raw snapshot for all 18
`K2I/MKI × F/C/P × D/N/U` scopes. It does not write a Dataset Contract or any
Normalized artifact. Do not reopen the historical pilot unless new source
evidence contradicts the closed audit.

## Semantics retained as-is

- `sv_*`: signed net contracts, `CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE`.
- `sa_*`: signed net purchase in 100 million KRW,
  `CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE`;
  never relabel as official LS confirmation.
- `U`: `CONFIRMED_EMPIRICAL(ALL)`.
- `D/N`: `CONFIRMED_EMPIRICAL(REGULAR/NIGHT)` with a documented
  institution/other category boundary; preserve raw codes and do not infer
  finalization cut-offs.
- Preserve institution and other corporation as separate `LS_NATIVE_CATEGORY`
  Raw values. `institutional_complex = institution + other_corp` is an
  [analysis-only feature](../config/LS_T8462_ANALYSIS_FEATURES.md), never a Raw
  rewrite or provider field.
- Option `U` provider aggregate differences: `OPTION_SPECIFIC_SEMANTICS`.
- `sv_18` remains the authoritative provider aggregate. Reconciliation fields
  are provenance only and never overwrite it.

## One daily invocation

Invoke only for the authorized post-full-session target, with the exact date:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_ls_t8462_daily_raw.py `
  --root . `
  --market-date 20260826 `
  --confirm-live-daily-raw
```

The collector issues one OAuth request and at most 18 serial `t8462` calls,
retry zero, with at least 1.05 seconds between data calls. It refuses another
attempt after a successful complete checkpoint for that market date. A failed
or partial occurrence does not consume the date permanently: the current run
stops, preserves its Raw/failure evidence, and a later scheduler occurrence may
start a new bounded run for the same still-missing target.

Accept a daily occurrence only when all 18 scopes pass,
each contains the requested market date, the checkpoint reports exact artifact
counts (18 Raw, 18 provenance, 19 ledger events), the secret scan passes, and
retention and institution-aggregate statuses are reviewed. A completed date
returns pre-network without OAuth or business calls. The lane remains
`READY_WITH_FINALITY_GATE`: scheduling Raw collection is allowed, while
Normalized/Published promotion and predictive use remain prohibited.

Landing is under `data/landing/ls_openapi/t8462_daily_raw/<market_date>/<run_id>/`.
Each scope has an unchanged Raw response and provenance sidecar; the run also has
an append-style ledger and checkpoint. KR bundle contract v5 runs the lane as a
20:30 child for the latest completed XKRX session and isolates its receipt from
the other 12 children. The provider scheduler serializes this provider path.
The 2026-08-26 live validation completed one OAuth plus 18 business calls,
retry zero, secret scan PASS, and zero Normalized/Published writes.

## Retention monitoring

Each request starts at `20200101`, so the returned earliest and second observed
market dates are recorded per scope. Baseline earliest is `20250718`, currently
classified as an observed `SOURCE_HISTORY_BOUNDARY` because older direct date
requests were valid empty. A future
earliest date becomes `ROLLING_RETENTION` only when it equals the prior run's
second observed market date. Larger jumps require review; backward movement is
also retained for review.

Normalized promotion remains forbidden. Product/session/unit/sign semantics
are empirically closed across the six retained product families. Remaining
gates are official or controlled-observation session finality, publication and
revision timing, an accepted post-full-session daily capture, and PIT review.
Historical backfill rows remain non-predictive; a future row is never available
before its actual accepted `captured_at`.

## Offline first-live acceptance and GUI boundary

`stock_data.orchestration.source_acceptance.evaluate_ls_t8462_first_live`
reviews only retained files. Acceptance requires the complete 18-scope
checkpoint, exact 1 OAuth / 18 data / zero retry accounting, 18 Raw responses,
18 provenance sidecars, 19 ledger events, secret-scan pass, target-date presence
in every scope, and an immediate same-date
`NOT_EXECUTED_ALREADY_ATTEMPTED` result proving the pre-network no-call gate.

Passing this review yields only
`FIRST_LIVE_RAW_ACCEPTED_DESCRIPTIVE_ONLY`. Session finality, revision timing,
and predictive PIT remain blocked; Normalized is still forbidden. The GUI may
show the retained Raw values only with source, Raw route, session code,
capture/availability timestamp when retained, and the explicit
`PIT_BLOCKED_SESSION_FINALITY_REVISION_UNRESOLVED` label. A historical Raw view
must say `HISTORICAL_RESEARCH_RAW`; it is not silently presented as a daily
operational capture.
