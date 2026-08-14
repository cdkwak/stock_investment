# LS OpenAPI Source Inventory

Status: `BOUNDED_AUDIT_COMPLETE / NO_NORMALIZED_WRITES`  
As of: 2026-08-15 KST

This is the source-selection record for official LS OpenAPI data beyond the
separate [t8462 derivatives pilot](LS_OPENAPI_DERIVATIVES_PILOT.md). It does
not authorize a historical backfill, WebSocket subscription, account query,
or order API. Current project state remains in
[DATA_STATUS](../project/DATA_STATUS.md).

## Evidence and limits

- Static source: the current official [LS OpenAPI service catalogue](https://openapi.ls-sec.co.kr/apiservice)
  and its REST request/response definitions. The documented REST limit for all
  inspected TRs is 1 request/second.
- Live bounded evidence: 11 distinct read-only business scopes, HTTP 200 and
  `rsp_cd=00000`, retry 0, 235,584 Raw bytes, no Normalized writes.
- The first process stopped after two calls because a valid-empty 2000-01-04
  t1633 response omitted its output block. The retained response was correctly
  reclassified `VALID_EMPTY`; a second process hash-verified both prior calls
  and made only the remaining nine. Total: OAuth 2 across two processes,
  business calls 11 with no duplicate scope, API failures 0.
- Landing runs:
  `20260814T173559Z_d76143254f48415da2f41996cfd9be38` and continuation
  `20260814T173750Z_5392932a4f5e4c1fa39fb018bfb9ead4` under
  `data/landing/diagnostics/ls_openapi_source_inventory/`.
- Offline reconciliation: all 11 Raw bodies match provenance hashes/bytes;
  scopes are unique and plan-bound; checkpoint/ledger chains pass; configured
  LS credentials have zero retained-file matches. The access token was not
  persisted.
- A date-range input does not by itself prove full-range delivery. Pagination,
  current instrument universes, PIT membership, and undocumented units remain
  explicit gates below.

## REST inventory

`Observed earliest` means the earliest row in this bounded pilot, not source
inception. “Not observed” means no live call was justified.

| Area / TR | Endpoint and request | Main response fields | History / observed evidence | Unit and semantics | Existing overlap / estimated cost | Verdict |
|---|---|---|---|---|---|---|
| Program t1631 | `/stock/program`; market and same-day/period selectors | total, arbitrage, non-arbitrage sell/buy/net quantity and amount | Date-capable; not observed | Official labels exist; numeric unit not stated in the inspected catalogue | Overlaps KRX program backlog; one call per query | `CROSS_CHECK_ONLY` pending unit/history pilot |
| Program t1632 | same endpoint; market/intraday selectors | time-series program totals | Intraday/current; not observed | Session/finalization not retained | No accepted project dataset | `HIGH_VALUE_DAILY_SOURCE` candidate |
| Program t1633 | same endpoint; KOSPI/KOSDAQ, amount/quantity, value/cumulative, daily/weekly/monthly, `fdate/tdate`, continuation date | date/index; total, arbitrage and non-arbitrage sell/buy/net; volume | 2026-08-14 KOSPI and KOSDAQ: 1 row each. Exact 2000-01-04: valid empty. Earliest observed 2026-08-14; continuation advertised | Program categories verified; monetary/quantity multiplier not yet verified | Potentially replaces KRX program gap. Backfill cost unknown until boundary and page size are measured | `SEMANTICS_UNRESOLVED`, high-priority follow-up |
| Program t1636 / t1637 | same endpoint; current stock list / per-stock history | per-stock program flow | Per-symbol; historical mode exists | Current-universe fan-out creates survivorship risk | Inferior to a market-wide official source | `NOT_USEFUL` for broad backtest history |
| Program t1640 / t1662 | same endpoint; mini summary / intraday chart | current or intraday program aggregates | Snapshot/intraday only | Finalization unresolved | Useful only for live validation | `CROSS_CHECK_ONLY` |
| Surrounding funds t8428 | `/stock/investinfo`; `fdate/tdate`, series selector, market, count and `key_date` continuation | date/index/turnover; customer deposits, change, receivables, credit balance, futures deposits, stock/mixed/bond/MMF funds | 500 rows 2024-07-19..2026-08-12, continuation `Y`. A nominal 2000-01-04 request returned the latest 10 rows, proving the date fields do not directly bound delivery in that form. Earliest observed 2024-07-19 | Official units: monetary balance/change fields are KRW 100 million; turnover is percent | Complements lagged KB liquidity snapshot. About 14 pages for 2000-present if continuation remains 500 rows | `HIGH_VALUE_BACKFILL_SOURCE` after continuation/boundary pilot |
| Investor t1601 / t1615 | `/stock/investor`; market/product selector, no as-of date | current individual/foreign/institution and detailed institution flow | Current snapshot only | Quantity/amount choice varies by TR; finalization unresolved | Project already has superior official/Toss market history | `DUPLICATE_OF_BETTER_SOURCE` |
| Investor t1602 / t1603 / t1621 | same endpoint; time/count/current-prior-day selectors | intraday investor flow | Recent/intraday only | Session and provisional/final semantics unresolved | Possible live cross-check only | `CROSS_CHECK_ONLY` |
| Investor t1617 / t1664 | same endpoint; market/product, time/daily and count/continuation | KOSPI/KOSDAQ and futures/call/put/mini investor/program/basis fields | Count-based recent history; no explicit historical date range in inspected schema | Product coverage is broad but date/session semantics need a pilot | Duplicates completed equity investor history and t8462 derivatives flow | `CROSS_CHECK_ONLY` |
| Foreign/institution t1702 | `/stock/frgr-itt`; symbol and date range | detailed per-stock foreign/institution estimates | True per-symbol range; not observed | Official category describes estimated foreign/institution data | Must remain distinct from exchange-final values; survivorship risk | `CROSS_CHECK_ONLY` |
| Foreign holding t1716 / t1717 | same endpoint; symbol/date range and filters | KRX individual/institution/foreign, program; FSC listed/foreign holding and exhaustion-rate fields; short flow | t1716 Samsung: 250 rows 2025-08-06..2026-08-14 despite a 1990 start; no continuation advertised in response | Share/ratio/flow fields coexist; some are estimate/regulator series and must not be merged semantically | Per-symbol current-universe fan-out; overlaps official price/short/investor sources | `CROSS_CHECK_ONLY`, not a survivorship-safe backfill |
| ETF t1901 | `/stock/etf`; ETF code | current price, NAV/iNAV-related values, volume, disparity/tracking fields | Snapshot only | Price/NAV/ratio semantics present; catalogue does not establish every multiplier | Complements KRX ETF source | `HIGH_VALUE_DAILY_SOURCE` candidate |
| ETF t1902 | same endpoint; ETF code/time | intraday ETF price/NAV | Intraday only | Intraday timestamp/finality required | Realtime-style validation | `CROSS_CHECK_ONLY` |
| ETF t1903 | same endpoint; ETF code and continuation date | date, price, volume, NAV, NAV difference/change, tracking error, disparity, reference index | KODEX 200: 20 rows 2026-07-20..2026-08-14, continuation `Y` | Official fields verified; units still require cross-check | Per-current-symbol history is survivorship-unsafe as a full ETF universe; about one call/page/symbol | `CROSS_CHECK_ONLY`; KRX per-date universe source remains better |
| ETF t1904 | same endpoint; ETF code | current fund net assets/AUM, constituents, weights, PDF application date, valuation/capitalization | Current constituent snapshot | Documented AUM/net-assets unit is KRW 100 million | Useful forward PIT capture, not historical reconstruction | `HIGH_VALUE_DAILY_SOURCE` candidate |
| ETF t1906 | same endpoint; ETF code | LP quote state | Current only | Quote/session semantics | No historical research priority | `NOT_USEFUL` for current Data gaps |
| Futures current t2111 | `/futureoption/market-data`; contract code | price, volume/value, OI, Greeks/IV, basis | Current only; not observed | Product-native units require contract metadata | Cross-check for t8462/official KRX | `CROSS_CHECK_ONLY` |
| Futures daily t2214 | same endpoint; contract, recent-month flag, continuation date/code, count | date OHLC, volume, value, OI/change | Expired official-example contract `A0166000`: valid empty. Expired-contract availability therefore not established | Fields clear; multipliers not independently checked | Current t8467 master cannot reconstruct historical universe | `SEMANTICS_UNRESOLVED` / not a standalone backfill |
| OI t2424 | same endpoint; contract, 30-second/minute/day, interval, all/current, count | current price/volume/OI plus OHLC and open/high/low/close OI/change | Current KOSPI200 future: 20 daily rows 2026-07-20..2026-08-14 | OI is contracts; price/value scaling still contract-dependent | Useful current cross-check; overlaps KRX contract data | `CROSS_CHECK_ONLY` |
| Futures/options master t8467 / t8433 | same endpoint; product/master selector | current futures or options codes and price bounds/reference values | t8467 returned 13 current KOSPI200 futures/spreads; no expired universe | Current master semantics | Cannot remove historical-universe survivorship gap | `SNAPSHOT_ONLY` |
| Chart t8466 | `/futureoption/chart`; contract, daily/weekly/monthly, count, explicit start/end, continuation; 500 plain or 2,000 compressed rows documented | OHLC, volume, value, OI | Static schema supports ranges, but no pilot and no retained historical contract universe | Contract-native units unresolved | Potentially efficient once a licensed/official historical universe exists | `CROSS_CHECK_ONLY` pending universe evidence |
| Fundamentals t3320 | `/stock/investinfo`; symbol | company/fiscal-period metadata; PER, EPS, PBR, ROA, ROE, EBITDA, EV/EBITDA, SPS, CPS, BPS, dividend/current market cap fields | Samsung returned one current snapshot referencing fiscal periods 2025-12 and 2026-03; no as-of input | Current/provider financial summary | Useful descriptive snapshot only; not PIT-safe for backtests | `SNAPSHOT_ONLY` |
| Financial ranking t3341 | same endpoint; current ranking filters | valuation/financial ranking fields | No historical as-of input | Current ranking | Would cause look-ahead if treated as historical fundamentals | `SNAPSHOT_ONLY` |

## Realtime schema inventory

No WebSocket was opened. The current official catalogue exposed `BMT`
(time-of-day investor flow), `CUR`, and `MK2` under realtime investment
information. `BMT` is a `REALTIME_ONLY` candidate for later validation, not a
historical source. The requested whole-market/unified program feeds and ETF NAV
feeds were not identifiable as published cards in the current official
OpenAPI catalogue during this audit. They remain `SEMANTICS_UNRESOLVED`; legacy
Xing names or private web routes must not be promoted without a current official
OpenAPI definition.

## Recommended TOP 5

1. **t8428 surrounding funds** — highest incremental value. It can replace the
   lagged KB liquidity slice with official daily balances and has explicit
   documented KRW 100 million units. Next step is a two-page continuation pilot
   to bind `key_date`, page overlap, true earliest coverage, and call count.
2. **t1633 market program trading** — directly addresses a documented gap with
   KOSPI/KOSDAQ and arbitrage/non-arbitrage splits. Next step is a bounded
   boundary/unit pilot; the 2000 date was valid empty and does not prove source
   exhaustion.
3. **t1904 + t1901 ETF daily snapshot** — strong forward PIT value for AUM,
   constituents, weights, NAV and disparity. Keep t1903 only as a per-symbol
   cross-check because it cannot provide survivorship-safe historical membership.
4. **t2214/t2424/t8466 derivatives price-volume-OI cross-check** — combines well
   with t8462 for current daily validation, but is not a historical backfill until
   expired-contract universe and unit semantics are solved.
5. **t1716 foreign-holding cross-check** — useful for recent regulator/holding
   validation on a bounded symbol set. Do not fan it out as historical market
   data; its per-symbol/current-universe shape is survivorship-unsafe and estimate
   semantics must stay separate from exchange-final data.

t3320/t3341 are intentionally excluded from the TOP 5 for backtesting: without
an as-of input they are current snapshots, not point-in-time fundamentals.
