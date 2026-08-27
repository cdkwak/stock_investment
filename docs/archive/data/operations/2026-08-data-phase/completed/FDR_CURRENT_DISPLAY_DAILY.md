# FDR current-display daily operation

## State

`PRIMARY_PRE_LANDING_CAUSE_NOT_RETAINED / NUMERIC_DATA_NOT_ACCEPTED / NO_REPEAT`

This was the only selected FinanceDataReader/Naver current-display operation.
It consumed its single allowed GET on 2026-08-21 and ended before a successful
body was available for Landing. The final circuit is the typed no-alternate
stop `FDR_DISPLAY_NO_ALTERNATE_ROUTE`; no typed observation or projection was
written. It is not a final EOD dataset or a periodic refresh procedure.
The sanitized durable checkpoint did not retain the primary pre-Landing failure
code, so this result cannot be generalized to Naver, FinanceDataReader, or the
availability of Korean daily prices.

## Frozen boundary

- Installed `FinanceDataReader==0.9.202`, route `NAVER:005930` only.
- Exact source-date request: `2026-08-21` to `2026-08-21`; never repeat the
  user's retained `005930 / 2026-08-01..10` evidence.
- One underlying Naver `fchart` GET maximum, serial; timeout 10 seconds; retry
  zero. The installed Naver reader's historical body is filtered locally to
  that one requested source date, and the raw-operation cap applies to the GET.
- No login, support, bootstrap, alternate symbol, alternate upstream/route, or
  follow-up request. Any transport, HTTP, empty, Landing, schema, date, unit,
  numeric, promotion, or replay failure consumes the attempt and stops it.
- No `.env`, authentication/account/order material, cookies, response headers,
  canonical/Normalized/Published history, Backtest, GUI, scheduler, or task
  mutation.

## Required transaction

1. Record a sanitized queue pre-call checkpoint with raw GET count zero.
2. Capture the one successful response body into immutable
   `data/landing/fdr_display_daily/NAVER_005930/<sha256>/response.bin` before
   parsing. Failure bodies and response headers are not retained.
3. Accept only the exact Naver columns `Open, High, Low, Close, Volume,
   Change`; one unique requested source date; positive finite OHLC; integer,
   nonnegative volume; KRW; and `AS_RETRIEVED` daily semantics.
4. Atomically write/read back only
   `data/state/current_observations/fdr_display_daily.json` through the UR-118
   current-observation store. Its daily index is normalized to UTC midnight as
   a source-date label, never an availability, intraday, 15m/30m/60m, or final
   EOD timestamp.
5. Write the sanitized state checkpoint
   `data/state/fdr_current_display_operation.json`, then replay the retained
   projection with zero provider calls.

## Completion rule

The exact date is single-use whether validation succeeds or fails. A failure is
a bounded negative result; valid prior display state is preserved and the route
circuit remains open. A successful current-session row remains display-only,
provisional/as-retrieved, PIT-blocked, and outside every finalized-history and
Backtest path.

## Completed evidence

- Pre-call checkpoint: `2026-08-21T10:06:49.0335204+09:00`; raw GET count 0.
- Execution: one raw GET only; timeout 10 seconds; retry zero; successful-body
  Landing unavailable, so no body/header was retained and no promotion ran.
- Sanitized checkpoint/readback: `data/state/fdr_current_display_operation.json`
  records `FAILED_BOUNDED`; its immediate replay used provider API 0 and no
  new request. The exact date is consumed permanently.
