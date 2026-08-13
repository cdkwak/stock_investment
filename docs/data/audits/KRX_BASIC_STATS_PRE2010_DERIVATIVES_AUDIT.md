# KRX Basic Statistics pre-2010 derivatives audit

Status: **FREE_OFFICIAL_SOURCE_CONFIRMED / NO_BULK_COLLECTION**

On 2026-08-14 KST, a logged-in KRX Data Marketplace session was used for bounded
manual queries only. No CSV/Excel file was downloaded and no historical backfill
or automated request stream was started.

## Historical checks

| Screen | Bounded evidence | Earliest confirmed row | Historical conclusion |
|---|---|---|---|
| All Issues Prices `[15001]` | Futures: 1996-05-06, 1998-01-05, 2000-01-04, 2008-01-02, 2009-01-02. Options: 1997-07-07, 1998-01-05, 2000-01-04, 2008-01-02, 2009-01-02 | Futures 1996-05-06 (4 contracts); options 1997-07-07 (60 contracts) | Expired contract/strike rows are available by historical trade date |
| Individual Issue Price Trend `[15002]` | Expired KOSPI200 December 2022 futures contract returned over its history | Not established for 1996/1997 | One-contract range route exists, but earliest-selector coverage remains unproven |
| Nearest-Month Futures Trend `[15003]` | 1996-05-06..1996-05-10 | 1996-05-06 | Inception-period nearest-month series available |
| Nearest-Month Options Trend `[15018]` | 1997-07-07..1997-07-11 | 1997-07-07 | Inception-period nearest-maturity strike rows available |
| Futures Basis `[15010]` | 1996-05-06..1996-05-10 | 1996-05-06 | Market/theoretical basis and divergence available |
| Options P/C Ratio `[15012]` | 1997-07-07..1997-07-11 | 1997-07-07 | PUT/CALL volume and ratio available |
| Strike/Maturity Price Table `[15013]` | Current CALL/strike/PUT maturity matrix | Historical date not selectable | Current snapshot only; unsuitable for historical reconstruction |

Representative all-issues counts were non-empty throughout the requested sample:
futures 4/4/4/7/7 rows and options 60/190/118/176/72 rows in the date order
shown above. This establishes coverage samples, not completeness of every date.

## Fields and delivery

- Futures contract rows: code/name, close/change/OHLC, spot, settlement, volume,
  turnover, open interest.
- Option contract rows: code/name, close/change/OHLC, implied volatility,
  next-day base price, volume, turnover, open interest.
- Futures basis: date, contract code/name, futures/spot/theoretical prices,
  market/theoretical basis, divergence percentage.
- Option P/C ratio: date, PUT volume, CALL volume, P/C ratio.
- Strike/maturity table: CALL values by maturity, strike, and PUT values by maturity.
- Every inspected screen exposed a download control. Excel and CSV were explicitly
  displayed on `[15001]` and `[15013]`; downloads were not executed.

The range screens reject periods longer than two years. An all-contract reconstruction
is feasible from one All Issues Prices query per trade date. The current retained
Korean calendar gives a planning estimate of 3,496 futures dates plus 3,153 option
dates, or 6,649 queries. Nearest-month, basis, and P/C series each require seven
two-year-or-shorter chunks for the missing era. Exact counts must be frozen from a
source-owned derivatives calendar before implementation.

## Decision boundary

Free official coverage is sufficient to defer paid KRX/FnGuide sourcing. Bulk
automation remains blocked by request-volume engineering and usage terms, not by
source absence. KRX's website terms prohibit unapproved copying/distribution and
point market-data users to separate terms. Obtain explicit bulk/internal-research
permission before implementing or running a collector. If approved, start with one
retry-zero Landing-first date pilot; do not infer missing contracts or construct a
continuous future during collection.

- [KRX website terms](https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd)
- [Source decision](../../providers/KOSPI200_PRE2010_DERIVATIVES_SOURCE_DECISION.md)
