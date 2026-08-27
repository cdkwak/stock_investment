# Offline Treasury-spread rebuild

`us_treasury_spread_daily` is derived only from the retained
`fred_treasury_yield_daily` Parquet files. The builder makes no network calls,
does not fill source nulls, and records every input/output file's SHA-256 and
row count plus the retained FRED state hash.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\manual\build\build_treasury_spread.py
```

The output directory is staged, read back, and atomically replaced only after
schema, date-key, formula, null-propagation, partition, and infinity checks
pass. The state is written atomically after the committed output is verified.

The resulting status remains `artifact_complete_provenance_limited`: the
manifest proves exact local derivation, but cannot manufacture the missing
lossless FRED Landing responses or call ledger.
