# BOK ECOS Korean Treasury historical source observations

Status: `IMPLEMENTATION_READY / LIVE_EXECUTION_REQUIRES_INDEPENDENT_REVIEW`.

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

- operation: `StatisticSearch`, one full-range request per verified item;
- exact planned requests: 6; hard cap: 6; maximum response rows: 10,000 each;
- maximum accepted observations: 60,000 across all responses;
- one serial BOK ECOS stream, 3-5 seconds random delay between calls;
- timeout 30 seconds, retry 0, no pagination, fallback, or automatic range split;
- any HTTP error, HTML, invalid JSON, identity mismatch, truncation, duplicate
  source date, out-of-range date, invalid decimal, or excess precision stops the
  run and leaves prior checkpointed scopes intact;
- a request failure is never resumable. A clean stop between checkpointed scopes
  resumes from the next scope without repeating completed requests.

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

After independent review, the bounded command is:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\backfill_bok_ecos_treasury.py `
  --project-root . `
  --plan .\docs\examples\bok_ecos_treasury_backfill.prepared.json `
  --approve-plan-sha256 eb54595c04e5cbc6cca2522fe9beb555bc8222cefdb36dda507771ff5777a847 `
  --confirm-live-historical-backfill
```

Do not execute this command merely because the implementation exists. D must
first review the plan digest, provider-stream availability, current credential
status and whether a new historical capture is still required.

On success, audit all six Landing hashes against ledger/checkpoint, verify exact
contract Arrow schema, PK uniqueness, source ranges and state/Parquet hashes.
Promotion beyond `ARTIFACT_COMPLETE_PROVENANCE_LIMITED` requires independent
evidence for historical first-publication and correction/supersession semantics.
