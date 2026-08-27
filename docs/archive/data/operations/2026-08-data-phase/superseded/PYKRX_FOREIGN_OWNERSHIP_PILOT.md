# C010 authenticated KRX foreign-ownership readiness

Status: PILOT_COMPLETE / ACTIVE_CANDIDATE. Do not rerun this pilot and do not
start a historical backfill from this document.

## Retained bounded result

- Run: `20260814T184100Z_96ec447fea774002ad08b1160d7d9289`
- Landing: `data/landing/diagnostics/pykrx_foreign_ownership_pilot/<run_id>/`
- Exactly 20 business requests and 25 total HTTP responses including five
  authentication responses; retry 0; all 20 bodies hash-reconciled.
- Recent 2026-08-14 snapshots returned KOSPI 942 and KOSDAQ 1,821 rows. Against
  the latest retained canonical universe (2026-08-12), the intersection covers
  942/942 KOSPI and 1,820/1,820 KOSDAQ symbols; KOSDAQ additionally contains
  newly observed `487400`. This is a two-day identity comparison, not same-day
  canonical coverage.
- Full-market snapshots are non-empty for 1997-01-03, 2000-01-04, 2010-01-04,
  and 2026-08-14. The 1997 foreign-ownership fields are null for every returned
  row; the five target fields are materially populated by 2000-01-04. A
  1995-01-04 KOSPI snapshot is valid-empty.
- Samsung Electronics period queries return structural rows back to the
  canonical-history start, 1995-05-02, but the foreign-ownership fields are
  null throughout the retained 1995 and 1997 probes. A 1990 probe is
  valid-empty. Therefore `1995-05-02` is the earliest observed reachable row,
  while `2000-01-04` is the earliest observed date with complete target
  semantics; neither is claimed as the provider's exact inception date.
- Historical delisted-symbol probes succeeded for KOSPI `003410` (127 rows,
  2024-01-02..2024-07-08) and KOSDAQ `030270` (246 rows, 2019-01-02..2019-12-30).
- KRX Samsung on 2026-08-14 reports 2,730,484,137 held shares and 46.70%; the
  retained LS t1716 observation reports 2,736,287,683 and 46.80%. The mismatch
  prevents treating LS as an interchangeable source and is retained as a
  cross-check/timing-semantics issue.

## Verified source semantics

- Installed pykrx source maps `MDCSTAT03701` to full-market/date and
  `MDCSTAT03702` to per-symbol/date-range.
- Full-market `balance_limit=0` means no restriction-only filter; `1` would
  return only foreign-ownership-limit securities and is not suitable for a
  complete market dataset.
- Source fields are `LIST_SHRS`, `FORN_HD_QTY`, `FORN_SHR_RT`,
  `FORN_ORD_LMT_QTY`, and `FORN_LMT_EXHST_RT`. Counts are shares and ratios are
  percentages, not fractions.
- pykrx's public wrapper casts ratios to float16 and replaces blanks with zero.
  A provider parser must instead parse the lossless raw response, preserve
  missing separately from valid zero, and use a non-lossy decimal/float64 type.

## Candidate contract

Candidate name: `kr_equity_foreign_ownership_daily`; candidate primary key:
`(date, market, symbol)`. Proposed source-preserving columns are `date`,
`market`, `symbol`, `source_name`, `listed_shares`, `foreign_held_shares`,
`foreign_ownership_percent`, `foreign_order_limit_shares`,
`foreign_limit_exhaustion_percent`, `source_operation`, and `collected_at`.

This is not yet a registered DatasetContract. The parser must preserve raw
zeros and nulls; reject duplicate keys, negative share/ratio values, nonfinite
values, malformed dates/symbols, and schema drift. Ratio recomputation is a
diagnostic tolerance check (0.02 percentage points), not a source rewrite.

## Survivorship and point-in-time policy

The historical collector, if later approved, must use `03701` full-market/date
responses. It must never fan out from today's ticker list or use `03702` to
construct the universe. `03702` is bounded QA only.

Source rows are labeled by trading date, but retained evidence does not establish
the original publication timestamp, later revisions, or historical availability
time. `availability_date` remains unknown and predictive use is blocked until
timing/revision behavior is separately verified. Listing shares and foreign
order limits must not be filled from current master data.

## Backfill decision

Classification: `HIGH_VALUE_BACKFILL_SOURCE`, with predictive use still blocked
by unresolved publication/revision timing.

- Preferred shape: `MDCSTAT03701` full-market/date snapshots. It preserves
  historical membership and delisted securities. The retained canonical
  calendar contains 13,118 KOSPI/KOSDAQ market-date scopes from 2000-01-04
  through 2026-08-12; later trading dates would be appended explicitly.
- Rejected primary shape: `MDCSTAT03702` ticker/range fan-out. Even a
  current-only 2,763-symbol list needs more than 38,000 two-year range calls
  for 2000-present, before adding historical/delisted identities. It requires
  historical ISIN identity, range splitting, and many security-period calls;
  ticker/ISIN reuse and missing delisted identities create omission/duplicate
  risk.
- Before backfill, define and review the contract, publication/revision policy,
  exact start boundary, call budget, checkpoint/resume, and source calendar.
  Normalized publication remains forbidden.

The retained pilot authorizes only contract/parser and backfill-plan review.
