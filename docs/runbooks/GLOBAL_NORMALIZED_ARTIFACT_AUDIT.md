# Yahoo/FRED Normalized artifact audit states

This offline utility deterministically audits the three retained global
Normalized datasets:

- `global_index_price_daily`;
- `fred_treasury_yield_daily`;
- `fred_usd_fx_daily`.

It reads only local Normalized Parquet. It makes no HTTP request, reads no
Landing body, and never modifies collector checkpoints or Normalized data.
Because the original runs retained neither lossless provider responses nor call
ledgers, every report is permanently classified
`PROVENANCE_LIMITED_NO_RETAINED_LANDING`. File hashes cannot reconstruct or
prove missing source provenance.

Each report contains the registered contract and expected Arrow schema,
observed physical-schema groups, exact file path/bytes/rows/row-groups/SHA-256,
coverage, partition consistency, primary-key duplicates/nulls, all-column null
counts, floating infinity counts, and provider-specific data validation.
Physical Arrow nullability is reported separately from observed required-value
nulls so legacy writer metadata is not mistaken for a source-value failure.

Preview without writing:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit_global_normalized_artifacts.py `
  --project-root . --dry-run
```

Create immutable audit states:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit_global_normalized_artifacts.py `
  --project-root .
```

States are content-addressed at:

```text
data/state/audits/global_normalized_artifacts/<dataset>/<audit SHA-256>.json
```

Creation uses an atomic no-overwrite hard-link commit. Repeating an unchanged
audit returns `ALREADY_RECORDED` without changing bytes or timestamps. If a
Normalized artifact later changes, its new manifest receives a new state file;
the earlier state remains immutable. An existing path with different bytes is
an error, never an overwrite.

These files are local-artifact audit evidence only. They do not upgrade the
datasets to source-provenance complete, DATA_COMPLETE, point-in-time, or
revision-aware status.
