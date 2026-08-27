# BOK ECOS Korean Treasury publication-finality observation

Status: `ACTIVE / DAILY_1710_SCHEDULER_INSTALLED / FINALITY_OBSERVATION_ONLY / NO_PROMOTION`

Current checkpoint: `BATCH_1_OF_3`. The 2026-08-26 17:00-18:00 KST batch
selected provider-native 2026-08-26 across all six tenors. Its prior-date
comparison is `PENDING_FIRST_BATCH`; the next batch must start at `20260826`.

This active operation is limited to BOK ECOS table `817Y002`, daily 2Y, 3Y,
5Y, 10Y, 20Y, and 30Y Korean Treasury percent yields. It observes publication
and next-provider-day revision behavior only. Historical backfill, Normalized or
Canonical promotion, automatic expected-latest inference, and numeric Dashboard
use remain prohibited until the review gate closes. The observation scheduler
itself is active because it writes only immutable diagnostic Landing/state and
cannot promote either daily dataset.

The source observation calendar is `PROVIDER_PUBLICATION`. The operation does
not use XKRX, Treasury futures, or a local business-day calendar to choose the
accepted date. In each batch, the six exact-tenor responses are requested over
one bounded range; the latest date common to all six provider responses is the
only selected provider-native date. The current calendar date is only a range
ceiling and is never itself treated as the expected date.

## Fixed operation policy

- Observation window: `17:00-18:00 Asia/Seoul`.
- Windows task: `STOCK_DATA_BOK_TREASURY_DAILY`, daily at exactly `17:10 KST`,
  `StartWhenAvailable=false`, `IgnoreNew`, and `PT15M`. It performs an API-zero
  no-op outside the window and after the three-batch review gate is reached.
- Required evidence: three separately executed provider-publication-day batches.
- Data requests: exactly six `StatisticSearch` calls at most, one per retained
  tenor, with retry zero and a 32-row response cap.
- Official UI evidence: one separate public `OSUUA02R03` table-information
  request. Its `prvsMrkYn` and `brknwsMrkYn` flags are stored separately from
  values. The response `header.ipAddr` is redacted before immutable diagnostic
  Landing because it is not source data.
- First range start: the reviewed retained boundary `20260813`. Later runs must
  start at the preceding batch's selected provider-native date.
- Landing root:
  `data/landing/diagnostics/bok_ecos_treasury_finality_observation/`.
- State: `data/state/bok_ecos_treasury_finality_observation.json`, version 1.
- Retained identity authority: the already reviewed six-tenor metadata summary
  with SHA-256
  `c0174b89888fc986791d5abc4b5c6eb4d03911bfb9f0b7348d453422488d4372`.
- No secret, key-bearing URL, headers, cookie, account identity, or raw client IP
  may enter logs, state, or documentation.

Landing is written before its response-ledger record. After all six responses
pass exact table/item/unit/date/duplicate checks, the operation records the
selected row and canonical row SHA-256 per tenor. On the next batch, the prior
selected date must be present in every tenor response; its fields and canonical
row bytes are compared with the preceding batch. Missing or changed evidence is
recorded and finality remains `UNKNOWN`.

## Execution and replay

The supported scheduler entry point is:

```powershell
.\.venv\Scripts\python.exe scripts/maintenance/run_bok_ecos_treasury_finality_observation.py `
  --project-root .
```

It checks the current state and window before loading `.env`. Manual diagnostic
execution inside the fixed window may use the lower-level pilot with the
existing runtime-only `BOK_ECOS_API_KEY`:

```powershell
.\.venv\Scripts\python.exe scripts/manual/pilot/pilot_bok_ecos_treasury.py `
  --project-root . `
  --phase finality-observation `
  --metadata-summary data/landing/diagnostics/bok_ecos_treasury_pilot/metadata_20260813T121302Z_c3273a9964264696b55827fbecc70880/metadata_summary.json `
  --approve-metadata-sha256 c0174b89888fc986791d5abc4b5c6eb4d03911bfb9f0b7348d453422488d4372 `
  --range-start-date 20260813 `
  --confirm-live-finality-observation
```

For later batches, omit `--range-start-date`; state supplies the exact previous
provider-native date. A completed same-window replay returns
`NOOP_ALREADY_SUCCEEDED` before credentials or network with API calls zero. A
complete Landing set stranded before state commit is finalized offline with API
zero. If state commits before the checkpoint's final `COMPLETE` write, the next
same-window API-zero run first reconciles exact state, Landing, ledger, selected
rows, response hashes, and UI marker, then hash-binds the checkpoint to state.
A partial retry-zero run is never resumed or adopted.

The first live batch used six `StatisticSearch` calls, one separate official UI
call, retry zero, and Normalized writes zero. Exact Landing/ledger hashes are
bound in the versioned state rather than copied into this mutable runbook. An
offline retained readback and the same-window CLI replay both returned API zero.
Owning validation passes 23 tests, including the state-first interruption
counterexample; no provider call is made during either recovery path.

## Review gate

Three consistent batches produce only
`THREE_BATCH_CONSISTENT_REVIEW_REQUIRED`. They do not prove permanent finality.
The Data owner must review all six availability dates, UI flags, prior-date
field/byte comparisons, call counts, and Landing hashes before proposing an
exact daily route. Any inconsistency or incomplete comparison keeps automatic
expected latest unset and finality `UNKNOWN`.
