# DATA.GO equity availability sentinel

Use this sentinel when a recent trading date may still be inside the provider's
publication lag. It probes both independent official streams serially:

1. `getStockPriceInfo`, shared by price and market-cap;
2. `getItemInfo`, used by provider universe.

The live run is capped at exactly two single-page calls, uses retry zero, and
stops on a schema, date, pagination, HTTP, or API-envelope anomaly. Responses
are retained under
`data/landing/diagnostics/data_go_kr_equity_availability/<run-id>/` with a
credential-free ledger and hash-bound manifest. It never changes a production
checkpoint, Landing path, Normalized dataset, canonical universe, or breadth.
Live and adoption modes share `data/state/data_go_kr_provider.lock`; do not run
them alongside another DATA.GO writer that has not adopted this shared lock.

Each exact HTTP response body and a credential-free call record are committed
to diagnostic Landing before API parsing, schema checks, or market
classification. A classification failure therefore retains hash-bound response
evidence referenced by the anomaly ledger. `KONEX` is a known source market
outside the current KOSPI/KOSDAQ Dataset Contracts: it remains in Landing, is
explicitly counted, and is excluded from scoped normalization. The manifest
records source rows, scoped rows, excluded-known rows, and per-market counts.
Any other market label fails closed after Landing capture.

Before an exact body is written, the sentinel scans for the configured service
key in its supplied, URL-decoded, and URL-encoded forms. On a match it writes
only redacted anomaly metadata and never persists the body. Offline adoption
rescans every retained file, including `.body`, rejects links/reparse points or
non-immediate manifest paths, independently validates raw call and ledger
identity/accounting, and proves the parsed Landing JSON equals the raw response.

`VALID_EMPTY_NOT_YET_AVAILABLE` deliberately does not mean a market-empty day.
It records only that the exact source stream returned zero rows when observed.
Do not promote it or add it to production `valid_empty_partitions`.

Live execution requires both an exact date and the explicit confirmation flag:

```powershell
.\.venv\Scripts\python.exe scripts\manual\diagnostic\data_go_kr_equity_availability_sentinel.py `
  --project-root . --date YYYYMMDD --confirm-live-two-call-sentinel
```

Only a manifest where both streams are `NONEMPTY_AVAILABLE` is adoption
eligible. After independent review, the offline adoption command copies the
exact hash-verified pair into the existing production Landing paths and marks
both dates `staged`; it makes zero network calls and writes no Normalized data:

```powershell
.\.venv\Scripts\python.exe scripts\manual\diagnostic\data_go_kr_equity_availability_sentinel.py `
  --project-root . --adopt-run <exact-run-directory>
```

The existing equity batch collector must then promote the staged pair and run
its normal schema, PK, atomic read-back, canonical-universe, and breadth gates.
Never recollect an adopted response. A partial or interrupted adoption requires
manual hash/state audit before any resume.
