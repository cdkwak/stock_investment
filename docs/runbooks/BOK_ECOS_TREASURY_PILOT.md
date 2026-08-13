# BOK ECOS official Treasury close-yield pilot

Status: `METADATA_CAPTURED_REVIEW_REQUIRED / VALUES_NOT_AUTHORIZED`.

This is a two-phase diagnostic, not a dataset collector. It makes no KRX call,
does not write Normalized data, and cannot alter the retained Toss Treasury
artifact. The implementation and tests were completed with local fixtures; no
live BOK, KOFIA, KRX, pykrx, or Toss request was made.

## Decision boundary

The pilot can determine whether a BOK ECOS daily close-yield series is a
defensible separate official observation. It cannot retroactively assign a
publication timestamp to Toss OHLC candles.

Even a matching official value remains blocked for predictive use unless the
official metadata or a contemporaneous capture establishes when that vintage
became public. `source_date`, `published_at`, and `captured_at` are distinct:

- `source_date`: ECOS `TIME`, validated against the one-date request scope;
- `published_at`: null unless the source explicitly supplies it;
- `captured_at_utc`: local HTTP response time, sufficient only for that captured
  vintage and never backdated to `source_date`.

## Preconditions

D must obtain the following from the official ECOS UI or documentation without
guessing:

1. one daily table code and exact table name;
2. exact item code, item name, and unit label for all six tenors
   (2Y/3Y/5Y/10Y/20Y/30Y);
3. four exact, distinct diagnostic dates:
   `recent_normal`, `two_year_introduction_boundary`,
   `retained_source_gap`, and `early_2019`;
4. a BOK ECOS API key placed only in the process environment as
   `BOK_ECOS_API_KEY`.

The repository contains no inferred table/item code and no executable default
configuration. The runner rejects placeholder codes, incomplete six-tenor
identity, a non-daily cycle, repeated dates, and non-canonical ordering.

Configuration schema (labels below are deliberately non-executable):

```json
{
  "table_code": "TABLE_CODE_FROM_REVIEW",
  "table_name": "exact ECOS table name",
  "cycle": "D",
  "tenors": {
    "2Y": {"item_code": "ITEM_2Y_FROM_REVIEW", "item_name": "exact name", "unit_name": "exact unit"},
    "3Y": {"item_code": "ITEM_3Y_FROM_REVIEW", "item_name": "exact name", "unit_name": "exact unit"},
    "5Y": {"item_code": "ITEM_5Y_FROM_REVIEW", "item_name": "exact name", "unit_name": "exact unit"},
    "10Y": {"item_code": "ITEM_10Y_FROM_REVIEW", "item_name": "exact name", "unit_name": "exact unit"},
    "20Y": {"item_code": "ITEM_20Y_FROM_REVIEW", "item_name": "exact name", "unit_name": "exact unit"},
    "30Y": {"item_code": "ITEM_30Y_FROM_REVIEW", "item_name": "exact name", "unit_name": "exact unit"}
  },
  "dates": {
    "recent_normal": "YYYYMMDD",
    "two_year_introduction_boundary": "YYYYMMDD",
    "retained_source_gap": "YYYYMMDD",
    "early_2019": "YYYYMMDD"
  }
}
```

## Exact two-phase budget

| Phase | ECOS operation | Hard raw-request cap | Hard observation cap | Purpose |
|---|---|---:|---:|---|
| metadata | `StatisticItemList` | 1 | 0 data observations | Validate exact table, daily cycle, six item identities, units, and source start/end metadata. |
| values | `StatisticSearch` | 8 | 16 | Two reviewed tenors (2Y, 3Y) x four reviewed dates, one date/item per request, maximum two returned rows each. |
| total | — | 9 | 16 | No retry, pagination, discovery fan-out, or automatic continuation. |

The metadata phase stops with `METADATA_CAPTURED_REVIEW_REQUIRED`. D must
inspect the immutable raw response and six-tenor summary, then pass the exact
summary SHA-256 to the value phase. The runner refuses a different config,
modified metadata Landing, altered summary, wrong digest, or unreviewed status.

The one-call metadata response was captured on 2026-08-13. It showed that the
API's exact `STAT_NAME` includes the official hierarchy prefix
`1.3.2.1. `, so the original strict parser stopped before producing a summary.
The retained HTTP-200 response and exact ledger were reconciled offline and the
reviewed config was corrected to the source label; no second request was made.
The value phase remains unauthorized until D independently approves the new
immutable metadata-summary digest.

## Storage and audit rules

