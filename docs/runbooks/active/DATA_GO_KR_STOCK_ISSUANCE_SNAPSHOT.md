# DATA.GO.KR stock-issuance snapshot backfill

Status: `PREPARED / NOT_EXECUTED`.

This bounded collector is frozen to the independently audited current-scope evidence:

- source snapshot: `20260812`
- declared rows: `152,676`
- page size: `9,999`; exact pages/call cap: `16`
- serial HTTPS, retry 0, shared DATA.GO.KR provider lock
- each exact response body and parsed Landing page is durable before checkpoint advance
- resumable only from contiguous, hash-verified pages; provider/schema anomaly is terminal
- no Dataset artifact or production Normalized/state write during capture

The registered source-observation contract uses `(capture_id, source_item_ordinal)` and
keeps snapshot date, capture time, page/body hashes, source record hash, event-effective
date, reason, and share count. Historical publication timing is unknown; predictive use
is blocked.

Print and independently verify the frozen plan digest before execution:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect_data_go_kr_stock_issuance_snapshot.py --print-plan
```

Start with at most two calls, audit the checkpoint/pages, then resume the exact run for
the remaining pages. Never restart completed pages or alter the frozen count evidence.
