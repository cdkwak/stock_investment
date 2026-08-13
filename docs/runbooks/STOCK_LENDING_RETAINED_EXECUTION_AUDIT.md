# Stock-lending retained-execution evidence audit

This offline audit verifies the retained FSC stock-lending evidence without
calling data.go.kr or changing Landing, Normalized, or checkpoint files. It
covers all three datasets: per-symbol detail, market aggregate, and participant
aggregate.

The audit proves:

- every retained JSON response is a successful, uniquely hashed response;
- historical pages are contiguous and agree on pagination totals;
- historical page/range checkpoints exactly match retained pages;
- operational checkpoints exactly match Normalized source dates;
- Parquet schema/order, year partitions, primary keys, required nulls,
  infinities, domain rules, row totals, and source-date coverage;
- market and participant source-absent dates relative to detail, without
  fabricating rows.

It does **not** resolve the historical execution-accounting incident. A wrapper
timeout left its child running and a resume overlapped it. Therefore the report
keeps `REVIEW_REQUIRED_OVERLAPPING_EXECUTION`, sets `exact_total_calls_known` to
false, and reports retained unique successful responses only as a lower bound.
Do not rerun the backfill to manufacture call accounting.

Dry run:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit_stock_lending_evidence.py --dry-run
```

Create or reuse an immutable content-addressed state:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit_stock_lending_evidence.py
```

The state is written atomically under
`data/state/audits/stock_lending_retained_execution/<audit SHA-256>.json`.
The writer independently rebuilds the entire report and immediately rechecks
all input files before creation. Existing identical state is reused; conflicting
content, path redirects, input mutation, or forged reports are rejected.
