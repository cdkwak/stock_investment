# Toss Securities Open API

## Status

- Project status: `ACTIVE` for selected read-only market paths.
- Accepted scopes: KOSPI/KOSDAQ candles, investor trading, and allowlisted
  program/short/credit/lending observations.
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

## Boundaries

- Only paths in `READ_ONLY_MARKET_PATHS` are approved.
- Account support allowlists only `GET /api/v1/accounts` and
  `GET /api/v1/holdings` under its separate operation and contract.
- No order, correction, cancellation, transfer, or withdrawal examples.
- Toss rows do not overwrite KRX or data.go.kr history and retain source/date semantics.
