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

## Fixed-snapshot completion sentinel

The B002-P3 runner is restricted in code to the already verified
`basDt=20191231`, KSD issuer customer `1115`, page 1, and `numOfRows=12`.
It holds the shared data.go.kr network lock, permits exactly one request with
zero retries, and writes the raw response envelope, redacted ledger, external
checkpoint, and hash-chain handoff before promotion. Only an HTTP-200/result-00
response containing exactly 12/12 unique records with the reviewed source fields
and identities can reach the existing append-only observation builder.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\run_rights_completion_sentinel.py
```

The authorized 2026-08-13 execution
`b002_p3_20260813T123002Z_8694970d70fb45f0b6d962173da34769` passed in one
request with zero retries. Response SHA-256:
`113ae1aaf5c8906afd15858f173e5772e37554a74afa9c2550fc122a80044fcd`.
The response contributed 12 immutable observations; together with the earlier
one-row response identity, the artifact contains 13 rows. This is intentional
observation provenance, not duplicate canonical events. Both locks were released.

A final zero-network audit reconciled the raw response SHA across envelope,
ledger, checkpoint, state snapshot, and the 12 corresponding Normalized rows.
It also verified the envelope/ledger/checkpoint/handoff file hashes, exact request
identity and source counts, two response identities, 13 state/artifact rows, zero
PK duplicates or nulls, no configured service-key value in retained evidence,
and both released locks. All gates passed. A scan of all locally retained
data.go.kr JSON and base64 response envelopes found no other small declared result
whose captured page is incomplete; the old 1/12 Rights envelope is the sole such
case and is already completed by B002-P3. No additional one-call completion probe
is justified from retained declared-count evidence.
