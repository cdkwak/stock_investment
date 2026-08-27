# A007 Investor H1 historical availability diagnostic

The one authorized Landing-only diagnostic was retained at
`20260813T103525Z_47ad701d154b430e89f18434bd152031`. A subsequent offline audit
made zero network calls and did not alter that run.

The retained ledger contains five authentication responses and exactly one
business response, all HTTP 200 with raw sequences 1..6, followed by
`DIAGNOSTIC_PASSED`. The 189-byte body SHA-256
`6ead29ac104ea3da7499b31e089e2f3634107d452a62278fc02d3859f4003c32`
matches both provenance and business-ledger records. Manifest, provenance,
scope, 502 expected dates, request counts, schema, and run ID reconcile. The
retained body has exactly one `2012-01-04` row and all four components plus total
are zero, so it satisfies the exact `PRE_AVAILABILITY_COLLAPSE` branch. A scan
against locally configured secret values and common credential/key labels found
no persisted credential. H1 must not be executed again.

The audit is reproducible without network access. Dry-run is the default and
does not write beneath the retained run:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\audit\verify_a007_investor_h1.py `
  .\data\landing\diagnostics\a007_investor_h1\20260813T103525Z_47ad701d154b430e89f18434bd152031
```

The verifier regenerates the manifest from the checksum-bound current canonical
date sources, validates the full request/ledger/provenance/hash/classification
chain, checks locally configured KRX values and sensitive key names without
persisting or printing secret values, and rechecks the original artifact hashes
after verification. `--write-append-only-evidence` explicitly opts into an
immutable content-addressed JSON under the run's `offline_verifications/`
directory. Repeating the same write is idempotent; conflicting content stops.

The frozen scope is exactly one authenticated `MDCSTAT30301` KOSPI volume
request for `2010-01-04..2012-01-04`. The expected set is exactly 502 canonical
KOSPI dates derived from checksum-bound retained 2010, 2011, and 2012 universe
partitions. Limits are one business request, six total raw HTTP requests, zero
retries, no redirects, and parallelism one.

Before business I/O the runner verifies the authenticated transport, explicit
zero-retry adapters, POST endpoint, complete form body, no query/JSON body, and
the shared `data/state/d_owned_krx_short_selling.lock`. The response is captured
losslessly with SHA-256 provenance and an append-only ledger before parsing.
HTML/restriction content, HTTP failure, malformed JSON, unexpected top-level or
row fields, duplicate/out-of-scope dates, invalid/nonnegative integers, or an
investor-total mismatch stop immediately. No checkpoint or Normalized output is
written.

Classification is intentionally narrow:

- exact 502-date set: `H1_FULL_RANGE_AVAILABLE`;
- exactly one `2012-01-04` row with every investor component and total equal to
  zero: `PRE_AVAILABILITY_COLLAPSE`;
- every other valid-shaped subset or value pattern: `AMBIGUOUS_STOP`, with no
  retry.

If separately authorized later, execution requires all four confirmations:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnostic\diagnose_a007_investor_h1.py `
  --acknowledge-no-active-krx-stream `
  --confirm-one-live-request `
  --confirm-landing-only `
  --confirm-scope 20100104_20120104_KOSPI_volume_H1_availability
```

Preserve the single Landing result regardless of classification. Never retry
H1, synthesize missing dates/zeros, or resume Investor from this diagnostic.
