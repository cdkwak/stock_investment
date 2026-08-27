# KOSPI200 pre-2010 derivatives source decision

Status: **FREE_OFFICIAL_SOURCE_CONFIRMED / BULK_USE_TERMS_GATE**

The free KRX Open API begins in 2010, but the logged-in KRX Data Marketplace
Basic Statistics screens retain earlier official observations. Bounded manual
queries reproduced KOSPI200 futures on 1996-05-06 and KOSPI200 options on
1997-07-07, plus representative dates in 1998, 2000, 2008, and 2009. Paid KRX
products and FnGuide are therefore fallbacks, not the next action.

## Confirmed free scope

- **All Issues Prices** returns every listed contract for a selected historical
  date, including expired futures and option strikes/maturities. Confirmed fields
  include contract code/name, OHLC, change, settlement or next-day base price,
  spot or implied volatility where applicable, volume, turnover, and open interest.
- **Individual Issue Price Trend** exposes one contract over a range and includes
  date, OHLC, spot, settlement, volume, turnover, and open interest. An expired
  December 2022 contract was returned; the bounded check did not establish that
  the selector can address every 1996/1997 contract.
- **Nearest-Month Futures/Options Trend**, **Futures Basis**, and **Options P/C
  Ratio** returned inception-period rows. Range queries are limited by the UI to
  two years.
- **Strike/Maturity Price Table** exposes a current CALL/strike/PUT maturity matrix
  but no historical date control, so it is not a historical reconstruction route.
- All inspected result screens expose a download control. Excel and CSV choices
  were explicitly verified on All Issues Prices and Strike/Maturity Price Table;
  no file was downloaded in this audit.

The authoritative all-contract route is therefore technically feasible through
All Issues Prices. Using retained Korean trading dates as a planning estimate,
1996-05-06..2009-12-30 requires 3,496 futures-date queries and
1997-07-07..2009-12-30 requires 3,153 options-date queries (6,649 total). The
four range-series screens can each be partitioned into seven two-year-or-shorter
requests. These are planning counts, not authorization to automate or collect.

## Remaining gate

Before any bulk request, obtain or record an official interpretation of the site
and market-data terms for automated retrieval, persistent personal-research storage,
and derived-result use. The website terms prohibit copying, distributing, or
transmitting site information without prior KRX permission and defer market-data
use to separate terms. Free screen access alone is not a redistribution or bulk-use
license.

If bulk use is permitted, implement a resumable Landing-first collector with exact
date/product checkpoints, conservative pacing, retry zero for the pilot, immutable
request ledgers, and contract/schema validation. Preserve raw contract rows; any
continuous-contract or adjustment policy belongs in a later versioned Derived layer.
Only if KRX declines permission or the free screen cannot be collected reliably
should paid KRX history or a written-coverage FnGuide quote be reconsidered.

## Evidence

- [Bounded Basic Statistics audit](KRX_PRE2010_DERIVATIVES_TERMS.md)
- [KRX Data Marketplace terms](https://data.krx.co.kr/contents/MDC/INFO/informationController/MDCINFO003.cmd)
- [KRX Open API service list](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)
- [KRX futures data products](https://data.krx.co.kr/contents/MDC/DATA/datasale/index.cmd?prodType=FF&viewNm=dataProdList)
- [KRX options data products](https://data.krx.co.kr/contents/MDC/DATA/datasale/index.cmd?prodType=FO&viewNm=dataProdList)

Do not purchase, contact a vendor, or start bulk collection without user approval.
