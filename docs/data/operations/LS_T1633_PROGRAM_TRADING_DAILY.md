# LS t1633 프로그램매매 일별 연구

Status: `RAW_RESEARCH / SCHEDULER_EXCLUDED / LIVE_FAILED_20260826`

This active runbook authorizes the provider-bounded LS OpenAPI `t1633`
program-trading route for KOSPI and KOSDAQ regular-session daily totals.

Standing project/Data authorization covers new eligible dates, bounded live
calls, provider-aware retry/backoff, and scheduler implementation without a
fresh review or approval. The exact 2026-08-19 receipt remains immutable and
same-occurrence idempotent; it does not prohibit a new date/run receipt.

## Finality policy

LS documents the active `/stock/program` `t1633` operation, including a 2026
field-change notice, but does not publish a revision-freeze clock. The reviewed
operational policy is therefore fail-closed empirical T+1: a market date is
eligible only on a later Seoul calendar date and only when it is the latest
retained XKRX session strictly before that operation date. This establishes
descriptive operational finality, not predictive revision safety.

The first accepted target is exactly `2026-08-19`. A missing, duplicate, or
different source date is failure; no value is forward-filled or substituted.

## Bounded operation

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m stock_data.orchestration.ls_t1633_daily_incremental --project-root . --market-date 2026-08-19
.\.venv\Scripts\python.exe -m stock_data.orchestration.ls_t1633_daily_incremental --project-root . --market-date 2026-08-19 --live
```

The historical first live phase used one OAuth request and at most four
retry-zero business calls to the documented `/stock/program` endpoint. The
current provider client retains the same four logical scopes and allows exactly
one bounded retry for a scope that returns HTTP 429, 500, 502, 503, or 504. It
honors a bounded `Retry-After` value and never retries schema, identity, or
semantic failures:

1. KOSPI amount (`gubun=0`, `gubun1=0`)
2. KOSPI quantity (`gubun=0`, `gubun1=1`)
3. KOSDAQ amount (`gubun=1`, `gubun1=0`)
4. KOSDAQ quantity (`gubun=1`, `gubun1=1`)

Every response is retained immutably under
`data/landing/ls_openapi/t1633_daily/<YYYYMMDD>/<run_id>/` before Normalized
promotion. Credentials, tokens, and request headers are never persisted.

## Acceptance and rollback

- All four responses must be HTTP 200, provider success, and contain exactly
  one row for the requested date.
- Amount and quantity dates must match for each market.
- Both normalized market rows must pass the contract and identity checks.
- KOSPI and KOSDAQ, the checkpoint, and the normalized root are one journaled
  promotion. Any capture, validation, or promotion failure preserves the prior
  valid root and checkpoint.
- Immediate same-date replay must return `NOOP_IDEMPOTENT` with zero OAuth and
  zero business calls.
- No Toss, KB, KRX Raw, or other provider fallback is allowed.

Scheduler installation may proceed autonomously only after a later live
transaction and API-zero replay pass. Until then this is a Raw research route,
is excluded from scheduler coverage, and cannot degrade production Health.
Dashboard numeric display remains Normalized-only and Health/freshness-gated.
Predictive use remains blocked.

## First live result

The execution boundary was subsequently approved and the exact 2026-08-19
operation started. OAuth succeeded. KOSPI amount returned HTTP 200 with one
exact-date row and its immutable response/provenance was retained. The second
scope, KOSPI quantity, returned HTTP 500. The then-current retry-zero rule stopped the run at
two business calls; KOSDAQ was not called. A redacted failure sidecar records
only scope, status, and control fields; the HTTP 500 body is not retained.

The joint transaction is `FAILED`: no Normalized root or completion checkpoint
exists, API-zero replay was not applicable, Dashboard remains numeric-free,
and no scheduler was installed or changed.

A second bounded validation targeted 2026-08-26 after the one-retry policy was
implemented. OAuth succeeded and the provider made three business calls,
including one transient retry of the failing logical scope. The provider error
repeated, so the joint operation again stopped without Normalized promotion or
completion checkpoint. The failure evidence is redacted and the response body
is not persisted. This result keeps the dataset in `Raw·연구 전용`, not in the
20:30 production bundle. Do not duplicate a completed occurrence; a failed date
may be investigated through a new bounded run because only successful
completion consumes occurrence idempotency.
