# yfinance NQ WebSocket pilot

## Completed exact boundary

`TESTED_NQ_ROUTE_TIMEOUT_NO_MESSAGE / NUMERIC_DATA_NOT_ACCEPTED / NO_REPEAT`.

- Provider/package: installed `yfinance==1.6.0` public `WebSocket` surface.
- Exact symbol: `NQ=F` only. `^GSPC`, SPY, AAPL, QQQ, option/support/query,
  HTTP, and every alternate symbol/route are excluded.
- Budget: one connection, one subscription, first decoded `PricingData` message
  within 15 seconds, reconnect zero, retry zero; HTTP/support/Auth/cookie calls
  zero. Use `verbose=False` and a private sanitized handler only.
- The process must be isolated and must close after the first message or timeout;
  it must not reconnect. It may retain only counts and sanitized schema/field
  presence/value-type evidence. Do not retain raw/base64/error payloads,
  headers, secrets, cookies, credentials, or `.env` content.

## Validation and stop rule

Accept delivery evidence only if the decoded mapping has exact `id=NQ=F`, finite
positive `price`, nonempty `exchange`/`currency`, and integral raw `time`.
`PricingData.time` has no accepted unit contract: do not infer seconds or
milliseconds from magnitude or wall clock. A message therefore creates no
numeric observation, Landing/current projection, GUI value, history/canonical,
or Backtest value. Timeout, empty, disconnect, decode error, or rate limit is a
single bounded stop with no retry and preserves all existing data.

## Completion

Record the one-shot counts and sanitized outcome in UR-134, then move it to
Review. Any future time-unit/retention/finality or numeric-use activation needs
a distinct approved request.

## Completed one-shot result

The 2026-08-21 `NQ=F` process used connection 1/subscription 1 and waited 15
seconds with reconnect, HTTP, Auth, and cookie counts all zero. It received zero
messages and stopped as `TIMEOUT_NO_MESSAGE`; no raw payload, time unit, numeric
observation, or data-layer state was retained. This is route-bounded delivery
evidence only, not provider-wide unavailability or numeric acceptance.
