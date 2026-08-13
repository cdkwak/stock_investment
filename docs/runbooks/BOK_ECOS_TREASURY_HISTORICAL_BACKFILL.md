# BOK ECOS Korean Treasury historical source observations

Status: `ARTIFACT_COMPLETE_PROVENANCE_LIMITED / DO_NOT_RERUN`.

This collector creates a separate Normalized dataset,
`bok_ecos_kr_treasury_yield_source_observation`. It never writes, replaces, or
bridges the retained Toss `kr_treasury_yield_daily` OHLC/volume candles. BOK
ECOS distributes the KOFIA final-quotation yield as one daily annual-percent
value; equality with any Toss candle field is not assumed.

## Frozen source identity and ranges

The prepared plan is
`docs/examples/bok_ecos_treasury_backfill.prepared.json`, bound to the retained
metadata summary SHA-256
`c0174b89888fc986791d5abc4b5c6eb4d03911bfb9f0b7348d453422488d4372`.
Its computed plan SHA-256 is
`eb54595c04e5cbc6cca2522fe9beb555bc8222cefdb36dda507771ff5777a847`.

| Tenor | Item | Verified start | Frozen end | Weekday upper estimate |
|---|---|---:|---:|---:|
| 2Y | `010195000` | 2021-03-10 | 2026-08-13 | 1,417 |
| 3Y | `010200000` | 1998-11-13 | 2026-08-13 | 7,240 |
| 5Y | `010200001` | 2000-01-04 | 2026-08-13 | 6,943 |
| 10Y | `010210000` | 2000-12-18 | 2026-08-13 | 6,694 |
| 20Y | `010220000` | 2006-01-25 | 2026-08-13 | 5,362 |
| 30Y | `010230000` | 2012-09-11 | 2026-08-13 | 3,633 |

The 31,289 total is only a Monday-Friday upper estimate, not an expected source
row count; holidays and source gaps are retained as absent observations. Every
individual range is below the fixed 10,000-row response cap.

## Exact request and safety budget

- operation: `StatisticSearch`, one full-range response per verified item;
- source responses: 6, but live backfill requests: exactly 5; the already
  captured and audited 3Y response is adopted and must not be requested again;
- hard live-request cap: 5; maximum response rows: 10,000 each;
- maximum accepted observations: 60,000 across all responses;
- one serial BOK ECOS stream, 3-5 seconds random delay between calls;
- timeout 30 seconds, retry 0, no pagination, fallback, or automatic range split;
- any HTTP error, HTML, invalid JSON, identity mismatch, truncation, duplicate
  source date, out-of-range date, invalid decimal, or excess precision stops the
  run and leaves prior checkpointed scopes intact;
- a request failure is never resumable. A clean stop between checkpointed scopes
  resumes from the next scope without repeating completed requests.

The adoption path accepts only a `PAGE_SEMANTICS_PASS_REVIEW_REQUIRED` 3Y run
with one HTTP-200 response, retry 0, no Normalized write, exact plan/metadata
hashes, exact endpoint/count summary, and byte-identical Landing hashes across
the source ledger and checkpoint. The backfill copies that immutable body into
its own Landing, records `ADOPTED_RESPONSE` rather than `HTTP_RESPONSE`, retains
the original capture ID/time, and lowers its live cap from six to five.

The approved execution completed on 2026-08-13. All five live calls returned
HTTP 200 with retry 0, and the adopted 3Y response reconciled exactly. The final
artifact contains 29,674 rows across 29 yearly Parquet files, covers
1998-11-13 through 2026-08-13, and passed full Landing-to-Parquet equality,
schema, PK, range, null-semantics, state-manifest and secret-leak audits.

The access key is a URL path segment. Full URLs are never logged. The ledger
contains only the redacted route, scope, sequence, status, elapsed time, byte
count, capture timestamp, and body SHA-256. Each body is written immutably to
Landing before status/schema parsing.

## Layers and identity

```text
data/landing/bok_ecos_kr_treasury_yield_source_observation/run_<id>/
  response_01_2Y.json ... response_06_30Y.json
  call_ledger.jsonl
  checkpoint.json

data/normalized/bok_ecos_kr_treasury_yield_source_observation/year=<year>/data.parquet
data/state/bok_ecos_kr_treasury_yield_source_observation.json
```

The immutable observation key is
`(capture_id, source_item_code, source_item_ordinal)`. This deliberately permits
a later independently captured response to coexist for revision comparison.
Landing body hash, capture timestamp, source table/item identity and original
ordinal remain on every row.

`published_at_utc` and `revision_id` remain null because neither is supplied by
the verified response. `availability_status` is always
`blocked_unknown_first_publication_and_revision`. Therefore the artifact is not
eligible for predictive features solely because historical values were fetched.

Yield is stored exactly as `decimal(9,3)` in `annual_percent`. Missing source
dates are not synthesized and source-empty values are not converted to zero.

## Review and execution boundary

Importing the collector performs no I/O. The CLI refuses live work without all
of the following: configured `BOK_ECOS_API_KEY`, exact retained metadata digest,
exact approved plan digest, and `--confirm-live-historical-backfill`.

The executed command is retained below only as provenance. Do not rerun it to
refresh or recreate the completed artifact:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\backfill_bok_ecos_treasury.py `
  --project-root . `
  --plan .\docs\examples\bok_ecos_treasury_backfill.prepared.json `
  --approve-plan-sha256 eb54595c04e5cbc6cca2522fe9beb555bc8222cefdb36dda507771ff5777a847 `
  --adopt-3y-page-run-dir .\data\landing\diagnostics\bok_ecos_treasury_page_semantics\run_20260813T123713Z_65cbbb0ce39245a6b9f26a2cc6a137be `
  --confirm-live-historical-backfill
```

Any future refresh must use a new reviewed observation plan and capture ID; it
must never overwrite or silently equate existing BOK ECOS or Toss observations.

On success, audit all six Landing hashes against ledger/checkpoint, verify exact
contract Arrow schema, PK uniqueness, source ranges and state/Parquet hashes.
Promotion beyond `ARTIFACT_COMPLETE_PROVENANCE_LIMITED` requires independent
evidence for historical first-publication and correction/supersession semantics.
