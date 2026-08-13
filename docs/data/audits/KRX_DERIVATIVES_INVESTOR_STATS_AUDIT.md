# KRX derivatives investor-statistics audit

Status: **PARTIAL_MANUAL_SOURCE_RETAINED / TARGETS_NOT_BUILT**

## Manual file integration, 2026-08-14

The user supplied 28 official KRX CSV downloads in `docs/krx_data`. They were
processed offline with zero KRX requests and copied byte-for-byte into the immutable
manual Landing snapshot whose inventory SHA-256 is
`53812ba1b32ea4185b33bb1083651319ac5dabe188b4362b15f2bbefd2ca2687`.
The manifest SHA-256 is
`d0105f3f5933e7e5eba07bba4f440a55988e2024d29fb8ad8dc387473af42cde`;
the independent audit is `2f5f2b726d4fa38e74030f3b8b519570f08a65c157c5027c82b91198249b49ac`.

All files are nonempty CP949 CSVs with the exact header `일자, 기관 합계,
기타법인, 개인, 외국인 합계, 전체`. They contain 6,753 physical rows and 6,734
unique dates from 1999-04-26 through 2026-08-13. Nineteen file-boundary dates overlap
with byte-equivalent logical values; there are no conflicts or duplicate file hashes.
The series misses zero retained canonical Korean trading dates through 2026-08-12
and adds the source date 2026-08-13. Category residuals after exact overlap removal
are -1 on 967 dates, zero on 4,459, and +1 on 1,308; source values are not corrected.

This is one futures net-buy series only. The CSV bytes do not identify whether the
measure is volume or trading value, its unit, or its session. They contain no sell,
buy, option, CALL/PUT, REGULAR/NIGHT, or detailed institutional-category series.
Therefore neither strict v1 target contract can be populated honestly, no Parquet
or empty options artifact was written, and no inferred values were introduced.
The originals remain in the manual inbox until the missing exports and their exact
download settings are supplied and the complete target artifacts pass validation.

Earlier on 2026-08-14 KST, the authenticated free KRX Data Marketplace Basic Statistics
screen `[15007] 투자자별 거래실적` was queried manually with bounded dates. No
CSV/Excel file was downloaded during that bounded discovery. The later user-provided
manual downloads are the separate retained evidence described above.

## Coverage finding

The screen rejects any start before 1999-01-04 for both KOSPI200 futures and
KOSPI200 options. One-day queries on 1999-01-04 through 1999-01-06 returned only
zero totals. A single 1999 daily-trend query for each product returned 174 rows,
with the first row on 1999-04-26 and the last on 1999-12-28. This exactly matches
the 174 retained Korean trading dates in that interval. Both products also
returned nonzero values on representative 2000-01-04, 2008-01-02, and 2009-01-02
queries.

Therefore the earliest confirmed meaningful source row is **1999-04-26** for
both products. The desired 1996-05 futures and 1997-07 options targets cannot be
met by this screen; those earlier gaps remain source-unavailable here. Continuity
is confirmed for the first available partial year and at the later sample dates,
not exhaustively for every intervening year.

## Source grain and fields

- Product choices are separate `코스피200 선물` and `코스피200 옵션` aggregates.
  The option screen can additionally select all rights, CALL, or PUT; both products
  can select all, regular, or night sessions.
- `기간합계` yields one row per investor category for the selected product,
  rights/session scope, and range. `일별추이` yields one date row with investor
  categories as columns for one selected measure and side.
- The source does not expose contract, maturity, strike, or expiry identity on this
  screen. Historical totals include contracts active on that date but cannot be
  allocated back to individual now-expired contracts.
- Fields are sell, buy, and net buy for both volume and trading value.
- Display units are selectable: volume `계약`, `천계약`, or `백만계약`; value
  `원`, `천원`, `백만원`, or `십억원`. The bounded checks used the defaults
  `계약` and `백만원`.
- Investor rows are `금융투자`, `보험`, `투신`, `은행`, `기타금융`,
  `연기금 등`, `기관합계`, `기타법인`, `개인`, `외국인`, and `합계`.
  KRX did not define historical taxonomy changes in this screen, so labels must be
  retained as source categories rather than assumed stable economic identities.
- The download popup explicitly offers Excel and CSV. It was inspected but not used.

## Request-cost boundary

The daily-trend screen limits a query to two years and exposes one measure/side
combination at a time. For the source-supported pre-2010 interval, six date chunks
per product are required. The complete requested dimensional scope (three sessions,
two measures, three sides, plus three option-right selections) costs 108 futures
and 324 options requests, or 432 total. A reduced ALL-session/ALL-right scope would
cost 36 requests per product, but would not satisfy the target contract.

KRX's current website terms prohibit unauthorized automated collection and copying.
The offline collector therefore requires a retained explicit KRX permission-evidence
SHA-256 and fails before transport or artifact creation without it. If permission is
obtained, freeze whether net buy is source-retained or derived-and-validated, exact
session/rights scope, taxonomy handling, date chunks, and request pacing, then begin
with one retry-zero Landing-first chunk pilot. Paid KRX/FnGuide data is not justified
until this free route is shown unusable or insufficient under permitted terms.

- [KRX Data Marketplace](https://data.krx.co.kr/)
- [KRX website terms](https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd)
- [Pre-2010 derivatives source decision](../../providers/KOSPI200_PRE2010_DERIVATIVES_SOURCE_DECISION.md)
