# LS OpenAPI Source Inventory

Status: `T8428_T1633_FOLLOWUP_COMPLETE / NO_NORMALIZED_WRITES / NO_SCHEDULE`

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

## t8428 and t1633 promotion follow-up

The bounded follow-up run
`data/landing/diagnostics/ls_t8428_t1633_followup/20260814T180318Z_89b4b2fc50cf4a18adb3ca03f3b48813`
used OAuth once and made exactly 17 serial business calls with retry 0: five
t8428 pages and twelve t1633 single-date scopes. All were HTTP 200 / source
success; Raw, provenance, ledger, checkpoint, plan and secret scan reconcile.
Raw size was 757,040 bytes and no Normalized or schedule write occurred.

### t8428 pagination and semantics

- Official continuation requires both the response header continuation signal/key
  and the body cursor: next request `key_date` is the previous
  `t8428OutBlock.date`; subsequent request headers use `tr_cont=Y` and the returned
  `tr_cont_key`. All five pages had a one-character header key and `tr_cont=Y`.
- Page ranges were 2024-07-19..2026-08-12, 2022-07-08..2024-07-19,
  2020-07-02..2022-07-08, 2018-06-26..2020-07-02, and
  2016-06-13..2018-06-26. Each page had 500 unique, strictly descending dates.
- Adjacent pages repeat their cursor date. There were 2,500 physical rows,
  2,496 unique dates, four identical boundary overlaps and zero conflicting
  overlaps. Deduplication is safe only after equality validation.
- Against the retained KOSPI trading-date set for the observed interval, t8428
  omitted 2022-12-28 and 2024-08-29 and included 2018-12-31, 2019-12-31 and
  2021-12-31. It is a provider business-statistics calendar, not an equity
  trading-calendar series; no missing date may be synthesized.
- The official request calls `fdate/tdate` an output period, but both an exact
  2000-01-04 request and the bounded broad request began at the latest available
  observation. Actual traversal is controlled by `cnt` plus continuation cursor.
  The exact-date response therefore did not test the year 2000 at all.
- The fifth page still advertised continuation. Earliest observed is 2016-06-13;
  a rough density estimate is about 14 total 500-row pages to reach 2000, but the
  source floor is unproven. Conservative verdict: `PAGINATION_UNRESOLVED`
  (mechanics verified, earliest reachable date not yet reached), not
  `FULL_BACKFILL`.
- Official field identities and units are fixed: `custmoney` customer deposits,
  `yecha` deposit change, `outmoney` receivables, `trjango` credit balance and
  `futymoney` futures deposits, all KRW 100 million; turnover is percent. Fund
  balance fields are also KRW 100 million.
- On common source date 2026-08-12, LS values equal the KB IVSA0070 values times
  10 within 4..8 KRW 100 million rounding units for all five compared fields.
  KB captured that row on 2026-08-14; LS still reported 2026-08-12 as latest in
  the 2026-08-15 KST pilot. This confirms the same lagged source family and a
  coarser KB display scale; LS is the preferable historical source.

### t1633 program semantics and history

- Official input `gubun1=0` means amount and `gubun1=1` quantity. The response
  labels are total, arbitrage and non-arbitrage buy/sell/net (`tot*`, `cha*`,
  `bcha*`). The suffix mapping is explicitly: `1=buy`, `2=sell`, `3=net`.
- Both KOSPI and KOSDAQ returned exact rows for 2026-08-13, 2026-07-31,
  2026-01-02, 2025-01-02 and 2021-01-04. The prior 2000-01-04 probe remains
  source-success valid-empty. Earliest positive evidence is therefore 2021-01-04;
  the floor lies somewhere earlier and is not yet established.
- Quantity and amount buy-sell-net identities reconcile exactly or within one
  source unit. Arbitrage plus non-arbitrage net likewise differs from total by at
  most one, consistent with provider rounding; raw values must never be rewritten.
- For 2026-08-14 KOSPI amount, LS arbitrage net was -113,526 while buy minus sell
  and KB `mprft_nt_b` were -113,525; LS non-arbitrage net and KB `nmp_nt_b` both
  equal 160,437. This independently binds the category mapping and rounding.
- The official t1633 definition names amount/quantity but omits multipliers. The
  KB match supports KRW million for amount; the magnitude supports thousand
  shares for quantity, but neither is promoted to an official confirmed unit.
  Verdict: fields confirmed, `UNIT_INFERRED_CROSS_SOURCE`; no Normalized contract
  or operational schedule yet.

## REST inventory

