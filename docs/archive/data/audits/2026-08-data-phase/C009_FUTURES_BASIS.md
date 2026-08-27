# C009 KOSPI200 Futures Nearest-Listed/Basis Design

## Decision

The retained futures rows support a deterministic **nearest source-listed
maturity** series. They do not support an inferred expiry calendar, a
pre-expiry roll schedule, back-adjustment, or a unit-normalized continuous
contract.

The implemented Derived dataset therefore selects the minimum retained
`maturity_month` independently for every `(date, bridge_segment, session)`.
It preserves provider and regular/night boundaries. `expiry_date` remains
null and the selection is not called an expiry-based front-month rule.

## Measured retained-data evidence

- Input provider bridge: 38,601 outright-contract rows, 4,086 dates,
  2010-01-04 through 2026-08-07.
- Every `(date, provider segment, session, maturity_month)` has exactly one
  contract; no ambiguous nearest maturity was found.
- Selected rows: 6,538 total: 2,466 legacy regular, 2,452 legacy night, and
  1,620 official regular.
- Selected close and spot values are non-null for all 6,538 rows.
- Settlement values are present for all 4,086 regular rows and absent for all
  2,452 legacy night rows.
- Provider-normalized value joins are exact and one-to-one: legacy via
  `(date, source_file_row_no)`, official via `(date, contract)`.
- The selected contract changes 40 times in each legacy session and 26 times
  in the official regular segment. Dataset-start rows are not transitions.
- At the 2019/2020 provider boundary, the legacy final date is 2019-12-30 and
  the official first date is 2020-01-02; the segments remain explicit.

## Roll alternatives

The selected nearest-listed rule is based only on source-observed identity.
Two same-day alternatives are reported in state for audit, not emitted as
additional datasets:

| Segment/session | Rows | Max-volume disagreements | Max-OI disagreements | Contract sequences (nearest / volume / OI) |
|---|---:|---:|---:|---:|
| Legacy regular | 2,466 | 0 | 74 | 41 / 41 / 41 |
| Legacy night | 2,452 | 0 | 34 | 41 / 41 / 41 |
| Official regular | 1,620 | 2 | 57 | 27 / 27 / 27 |

The volume rule nearly reproduces nearest-listed, but using same-day volume
as the definition would still be a different end-of-day selection policy.
Open interest rolls earlier on some dates. Neither alternative provides a
verified expiry date or justifies a calendar roll.

## Basis and session rule

For regular sessions only, `settlement_basis` is the same source row's
`settlement_price - spot_value`. It is explicitly labeled a source-native
price difference with unverified unit, not index points. Legacy night rows
retain their close and spot values, but settlement and basis remain null
because the night/session alignment is not verified. No regular/night merge
or date shift is performed.

## Predictive-use boundary

All selected values are end-of-day observations and are marked T+1 only.
There is no back-adjustment or return-continuity transformation. Research
requiring a tradable pre-close roll policy needs a separately specified
selection timestamp and verified expiry/session semantics.

