# UR-241 Toss 005930 NXT session-close one-shot

Status: `RETAINED_API_ZERO_INFERRED_NXT_CLOSE_20260821 / VENUE_INFERRED / NOT_LIVE / DISPLAY_ONLY / PIT_BLOCKED`.

This independently authorized route is only `GET /api/v1/prices?symbols=005930`
for 2026-08-21 KST. It does not repeat or modify UR-141. Fixed budget: OAuth
`<=1`, business GET `<=1`, timeout10, retry/redirect/fallback zero; an isolated
UR-241 durable claim precedes runtime client construction.

At this after-hours clock, this is never a live/current tick. The initial route
was restricted to exact identity 005930, KRW per share and an aware provider
timestamp on the expected KST date in `[19:55:00,20:00:00]`. The provider row
does not declare a venue/session field. Under the route-local user-authorized
rework, its exact exclusive time window is classified only as
`TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW`, with
`venue_inferred=true`; it is not provider-declared NXT. The result remains
display-only/PIT-blocked, `PROVISIONAL`, not live, finalized,
canonical/history/Backtest/GUI/scheduler data.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_stock_nxt_close_ur241.py --project-root . --expected-market-date 2026-08-21 --confirm-live-005930-nxt-close
```

## Exact bounded outcome and retained API-zero recovery

OAuth `1` and business GET `1` were consumed with timeout10 and
retry/redirect/fallback zero. The retained 497-byte successful Landing has
SHA-256 `876eb70453142829b0eb7a02ebef89fc94492ac1ed8da9f737b63ce4ea1c691c`.
It contains one 005930/KRW row with aware provider time 19:59:59 KST and no
venue field. The one-shot route is terminal: it must never invoke OAuth or the
business endpoint again.

The rework performed an API-zero-only retained-Landing recovery. It atomically
projects `data/state/current_observations/toss_005930_nxt_close_ur241.json`
with identity `KR_EQUITY_CURRENT/XKRX/005930`, provider
`tossinvest_open_api`, currency `KRW` and unit exactly `KRW per share`, interval
`snapshot`, finality `PROVISIONAL`, and route/source-route suffix
`TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW`. Its durable recovery
metadata explicitly records `venue_inferred=true`, `not_live=true`, currency
`KRW`, and `external_api_calls=0`; immediate replay also made zero calls. The
recovery hashes the complete retained Landing against the exact SHA-256 above
before parsing or projection. This route-local inference neither asserts
provider-declared NXT nor satisfies a shared <=60m live-current gate.

The supported retained-only command is:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_stock_nxt_close_ur241.py --project-root . --expected-market-date 2026-08-21 --recover-retained-api-zero
```
