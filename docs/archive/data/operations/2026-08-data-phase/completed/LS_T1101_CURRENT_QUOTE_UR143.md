# UR-143 LS t1101 current-quote pilot

Status: **FAILED_BOUNDED_OAUTH_STAGE_20260821 / NO_REPEAT**

## Time-label contract

LS's official t1101 example describes a current stock quote/quote-book request
at `POST /stock/market-data`, `tr_cd=t1101`, input `t1101InBlock.shcode`, and
response `t1101OutBlock`. The current-price response carries exact `shcode`,
`price`, and `hotime`, but no source-date field. The official t1301 example is
time-and-sales with `chetime`; it is not needed by this pilot.

`hotime` is accepted only as a composed timestamp, not as an independently
dated value. The composition rule is:

1. retrieval must be inside the versioned XKRX cash `REGULAR` session;
2. its session label must be KST date `2026-08-21`;
3. `hotime` must be exact `HHMMSScc`; it is combined with that same regular
   session label in KST;
4. the resulting provider time must not be after retrieval and must be no older
   than 60 minutes.

This disallows midnight rollover, non-session/holiday relabelling, future time,
and retrieval-time-only freshness. It exposes the observation as provisional,
display-only/PIT-blocked and `provider_native_price` because the response
example does not carry an explicit currency field.

## Exact route and budget

- Identity: `KR_EQUITY_CURRENT / XKRX / 005930`; interval `snapshot`.
- Endpoint: LS `POST /stock/market-data`; `tr_cd=t1101`; body exactly
  `{"t1101InBlock":{"shcode":"005930"}}`.
- Max calls: OAuth `<=1`, t1101 POST `<=1`, serial; timeout 10 seconds;
  retry/continuation/fallback zero.
- Before runtime `.env` loading or client invocation, atomically claim
  `data/state/ls_t1101_current_quote_ur143.json`. A terminal or orphaned file
  is no-repeat.
- Successful t1101 bytes only are retained first under
  `data/landing/ls_openapi/t1101_current_quote_ur143/`; failure/auth bodies and
  response headers are never retained. Exact validation may then atomically
  write only `data/state/current_observations/ls_t1101_current.json`, preserve
  prior valid state, and prove API-zero replay.
- Never inspect, print, copy, or persist `.env`, credentials, tokens, auth
  requests/responses, account material, or headers. No GUI, scheduler,
  Normalized/Published/canonical/history, Backtest, order, or account action.

## Invocation

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\ls_t1101_current_quote_pilot.py `
  --project-root . --confirm-live-005930
```

The consumed t8412 OAuth/data route is never replayed. A t1301 call needs its
own route selection, source-time contract, and budget; it is not a fallback.

## Completed outcome

The 2026-08-21 invocation consumed one OAuth attempt and stopped with sanitized
`ConnectionError` before a t1101 response (business `0`), with timeout 10
seconds and retry/continuation/fallback zero. There is no response body,
Landing file, source timestamp, observation, or display value. The durable
claim is terminal for this exact t1101/date route. It does not decide t1301 or
LS-wide availability, and neither t1301 nor t8412 may be treated as a retry.
