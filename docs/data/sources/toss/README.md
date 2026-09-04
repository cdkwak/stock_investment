# Toss Securities Open API

## Status

- Project status: `ACTIVE` for selected read-only market paths.
- Accepted scopes: KOSPI/KOSDAQ candles, investor trading, allowlisted
  program/short/credit/lending observations, and the explicit U.S. watchlist
  quote lane below.
- Toss account holdings support now has a separate identifier-free, read-only
  contract and offline-tested atomic snapshot path. Its first external run is
  governed by [the account snapshot operation](../../operations/TOSS_ACCOUNT_SNAPSHOT_READONLY.md).
  Trading remains out of scope.

## Official reference

- [Toss Securities Open API docs](https://developers.tossinvest.com/docs)
- [Market data guide](https://developers.tossinvest.com/docs/market-data)

Base URL: `https://openapi.tossinvest.com`. OAuth token route:
`POST /oauth2/token` with client credentials.

## Authentication

- `TOSSINVEST_BASE_URL` (optional override)
- `TOSSINVEST_CLIENT_ID`
- `TOSSINVEST_CLIENT_SECRET`

Tokens remain in memory. Never print token requests/responses, Authorization
headers, or `.env`. Account-scoped APIs additionally require an account header;
the selector stays in runtime memory and is never logged or persisted.

## Safe read example

Use the project's allowlisted client rather than hand-building auth:

```python
from stock_data.providers.tossinvest import TossInvestClient

client = TossInvestClient.from_environment()
result = client.get_market_data(
    "/api/v1/market-indicators/KOSPI/candles",
    params={"interval": "1d"},
)
```

Confirm the official parameter names for the endpoint before running. The
client rejects non-allowlisted paths, uses bounded timeouts, and exposes rate
limit metadata without exposing credentials.

Available diagnostics:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnostic\smoke_tossinvest_market.py --help
```

## Project route

- Client: `src/stock_data/providers/tossinvest/client.py`
- Daily refresh: `scripts/manual/collect/refresh_toss_market_investor_daily.py`
- Historical probe/backfill: `scripts/manual/research/` and `scripts/manual/backfill/`

## U.S. watchlist quote lane

- Lane: `TOSSINVEST_US_QUOTES_30M`; provider route: one
  `GET /api/v1/prices?symbols=...` request for at most 200 symbols.
- Explicit requested order: `SKHY,SOXL,SOXX,TQQQ,QQQ,EWY,SGOV,VGLT` after
  filtering against the Yahoo ETF/equity identity registries. Partial non-empty
  responses fail closed; an empty result is valid-empty and preserves prior data.
- The cadence group is `GLOBAL_30M`, the closest existing group used by the
  Yahoo intraday/current lane (`PT30M`). Eligibility is `[17:00,06:00)` KST,
  spanning U.S. pre-market, regular trading, and part of after-hours.
- `session_hint` is independently derived in `America/New_York` as
  `pre_market`, `regular`, `after_hours`, or `closed`; it is descriptive and is
  not an official session/finality claim.
- A run makes exactly one `STOCK_PRICE` call. A 429 or any supplied
  `retry_after_seconds` returns `SKIPPED_RATE_LIMIT`; there is no retry.
- Landing is retained before Normalized append. The latest display artifact is
  `artifacts/intraday/tossinvest_us_quotes_latest.json`; sampled rows append to
  `data/normalized/tossinvest_us_quote_30m/`. These are as-retrieved quotes, not
  bars or official closes.
- `/api/v1/stocks/all` is forbidden in this lane. Public/guest display paths do
  not construct the client or invoke this scheduler-only operation.

## Boundaries

- Only paths in `READ_ONLY_MARKET_PATHS` are approved.
- The U.S. quote lane never calls account, holdings, balance, or order routes
  and never persists account data or authorization material.
- Account support allowlists only `GET /api/v1/accounts` and
  `GET /api/v1/holdings` under its separate operation and contract.
- No order, correction, cancellation, transfer, or withdrawal examples.
- Toss rows do not overwrite KRX or data.go.kr history and retain source/date semantics.
