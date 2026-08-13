# Rights diagnostic source-observation promotion

This offline utility promotes an already retained B002-P1 response into the
registered version-2 `kr_equity_rights_schedule` Normalized contract. It makes
no source request. The retained diagnostic returned one record from a declared
twelve-record result, so its status is always
`PARTIAL_DIAGNOSTIC_SOURCE_OBSERVATION`.

The immutable observation key is `(landing_response_body_sha256,
source_item_ordinal)`. The utility verifies the handoff, envelope, decoded body,
and redacted call-ledger hash chain before parsing. It appends a new response
identity, validates the complete artifact, and promotes the dataset and state as
one rollback-protected transaction. A repeat of the same verified identity is
byte-idempotent.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\build_rights_observation.py `
  --project-root . `
  --diagnostic-root .\data\landing\diagnostics\b002_p1_rights\<run-id>
```

This artifact is not a canonical corporate-action event table, does not prove
historical completeness, and supplies no adjustment factor or announcement
date. Do not reclassify the canonical Rights blocker from this observation.