`Observed earliest` means the earliest row in this bounded pilot, not source
inception. “Not observed” means no live call was justified.

| Area / TR | Endpoint and request | Main response fields | History / observed evidence | Unit and semantics | Existing overlap / estimated cost | Verdict |
|---|---|---|---|---|---|---|
| Program t1631 | `/stock/program`; market and same-day/period selectors | total, arbitrage, non-arbitrage sell/buy/net quantity and amount | Date-capable; not observed | Official labels exist; numeric unit not stated in the inspected catalogue | Overlaps KRX program backlog; one call per query | `CROSS_CHECK_ONLY` pending unit/history pilot |
| Program t1632 | same endpoint; market/intraday selectors | time-series program totals | Intraday/current; not observed | Session/finalization not retained | No accepted project dataset | `HIGH_VALUE_DAILY_SOURCE` candidate |
| Program t1633 | same endpoint; KOSPI/KOSDAQ, amount/quantity, value/cumulative, daily/weekly/monthly, `fdate/tdate`, continuation date | date/index; total, arbitrage and non-arbitrage buy/sell/net; volume | Both markets positive on all five probes through 2021-01-04; 2000-01-04 valid empty | Field mapping confirmed; amount/quantity units remain cross-source inferred, not officially stated | Potentially replaces KRX program gap; exact floor and pagination cost remain unverified | `HIGH_VALUE_BACKFILL_SOURCE` candidate, promotion gated on unit/floor contract |
| Program t1636 / t1637 | same endpoint; current stock list / per-stock history | per-stock program flow | Per-symbol; historical mode exists | Current-universe fan-out creates survivorship risk | Inferior to a market-wide official source | `NOT_USEFUL` for broad backtest history |
| Program t1640 / t1662 | same endpoint; mini summary / intraday chart | current or intraday program aggregates | Snapshot/intraday only | Finalization unresolved | Useful only for live validation | `CROSS_CHECK_ONLY` |
| Surrounding funds t8428 | `/stock/investinfo`; `fdate/tdate`, series selector, market, count and `key_date` continuation | date/index/turnover; customer deposits, change, receivables, credit balance, futures deposits, stock/mixed/bond/MMF funds | Five verified 500-row pages reach 2016-06-13; four identical cursor overlaps; continuation remains `Y` | Official monetary units are KRW 100 million; turnover percent. Calendar differs slightly from equity sessions | Complements lagged KB liquidity snapshot. About 14 pages estimated for 2000-present, floor unproven | `PAGINATION_UNRESOLVED`; strong backfill candidate after one floor-reaching pilot |
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

## ETF daily schema and call-cost design

No ETF call was added in this follow-up and no collector was scheduled.

- **Universe/master:** t1901 and t1904 are code-keyed and do not return an ETF
  universe. A survivorship-safe daily universe must come from the existing KRX
  full-market/date source; the current LS symbol set must not be used to recreate
  history.
- **Summary snapshot candidate:** capture partition, `captured_at`, requested ETF
  code, source/PDF date, price/OHLC/volume/value, NAV and NAV change, tracking
  error, disparity, foreign holding/exhaustion and reference-index/futures fields
  from t1901. Grain would be capture × ETF code.
- **AUM/constituent candidate:** t1904 header preserves PDF application date,
  NAV, net asset total (`etftotcap`, KRW 100 million), constituent count, CU
  shares, cash, manager and valuation totals. Component rows preserve source
  ordinal/code, quantity or cash amount, valuation, market capitalization,
  weight and `profitdate`. Grain would be capture × ETF code × source component
  ordinal; no component identity inference is allowed.
- **Cost:** t1901 plus t1904 requires two calls per ETF. At the official 1
  request/second limit, a retained universe near 1,160 ETFs implies about 2,320
  calls and at least 39 minutes per daily full-market snapshot, before validation.
  That is inferior to the current KRX full-market route. LS adoption should be
  limited to a small benchmark set for daily cross-checks unless a bulk official
  endpoint is documented.

t3320 and t3341 remain explicitly `SNAPSHOT_ONLY`. They are excluded from every
backtest historical-fundamental candidate list because neither accepts an as-of
date and both can introduce look-ahead bias.

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
   documented KRW 100 million units. Pagination is now verified through five
   pages; the remaining gate is one separately bounded floor-reaching pilot and
   a calendar-aware source-observation contract.
2. **t1633 market program trading** — directly addresses a documented gap with
   KOSPI/KOSDAQ and arbitrage/non-arbitrage splits. History is positive through
   2021 and the KB category cross-check passes; exact unit multipliers and source
   floor remain contract gates.
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
