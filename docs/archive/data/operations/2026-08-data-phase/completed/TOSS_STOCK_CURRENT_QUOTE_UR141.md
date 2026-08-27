# UR-141 Toss stock current quote

Status: **FAILED_BOUNDED_OAUTH_STAGE_20260821 / NO_REPEAT**

This is the sole Toss route activated by UR-141. It is materially distinct from
the consumed UR-126 `market-indicators/prices?symbols=KOSPI` route. It does not
authorize market indicators, candles, investor data, accounts, holdings, orders,
other symbols, or a provider-family sweep.

## Exact route and acceptance gate

- Provider and endpoint: Toss Invest Open API `GET /api/v1/prices`.
- Parameters: exactly `symbols=005930`.
- Identity: `KR_EQUITY_CURRENT / XKRX / 005930`.
- Interval, source route, and unit: `snapshot`, `/api/v1/prices`, `KRW`.
- Expected provider KST date: `2026-08-21` only.
- Provider timestamp must be timezone-aware, no later than retrieval, and no
  older than 60 minutes at retrieval. Retrieval time alone never establishes
  currency, market date, or freshness.
- Finality is `PROVISIONAL`; the projection is `display_only=true` and
  `pit_safe=false`. It is never Normalized, Published, canonical, Backtest,
  GUI-runtime, or scheduler data.

## Fixed budget and durable boundary

- Total network cap: OAuth `<=1` plus one serial price GET `<=1`.
- Connect/read timeout: 10 seconds; retry zero; fallback zero.
- Before runtime client construction/invocation, atomically write the exact
  date's `ATTEMPTING` claim to
  `data/state/toss_stock_current_quote_ur141.json`. Existing terminal or
  orphaned attempts are no-repeat and fail closed.
- Runtime configuration may be loaded only by `TossInvestClient.from_environment`.
  Never open, inspect, print, copy, or modify `.env`; never retain credentials,
  tokens, auth bodies, auth headers, account identifiers, or response headers.
- A successful business body is retained first, unchanged, only at
  `data/landing/tossinvest/stock_current_quote_ur141/`. No failure body is
  retained. Then exact schema/symbol/currency/timestamp/age validation may
  atomically promote only
  `data/state/current_observations/toss_005930_price_snapshot.json` and prove
  an API-zero local replay.
- Any transport, non-2xx, schema, date, age, local-write, or readback failure
  records only the sanitized failure class and completed OAuth/business counts;
  it preserves a valid prior observation and forbids a repeat of this exact
  route/date.

## Exact invocation

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_stock_current_quote_ur141.py `
  --project-root . --expected-market-date 2026-08-21 --confirm-live-005930
```

The command is one-shot. A later identity, date, endpoint, or route requires a
new Data Status selection and runbook; an existing completed result is read only
through the runner's API-zero replay.

## Completed exact outcome

The authorized 2026-08-21 invocation consumed OAuth `1` and stopped with the
sanitized `TossInvestHTTPError` before the quote GET (business `0`), with timeout
10 seconds and retry/fallback `0`. The successful-body gate was never reached:
there is no Landing file, timestamp/age validation, typed projection, or numeric
display. The durable route/date claim is terminal `FAILED`; do not rerun this
endpoint/identity/date. No credentials, auth material, response body, or headers
were retained.
