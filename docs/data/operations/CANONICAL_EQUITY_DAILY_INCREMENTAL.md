# Canonical Equity Daily Incremental

Status: `DAILY_INCREMENTAL_READY` for an explicitly reviewed date whose official
D+1-business-day 13:00 KST publication deadline has passed.

## Trading-date authority

The equity chain distinguishes three states:

- `SOURCE_AVAILABLE`: both official data.go.kr streams return non-empty,
  exact-request-date KOSPI/KOSDAQ rows with valid schema. This is the strongest
  conclusion produced by the existing two-call availability sentinel.
- `PUBLICATION_WINDOW_PASSED`: the official D+1-business-day 13:00 KST deadline
  has passed. The reviewed deadline is supplied explicitly; code does not infer
  Korean business days.
- `FINALIZED_FOR_DAILY_INGEST`: after the publication window, both exact-date
  streams are non-empty and schema/PK/market validation passes.
- `CANONICAL_ACCEPTED_DATE`: after finalized confirmation, the price, market-cap,
  provider-universe, canonical-universe, Landing/hash, and atomic transaction
  gates all pass and the date is committed to canonical state.

These states are ordered and cannot be skipped. A non-empty response before the
window proves only `SOURCE_AVAILABLE`. Weekdays, Korean holidays, another provider, a current
snapshot, or the existence of a same-date row in an unrelated dataset cannot
establish either later state.

The official portal states that both services update after 13:00 on the business
day following the base date. This is daily-ingest finality authority only;
revision/correction remains `UNRESOLVED` and no historical `PIT_SAFE` claim is
created. The current retained maximum is `CANONICAL_ACCEPTED_DATE=2026-08-13`. A
2026-08-12 sentinel pair captured at 2026-08-14 00:41 KST was non-empty and was
later adopted/promoted. That observation demonstrates publication lag; it does
not define the general finality time or prove 2026-08-13 finalized.

## Planned transaction after the blocker closes

One date is one transaction containing immutable source Landing, normalized
price, market cap, provider universe, derived canonical universe, and canonical
accepted-date state. Candidate partitions must be validated and hash-bound
before promotion. Promotion must use a transaction journal and compare-and-swap
pre-manifests for every affected partition and state file. Any failure restores
all pre-transaction artifacts; no subset may classify the date as accepted.

Only after that transaction commits may the same date's market breadth be
calculated and atomically appended. Breadth failure does not roll back the
accepted source chain, but it must remain explicitly pending and cannot be
silently reported current.

Run one date with `scripts/manual/collect/canonical_equity_daily_incremental.py`, an
explicit ISO-8601 KST publication deadline, and
`--confirm-live-two-call-atomic`. Each run makes exactly two calls, retry zero.
The 2026-08-14 run passed its publication window but both exact-date streams
were valid empty, so it remains unaccepted and must not be retried automatically.
