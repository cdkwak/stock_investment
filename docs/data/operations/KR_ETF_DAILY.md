# Korean watchlist ETF daily operation

Status: `20:30_KST_AUTOMATION_ACTIVE / DISPLAY_ONLY / PIT_BLOCKED`.

`KR_ETF_PRICE_DAILY` runs immediately after `CANONICAL_EQUITY_DAILY` in the
existing 20:30 KST Korean-market bundle. It retains current-list identity and
daily prices only for a small selected-symbol set. It does not collect the full
ETF universe and does not replace the retained full-market Raw
`kr_etf_universe_daily` or `kr_etf_ohlcv_daily` research artifacts.

## Contracts and symbol scope

- `kr_etf_master`: current KRX identity with `security_type=ETF`, listing
  status, nullable listing date, leverage multiple, and source provenance.
- `kr_etf_price_daily`: provider-native OHLC, volume, trading value, nullable
  NAV, and source provenance. Valid zero no-trade values are retained.
- Scheduled symbols are the sorted union of Korean ETF entries in
  `artifacts/local_user/watchlists.json` (`market="KRX"` and
  `security_type="ETF"`) and symbols already present in
  `data/normalized/kr_etf_master`.
- Symbols must be six-character uppercase KRX codes; alphanumeric codes such
  as `0193M0` are valid. The union is capped at 25 symbols per run (raised from 10 on 2026-09-05 after the watchlist + retained master + manual-account ETFs reached 12 and the 20:30 lane failed with `symbols must contain between 1 and 10 values`). Past the cap, watched and held ETFs are selected first and retained-master leftovers are dropped (`symbols_dropped` in the result); only more than 25 watched/held ETFs raises (`KrEtfSelectionError`, whose message is written to the scheduler receipt).

To add an ETF, add a matching KRX/ETF entry to the local watchlist. The lane
discovers it on its next run; no scheduler or registry edit is required.

## Scheduled window and result

The lane uses the exchange-calendar helper to select the latest completed XKRX
session on or before the occurrence time. For each symbol it plans
`latest retained date + 1` through that target, capped to the most recent 30
XKRX sessions. An empty retained symbol starts with the same 30-session cap.
Symbols already covering the target are skipped. If every symbol is current,
the lane returns `ALREADY_CURRENT` with zero provider calls.

The result records `schema_version`, `lane`, `status`, `target_session`,
per-symbol `latest_before` and `latest_after`, `api_calls`, `retry_count=0`,
`predictive_use=false`, `symbols`, and any `provider_gap_dates`. If a captured
frame does not contain the target session, the lane returns
`EXPECTED_PROVIDER_LAG`; this is expected lag, not a failed bundle lane.

## Landing-first call boundary

The scheduled wrapper reuses `run_kr_etf_daily`; Landing capture, read-back,
validation, atomic Parquet promotion, state, and checkpoint logic are not
duplicated. `PykrxEtfClient(manual=True, requested_days=1)` satisfies the same
automated pykrx safety gate as the other scheduled pykrx lanes. Credentials are
loaded from `.env` by the provider and must never be printed.

For `N` symbols that need collection, the reused operation performs
`1 + 2 * N` pykrx data-method calls: one exact-target ticker list, one ticker
name per symbol, and one OHLCV range per symbol. At the current two-symbol scope
that is five calls. It makes no retries, never calls the portfolio-deposit-file
endpoint, and never backprojects current membership. Each response is written
to immutable Landing and read back before validated Normalized data is written
atomically.

## Human-run commands

Run from the repository root with Python 3.13 after the 20:30 KST slot. The dry
run performs no provider calls:

```powershell
$env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_ETF_PRICE_DAILY --dry-run
```

First bounded live lane run (the lane itself enforces at most 25 symbols, 30
XKRX sessions per symbol, and retry zero):

```powershell
$env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_ETF_PRICE_DAILY
```

The direct manual collector remains available for an explicitly bounded
diagnostic or recovery range. It retains its separate 10-calendar-day limit.
Both datasets are display-only: as-retrieved membership and values do not
establish revision finality or predictive PIT safety.
