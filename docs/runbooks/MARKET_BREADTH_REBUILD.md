# Market Breadth Retained-Input Rebuild

`kr_market_breadth_daily` is Derived data. Its frozen contract is
`KR_MARKET_BREADTH_DAILY`: key `(date, market)`, partition `(market, year)`, and
nonnegative integer `advancing`, `declining`, `unchanged`, and `total` counts.
The three component counts must sum to `total`.

The rebuild uses only retained `kr_equity_price_daily` and
`kr_equity_canonical_universe_daily` Parquet. It performs no provider calls.
For each security, direction compares the current close with its immediately
preceding retained close observation. Only point-in-time canonical members on
the current date enter the aggregate.

Run the non-mutating gate first:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\rebuild_market_breadth.py `
  --project-root . --mode dry-run
```

The gate validates both input contracts, detects input changes during the run,
validates exact output Arrow schema, and refuses any change to an existing
derived key or value. Apply requires the exact dataset confirmation:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\rebuild_market_breadth.py `
  --project-root . --mode apply `
  --confirm-rebuild kr_market_breadth_daily
```

Apply stages the complete dataset and deterministic state before promotion.
The state records zero API calls, input contract versions and byte manifests,
the output manifest, coverage, rows, semantic fingerprint, and the fingerprint
of preserved existing rows. A transaction marker provides rollback or finalizes
a verified promotion after interruption. Do not run apply concurrently with an
equity input writer.
