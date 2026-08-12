# A007 follow-on: balance then investor

Status: offline readiness review complete; execution remains D-gated while the
single KRX stream is owned by the trading phase.

This runbook does not authorize a live request. It fixes the verified order,
scope, capacity, and stop conditions for the two remaining v2 short-selling
datasets after trading has completed and passed its boundary audit.

## Phase gates

| Phase | Readiness | Earliest source date | Through date used for capacity | Business scopes | Canonical dates | Expected normalized rows |
|---|---|---:|---:|---:|---:|---:|
| balance | READY, predictive use blocked | 2016-06-30 | 2026-08-07 | 4,958 | 2,479 | not predetermined; source rows are retained exactly |
| investor | READY, T+1 minimum | 2008-01-02 | 2026-08-07 | 40 | 4,587 | 91,740 if every canonical date is returned for all 4 market/metric scopes |

The counts above are deterministic outputs of `calculate_backfill_estimate`
with a query ceiling of 2026-08-11 against the retained canonical
`kr_equity_universe_daily` calendar and the 25-probe authenticated pilot. The
actual last canonical date retained under that ceiling is 2026-08-07. The
investor count is 10 chunks times two markets times two source metrics. Every
chunk is at most 730 calendar days.

## Contracts and physical layout

- Balance contract: `kr_short_selling_balance_daily` v2, PK
  `(date, market, symbol)`, partitions `market/year`. `date` is the KRX
  report-obligation occurrence date, not a verified publication date. Predictive
  use remains blocked even after collection completes.
- Investor contract: `kr_short_selling_investor_daily` v2, PK
  `(date, market, investor_type, metric)`, partitions `market/year`. `metric`
  preserves `volume` (shares) and `trading_value` (KRW) rather than mixing units.
  Availability is no earlier than the project T+1 minimum.
- Exact raw HTTP 200 bodies go first to
  `data/landing/pykrx/short_selling/{balance|investor}`. Each body has an
  immutable v2 provenance sidecar linked to one run ledger response by run ID,
  raw sequence, scope hash, body hash, byte count, status, and content type.
- Normalized writes are atomic partition upserts. Checkpoints are separate per
  dataset at `data/state/kr_short_selling_{balance|investor}_daily_v2.json` and
  advance only after Landing read-back, parse, validation, and normalized write.
- The shared D-owned lock remains
  `data/state/d_owned_krx_short_selling.lock`; only one authenticated KRX process
  may exist.

## Parser and validation gates

Balance preserves source name, shares, KRW amounts, market cap, and source
percent ratio. PK/null/negative checks, six-character KRX code checks, and
ratio bounds are enforced. A valid-empty response on a canonical requested date
is treated as anomalous and stops the batch; it is never normalized as zero.

Investor expands each source row into the five explicit source classes for one
metric. It requires the source total to equal the four component classes. A
blank-date all-zero placeholder is classified as valid-empty. More importantly,
every requested chunk must return exactly the canonical trading dates in that
range; a missing or extra date stops before normalized completion.

Resume accepts only a scope whose Landing body, provenance sidecar, unique HTTP
ledger event, checkpoint hash/row count, and normalized rows reconcile exactly.
There is no retry loop or public unauthenticated fallback.

## Capacity and batching

Pilot-median projections are estimates, not row-count promises:

| Phase | Landing estimate | Normalized Parquet estimate | Runtime at 8s/call | Runtime at 9s/call | Recommended bounded batch |
|---|---:|---:|---:|---:|---:|
| balance | 896,699,547 bytes | 237,851,298 bytes | 11.02 h | 12.40 h | 400 business calls (200 dates) |
| investor | 3,742,992 bytes | 8,102,936 bytes | 0.09 h | 0.10 h | all 40 business calls after a four-call boundary batch |

Raw-call budgets must include authentication/session traffic in addition to
business calls. The retained pilot observed 15 authentication requests. Use a
minimum 25-call authentication reserve for each fresh process; if authentication
behavior exceeds that reserve, stop and audit rather than increasing the budget
mid-run. Therefore a 400-business-call batch uses `--max-raw-calls 425`, the
four-call investor boundary batch uses 29, and the remaining 36-call investor
resume uses 61.

Before each next batch, D reconciles the checkpoint and audits the preceding
batch. The current canonical calendar yields these exact ascending disjoint
balance batches after the one-date boundary batch:

| Batch | Start | End | Business calls | Raw-call cap |
|---:|---:|---:|---:|---:|
| 2 | 2016-07-01 | 2017-04-19 | 400 | 425 |
| 3 | 2017-04-20 | 2018-02-14 | 400 | 425 |
| 4 | 2018-02-19 | 2018-12-11 | 400 | 425 |
| 5 | 2018-12-12 | 2019-10-07 | 400 | 425 |
| 6 | 2019-10-08 | 2020-07-27 | 400 | 425 |
| 7 | 2020-07-28 | 2021-05-20 | 400 | 425 |
| 8 | 2021-05-21 | 2022-03-14 | 400 | 425 |
| 9 | 2022-03-15 | 2022-12-29 | 400 | 425 |
| 10 | 2023-01-02 | 2023-10-25 | 400 | 425 |
| 11 | 2023-10-26 | 2024-08-16 | 400 | 425 |
| 12 | 2024-08-19 | 2025-06-19 | 400 | 425 |
| 13 | 2025-06-20 | 2026-04-14 | 400 | 425 |
| 14 | 2026-04-15 | 2026-08-07 | 156 | 181 |

Recompute this table if the canonical artifact changes before execution.

## D-gated command templates (do not execute while trading owns KRX)

First balance boundary (one canonical date, two market calls):

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\backfill_pykrx_short_selling.py --project-root . --dataset balance --start 2016-06-30 --end 2016-06-30 --max-business-calls 2 --max-raw-calls 27 --confirm-live-collection
```

Subsequent balance batches use the exact table above in order. For example,
batch 2 is:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\backfill_pykrx_short_selling.py --project-root . --dataset balance --start 2016-07-01 --end 2017-04-19 --max-business-calls 400 --max-raw-calls 425 --confirm-live-collection
```

Investor boundary (the first canonical chunk produces four calls):

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\backfill_pykrx_short_selling.py --project-root . --dataset investor --start 2008-01-02 --end 2009-12-30 --max-business-calls 4 --max-raw-calls 29 --confirm-live-collection
```

The boundary end is the exact end of the first canonical 730-day chunk. After
reconciliation, resume the full canonical range with 36 remaining business
calls and a fresh 25-call authentication reserve:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\backfill_pykrx_short_selling.py --project-root . --dataset investor --start 2008-01-02 --end 2026-08-11 --max-business-calls 36 --max-raw-calls 61 --confirm-live-collection
```

## Operational dependencies and stop conditions

Execution requires: trading phase DATA_COMPLETE and independently audited;
zero active KRX process; released shared lock; installed pykrx 1.2.8; credentials
available only in process environment; canonical universe partitions complete
for the requested range; sufficient disk headroom; and D approval of the actual
date boundary and throttle configuration.

Immediately stop on HTTP 403/429/5xx, auth/session anomaly, HTML or invalid JSON,
schema change, unexpected valid-empty, investor date-coverage mismatch, ledger
or provenance mismatch, checkpoint inconsistency, duplicate scope, lock conflict,
or raw-call-budget exhaustion. Do not retry automatically.

