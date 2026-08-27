# pykrx Dataset Coverage / Gap Audit

Status: **READ_ONLY_AUDIT / NO COLLECTION AUTHORIZED**  
Audit date: **2026-08-15 KST**

## Executive conclusion

pykrx exposes useful data that is not yet represented by an accepted project
artifact. The highest-value omissions are:

1. historical foreign ownership and foreign ownership limits;
2. historical equity valuation/fundamentals;
3. historical index constituents and dated sector classification;
4. market-wide historical ETF OHLCV/NAV and historical ETF portfolio files;
5. corporate-credit and CD yields not present in the current Korean Treasury set.

These are real coverage gaps, but none is ready for immediate bulk collection.
Foreign ownership, fundamentals, ETF, and index membership still require proof of
historical range, dated-universe completeness, publication/revision timing, and
survivorship-safe operation. pykrx is also a scraping transport over KRX/Naver, not
an independent primary source. Its latest release warns that outputs may differ from
official data and that provider terms apply.

Most apparent pykrx opportunities are already covered or are low-value duplicates:
equity price/cap/universe, index OHLCV, market investor flow, short-selling Trading
and Balance, and Korean government-bond yields already have retained artifacts or
better current sources. pykrx futures has only a single-date cross-section wrapper;
options historical APIs are not publicly implemented in `future_api.py`.

**Decision:** collect nothing now. Retain the existing bounded pilot designs for
foreign ownership, fundamentals, and ETF; add historical index membership/sector and
credit-yield source checks to the candidate backlog only when the backtest feature
plan demonstrates need.

## Scope and evidence boundary

This audit used:

