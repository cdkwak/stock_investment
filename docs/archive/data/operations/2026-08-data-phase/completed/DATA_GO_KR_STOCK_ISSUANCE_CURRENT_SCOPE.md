# DATA.GO.KR stock-issuance current-scope count

Status: `COMPLETE / OFFLINE_AUDIT_PASS / DO_NOT_RERUN`.

Run `20260813T172157Z_3d52035e3c1643fc8336fce42227323b` made exactly one
retry-free HTTP-200 request. It returned source snapshot `20260812`, declared total
`152,676`, and therefore 16 pages at 9,999 rows. Manifest SHA-256 is
`d44592d87c4d2fdd4799af6916610e74dbe2bcb7003313ac6ca7470429eb9129`.
The zero-network verifier reproduced the count and original evidence. Do not rerun.

The completed guide-sample pilot proved the official operation, exact source schema,
and future-effective-event behavior. This second and final discovery step makes one
retry-free HTTPS request with no `basDt`, `pageNo=1`, and `numOfRows=1`. It determines
only the current source snapshot date, declared total, and resulting page count.

The response is captured before parsing under the shared DATA.GO.KR provider lock.
Credential echo, HTTP/source error, empty data, page/count drift, schema/domain drift,
or path anomaly stops the run. No production checkpoint, contract, or dataset is
written, and a successful count does not itself authorize a backfill.

The command below is record-only and must not be executed again:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\data_go_kr_stock_issuance_pilot.py `
  --project-root . --confirm-live-current-scope-count
```

Run exactly once after confirming the provider lock and request stream are absent.
Audit the retained run offline before deciding whether a bounded full-snapshot
collection has enough research value and an acceptable call budget.
