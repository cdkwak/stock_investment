# DATA.GO.KR stock-issuance current-scope count

Status: `REVIEWED_IMPLEMENTATION / NOT_EXECUTED`.

The completed guide-sample pilot proved the official operation, exact source schema,
and future-effective-event behavior. This second and final discovery step makes one
retry-free HTTPS request with no `basDt`, `pageNo=1`, and `numOfRows=1`. It determines
only the current source snapshot date, declared total, and resulting page count.

The response is captured before parsing under the shared DATA.GO.KR provider lock.
Credential echo, HTTP/source error, empty data, page/count drift, schema/domain drift,
or path anomaly stops the run. No production checkpoint, contract, or dataset is
written, and a successful count does not itself authorize a backfill.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\data_go_kr_stock_issuance_pilot.py `
  --project-root . --confirm-live-current-scope-count
```

Run exactly once after confirming the provider lock and request stream are absent.
Audit the retained run offline before deciding whether a bounded full-snapshot
collection has enough research value and an acceptable call budget.
