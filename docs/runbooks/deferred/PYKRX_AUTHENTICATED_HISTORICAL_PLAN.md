# Authenticated pykrx historical collection plan

Status: design only. Historical automation remains disabled.

## Verified feasibility evidence

The bounded 2026-08-11 diagnostic used installed `pykrx` 1.2.8 with its KRX
login session. It made 12 of at most 14 raw HTTP requests, sequentially, with
zero retries and a 20-second timeout per request. Authentication and all five
public probes succeeded:

| Domain | Probe | Result |
|---|---|---|
| Short selling | `003410`, 2024-07-08 (last locally observed date before delisting) | one valid all-zero source row |
| Investor flow | `005930`, 2026-08-10 | one row |
| Fundamentals/valuation | `003410`, 2024-07-08 | one row |
| ETF OHLCV | all ETFs, 2026-08-10 | 1,160 rows; includes `069500` |
| Foreign ownership | `005930`, 2026-08-10 | one row |

This proves authenticated endpoint feasibility for the tested observations. It
does not prove earliest coverage, completeness, stable pagination/range limits,
or permission for unattended bulk collection.

## Source boundary

- Authentication must load `KRX_ID` and `KRX_PW` into the process environment
  before importing `pykrx`. Credentials never enter Landing, ledgers, logs, or
  task state.
- A single authenticated process owns the KRX session and dataset lock.
- `data.krx.co.kr` scraping outside the authenticated pykrx path remains disabled.
- Program trading metadata exists locally but has no public pykrx wrapper.
  Stock lending, credit, and corporate-action economic terms were not found as
  supported pykrx operations. The smoke result does not unblock those domains.

## Candidate dataset sequence

Before writing any data, D must approve a separate task that fixes the schema,
primary key, source operation, units, and point-in-time semantics.

1. `kr_equity_fundamental_daily` candidate: `(date, symbol)` observations for
   BPS/PER/PBR/EPS/dividend yield/DPS. Source zeros and missing values must be
   preserved without interpreting their economic reason.
2. `kr_equity_foreign_ownership_daily` candidate: `(date, symbol)` listed shares,
   held shares, ownership ratio, limit shares, and limit-exhaustion ratio.
3. `kr_etf_ohlcv_daily` candidate: `(date, symbol)` market-wide daily ETF response,
   with NAV, OHLC, volume, value, and source underlying-index value.
4. Short-selling contracts: reconcile the tested status endpoint with the
   existing trading/balance/investor contracts before choosing outputs. A valid
   historical zero row is not evidence of full historical coverage.
5. Investor flow: treat pykrx as a possible official-source validation or
   extension task. Do not silently concatenate it with the checksum-fixed legacy
   dataset or Toss A001 because their provider boundaries and unit semantics differ.

## Collection architecture

Provider → lossless Landing → Normalized → optional Derived. No direct writes to
Normalized before the corresponding Landing response passes read-back and hash
validation.

- One authoritative writer and one atomic process lock per dataset.
- Concurrency: one raw HTTP request at a time; no parallel workers.
- Conservative client throttle: at least 5 seconds between raw HTTP requests.
  This is a project safety limit, not a claimed KRX-published rate limit.
- Every run requires an explicit raw-request budget. Authentication, ticker
  finder initialization, session refresh, and business calls all count.
- Default future pilot budget: at most 25 raw requests. A larger daily/run budget
  requires a separate D decision after pilot latency and response behavior are
  audited.
- Request timeout: 20 seconds. A future collector may implement at most two
  bounded retries for transient connection/429/5xx failures only after that retry
  policy is explicitly approved; parser/auth/schema failures are not retried.
- Session refresh calls are ledgered and consume the same budget.
- No polling and no automatic continuation after budget exhaustion.

## Landing and call ledger

For every raw request retain:

- task/run ID and monotonic sequence number;
- public operation and sanitized endpoint path;
- non-secret query scope (date range, symbol or market);
- HTTP status, elapsed time, response byte count, and SHA-256;
- exact raw non-auth response body;
- parsed row count/schema or visible parse error;
- classification: `SUCCESS`, `VALID_EMPTY`, `AUTH_ERROR`, `HTTP_ERROR`,
  `PARSER_ERROR`, or `BUDGET_EXHAUSTED`.

Never retain login response bodies, cookies, passwords, prepared login payloads,
or credential-bearing headers. Scan every diagnostic/collection artifact for the
actual credential values before promotion.

## Checkpoint and resume

- Checkpoint grain is the smallest independently verified source request, not a
  guessed trading-day completion flag.
- Persist completed request scopes, raw-body hash, source rows, valid-empty
  scopes, failures, request count, and next cursor/range atomically.
- Resume only scopes whose Landing files reconcile with their checkpoint hashes
  and row counts. Contradictory or incomplete Landing is a visible error.
- Merge Normalized data atomically by its declared primary key; collection failure
  must never overwrite already verified partitions.
- Use the canonical historical universe for each date where symbol fan-out is
  required. Never derive historical membership from today's ticker list.

## Gates before historical automation

1. Define and test the Dataset Contract and physical Arrow schema.
2. Run an explicitly budgeted micro-pilot covering recent/historical and
   listed/delisted observations for that one dataset.
3. Determine actual source range/chunk limits without broad probing.
4. Validate Landing→Normalized exactness, PK/null/infinity/duplicate rules,
   valid-empty behavior, and survivorship handling.
5. Independently review call accounting, session refresh behavior, locks, and
   resume idempotence.
6. Only then may D approve a checkpointed historical backfill with a fixed call
   budget. Successful smoke testing alone never enables automation.
