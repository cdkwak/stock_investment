# BOK ECOS Korean Treasury publication-finality observation

Status: `THREE_BATCH_GATE_REACHED / API_ZERO_OBSERVER_IDLE / NO_PROMOTION`

Current checkpoint: `THREE_BATCH_CONSISTENT_REVIEW_REQUIRED` was reconciled and
reviewed from the retained 2026-08-26, 2026-08-27, and 2026-08-28 batches. Each
batch selected its same-day provider-native date across all six tenors. The
second and third batches found the preceding selected date unchanged for every
field and canonical row byte; all three separate table-information responses
reported `prvsMrkYn=N` and `brknwsMrkYn=N`.

This active operation is limited to BOK ECOS table `817Y002`, daily 2Y, 3Y,
5Y, 10Y, 20Y, and 30Y Korean Treasury percent yields. It observes publication
and next-provider-day revision behavior only. The reviewed evidence supports
`BOUNDED_THREE_BATCH_AVAILABILITY_CONFIRMED`, not a permanent provider promise.
Historical backfill, Normalized or Canonical promotion, automatic expected-
latest inference, predictive use, and numeric Dashboard use remain prohibited.
The observation scheduler remains installed but now exits before credentials or
network at the reached review gate and cannot promote either daily dataset.

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

Each of the three live batches used six `StatisticSearch` calls, one separate
official UI call, retry zero, and Normalized writes zero. Exact Landing/ledger
hashes are bound in the versioned state rather than copied into this mutable
runbook. The 2026-08-29 scheduler last receipt is `PASS / SUCCESS /
NOOP_REVIEW_GATE_REACHED`, retained batch count 3 to 3, API calls zero, and
Normalized writes zero. Owning validation covers both complete-Landing recovery
and the inverse state-first/checkpoint-second interruption without provider
calls.

## Reviewed gate decision

The retained gate supports only these bounded claims:

- `BOUNDED_THREE_BATCH_AVAILABILITY_CONFIRMED`: all six tenors shared the
  provider-native observation date at the 17:10 occurrence on three consecutive
  provider-publication days.
- The 2026-08-26 and 2026-08-27 rows were unchanged, field-for-field and
  canonical-byte-for-byte, when retrieved in the following batch.
- The table-level UI markers were `N/N` in all three captures; they remain
  separate table evidence and are not row-level API finality fields.
- `PERMANENT_FINALITY_UNKNOWN`: three observations do not establish an official
  publication clock, revision deadline, permanent lag, or predictive vintage.

The exact next daily-route gate is therefore provider-native and fail-closed: a
separate implementation may request only the bounded range from the last
accepted provider date through the current date, require one latest date common
to all six exact tenors, and require the preceding selected date to remain
unchanged before any descriptive use. It must not infer an XKRX/business-day
expected date. Any Normalized/Canonical promotion, automated expected-latest
policy, or Dashboard numeric route requires its own reviewed contract and is
outside this observation operation.
