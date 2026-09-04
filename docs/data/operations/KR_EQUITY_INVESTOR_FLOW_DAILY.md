# Korean Equity Investor Flow Daily

## Purpose and boundary

`KR_EQUITY_INVESTOR_FLOW_DAILY` collects per-symbol KRX net-purchase amounts
for Korean common and preferred stocks selected from the local watchlist. It is
a descriptive, as-retrieved source dataset; publication revision/finality and
point-in-time safety are not established, so predictive use is blocked.

The installed pykrx source (1.2.8) defines
`stock.get_market_trading_value_by_date(fromdate, todate, ticker, ..., on,
detail, freq)`. The lane explicitly passes `on="순매수"` and `detail=False`.
That aggregate net-purchase amount view returns `기관합계`, `기타법인`, `개인`,
`외국인합계`, and `전체`, denominated in won. `detail=True` is not used because
it expands the institution aggregate into subcategories.

The installed implementation has no 31-calendar-day restriction for this
function. The project nevertheless bounds a single call to 366 calendar days;
the scheduled lane uses only five XKRX sessions.

## Selection and schedule

- At the 20:30 KST bundle slot, this lane runs immediately after
  `KR_EQUITY_PROVISIONAL_DAILY`.
- Select `KOSPI` or `KOSDAQ` watchlist entries whose `security_type` is exactly
  `보통주` or `우선주`, using `stock_data.gui.watchlist_service`.
- Union those symbols with symbols already retained by this dataset, retaining
  watchlist-first deterministic ordering and a hard cap of 40 symbols per run.
- Target the latest completed XKRX session and request the last five XKRX
  sessions. A complete retained five-session window is an API-zero replay;
  otherwise there is at most one pykrx call per planned symbol.
- A dry run reports selected and planned symbols, the five-session window, the
  40-symbol cap, and estimated calls without constructing the live provider.

## Landing, normalization, and validation

Each call is captured before normalization at
`data/landing/kr_equity_investor_flow_daily/<run_id>/symbol=<code>.json`, with
a read-back-verified SHA-256 receipt and checkpoint. Validated rows are
atomically promoted to
`data/normalized/kr_equity_investor_flow_daily/symbol=<code>/year=<yyyy>/`.

The normalized columns are `date`, `symbol`, `foreign_net`, `institution_net`,
`individual_net`, `other_corp_net`, `total_net`, `source`, and `captured_at`.
Amounts are signed `int64` won values and `source` is `pykrx`. Exact columns,
six-character uppercase alphanumeric symbols, and unique `(date, symbol)` keys
fail closed. If the four participant amounts do not sum to `total_net` within
one percent (with a one-won minimum tolerance), the receipt records a warning;
the mismatch alone does not reject otherwise contract-valid source data.

Retained overlapping values cannot change. Provider gaps are recorded as
`SUCCEEDED_WITH_PROVIDER_GAPS`/`EXPECTED_PROVIDER_LAG` and are not considered a
successful idempotency key, so a later run may fill them.

## Human commands (not executed during offline validation)

Set `$env:PYTHONIOENCODING='utf-8'` first.

Dry-run the lane:

```powershell
.venv\Scripts\python.exe scripts\maintenance\run_provider_scheduler.py --lane KR_EQUITY_INVESTOR_FLOW_DAILY --dry-run
```

Run the current daily lane:

```powershell
.venv\Scripts\python.exe scripts\maintenance\run_provider_scheduler.py --lane KR_EQUITY_INVESTOR_FLOW_DAILY
```

Perform the one-time one-year backfill for the currently resolved symbols:

```powershell
$symbols = .venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from stock_data.orchestration.kr_equity_investor_flow_daily import resolve_kr_equity_investor_flow_symbols; print(','.join(resolve_kr_equity_investor_flow_symbols(Path.cwd())))"
.venv\Scripts\python.exe scripts\manual\collect\refresh_kr_equity_investor_flow.py --symbols $symbols --start 2025-09-05 --end 2026-09-04
```

These commands may use `KRX_ID`/`KRX_PW` through the existing pykrx login
wrapper. Never print or persist those environment values.
