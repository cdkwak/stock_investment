# pykrx Short Investor range recheck

Status: `BACKFILL_COMPLETE` from the observed source boundary. The retained
diagnostics remain evidence that pre-2017 requests must not be synthesized.

## Retained execution

- pykrx: `1.2.8`
- Run: `20260814T190658Z_f49d29d329fb4418932e65102f9b0953`
- Landing: `data/landing/diagnostics/pykrx_short_investor_range_recheck/<run_id>/`
- Calls: 18 KRX business HTTP 200, no additional authentication request, retry 0.
- Evidence: 18 Raw bodies, 18 provenance sidecars, append-only ledger,
  diagnostic checkpoint and manifest. All body hashes/bytes and raw sequences
  1..18 reconcile; no 403/429/non-200 response.
- Source operation: `MDCSTAT30301`; volume values are shares and trading-value
  values are KRW under the existing source contract.

## Requests and results

Every successful Raw response is reverse chronological. Every pykrx DataFrame
is ascending and matches the Raw dates and five investor values exactly.

| Market | Metric | Requested range | Expected | Raw / DataFrame | Result |
|---|---|---:|---:|---:|---|
| KOSPI | volume | 2020-01-06..2020-01-10 | 5 | 5 / 5 | REGRESSION_PASS_MULTIROW |
| KOSPI | trading value | 2020-01-06..2020-01-10 | 5 | 5 / 5 | REGRESSION_PASS_MULTIROW |
| KOSPI | volume | 2026-08-07 | 1 | 1 / 1 | RANGE_PASS |
| KOSPI | trading value | 2026-08-07 | 1 | 1 / 1 | RANGE_PASS |
| KOSPI | volume | 2026-08-03..2026-08-07 | 5 | 5 / 5 | RANGE_PASS |
| KOSPI | trading value | 2026-08-03..2026-08-07 | 5 | 5 / 5 | RANGE_PASS |
| KOSPI | volume | 2026-07-10..2026-08-07 | 20 | 20 / 20 | RANGE_PASS |
| KOSPI | trading value | 2026-07-10..2026-08-07 | 20 | 20 / 20 | RANGE_PASS |
| KOSPI | volume | 2026-05-13..2026-08-07 | 60 | 60 / 60 | RANGE_PASS |
| KOSPI | trading value | 2026-05-13..2026-08-07 | 60 | 60 / 60 | RANGE_PASS |
| KOSDAQ | volume | 2026-07-10..2026-08-07 | 20 | 20 / 20 | RANGE_PASS |
| KOSDAQ | trading value | 2026-07-10..2026-08-07 | 20 | 20 / 20 | RANGE_PASS |
| KOSPI | volume | 2015-01-02..2015-01-08 | 5 | 1 / 1 | RANGE_END_ONLY; zero row on 2015-01-08 |
| KOSPI | trading value | 2015-01-02..2015-01-08 | 5 | 1 / 1 | RANGE_END_ONLY; zero row on 2015-01-08 |
| KOSPI | volume | 2010-01-04..2010-01-08 | 5 | 1 / 1 | RANGE_END_ONLY; zero row on 2010-01-08 |
| KOSPI | trading value | 2010-01-04..2010-01-08 | 5 | 1 / 1 | RANGE_END_ONLY; zero row on 2010-01-08 |
| KOSPI | volume | 2008-01-02..2008-01-08 | 5 | 1 / 1 | RANGE_END_ONLY; zero row on 2008-01-08 |
| KOSPI | trading value | 2008-01-02..2008-01-08 | 5 | 1 / 1 | RANGE_END_ONLY; zero row on 2008-01-08 |

## Interpretation and quality

- The installed pykrx integration example is reproducible for both measures.
- The largest verified recent safe window is 60 trading days for KOSPI; KOSDAQ
  is independently verified at 20 trading days.
- This is not a pykrx wrapper regression: all 18 Raw/DataFrame row counts and
  values match. The old collapse is present in Raw itself on pre-availability
  ranges.
- The behavior is date-dependent, not a general short-window or long-window
  failure. Recent 60-day ranges work, while 2008/2010/2015 five-day ranges
  return only a zero-valued range-end row.
- The earliest multi-row date in this recheck is 2020-01-06. Previously retained
  bounded evidence establishes positive source rows beginning at 2017-05-22,
  but the exact 2017-2019 60-day chunk behavior was not retested here.
- All non-collapsed rows have unique trading dates, no negative values, and
  exact `합계 = 기관 + 개인 + 외국인 + 기타`. Weekend and holiday dates inside
  calendar ranges are omitted rather than emitted as zeros.
- The historical range-end zero rows are not `VALID_EMPTY` and must never be
  promoted or expanded into missing dates.

## Backfill estimate and gate

Provisional safe chunk: 60 trading days, but only for the verified recent
segment. The retained calendar has approximately 2,265 trading days from the
earliest positive evidence date 2017-05-22 through 2026-08-14:

`2 markets x 2 measures x ceil(2,265 / 60) = 152 business calls`.

At the existing minimum eight-second interval this is at least 20.3 minutes,
plus authentication/network overhead. Estimated Raw plus provenance/ledger is
roughly 2-3 MiB. Restricting a first plan to the directly multi-row-verified
2020-01-06 start would be about 112 business calls.

The follow-up boundary run `20260814T192734Z_75d845399ad14397a5b23e3a33817a34`
returned 60/60 dates for KOSPI and KOSDAQ, both volume and trading value, for
2017-05-22..2017-08-14. The resulting production backfill completed 152
non-overlapping 60-trading-day scopes (KOSPI/KOSDAQ × volume/value) through
2026-08-07: 45,200 normalized rows, no PK duplicates or nulls, and a
`BATCH_COMPLETE` checkpoint. The pre-2017-05-22 gap remains explicit.
