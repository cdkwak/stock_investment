# LS t8462 daily Raw collection

Status: `DAILY_COLLECTION_READY`
Pilot/audit status: `CLOSED`

This procedure captures one append-only post-close Raw snapshot for all 18
`K2I/MKI × F/C/P × D/N/U` scopes. It does not write a Dataset Contract or any
Normalized artifact. Do not reopen the historical pilot unless new source
evidence contradicts the closed audit.

## Semantics retained as-is

- `sv_*`: signed net quantity.
- `sa_*`: `UNIT_INFERRED_MULTI_DATE_MATCH`; never relabel as confirmed.
- `U`: `SESSION_INFERRED(ALL)`.
- `D/N`: `SESSION_UNRESOLVED`; preserve raw code only.
- Option `U` provider aggregate differences: `OPTION_SPECIFIC_SEMANTICS`.
- `sv_18` remains the authoritative provider aggregate. Reconciliation fields
  are provenance only and never overwrite it.

## One daily invocation

Invoke only after the target Korean trading-day close, with the exact date:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect_ls_t8462_daily_raw.py `
  --root . `
  --market-date YYYYMMDD `
  --confirm-live-daily-raw
```

The collector issues one OAuth request and at most 18 serial `t8462` calls,
retry zero, with at least 1.05 seconds between data calls. It refuses a second
attempt when a checkpoint already exists for that market date. Any HTTP 403,
429, provider error, schema anomaly, secret echo, or partial run stops the day;
do not retry automatically.

The first live daily run is manual only. Accept it only when all 18 scopes pass,
each contains the requested market date, the checkpoint reports exact artifact
counts (18 Raw, 18 provenance, 19 ledger events), the secret scan passes, and
retention and institution-aggregate statuses are reviewed. Immediately invoke
the same command again and require `NOT_EXECUTED_ALREADY_ATTEMPTED` before any
OAuth or data request. Only then may `DAILY_COLLECTION_OPERATIONAL` be proposed.
No scheduler is installed or authorized by this runbook.

Landing is under `data/landing/ls_openapi/t8462_daily_raw/<market_date>/<run_id>/`.
Each scope has an unchanged Raw response and provenance sidecar; the run also has
an append-style ledger and checkpoint. No task scheduler is installed by this
change. Scheduling must call this command once after close on verified trading
days and must not overlap the KB or KRX provider locks/streams.

## Retention monitoring

Each request starts at `20200101`, so the returned earliest and second observed
market dates are recorded per scope. Baseline earliest is `20250718`. A future
earliest date becomes `ROLLING_RETENTION` only when it equals the prior run's
second observed market date. Larger jumps require review; backward movement is
also retained for review.

Normalized promotion remains forbidden until amount-unit evidence, `U=ALL`
evidence, and the decision to exclude or retain unresolved `D/N` are reviewed.
If `D/N` never resolves, a future contract may consider `U` only.
