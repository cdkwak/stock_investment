# DATA.GO.KR stock-issuance source-history backfill

Status: `COMPLETE / ARCHIVED / DO_NOT_RERUN`.

The completed run is
`20260813T173606Z_28afa7bd957b42aab02604f79cd47588`: 16 pages, 152,676
rows, 16 total source calls including the adopted original page-1 call, retry 0.
Offline audit and full Landing-to-Normalized comparison passed. The immutable
Normalized artifact contains 152,676 rows for source reference dates
2020-07-14..2026-08-12; publication timing remains unknown and predictive use is
blocked. This document is retained as execution evidence, not current authority.

This bounded collector is frozen to the independently audited current-scope evidence:

- unfiltered source history: row-level `basDt` varies; frozen observed maximum `20260812`
- declared rows: `152,676`
- page size: `9,999`; exact pages/call cap: `16`
- serial HTTPS, retry 0, shared DATA.GO.KR provider lock
- each exact response body and parsed Landing page is durable before checkpoint advance
- resumable only from contiguous, hash-verified pages; provider/schema anomaly is terminal
- no Dataset artifact or production Normalized/state write during capture

The registered source-observation contract uses `(capture_id, source_item_ordinal)` and
keeps snapshot date, capture time, page/body hashes, source record hash, event-effective
date, reason, and share count. Historical publication timing is unknown; predictive use
is blocked. `issuStckCnt` is a signed source value: page 1 contains negative values,
which are retained exactly and counted in the audit rather than coerced or discarded.
Likewise, source issue-date placeholders such as `00000101` and `19999999` are
preserved in a source-token column; the parsed date stays null with an explicit status.

The historical plan inspection command was:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect_data_go_kr_stock_issuance_snapshot.py --print-plan
```

The original first full page remains retained in stopped run
`20260813T172725Z_e068322b55de43d99434b377c436f1bb`: it proved that the unfiltered
operation is history, not one-date current snapshot (95 distinct `basDt` values on
page 1). Preserve that terminal checkpoint. Adopt its exact page bytes into a new v2
run with zero calls, audit, then resume the remaining 15 pages. Never restart completed
pages or alter the frozen count evidence. These instructions are record-only now;
do not execute another capture from this runbook.
