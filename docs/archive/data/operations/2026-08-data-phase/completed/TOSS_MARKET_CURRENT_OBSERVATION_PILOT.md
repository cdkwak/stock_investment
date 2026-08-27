# Toss KOSPI current-observation pilot

Status: **COMPLETED_ONE_SHOT_TYPED_OAUTH_OR_TRANSPORT_FAILURE_20260821 / NO_REPEAT**

This is the sole active Toss current-observation procedure. It exists separately
from `toss_market_investor_daily`, which promotes Normalized/Published history
and is not authorized by this pilot.

## Exact scope

- Provider: Toss Invest Open API, runtime-only client configuration.
- Endpoint: `GET /api/v1/market-indicators/prices`.
- Parameters: exactly `symbols=KOSPI`.
- Expected provider timestamp KST date: exactly `2026-08-21`.
- Route: `toss-market-price:KOSPI:snapshot:PROVISIONAL`.
- One OAuth request plus one serial market GET maximum; timeout 10 seconds;
  retry zero; no fallback.

KOSDAQ, candles, investor trading, account/holdings, orders, every other
endpoint, scheduler, canonical promotion, and Backtest are outside this scope.

## Preconditions (satisfied before the one attempt)

1. This runbook and the matching Data Status route are active.
2. The date has no existing entry in
   `data/state/toss_market_current_observation_pilot.json`.
3. Runtime configuration may be loaded only by `TossInvestClient.from_environment`.
   Do not open, inspect, print, copy, or modify `.env`; do not retain credentials,
   tokens, authentication bodies, or authentication headers.
4. Use the exact command below. No diagnostic OAuth or endpoint probe is allowed.

## Procedure

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_market_current_observation.py `
  --project-root . --expected-market-date 2026-08-21 --confirm-live-kospi
```

The runner executes Landing-first: it retains the market response at
`data/landing/tossinvest/current_observation/`, requires exactly one KOSPI row,
and rejects a provider timestamp whose KST date differs from `2026-08-21`.
Only then it atomically writes the separate display-only current-observation
envelope at `data/state/current_observations/toss_kospi_price_snapshot.json`.
It neither writes Normalized/Published/Backtest data nor makes a GUI call.

On any typed or local failure, stop globally with no retry and record only the
failure class plus OAuth/market call counts. Do not repeat the date. A completed
date returns an API-zero readback replay with no runtime client required.

## Completed one-shot result

On 2026-08-21 KST, the exact command made one OAuth attempt and stopped with
the sanitized `TossInvestHTTPError` before any market GET. The durable pilot
state records `token_calls=1`, `market_calls=0`, `landing_file=null`, and
`status=FAILED`; no provider body, authentication response/header/material,
Landing file, display projection, canonical data, or scheduler change exists.
The date is consumed. Do not rerun this command, test a different Toss endpoint,
or treat this result as a market-data or entitlement conclusion.