- official pykrx `master` source for
  [`stock_api.py`](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/stock/stock_api.py),
  [`future_api.py`](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/stock/future_api.py),
  and [`bond.py`](https://github.com/sharebook-kr/pykrx/blob/master/pykrx/bond/bond.py);
- the official [v1.2.8 release](https://github.com/sharebook-kr/pykrx/releases/tag/v1.2.8),
  released 2026-05-04, and the locally installed matching version `1.2.8`;
- all top-level public functions in those files, including dispatch overloads but
  excluding private helpers and deprecated aliases as separate capabilities;
- the current 55-contract
  [Dataset Index](../../../../../../data/DATASET_INDEX.md), contract registry, current
  [Data Status](../../../../../../data/DATA_STATUS.md), and the latest retained
  [D001 inventory](../../inventory/D001_DATASET_INVENTORY.md).

No pykrx/KRX/provider call was made. No Parquet body was scanned. No Landing,
Normalized, Published, contract, collector, checkpoint, lock, or schedule was changed.

### Evidence caveat: 55 contracts vs immutable inventory

The current registry and Dataset Index contain **55** contracts. The latest immutable
D001 snapshot predates four current registrations and reports **51 registered, 38
observed, 13 missing, 42 artifact roots, and zero unregistered artifacts**. Therefore
this audit uses current contracts for semantic coverage and D001 only for the artifact
state it actually observed. It does not reinterpret the older snapshot as evidence for
the four later registrations.

## Classification

- `FULLY_COVERED`: current accepted dataset/artifact covers the economic capability.
- `COVERED_BY_BETTER_PRIMARY_SOURCE`: covered, but pykrx is not the chosen source.
- `PARTIALLY_COVERED`: related data exists but fields/history/grain are incomplete.
- `CONTRACT_EXISTS_NO_ARTIFACT`: exact/near contract exists without accepted artifact.
- `MISSING_CONTRACT`: public capability has no matching contract.
- `MISSING_HIGH_VALUE_DATASET`: missing and likely material to research/backtests.
- `DUPLICATE_LOW_VALUE`: would duplicate retained data without material new dimensions.
- `PYKRX_NOT_IMPLEMENTED`: requested economic capability has no actual public wrapper.
- `SEMANTICS_UNRESOLVED`: endpoint exists, but source behavior/PIT meaning blocks use.

Recommended source roles are `PRIMARY`, `SECONDARY`, `CROSS_CHECK`,
`SOURCE_OBSERVATION_ONLY`, and `DO_NOT_ADOPT`.

## Public API capability inventory and mapping

Deprecated `get_business_days` is counted with `get_previous_business_days`.
Dispatch facades such as `get_market_ohlcv`, `get_market_cap`,
`get_market_fundamental`, `get_index_ohlcv`, `get_index_fundamental`, and
`get_shorting_balance` are not double-counted against their by-date/by-ticker
implementations. ETF trading overloads are one economic capability.

| pykrx capability | Public API | Economic fields / grain | Current dataset | Coverage | Current source / artifact evidence | Gap status | Recommended role | Priority |
|---|---|---|---|---|---|---|---|---|
| Business calendar | `get_nearest_business_day_in_a_week`, `get_previous_business_days` | trading dates | no standalone contract | Utility only | dataset dates/source calendars | `DUPLICATE_LOW_VALUE` | `DO_NOT_ADOPT` | P3 |
| Historical ticker universe/name | `get_market_ticker_list`, `get_market_ticker_name` | date, market, symbol, name | `kr_equity_universe_daily`, canonical universe | 1995-05-02..2026-08-12 | canonical provider chain; PIT-safe | `COVERED_BY_BETTER_PRIMARY_SOURCE` | `CROSS_CHECK` | P3 |
| Equity OHLCV | `get_market_ohlcv*` | OHLC, volume, value, change | `kr_equity_price_daily` | 1995-05-02..2026-08-12 | marcap → KRX Open API → data.go.kr | `FULLY_COVERED` | `CROSS_CHECK` | P3 |
| Market cap/listed shares | `get_market_cap*` | close, cap, volume, value, listed shares | `kr_equity_market_cap_daily`; universe/master | 1995-05-02..2026-08-12 | canonical provider chain | `FULLY_COVERED` | `CROSS_CHECK` | P3 |
| Period price change/delisting flag | `get_market_price_change*` | start/end/change/rate/volume/value; optional delist | equity price + dated universe | reconstructible | accepted PIT price/universe | `DUPLICATE_LOW_VALUE` | `DO_NOT_ADOPT` | P3 |
| Foreign ownership/limit | `get_exhaustion_rates_of_foreign_investment*` | listed shares, held shares, ownership %, limit shares, exhaustion % | none; candidate `kr_equity_foreign_ownership_daily` only | retained single-symbol feasibility evidence; no production artifact | authenticated KRX candidate | `MISSING_HIGH_VALUE_DATASET` / `SEMANTICS_UNRESOLVED` | `PRIMARY` candidate | **P0** |
| Equity valuation | `get_market_fundamental*` | BPS, PER, PBR, EPS, DIV, DPS | none; candidate `kr_equity_fundamental_daily` only | one bounded feasibility observation; no artifact | authenticated KRX candidate | `MISSING_HIGH_VALUE_DATASET` / `SEMANTICS_UNRESOLVED` | `PRIMARY` candidate | **P0** |
| Investor totals by investor | `get_market_trading_{value,volume}_by_investor` | sell/buy/net, volume/value, market or symbol | `kr_market_investor_*` bridge; `kr_investor_flow_daily` missing | market bridge 1999-01-04..2026-08-11 | legacy + Toss bridge | `PARTIALLY_COVERED` | `SECONDARY` | P2 |
| Investor daily series | `get_market_trading_{value,volume}_by_date` | date × investor; sell/buy/net | same investor datasets | net-purchase bridge retained; full field equivalence not proven | provider boundaries/units differ | `PARTIALLY_COVERED` | `SECONDARY` | P2 |
| Investor symbol ranking | `get_market_net_purchases_of_equities*`, `get_market_trading_value_and_volume_by_ticker` | investor × symbol value/volume/rank | no canonical symbol-level dataset | none accepted | pykrx endpoint only | `MISSING_CONTRACT`; feature use case unclear | `SECONDARY` candidate | P2 |
| Index master | `get_index_ticker_list`, `get_index_ticker_name`, `get_index_listing_date` | index code/name/listing date | `kr_index_daily` lacks full master semantics | OHLCV artifact retained | pykrx | `PARTIALLY_COVERED` | `PRIMARY` metadata candidate | P2 |
| Index OHLCV | `get_index_ohlcv*` | OHLC, volume, value, cap | `kr_index_daily` | 1975-01-04..2026-08-07 | pykrx retained artifact | `FULLY_COVERED` | `PRIMARY` maintenance | P3 |
| Historical index constituents | `get_index_portfolio_deposit_file` | index × date × constituent | none | no accepted artifact | date-parameter wrapper exists | `MISSING_HIGH_VALUE_DATASET` / `SEMANTICS_UNRESOLVED` | `PRIMARY` candidate | **P0** |
| Index valuation | `get_index_fundamental*` | BPS, PER, PBR, EPS, DIV, DPS | none | no accepted artifact | pykrx endpoint only | `MISSING_CONTRACT` | `SECONDARY` candidate | P1 |
| Index period performance | `get_index_price_change*` | start/end/change/rate/volume/value | derivable from `kr_index_daily` | retained OHLCV | existing index data | `DUPLICATE_LOW_VALUE` | `DO_NOT_ADOPT` | P3 |
| Dated sector classification | `get_market_sector_classifications` | symbol, market, sector at requested date | none; canonical universe has no verified historical sector membership | no accepted artifact | pykrx date wrapper exists | `MISSING_HIGH_VALUE_DATASET` / `SEMANTICS_UNRESOLVED` | `PRIMARY` candidate | **P0** |
| Short status by symbol/date | `get_shorting_status_by_date` | volume, value, ratio, balance-related status | Trading + Balance contracts | Trading 2008..2026; Balance 2016..2026 | authenticated KRX retained | `PARTIALLY_COVERED`; reconcile fields | `CROSS_CHECK` | P2 |
| Short volume/value cross-section/history | `get_shorting_{volume,value}_by_{ticker,date}` | volume/value/ratio by symbol or date | `kr_short_selling_trading_daily` | 2008-01-02..2026-08-07; 10,161,884 rows | authenticated KRX | `FULLY_COVERED` | `PRIMARY` maintenance | P3 |
| Short investor flow | `get_shorting_investor_{volume,value}_by_date` | investor × date × volume/value | `kr_short_selling_investor_daily` | contract exists; no accepted artifact | range collapses to end-date row | `CONTRACT_EXISTS_NO_ARTIFACT` / `SEMANTICS_UNRESOLVED` | `DO_NOT_ADOPT` now | P1 blocked |
| Short top-50 rankings | `get_shorting_{volume,balance}_top50` | ranked symbol snapshots | no separate contract | derivable from accepted detail if fields align | Trading/Balance detail | `DUPLICATE_LOW_VALUE` | `DO_NOT_ADOPT` | P3 |
| Short balance | `get_shorting_balance*` | symbol/date balance shares/value/ratio | `kr_short_selling_balance_daily` | 2016-06-30..2026-08-07; 6,035,958 rows | authenticated KRX | `FULLY_COVERED`; PIT blocked | `PRIMARY` maintenance | P3 |
| ETF/ETN/ELW universe/name | `get_etx_ticker_list`, `get_{etf,etn,elw}_ticker_list/name`, `get_etf_isin` | dated ticker sets; names/ISIN | no ETF/ETN/ELW master contract | ETF 1,160-row recent feasibility evidence only | KRX/pykrx | `MISSING_HIGH_VALUE_DATASET` for ETF; ETN/ELW lower | `PRIMARY` candidate | **P0 ETF**, P2 others |
| ETF OHLCV/NAV | `get_etf_ohlcv_by_date`, `get_etf_ohlcv_by_ticker` | NAV, OHLC, volume, value, cap, net assets, listed shares, underlying index | no contract; candidate `kr_etf_ohlcv_daily` only | recent 1,160-row full-market feasibility evidence; no artifact | KRX full-market/date | `MISSING_HIGH_VALUE_DATASET` / `SEMANTICS_UNRESOLVED` | `PRIMARY` candidate | **P0** |
| ETF period price change | `get_etf_price_change_by_ticker` | start/end/change/rate/volume/value | none | no accepted ETF dataset | derivable after ETF OHLCV | `DUPLICATE_LOW_VALUE` | `DO_NOT_ADOPT` separately | P3 |
| ETF portfolio deposit file | `get_etf_portfolio_deposit_file` | component, contract count, value, weight by ETF/date | none | no accepted artifact | KRX per-ETF/date | `MISSING_HIGH_VALUE_DATASET` / historical completeness unproven | `PRIMARY` candidate | **P0** |
| ETF NAV deviation | `get_etf_price_deviation` | close, NAV, deviation by date | none | no accepted artifact | pykrx/KRX | `MISSING_CONTRACT` / formula & availability unresolved | `SECONDARY` | P1 |
| ETF tracking error | `get_etf_tracking_error` | NAV, index, tracking error by date | none | no accepted artifact | pykrx/KRX | `MISSING_CONTRACT` / formula & availability unresolved | `SECONDARY` | P1 |
| ETF investor trading | `get_etf_trading_volume_and_value` overloads | investor sell/buy/net volume/value; market or ETF | none | no accepted artifact | pykrx/KRX | `MISSING_CONTRACT`; useful after ETF universe | `SECONDARY` | P1 |
| Corporate major changes | `get_stock_major_changes` | name/sector/par-value/CEO changes by symbol/date | corporate-action source observations do not cover this identity history | no accepted artifact | pykrx per-symbol route | `MISSING_CONTRACT`; survivorship and event semantics unresolved | `SOURCE_OBSERVATION_ONLY` | P2 |
| Market-wide OHLCV convenience | `get_market_ohlcv_by_market` | current market cross-section; no date parameter in signature | equity price dataset | historical accepted chain exists | Naver/KRX wrapper | `DUPLICATE_LOW_VALUE` | `DO_NOT_ADOPT` | P3 |
| Futures universe/name | `get_future_ticker_list`, `get_future_ticker_name` | current product codes/names | derivatives contracts/bridges | retained contract-level histories | data.go.kr + legacy KRX | `DUPLICATE_LOW_VALUE` | `CROSS_CHECK` | P3 |
| Futures single-date OHLCV | `get_future_ohlcv`, `get_future_ohlcv_by_ticker` | product cross-section OHLCV/open interest at one date | futures normalized/bridge contracts | bridge 2010-01-04..2026-08-07 | data.go.kr + legacy KRX | `COVERED_BY_BETTER_PRIMARY_SOURCE` | `CROSS_CHECK` | P3 |
| Futures historical range | no public by-date wrapper | requested range history | futures bridge | 2010..2026; pre-2010 target gap | free official KRX screen confirmed separately | `PYKRX_NOT_IMPLEMENTED` | `DO_NOT_ADOPT` as transport | P3 |
| Options product/OHLCV | no public wrapper in `future_api.py` | option universe/history | options normalized/bridge/PCR contracts | 2010..2026; pre-2010 gap | data.go.kr + legacy KRX | `PYKRX_NOT_IMPLEMENTED` | `DO_NOT_ADOPT` as transport | P3 |
| OTC bond yields | `get_otc_treasury_yields` | government 1/2/3/5/10/20/30Y, housing 5Y, corporate AA-/BBB- 3Y, CD91; yield/change | BOK/Toss Treasury contracts cover government tenors only | BOK 1998..2026; Toss 2019..2026 | BOK primary / Toss secondary | government `COVERED_BY_BETTER_PRIMARY_SOURCE`; credit/CD `MISSING_HIGH_VALUE_DATASET` | `CROSS_CHECK` government; `SECONDARY` candidate credit/CD | **P1** |

## Field-level high-risk checks

### Foreign ownership

The public wrapper exposes five distinct source fields: listed shares, foreign-held
shares, foreign ownership percent, foreign order-limit shares, and foreign limit
exhaustion percent. No current contract contains all five. Similarity to market-cap or
universe datasets is insufficient for coverage. The deferred readiness design records
raw KRX fields and warns that pykrx converts blank values to zero and ratios to
`float16`; production parsing must preserve missing vs valid zero and non-lossy ratios.

### Historical valuation

The wrapper exposes BPS, PER, PBR, EPS, DIV, and DPS both by symbol/date range and
whole-market/date. No current contract stores these fields. A historical date argument
proves query shape, not PIT safety: the source may return later-corrected values, and
the audit has no publication timestamp or revision-vintage evidence. It must not enter
production backtests until an explicit availability policy is proven.

### ETF

pykrx has a dated market universe, market-wide daily ETF data, per-ETF date-range
OHLCV/NAV, portfolio deposit files, deviation, tracking error, and investor trading.
The current LS t1901/t1904 design is snapshot/cross-check only and cannot reconstruct
historical membership. The KRX full-market/date route is the better candidate because
it can avoid current-symbol fan-out, but historical delist coverage and publication
semantics remain unproven.

### Index constituents and sectors

`kr_equity_canonical_universe_daily` answers which equities existed, not which belonged
to KOSPI200/KOSDAQ150 or a sector on a date. These are separate, high-value backtest
universe/filter dimensions. The wrappers accept dates, but historical retention,
reconstitution timing, and whether old responses are revised must be audited before
claiming PIT safety.

### Short selling

Trading and Balance already hold the high-volume detail. Top-50 outputs are rankings,
not new economic primitives. The material missing dimension is investor short flow,
for which a contract exists but the source range collapses to one end-date row. That is
a source-semantics blocker, not a reason to retry or create duplicate datasets.

### Bond

BOK ECOS remains primary for Korean government yields; Toss is secondary. pykrx adds
potentially useful corporate AA-/BBB- 3-year and CD91 observations. Those are different
macro variables, not replacements for government tenor yields. Their publication
time, methodology/representative yield identity, revisions, and earliest history must
be established before defining credit-spread features.

## All 55 current contracts: pykrx disposition

This appendix proves every current registered contract was considered. Grouped cells
share the same disposition; grouping does not merge contracts or schemas.

| Current contract(s) | pykrx relationship | Disposition |
|---|---|---|
| `kr_equity_price_daily`, `kr_equity_market_cap_daily`, `kr_equity_universe_daily`, `kr_equity_master`, `kr_equity_canonical_universe_daily` | direct/near equity APIs | covered by accepted canonical chain; pykrx cross-check only |
| `kr_index_daily` | direct index OHLCV | fully covered; pykrx remains current source |
| `kr_market_breadth_daily` | no direct primitive | derived from accepted equity/universe; no pykrx dataset needed |
| `kr_investor_flow_daily` | direct investor API | contract exists/no artifact; bridge is current usable interface |
| `kr_market_investor_net_purchase_daily`, `kr_market_investor_trading_daily`, `kr_market_investor_net_purchase_bridge_daily` | direct/near investor APIs | retained provider-boundary history; pykrx secondary only |
| `kr_short_selling_trading_daily`, `kr_short_selling_balance_daily` | direct short APIs | fully covered artifacts; no duplicate ranking datasets |
| `kr_short_selling_investor_daily` | direct short-investor API | contract exists/no artifact; semantics unresolved/stopped |
| `kr_derivatives_futures_daily`, `kr_kospi200_futures_daily`, `kr_kosdaq150_futures_daily`, `krx_legacy_kospi200_futures_daily`, `kr_kospi200_futures_provider_bridge_daily`, `kr_kospi200_futures_nearest_listed_daily` | pykrx single-date futures only | better retained sources; historical pykrx wrapper not implemented |
| `kr_derivatives_options_daily`, `kr_kospi200_options_daily`, `kr_kosdaq150_options_daily`, `krx_legacy_kospi200_options_daily`, `kr_kospi200_options_provider_bridge_daily`, `kr_kospi200_option_pcr_daily` | no public pykrx options wrapper | pykrx not implemented; existing source chain remains authoritative |
| `kr_kospi200_futures_investor_net_purchase_daily`, `kr_kospi200_futures_investor_trading_daily`, `kr_kospi200_options_investor_trading_daily` | no public derivative-investor wrapper | manual official KRX/other source boundary; pykrx not a transport candidate |
| `kr_market_liquidity_daily`, `kr_credit_balance_daily` | no matching public pykrx wrapper | data.go.kr primary; no pykrx adoption |
| `kr_stock_lending_daily`, `kr_stock_lending_market_daily`, `kr_stock_lending_participant_daily` | no matching public pykrx wrapper | data.go.kr primary; no pykrx adoption |
| `kr_equity_dividend`, `kr_equity_dividend_source_observation`, `kr_equity_rights_schedule`, `kr_equity_stock_issuance_source_observation` | no canonical corporate-action terms API; major-changes API is different | keep source observations separate; do not infer event coverage |
| `global_index_price_daily`, `fred_treasury_yield_daily`, `fred_usd_fx_daily`, `us_treasury_spread_daily` | outside pykrx economic scope | Yahoo/FRED remain primary |
| `bok_ecos_kr_treasury_yield_source_observation`, `kr_treasury_yield_daily` | overlaps government yield subset | BOK primary/Toss secondary; pykrx cross-check only |
| `kr_equity_credit_trading_daily`, `kr_equity_program_trading_daily`, `kr_equity_securities_lending_daily`, `kr_equity_short_selling_daily` | Toss contracts; some overlap with pykrx investor/short data | current-symbol Toss artifacts missing; do not use pykrx to silently fill different grains |
| `kb_market_breadth_snapshot`, `kb_program_trading_snapshot`, `kb_investor_flow_snapshot`, `kb_market_liquidity_snapshot`, `kb_derivatives_summary_snapshot`, `kb_domestic_index_snapshot`, `kb_global_symbol_snapshot` | realtime/provisional snapshot domain | historical pykrx capabilities are not substitutes; keep provider/date semantics separate |

Contract count represented above: **55 of 55**.

## Already Covered

- Equity OHLCV, market cap, listed-universe identity, and canonical universe.
- Index OHLCV.
- Short-selling Trading and Balance.
- Market-level investor bridge for its documented provider segments.
- KOSPI200 futures/options/PCR from 2010 onward through current source boundaries.
- Korean government-bond tenor history through BOK/Toss.

No new pykrx dataset is justified for these capabilities.

## Better Source Exists

- Equity price/cap/universe: the canonical marcap → KRX Open API → data.go.kr chain.
- Korean government yields: BOK ECOS primary, Toss secondary.
- Stock lending and liquidity/credit: data.go.kr.
- Derivatives: current data.go.kr/legacy/manual KRX/LS boundaries; pykrx lacks the
  required historical options and derivative-investor public wrappers.
- Global indices/rates/FX: Yahoo/FRED.

## Partial Gap

- Investor flow: current market bridge is useful, but sell/buy/volume and symbol-level
  equivalence with pykrx are not established.
- Index master: OHLCV exists, but complete master/listing/constituent histories do not.
- Short selling: investor dimension remains stopped; status fields need reconciliation.
- ETF: recent feasibility evidence exists, but no contract or accepted history.
- Corporate identity changes: the wrapper exists, but it is not canonical corporate
  action or PIT event identity.

## Missing High-Value Dataset

| Candidate | Why valuable | Backtest feature value | Different from current data | Expected history | PIT risk | Recommended source | Bounded pilot? |
|---|---|---|---|---|---|---|---|
| `kr_equity_foreign_ownership_daily` | ownership/capacity/crowding regime | foreign ownership change, limit pressure, universe filters | current equity chain has listed shares but not held/limit fields | source floor unknown; full-market/date wrapper | publication/revision timing; blank→zero; delist completeness | official KRX via lossless authenticated route | Existing deferred pilot only; do not run now |
| `kr_equity_fundamental_daily` | core valuation and quality inputs | value factors, valuation regimes, portfolio filters | no BPS/PER/PBR/EPS/DIV/DPS contract | source floor unknown; market/date and symbol/range wrappers | corrected historical values and reporting availability | official KRX via lossless authenticated route | Existing bounded design; review only after feature spec |
| `kr_index_constituent_daily` | prevents index-membership look-ahead | KOSPI200/KOSDAQ150 historical universes | canonical equity universe is not index membership | wrapper accepts historical date; floor unknown | rebalance effective time, retrospective revisions | official KRX | New metadata/source pilot required |
| `kr_equity_sector_classification_daily` | stable sector-neutral analysis | sector exposure, neutralization, rotation | no historical sector membership contract | wrapper accepts date; floor unknown | reclassification timing and historical rewrite | official KRX | New bounded two-date/reclassified-symbol pilot |
| `kr_etf_ohlcv_daily` | ETF research universe and regime proxies | ETF momentum/liquidity/NAV/dislocation | LS candidates are current-symbol snapshots only | full-market/date route; floor/delist coverage unknown | NAV cutoff, revisions, delisted ETFs | official KRX full-market/date | Existing deferred ETF pilot only |
| `kr_etf_portfolio_daily` | actual historical holdings/exposure | constituent exposure, replication drift | no PDF/weight contract | per-ETF/date wrapper; range economics unknown | current-symbol fan-out, PDF effective date, cash rows | official KRX | Only after ETF dated-universe proof |
| `kr_credit_benchmark_yield_daily` | missing credit/liquidity regime | AA-/BBB- spreads, CD-government spread | current Korean rates are government tenors | wrapper supports date range by instrument; floor unknown | methodology/publication/revisions | KOFIA/KRX official route; pykrx cross-check transport | Small metadata + two-instrument pilot if feature approved |
| `kr_index_fundamental_daily` | market-level valuation regime | index PER/PBR/DIV and valuation breadth | no index valuation contract | source floor unknown | constituent/revision and effective-date semantics | official KRX | Lower priority than equity fundamentals |

## Low-Value / Duplicate

- Business calendar as a standalone dataset.
- Period price-change tables derivable from accepted OHLCV.
- Short-selling top-50 tables derivable from detailed Trading/Balance where schemas
  align.
- Futures current cross-sections that duplicate the retained provider bridge.
- ETF period price-change as a separate dataset; derive it after ETF OHLCV exists.
- ETN/ELW names alone without an approved research use case.

## History and PIT assessment

| Capability | Historical query shape | Survivorship-safe route? | Revision/publication evidence | Expected calls | Access risk | Production decision |
|---|---|---|---|---|---|---|
| Foreign ownership | market/date and symbol/range | market/date potentially yes | unknown | one business call per market/date in current design | authenticated KRX restrictions | blocked pending bounded evidence |
| Equity fundamentals | market/date and symbol/range | market/date potentially yes | unknown; high revision risk | likely one per market/date | authenticated KRX restrictions | blocked pending availability policy |
| ETF OHLCV | market/date and symbol/range | market/date potentially yes | NAV cutoff/revisions unknown | one full-market business call/date | authenticated KRX restrictions | bounded pilot design only |
| ETF PDF | ETF/date | only if driven by dated ETF universe | PDF effective time/revisions unknown | at least one call/ETF/date; high volume | authenticated KRX restrictions | defer until narrow use case |
| Index constituents | index/date | potentially yes | rebalance timing/revisions unknown | one call/index/date; daily repetition wasteful | authenticated KRX restrictions | test change-point strategy only |
| Sector classification | market/date | potentially yes | reclassification timing unknown | one call/market/date | authenticated KRX restrictions | bounded semantic pilot first |
| Credit/CD yields | instrument/date range | no universe fan-out | publication/revisions unknown | one range call/instrument | authenticated KRX restrictions | metadata-first comparison with BOK/KOFIA |
| Short investor | market/date range | market-level | observed range collapse | bounded range calls already failed semantically | authenticated KRX restrictions | stopped; no retry |

The presence of a date parameter does not prove first-availability time, historical
immutability, delisted coverage, or PIT safety. Any future pilot must capture lossless
Landing, preserve valid empty vs failure, use retry zero unless separately approved,
and remain under the shared KRX lock and explicit raw-call budget.

## Ranked final candidates

1. **Foreign ownership/limit** — highest immediate cross-sectional and regime value;
   distinct five-field schema; use the existing bounded full-market pilot design.
2. **Historical equity valuation** — direct value-factor input; highest revision/PIT
   risk; proceed only after feature availability rules are specified.
3. **Historical index constituents** — essential for unbiased KOSPI200/KOSDAQ150
   backtests; design a low-call change-point pilot rather than daily brute force.
4. **Historical sector classification** — valuable for exposure controls and sector
   strategies; verify reclassification/effective-date semantics.
5. **ETF market-wide OHLCV/NAV** — useful alternative-asset and liquidity universe;
   existing pilot design is appropriate, but no bulk run.
6. **ETF portfolio constituents/PDF** — high feature value but high call cost; pursue
   only for a bounded ETF strategy universe after dated-universe proof.
7. **Corporate AA-/BBB- and CD91 yields** — useful credit/liquidity macro features;
   compare official KOFIA/KRX definitions before choosing pykrx transport.
8. **Index fundamentals** — useful market valuation regime, but lower priority than
   symbol-level fundamentals and constituent history.

No additional short-selling dataset is recommended now: Trading and Balance are
already retained, rankings are duplicate, and Investor is a known semantic stop.

## Final answer

Among the data currently exposed by pykrx, the project is genuinely missing five
high-value economic families: **foreign ownership/limits, historical valuation,
historical index membership/sector classification, historical ETF market/PDF data,
and corporate-credit/CD yields**. ETF tracking/deviation/investor flow and index
fundamentals are secondary extensions.

- **Collect now:** none.
- **Already sufficiently covered elsewhere:** equity price/cap/universe, index OHLCV,
  short Trading/Balance, government yields, and current derivatives history.
- **Collect later if the backtest feature plan requires it:** index constituents,
  sectors, ETF PDF, credit/CD yields, and symbol-level investor flow.
- **Do not collect:** duplicate price-change/ranking tables, pykrx futures snapshots,
  unsupported options/derivative-investor capabilities, or another Short Investor
  retry without new semantics evidence.
- **Existing bounded pilots, still not authorized to run:** foreign ownership,
  fundamentals, and ETF OHLCV.

## References

- [pykrx repository](https://github.com/sharebook-kr/pykrx)
- [pykrx v1.2.8 release](https://github.com/sharebook-kr/pykrx/releases/tag/v1.2.8)
- [pykrx README and provider-use warning](https://github.com/sharebook-kr/pykrx/blob/master/README.md)
- [Current Dataset Index](../../../../../../data/DATASET_INDEX.md)
- [Current Data Status](../../../../../../data/DATA_STATUS.md)
- [D001 Dataset Inventory](../../inventory/D001_DATASET_INVENTORY.md)
- [Superseded authenticated historical plan](../../../../operations/2026-08-data-phase/superseded/PYKRX_AUTHENTICATED_HISTORICAL_PLAN.md)
- [Superseded foreign-ownership readiness](../../../../operations/2026-08-data-phase/superseded/PYKRX_FOREIGN_OWNERSHIP_PILOT.md)
- [Superseded ETF readiness](../../../../operations/2026-08-data-phase/superseded/PYKRX_ETF_PILOT.md)
- [KRX free-source backlog](../../krx/KRX_FREE_SOURCE_BACKLOG.md)
