# Cboe public index history CSVs

Status: `ACTIVE_PRIMARY_FOR_VIX_TERM_INDICES / DESCRIPTIVE / PIT_BLOCKED`  
Access checked: 2026-09-04 KST

## Registered source

`VIX9D`, `VIX3M`, `VIX6M`, and `SKEW` use Cboe's publicly served, complete
daily-history files:

- `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX9D_History.csv`
- `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv`
- `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX6M_History.csv`
- `https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv`

The endpoint is not parameterized: one HTTP GET returns the complete available
history for one symbol. The retained request uses a project User-Agent, retry
count zero, and a hard budget of one download per selected symbol per lane run.
This route replaces Yahoo period history for these four symbols because Yahoo's
daily rows stop at 2026-07-17 while the Cboe files continued through the latest
completed source session when checked.

## Schema and Landing

The parser accepts a UTF-8 BOM, US-style `MM/DD/YYYY` dates, case/spacing
variants of the date header, and either:

- `DATE,OPEN,HIGH,LOW,CLOSE`; or
- `DATE,<single value>` (including `SKEW`), mapped to
  `open=high=low=close`.

Dates must be unique and strictly increasing. Close and all resulting OHLC
values must be finite, with valid OHLC relationships. Volume is retained as
nullable `Int64` with every value null, as allowed by
`global_index_price_daily`; `provider_gap_dates` is empty.

Every response is captured before parsing under
`data/landing/cboe_index_history/<run_id>/<symbol>.csv`. The adjacent
`<symbol>.json` binds provider, URL, timestamp, HTTP status, byte count, and
SHA-256. Promotion continues through the existing whole-dataset/state CAS.

## Operational path

Dry-run the mixed-provider lane (zero API calls):

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe scripts\maintenance\run_provider_scheduler.py --project-root . --lane GLOBAL_INDEX_DAILY --dry-run
```

Prepare the four-symbol full-history candidate (exactly four downloads):

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe scripts\manual\collect\refresh_global_current.py --project-root . --phase cboe_index --symbols VIX9D VIX3M VIX6M SKEW --end <LAST_US_SESSION> --confirm-live-landing-only
```

Review the checkpoint and Landing hashes, then promote with no network access:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe scripts\manual\collect\refresh_global_current.py --project-root . --promote-checkpoint data\state\global_current_refresh\<RUN_ID>\checkpoint.json --confirm-offline-promotion --approval-digest <APPROVAL_DIGEST>
```

Rebuild the retained-data-only derived dataset:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe -m stock_data.derived.vix_term_structure --project-root .
```

The endpoint is treated as a public/guest, personal-use source. The public mode
may display the derived descriptive regime, but neither the source history nor
the derived term structure gains predictive PIT eligibility.
