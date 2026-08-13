# KRX derivatives investor-statistics audit

Status: **FREE_OFFICIAL_SOURCE_CONFIRMED / P1_TARGET / NO_BULK_COLLECTION**

On 2026-08-14 KST, the authenticated free KRX Data Marketplace Basic Statistics
screen `[15007] 투자자별 거래실적` was queried manually with bounded dates. No
CSV/Excel file was downloaded and no collector, Landing artifact, or dataset was
created.

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
per product are required. Capturing source sell and buy for volume and value needs
at least 24 queries per product (48 total); retaining the source net-buy series as
well raises this to 36 per product (72 total). A one-day period-total approach
would be much more expensive and is not recommended.

Before collection, review KRX bulk/internal-use terms and freeze whether net buy is
source-retained or derived-and-validated, exact session/rights scope, taxonomy
handling, date chunks, and request pacing. Begin only with one retry-zero,
Landing-first chunk pilot. Paid KRX/FnGuide data is not justified until this free
route is shown unusable or insufficient under permitted terms.

- [KRX Data Marketplace](https://data.krx.co.kr/)
- [KRX website terms](https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd)
- [Pre-2010 derivatives source decision](../../providers/KOSPI200_PRE2010_DERIVATIVES_SOURCE_DECISION.md)
