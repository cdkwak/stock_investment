# KOSPI200 pre-2010 derivatives source decision

Status: **SOURCE_FOUND_WITH_LIMITS / LICENSE_GATE**

The free KRX Open API cannot fill this gap: its service catalogue limits futures
and options daily trading data to 2010-01-04 onward. The preferred authoritative
source is therefore the paid KRX Data Marketplace daily futures and options
products. FnGuide DataGuide is a fallback only if the vendor confirms the exact
historical coverage and permitted use in writing.

## Required scope

- KOSPI200 futures: listing inception in May 1996 through 2009-12-30.
- KOSPI200 options: 1997-07-07 through 2009-12-30.
- All listed contracts, with trade date, product/contract identity, maturity or
  expiry, OHLC, settlement price, volume, open interest, underlying, session,
  field units, and any historical specification changes.
- Raw contract rows only. A continuous contract or adjustment policy is a later,
  separately versioned Derived-layer decision.

Before purchase, obtain a written coverage statement, sample files spanning first
listing/expiry and the 2009-to-2010 boundary, delivery format, revision policy,
price, and a license that permits personal internal research and derived results.
Academic users should ask whether the published education/public-interest discount
applies. Do not buy or contact a vendor without user approval.

## Acceptance boundary

Accept a source only when source-owned contract identities, exact coverage starts,
session meaning, units, missing-value conventions, revision behavior, and license
are explicit. Retain provider files in Landing and keep the pre-2010 provider/session
boundary visible in any bridge. Do not infer missing contracts, continuous rolls,
or back-adjustments.

## Authoritative references

- [KRX Open API service list](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)
- [KRX futures data products](https://data.krx.co.kr/contents/MDC/DATA/datasale/index.cmd?prodType=FF&viewNm=dataProdList)
- [KRX options data products](https://data.krx.co.kr/contents/MDC/DATA/datasale/index.cmd?prodType=FO&viewNm=dataProdList)
- [KRX data purchase process](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA001.jsp)
- [KRX data licensing](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA004.jsp)
- [KRX distribution products](https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA002.jsp)
- [FnGuide DataGuide time-series guide](https://help-dataguide.fnguide.com/ko/articles/%EC%8B%9C%EA%B3%84%EC%97%B4-%EB%8D%B0%EC%9D%B4%ED%84%B0-%EB%B6%84%EC%84%9D%ED%95%98%EA%B8%B0-%EC%8B%9C%EA%B3%84%EC%97%B4)

KRX data-product contact details are published on the purchase page. They are an
external-action gate, not authorization for this repository to place an order.
