# UR-239 Toss 000660 current quote

Status: `TERMINAL_STALE_PROVIDER_TIMESTAMP_NO_REPEAT_20260821 / NUMERIC_FREE`.

Only `GET /api/v1/prices?symbols=000660` is authorized, for identity
`KR_EQUITY_CURRENT/XKRX/000660` and provider KST date `2026-08-21`. It is not a
repeat of UR-141 005930 and does not authorize another symbol, market
indicators, candles, account/holdings/orders, fallback, GUI, history, canonical,
Backtest, or scheduler work.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_stock_current_quote_ur239.py --project-root . --expected-market-date 2026-08-21 --confirm-live-000660
```

The client alone may load `.env`; no caller opens, prints, retains, or modifies
configuration, token/auth material, headers, cookies, accounts, or orders. Fixed
serial budget: OAuth `<=1`, business GET `<=1`, timeout 10 seconds,
retry/redirect/fallback zero. Before client construction the runner atomically
claims `data/state/toss_stock_current_quote_ur239.json`; terminal/orphan claims
never retry. A successful body is Landing-first under
`data/landing/tossinvest/stock_current_quote_ur239/`; strict exact symbol, KRW,
aware provider timestamp today KST/<=60m and market/session checks may promote
only display-only/PIT-blocked
`data/state/current_observations/toss_000660_price_snapshot_ur239.json`. Replay
is API-zero; any failure preserves prior projection and terminalizes this route.

## Terminal outcome

The sole attempt consumed OAuth `1` and business GET `1`, retry/redirect/fallback
zero. Its successful Landing body was retained and hash-read, but its aware
provider timestamp was `2026-08-21T10:59:59+00:00` / 19:59:59 KST, over two
hours old at the exact 22:12 KST operation clock. The <=60-minute gate rejected
it: no typed projection or numeric display was written, prior state is preserved,
and this exact route/identity/date cannot retry. Terminal local readback uses
provider API `0`.