All output is diagnostic:

```text
data/landing/diagnostics/bok_ecos_treasury_pilot/
  metadata_<run_id>/
    response_01_item_metadata.json
    metadata_summary.json
    checkpoint.json
    call_ledger.jsonl
  values_<run_id>/
    response_01_<date>_2Y.json ... response_08_<date>_3Y.json
    observations.json
    comparison_to_toss.json
    checkpoint.json
    call_ledger.jsonl
```

- Response bodies are written immutably before parsing.
- The append-only ledger stores only a redacted route, scope, sequence, HTTP
  status, elapsed time, byte count, and response SHA-256. It never stores the
  full URL because the ECOS key is a path segment.
- A response containing the literal credential is rejected and not persisted.
- Checkpoint identity includes config and approved-metadata hashes. A completed
  scope resumes only when Landing and checkpoint hashes match exactly.
- Ledger HTTP sequences and completed scopes must reconcile. Retry0 forbids
  resuming a run after an HTTP request failure.
- A process lock under the diagnostic Landing root prevents overlapping BOK
  pilot execution.
- HTTP timeout is 20 seconds. No retry adapter, loop, sleep loop, pagination, or
  fallback source exists.

## Parser and identity gates

Only documented ECOS envelope/field names used by this pilot are parsed:

- metadata: `StatisticItemList.list_total_count/row`, `STAT_CODE`, `STAT_NAME`,
  `ITEM_CODE`, `ITEM_NAME`, `CYCLE`, `UNIT_NAME`, `START_TIME`, `END_TIME`;
- values: `StatisticSearch.list_total_count/row`, `STAT_CODE`, `STAT_NAME`,
  `ITEM_CODE1`, `ITEM_NAME1`, `UNIT_NAME`, `TIME`, `DATA_VALUE`;
- documented no-data result: `RESULT.CODE = INFO-200`, retained as
  `VALID_EMPTY`, never converted to zero.

Every table/item/name/unit/date must equal the reviewed configuration.
`DATA_VALUE` must be finite decimal text. The diagnostic observation sets
`published_at = null`, `revision_id = null`, and
`availability_status = blocked_unknown_first_publication_and_revision` because
the above value response does not establish those semantics.

The six-tenor metadata gate must pass before values, even though the bounded
value sample uses only 2Y and 3Y. This prevents a two-item success from being
misreported as evidence that all desired tenors share one series definition.

## Toss comparison and revision policy

The value phase reads retained Toss partitions only when the exact date/tenor
exists and writes a separate diagnostic comparison. Results are classified as
`EXACT_VALUE_MATCH`, `DISTINCT_SERIES_CANDIDATE`, or `TOSS_MISSING`.
`compatibility_inferred` is always false. No Toss row, contract, field, unit,
or availability date is changed.

One matching value does not establish series identity. Promotion requires
source methodology, six-tenor metadata, history, and publication semantics.
Future repeated observations must use new immutable run directories and be
compared as vintages. A changed official value is a new observed version, never
an overwrite. If ECOS exposes no source revision ID or vintage timestamp,
predictive eligibility begins no earlier than each actual `captured_at` and
historical PIT remains blocked.

## Exact future commands — do not execute without D approval

Metadata phase:

```powershell
$env:BOK_ECOS_API_KEY = '<process-only key>'
.\.venv\Scripts\python.exe .\scripts\manual\pilot_bok_ecos_treasury.py --project-root . --config <reviewed-config.json> --phase metadata --confirm-live-manual-pilot
```

After D independently reviews the raw metadata and copies the emitted digest:

```powershell
$env:BOK_ECOS_API_KEY = '<process-only key>'
.\.venv\Scripts\python.exe .\scripts\manual\pilot_bok_ecos_treasury.py --project-root . --config <same-reviewed-config.json> --phase values --metadata-run-dir <metadata-run-dir> --approve-metadata-sha256 <exact-sha256> --confirm-live-manual-pilot
```

Resume is allowed only for a clean, partially checkpointed values run with no
request-failure event:

```powershell
$env:BOK_ECOS_API_KEY = '<process-only key>'
.\.venv\Scripts\python.exe .\scripts\manual\pilot_bok_ecos_treasury.py --project-root . --config <same-reviewed-config.json> --phase values --metadata-run-dir <metadata-run-dir> --approve-metadata-sha256 <exact-sha256> --resume-run-dir <values-run-dir> --confirm-live-manual-pilot
```

The runner has no default live mode and refuses execution without the explicit
confirmation flag. This runbook does not authorize those commands.
