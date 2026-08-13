# DATA.GO.KR stock-issuance one-call pilot

Status: `COMPLETE / OFFLINE_AUDIT_PASS_FUTURE_EFFECTIVE_EVENT / DO_NOT_RERUN`.

The single retained run is
`20260813T171515Z_bc212698423247dea3d3693436fd1a8a`. It made one retry-free
HTTP-200 request and returned the guide's exact 2/2 rows and field schema. One row
has `basDt=20231226` and effective issue date `20231227`; the original fail-closed
`PILOT_STOPPED` manifest is preserved, while `offline_audit.json` records the
zero-network corrected interpretation. Manifest SHA-256 is
`9d544147caf3952de5d32dc029751118d9c78aa4c98c7a8d51665c25f0ff3fb1`.

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
daily snapshot semantics. A source snapshot may contain a future effective issue date;
that record is preserved and counted rather than rejected. Predictive use remains
blocked because the source has no retained announcement/publication timestamp.

The command below is retained for audit history and must not be run again:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\data_go_kr_stock_issuance_pilot.py `
  --project-root . --confirm-live-one-call-pilot
```

Any HTTP, entitlement, schema, count, identity, domain, or credential-echo anomaly is
terminal for this run. Preserve the diagnostic directory and audit it offline. Do not
retry, create a contract, or start a backfill merely because transport succeeds.

Reproduce its evidence with zero network calls:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\data_go_kr_stock_issuance_pilot.py `
  --project-root . --verify-run <exact-run-directory>
```
