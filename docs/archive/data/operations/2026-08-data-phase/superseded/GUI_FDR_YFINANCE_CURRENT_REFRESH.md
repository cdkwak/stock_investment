# GUI FDR/yfinance current refresh

## State

`ACTIVE_BOUNDED_UR115_PILOT`

This operation validates a separate current-display lane. It never updates or
relabels finalized EOD, canonical, Published, feature, or Backtest data.

## Frozen pilot scope

1. FinanceDataReader 0.9.202: `DataReader("000660")`, exact trailing seven
   calendar days through the operation date, one logical invocation, timeout
   boundary 10 seconds, retry zero. This is a Naver-backed daily polling route,
   not a stream. It must retain source-native OHLCV/Change, date, and incomplete
   current-day status without adjustment inference.
2. yfinance 1.6.0: `Ticker("^GSPC").history(period="1d", interval="5m")`
   through an injected counted `curl_cffi` session, at most three HTTP GETs in
   total including support/bootstrap traffic, timeout 10 seconds, retry zero.
   Stop globally on rate limit, non-200, empty, schema, timestamp, timezone, or
   unit failure. No alternate symbol, host, route, Auth, cookie inspection, or
   fallback is permitted in the same attempt.
3. The first yfinance history attempt ended in `YFRateLimitError` and is frozen
   without retry. A distinct user-requested WebSocket phase may therefore test
   only `^GSPC`: one connection, one subscription, first message within 15
   seconds, reconnect zero, heartbeat zero, and no alternate symbol. It uses the
   public yfinance 1.6.0 `WebSocket` class without HTTP bootstrap, Auth, or
   cookie access. A timeout/schema/symbol/timestamp/currency failure stops the
   route and retains no numeric projection.

The retained `005930 / 2026-08-01..10`, SPY/AAPL/QQQ option/support/query
attempts, UR-094 query1 route, UR-098 query2 route, and UR-108 FRED VIX pilot
must not be repeated.

## Acceptance

- Landing-first sanitized response evidence or a typed body-free failure.
- Exact symbol, provider route, source timestamp, retrieval timestamp, interval,
  currency/unit, provisional/final state, schema and row counts.
- `UPDATED` is allowed only after a newer validated observation is atomically
  committed to the separate current-display projection and read back.
- Failure preserves the prior valid projection and opens the route circuit;
  there is no automatic retry.
- GUI may show the projection only as provider-labelled current/provisional
  data. It never promotes the observation into EOD or Backtest history.

## Forbidden

- `.env`, cookies, Auth, tokens, headers, account/order operations.
- Scheduler changes, background fan-out, implicit symbol expansion.
- Canonical, Published, Backtest, or existing retained EOD mutation.
