# Same-day provisional Korean equity closes

Status: `IMPLEMENTED_OFFLINE / FIRST_BOUNDED_LIVE_RUN_PENDING`

## Why this lane exists

`kr_equity_price_daily` accepts official canonical rows on D+1 after 13:00 KST.
Korean indices and selected Korean ETFs can already carry the same-day close at
20:30. Without a separate source, a watchlist and the 20:50 condition summary can
therefore compare different session dates.

`KR_EQUITY_PROVISIONAL_DAILY` observes the just-completed XKRX session at 20:30.
It calls pykrx 1.2.8 `stock.get_market_ohlcv_by_ticker(date, market)` exactly once
for `KOSPI` and once for `KOSDAQ`. Each untouched provider frame is written and
read back in immutable Landing before schema validation or Normalized promotion.

## Meaning and precedence

`잠정` means an as-retrieved, same-session pykrx close used only for display and
condition alerts. It is not the D+1 canonical price, an official finality claim,
or a Backtest/predictive input.

- Dataset: `kr_equity_price_provisional_daily`, Normalized Parquet partitioned by
  `market/year`.
- Key: `(date, market, symbol)`.
- Columns: all `kr_equity_price_daily` columns plus constant
  `provisional=True` and UTC `observed_at`.
- Reader rule: canonical rows always win. Provisional rows are appended to a
  stock series only when their date is newer than the latest canonical date;
  overlap is never returned.
- A retained target session is `ALREADY_CURRENT` with provider calls `0`.
- Two valid-empty market frames are `EXPECTED_PROVIDER_LAG`; one empty and one
  non-empty market fails closed without a Normalized write.

Physical housekeeping runs after the 14:10 Canonical lane. It may remove only
provisional rows that already have the same canonical `(date, market, symbol)`
and are older than 5 XKRX sessions. Reader precedence is the correctness rule;
cleanup is bounded storage maintenance.

## First bounded human run

These commands were not run during implementation. Run them from the repository
root tonight, in order. The dry-run performs zero provider calls. The live
single-lane command is bounded to the two market-wide pykrx calls for the lane's
20:30 eligible XKRX target.

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_EQUITY_PROVISIONAL_DAILY --dry-run
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_EQUITY_PROVISIONAL_DAILY
```

The normal scheduled route is the existing
`STOCK_DATA_KR_MARKET_DAILY_2030` task. No new Windows task is required.
