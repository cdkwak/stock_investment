# DATA.GO.KR stock-issuance one-call pilot

Status: `REVIEWED_IMPLEMENTATION / NOT_EXECUTED`.

This diagnostic validates the existing official V3 guide before any new Dataset
Contract or historical collection is approved. It makes exactly one retry-free HTTPS
request for the guide's retained positive example:

- operation: `getStocIssuInfo_V3`
- `basDt=20231226`, `pageNo=1`, `numOfRows=10`, expected `totalCount=2`
- one shared DATA.GO.KR provider lock
- raw response persisted before parsing, credential echo blocked before body write
- no production checkpoint, Normalized, Derived, or Published write

The pilot validates exact source fields, snapshot date, corporate-number and ISIN
shape, issue dates, issued-share count, exact row uniqueness, unit (`shares`), and
daily snapshot semantics. It explicitly blocks predictive use because the source has
no announcement/publication timestamp.

Run once only after confirming no DATA.GO.KR stream or provider lock is active:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\data_go_kr_stock_issuance_pilot.py `
  --project-root . --confirm-live-one-call-pilot
```

Any HTTP, entitlement, schema, count, identity, domain, or credential-echo anomaly is
terminal for this run. Preserve the diagnostic directory and audit it offline. Do not
retry, create a contract, or start a backfill merely because transport succeeds.

After a successful run, reproduce its evidence with zero network calls:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\data_go_kr_stock_issuance_pilot.py `
  --project-root . --verify-run <exact-run-directory>
```
