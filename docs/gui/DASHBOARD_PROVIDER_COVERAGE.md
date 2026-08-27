# Dashboard Provider Coverage: KB + LS + Toss

> Audit date: 2026-08-17 KST
> Mode: repository-only source coverage audit. No OAuth, API, WebSocket, account,
> order, or market-data request was made. This document is a GUI/source-selection
> view and does not change or override
> [Data Status](../data/DATA_STATUS.md), Dataset Contracts, checkpoints, or active
> runbooks.

The coverage classifications are broader capability evidence. Final source ownership
for the selected 19-variable MVP is in
[Dashboard Daily Source Routing](DASHBOARD_DAILY_SOURCE_ROUTING.md).

## 1. Executive conclusion

The requested 34 economic-variable rows produce the following fail-closed result.

| Verdict | Count | Dashboard meaning |
|---|---:|---|
| `COVERED` | 0 | No variable is complete across history, current observation, daily finality, units, and date/PIT semantics using only these three providers. |
| `COVERED_WITH_LIMITS` | 10 | A useful history plus a current/latest observation exists, but coverage, publication/revision, unit, layer, or PIT limits remain. |
| `CURRENT_ONLY` | 10 | A provider-labelled current/recent observation is available; a safe historical daily series is not. |
| `HISTORY_ONLY` | 0 | No requested variable is supported only by history without any plausible latest/current observation. |
| `UNVERIFIED` | 6 | A partial/documented candidate exists, but retained evidence is insufficient for the requested grain or semantics. |
| `NOT_COVERED` | 8 | No suitable KB, LS, or Toss source was identified in the retained inventory/guides/evidence. |
| **Total** | **34** | One row per variable below; KOSPI and KOSDAQ and the two Korean Treasury tenors are counted separately. |

Therefore, **KB + LS + Toss can put 20 of 34 requested variable rows on a
provider-labelled descriptive Daily Dashboard today** if
`COVERED_WITH_LIMITS + CURRENT_ONLY` are accepted. This is not 20 canonical daily
datasets. Only **7 of 34 rows** have a credible provider daily-final route without
depending on an unresolved KB/LS snapshot-finality interpretation: KOSPI, KOSDAQ,
KOSPI/KOSDAQ index OHLC, index volume, market investor net purchase, Korea Treasury
3Y, and Korea Treasury 10Y. All seven still carry coverage and/or PIT limitations,
so none is classified plain `COVERED`.

The practical provider split is:

- **Toss:** strongest retained daily-final provider for KOSPI/KOSDAQ market
  indicators, KOSPI/KOSDAQ investor trading, and six Korean Treasury yield candles.
- **KB:** strongest compact current-snapshot source for Korean indices/breadth,
  market flows/liquidity, selected derivatives, U.S. indices, FX-labelled rows, and
  WTI. Its seven IVSA0070 slices retain independent dates; Normalized publication is
  blocked.
- **LS:** strongest source-observation candidate for program trading (`t1633`),
  surrounding funds/credit (`t8428`), derivatives prices/OI/basis discovery, and
  derivatives investor flow (`t8462`). Raw evidence is not silently promoted to a
  canonical daily dataset.

No provider values are averaged, reconciled into a synthetic number, or used to
fill another provider's missing value. Historical `*_daily` and current
`*_snapshot` remain separate.

## 2. Verdict and time-layer rules

| Term | Rule used in this audit |
|---|---|
| `COVERED` | Verified useful history, current/latest observation, daily finality, units, date/session semantics, and no material blocker. |
| `COVERED_WITH_LIMITS` | Both historical and latest/current use are evidenced, but an explicit coverage, Raw/Normalized, unit, publication/revision, PIT, or universe limit remains. |
| `CURRENT_ONLY` | Current/recent provider observation can support a labelled dashboard tile, but no accepted historical daily series exists at the requested grain. |
| `HISTORY_ONLY` | Historical series exists but there is no evidenced ongoing/latest route. |
| `UNVERIFIED` | The guide/catalog suggests a route, or only a narrower grain is evidenced, but the requested variable cannot yet be asserted. |
| `NOT_COVERED` | No suitable endpoint/source was found in the retained evidence. |
| `history_available` | `Yes` means retained history or a verified historical response shape. `Candidate` is not treated as verified history. Survivorship-unsafe per-current-symbol history is labelled explicitly. |
| `current_snapshot_available` | A point/current/recent provider observation exists. It does not imply an accepted snapshot artifact. |
| `daily_final_available` | A dated daily final or a defensible latest-final route exists. Unknown publication/session finality is `No` or `Blocked`, even if a row is dated. |

`capture_date`, `market_date`, `source_date`, `reference_date`, and
`source_reported_datetime` are not interchangeable. In particular, KB
`IVSA0070.inq_dy_tm` does not assign one market date to every slice, Toss historical
collection time does not reconstruct original availability, and an LS source date
does not prove publication or revision timing.

## 3. Market

### Provider endpoint and availability matrix

| economic_variable | KB endpoint / availability | LS endpoint / availability | Toss endpoint / availability | history_available | current_snapshot_available | daily_final_available | verdict |
|---|---|---|---|---|---|---|---|
| KOSPI | `IVSA0070.out2` (`KGG01P`) current level; `IVS11560` history per required index code is documented but unpiloted | `t8428` retains a provider index field alongside funds, but is not the accepted index source | `GET /api/v1/market-indicators/KOSPI/candles` / `getMarketIndicatorCandles`; retained probe finds history in 2014 | Yes, Toss 2014+; LS `t8428` index field is only provenance | Yes, KB | Yes, Toss latest candle | `COVERED_WITH_LIMITS` |
| KOSDAQ | `IVSA0070.out2` (`QGG01P`) current level; `IVS11560` history candidate | No accepted index-history route in retained LS evidence | `GET /api/v1/market-indicators/KOSDAQ/candles` | Yes, Toss 2014+ | Yes, KB | Yes, Toss latest candle | `COVERED_WITH_LIMITS` |
| KOSPI200 | `IVSA0070.out2` (`K2G01P`) current level cross-check | No dedicated spot-index series verified; derivative spot/basis fields are not a spot-index dataset | No KOSPI200 market-indicator symbol in retained Toss OpenAPI 1.2.14 | KRX/pykrx ticker `1028` retained separately, 1990-01-03..2026-08-07 | Yes, KB | Yes through retained KRX/pykrx | `COVERED_EOD_T_PLUS_1` |
| Index OHLC | `IVS11560` documents per-index OHLC history; no retained bounded index pilot | No accepted market-index OHLC route in the source inventory | KOSPI/KOSDAQ `getMarketIndicatorCandles` supplies daily candles | Yes, KOSPI/KOSDAQ only | Latest final candle; KB has level, not OHLC snapshot | Yes, KOSPI/KOSDAQ only | `COVERED_WITH_LIMITS` |
| Index/market volume | `IVSA0070.out2.vlm` for selected domestic indices | `t8428.volume` exists but its unit is undocumented and it is not the primary value of `t8428` | KOSPI/KOSDAQ candles expose provider volume | Yes, Toss KOSPI/KOSDAQ | Yes, KB | Yes, Toss KOSPI/KOSDAQ | `COVERED_WITH_LIMITS` |
| Index/market trading amount | `IVSA0070.out2.dl_tw_amt` current selected-index field | Derivative TRs expose product trading value; no accepted Korean spot-market aggregate route | No retained KOSPI/KOSDAQ market-indicator trading-amount evidence | No verified history | Yes, KB | No; KB slice date/unit review remains | `CURRENT_ONLY` |
| Advances / declines / unchanged | `IVSA0070` scalar KOSPI/KOSDAQ up/down/unchanged and upper/lower-limit counts | No retained accepted breadth endpoint | None | No | Yes, KB | Blocked on slice-specific market date/finality | `CURRENT_ONLY` |
| Total market capitalization | Current per-symbol/ranking KB views exist, but no verified complete-market aggregate response | No verified full-market capitalization endpoint; ETF AUM is a different variable | `listStocks` has identity fields, not market cap; no verified market-cap endpoint | No | Partial per-symbol/ranking only | No | `UNVERIFIED` |

### Semantics and daily routing

| economic_variable | unit_semantics | date/session_semantics | known_limitations | recommended_daily_primary | recommended_secondary | external_source_still_required |
|---|---|---|---|---|---|---|
| KOSPI | Index points; Toss OHLCV remains provider-native | Toss daily bucket; KB slice date independent and may be `DATE_UNRESOLVED` | Toss retained evidence starts in 2014 and is shorter than the canonical KRX history | Toss latest finalized KOSPI candle | KB `IVSA0070` labelled current cross-check | Yes for pre-2014/full authoritative history; no for a labelled current tile |
| KOSDAQ | Index points; provider-native candles | Same boundary as KOSPI | Same short-history and availability limits | Toss latest finalized KOSDAQ candle | KB `IVSA0070` | Yes for full history; no for a labelled current tile |
| KOSPI200 | KRX/pykrx ticker `1028` index points | KRX trading date; final close usable EOD T+1 | No Toss route; futures spot field is not a substitute; one 1995-01-04 OHLC source anomaly is flagged | retained `kr_kospi200_index_daily` | KB `IVSA0070` current provider view only | No for retained history; refresh policy remains separate |
| Index OHLC | Toss KOSPI/KOSDAQ provider-native points; retained KRX/pykrx KOSPI200 points; no cross-provider normalization | Toss daily KST bucket; KOSPI200 KRX trading date and EOD T+1 close | Toss publication time is not reconstructed; KOSPI200 has no Toss route and one preserved 1995-01-04 source anomaly | Toss candles for KOSPI/KOSDAQ; retained `kr_kospi200_index_daily` for KOSPI200 | KB close/level cross-check only | Yes for longer authoritative KOSPI/KOSDAQ history; KOSPI200 retained history is already covered |
| Index/market volume | Provider-native integer; exact market construction/multiplier not strengthened here | Toss daily final versus KB current slice | KB and Toss values must not be averaged; LS `volume` unit is unresolved | Toss KOSPI/KOSDAQ candle | KB `out2.vlm` current cross-check | Yes if an official canonical volume history is required |
| Index/market trading amount | KB `dl_tw_amt`; unit/multiplier is not established by retained contract evidence | KB domestic-index slice date/finality unresolved | Current only; no accepted `*_daily` from the three providers | KB only after displaying provider/date/unit status | None | Yes |
| Advances / declines / unchanged | Security counts; upper/lower counts are separate source fields | Post-close per-market date must be independently established | No history; current KB snapshot publication blocked | KB provisional snapshot | None | Yes for history and accepted daily final |
| Total market capitalization | Requested grain interpreted as KOSPI/KOSDAQ market total in KRW | No market-date-valid complete-universe response proved | Rankings/current-symbol views are selection-biased and cannot be summed as a market total | None | None | Yes |

## 4. Investor / Flow

### Provider endpoint and availability matrix

| economic_variable | KB endpoint / availability | LS endpoint / availability | Toss endpoint / availability | history_available | current_snapshot_available | daily_final_available | verdict |
|---|---|---|---|---|---|---|---|
| Individual / foreign / institution net purchase | `IVSA0070.out5.kspi_nt_b/ksdq_nt_b` current market snapshot; `IVU10430` is true range only per sampled symbol | `t1601/t1615` current and `t1602/t1603/t1621` intraday; finality unresolved | `GET /api/v1/market-indicators/{KOSPI,KOSDAQ}/investor-trading`; retained complete 2014-07-01..2026-08-11 input | Yes, Toss market-level | Yes, KB and LS current views | Yes, Toss daily records | `COVERED_WITH_LIMITS` |
| Program trading | `IVSA0070.mprft_nt_b/nmp_nt_b` current arbitrage/non-arbitrage net fields; unit/date unresolved; `IVU10450` recent per-symbol | `POST /stock/program`, `t1633`; Raw market history complete within observed floors: KOSPI 2001-08-01+, KOSDAQ 2003-01-13+ | `getStockProgramTrades`, per-symbol true cursor only; historical full-market aggregation rejected | Yes, LS Raw | Yes, KB; LS can return latest dated row | Blocked: LS publication/revision policy and accepted contract absent | `COVERED_WITH_LIMITS` |
| Foreign holding / holding ratio | `IVU10140` current per-symbol and `IVU10020` current ranking; no historical panel | `POST /stock/frgr-itt`, `t1716/t1717`; per-symbol range/current, 250-row recent Samsung evidence | `getStockInvestorTrading.foreignerHolding`; per-symbol cursor, current resolver | Only per-current-symbol/recent and survivorship-unsafe | Yes, per symbol/ranking | No accepted market-wide final | `CURRENT_ONLY` |
| Credit | `IVA10370` / `IVSA0070` lagged current balance fields | `POST /stock/investinfo`, `t8428`; 4,991 unique dates, 2006-06-01..2026-08-12 observed | `getStockCreditTrades`; per-symbol history, representative evidence begins 2023 and fails delisted-symbol gate | Yes, LS Raw aggregate; Toss per-symbol unsafe | Yes, lagged KB/LS latest row | Blocked: LS publication/revision timing unknown | `COVERED_WITH_LIMITS` |
| Securities lending | No verified market-data endpoint in retained KB catalog | `t1716/t1717` does not establish a complete lending dataset | `getStockSecuritiesLending`; per-symbol cursor, representative evidence 2021+, evening update, delisted-symbol 404 | Per-current-symbol only; not survivorship-safe | Latest per-symbol observation is possible | No accepted market-wide/current-universe final | `UNVERIFIED` |
| Short selling | No verified short-sale market endpoint in retained KB catalog | LS t1716 retained per-symbol EOD values; KRX-only status is empirical, not official | Toss `getStockShortSelling`; per-symbol EOD, representative evidence 2019+, evening update, delisted-symbol 404 | Per-current-symbol only; not survivorship-safe | KRX official block is Primary; labelled per-symbol provider-EOD block is possible | No verified intraday source or marketwide provider aggregate | `COVERED_WITH_LIMITS` |

### Semantics and daily routing

| economic_variable | unit_semantics | date/session_semantics | known_limitations | recommended_daily_primary | recommended_secondary | external_source_still_required |
|---|---|---|---|---|---|---|
| Individual / foreign / institution net purchase | Toss buy/sell amounts are KRW; net is derived within the same Toss row only; bridge provider segments remain distinct | Toss source daily date plus retained availability metadata; KB slice date independent | Toss starts 2014-07-01; predictive use remains blocked; do not equate its unit with an older provider segment | Toss market investor daily final | KB current snapshot with explicit availability label | No for current KOSPI/KOSDAQ daily display; yes for longer/canonical history |
| Program trading | LS amount = million KRW and quantity = thousand shares are `CONFIRMED_EMPIRICAL_MULTI_DATE`, not LS-official; KB exposes only two net fields with blank unit | LS source date, regular-market aggregate; publication/revision unknown; KB date unresolved | LS is Raw only, no contract/Normalized writer/schedule; Toss market total is forbidden because rows are missing for valid PIT-universe symbols | LS `t1633` provider view after a controlled daily Raw path is accepted | KB provisional current cross-check | Yes for an accepted PIT/final canonical series and official LS unit statement |
| Foreign holding / holding ratio | Shares and ratios coexist with provider/regulator/estimate fields; do not merge LS, Toss, and KB values | Per-symbol source date/current ranking date; no source-date-valid full historical universe | LS and Toss values may differ; rankings are selected cross-sections; no market-wide daily final | LS `t1716` for a bounded current watchlist only | Toss per-symbol current observation or KB current view, kept separate | Yes for survivorship-safe market history and canonical daily final |
| Credit | LS `trjango` is officially KRW 100 million; Toss per-symbol fields use shares/ratios; KB is coarser/lagged | `t8428` uses a provider business-statistics calendar and may lag; no missing dates synthesized | LS continuation stopped on source `IGW40014`; publication/revision unknown; Toss current-universe history unsafe | LS `t8428` source observation | KB lagged current cross-check | Yes for PIT-safe/final canonical use; no for labelled descriptive LS history |
| Securities lending | Toss shares and KRW fields are documented per symbol | Evening confirmation claimed by source metadata; `updatedAt` must be retained | No safe historical universe; no accepted market aggregate/snapshot artifact; thousands of daily symbol calls | None for a full-market tile; Toss only for an explicitly bounded watchlist | None | Yes |
| Short selling | KRX official shares/KRW/ratios are Primary; Toss shares/KRW/rates and LS shares/million-KRW are provider-native per symbol | `KRX_ONLY` through 2025-03-03 and official `KRX_NXT_COMBINED` thereafter; Toss/LS are `KRX_ONLY_EMPIRICALLY_CONFIRMED` provider EOD | Same survivorship and complete-universe problem; never substitute provider data for official aggregate | Dashboard has separate `LATEST OFFICIAL` and `LATEST PROVIDER` blocks | Same-date remainder only as labelled `AGGREGATE_MINUS_KRX_ONLY_INFERRED` | No true LIVE claim |

## 5. Derivatives

### Provider endpoint and availability matrix

| economic_variable | KB endpoint / availability | LS endpoint / availability | Toss endpoint / availability | history_available | current_snapshot_available | daily_final_available | verdict |
|---|---|---|---|---|---|---|---|
| KOSPI200 futures price/OHLC | `IVSA0070.out3` selected current contract; `IVS11560` true per-instrument chart history | `POST /futureoption/market-data`: `t2111` current, `t2214` daily, `t2424` recent OI chart; `t8466` range chart; current/expired universe incomplete | None | Only current-instrument/recent history; safe expired-contract history not established | Yes, KB and LS | No accepted full-contract daily final | `CURRENT_ONLY` |
| Futures basis | No dedicated contracted field in retained KB snapshot; a cross-field subtraction is not authorized | `t2111` documents current basis with price/volume/value/OI/Greeks | None | No verified safe history | Documented current LS field, not live-observed | No | `CURRENT_ONLY` |
| KOSPI200 option CALL / PUT | `IVSA0070.out3` one selected CALL and PUT quote/OI; `IVS11560` per-instrument history candidate | `t2111/t2214/t2424/t8466` are contract-keyed current/chart candidates; current option master only | None | Current-contract/recent only; historical option universe absent | Yes, selected contracts | No accepted complete-chain final | `CURRENT_ONLY` |
| Option PCR | No total call/put market activity slice; selected contracts cannot form PCR | No verified complete option-chain/total-market daily operation in retained inventory | None | No | No valid total-scope input | No | `NOT_COVERED` |
| Open interest | `IVSA0070.out3.nstmt_agr_q` for selected future/CALL/PUT | `t2111` current and `t2424` recent daily OHLC of OI; OI unit contracts | None | Limited recent/current-instrument history | Yes | No accepted full-universe final | `CURRENT_ONLY` |
| Individual / foreign / institution futures/options flow | `IVSA0070.out5` KOSPI200 futures/CALL/PUT fields are constant source zero and normalize to unavailable | `POST /futureoption/investor`, `t8462`; 4,734 Raw rows, six products x D/N/U, 2025-07-18..2026-08-14 | None | Yes, limited LS Raw | Yes through LS post-close Raw candidate | Raw collection ready; Normalized daily final blocked | `COVERED_WITH_LIMITS` |
| VKOSPI | None | No dedicated VKOSPI index endpoint verified; volatility-futures values are not the spot index | None | No | No | No | `NOT_COVERED` |

### Semantics and daily routing

| economic_variable | unit_semantics | date/session_semantics | known_limitations | recommended_daily_primary | recommended_secondary | external_source_still_required |
|---|---|---|---|---|---|---|
| KOSPI200 futures price/OHLC | Contract-native price, volume, value and OI; multiplier requires contract metadata | Day/night source dates can cross capture dates; legacy KB evidence exposed the next calendar date during evening capture | No source-date-valid historical contract universe; expired lookup and truncation unverified | LS `t2111/t2424` for current labelled observation after a bounded pilot | KB `IVSA0070.out3` selected-contract snapshot | Yes for canonical full-contract history/daily final |
| Futures basis | LS provider basis field; do not recompute by mixing providers | Exact contract/session timestamp required | `t2111` was documented but not observed in the retained pilot | LS `t2111` after bounded pilot | None | Yes for history and accepted methodology |
| KOSPI200 option CALL / PUT | Contract-native price/volume/value/OI/IV; strike/maturity/right identity required | Selected current instrument/session only | KB shows one selected CALL/PUT, not a complete chain; expired universe missing | LS contract-keyed current view after pilot | KB selected quote cross-check | Yes for full-chain history/final |
| Option PCR | Must be put total divided by call total within one provider/scope; volume and OI PCR are separate | Same market/session/final cutoff required | No complete total option activity input among the three | None | None | Yes |
| Open interest | Contracts; price/value scaling remains contract-dependent | Instrument/session source date, never capture date | Selected/recent contracts only; no historical universe | LS `t2424` current/recent provider view | KB `out3.nstmt_agr_q` | Yes for complete-market daily history |
| Individual / foreign / institution futures/options flow | LS `sv_*` quantity unit unresolved; `sa_*` amount is inferred as KRW 100 million for K2I futures `U`; signs are source-native. KB zeros are unavailable, not zero flow | LS raw D/N/U are distinct; `U` is all-session-like only by inference and is not `D+N`; D/N final meanings unresolved | 263-row apparent retention window; option-U institution aggregate differences; no Normalized writer; KB cannot serve as secondary for these fields | LS `t8462` Raw provider view with D/N/U exposed | None; KB affected fields must remain unavailable | Yes for confirmed units/session semantics and canonical daily final |
| VKOSPI | Expected unit would be index points, but no provider series is identified | Official final publication/revision semantics absent | Do not substitute volatility futures spot/settlement or another volatility index | None | None | Yes |

## 6. Macro

### Provider endpoint and availability matrix

| economic_variable | KB endpoint / availability | LS endpoint / availability | Toss endpoint / availability | history_available | current_snapshot_available | daily_final_available | verdict |
|---|---|---|---|---|---|---|---|
| Korea base rate | None identified | None identified | Market-indicator catalog has no policy-rate instrument | No | No | No | `NOT_COVERED` |
| Korea Treasury 3Y | No dedicated verified Treasury history; KB liquidity/futures fields are different variables | `t8428.bndsmoney` is not a usable bond-yield definition | `GET /api/v1/market-indicators/KR_BOND_3Y/candles`; retained complete 2019-01-02..2026-08-10 artifact | Yes | Latest daily row | Yes, provider daily candle | `COVERED_WITH_LIMITS` |
| Korea Treasury 10Y | Same KB limitation | Same LS limitation | `GET /api/v1/market-indicators/KR_BOND_10Y/candles`; retained complete artifact | Yes | Latest daily row | Yes, provider daily candle | `COVERED_WITH_LIMITS` |
| USD/KRW | `IVSA0070.out4` includes `KRWUSDCOMP`; `IVA60190` is a current FX snapshot family | No retained verified spot-FX route | Official schema has a dateTime point-lookup exchange-rate operation, but exact pair/direction evidence is not retained here | No verified daily history | Candidate/current KB | No; quote direction/unit/date unresolved | `UNVERIFIED` |
| USD/JPY | `IVSA0070.out4` includes `JPYUSDCOMP`; `IVA60190` current FX family | No retained verified spot-FX route | Point-lookup exchange-rate operation exists, exact pair/direction unverified | No verified daily history | Candidate/current KB | No | `UNVERIFIED` |
| JPY/KRW | No direct verified row; deriving from unresolved KB quote directions is prohibited | None | No direct retained route | No | No | No | `NOT_COVERED` |
| U.S. major indices | `IVSA0070.out4`: Dow, S&P 500, Nasdaq Composite, SOX plus S&P/Nasdaq futures; `GSC10060` per-symbol overseas chart history is documented | No retained accepted route | Market-indicator catalog is Korean-only | KB chart history is candidate; current snapshot verified | Yes, KB with row-specific timestamps | No accepted daily-final dataset | `CURRENT_ONLY` |
| U.S. rates | No verified U.S. Treasury/rate row or history endpoint in retained KB evidence | None | None | No | No | No | `NOT_COVERED` |
| DXY | No DXY row in retained KB global-symbol slice/catalog evidence | None | None | No | No | No | `NOT_COVERED` |

### Semantics and daily routing

| economic_variable | unit_semantics | date/session_semantics | known_limitations | recommended_daily_primary | recommended_secondary | external_source_still_required |
|---|---|---|---|---|---|---|
| Korea base rate | Annual percent expected; no source contract | Policy effective/announcement date would need explicit treatment | No endpoint/source | None | None | Yes, e.g. official BOK/ECOS after separate source definition |
| Korea Treasury 3Y | Toss OHLC values are decimal percent; volume unit unknown; not identical to BOK/KOFIA final quotation yield | Midnight timestamp is a daily bucket, not publication time; availability is null/PIT blocked | Provider method, benchmark selection, revisions and original availability unknown | Toss latest `KR_BOND_3Y` candle | None within trio | No for a provider-labelled descriptive tile; yes for authoritative/PIT use |
| Korea Treasury 10Y | Same as 3Y | Same as 3Y | Same; never merge with BOK values | Toss latest `KR_BOND_10Y` candle | None within trio | No for descriptive tile; yes for authoritative/PIT use |
| USD/KRW | `KRWUSDCOMP` label/value cannot be relabelled to the requested quote convention without official unit/direction evidence | KB row carries its own `dt_tm`; rows in one response have different dates | No retained successful semantic pilot or daily history | None until quote direction is resolved | Toss point lookup only after official schema/pilot confirmation | Yes |
| USD/JPY | Same direction/unit blocker for `JPYUSDCOMP` | Row-specific timestamp/date | Same | None until resolved | Toss candidate | Yes |
| JPY/KRW | Cross-rate derivation requires confirmed input quote conventions and one explicit Derived contract | No aligned source/session rule | Do not combine providers or silently calculate in GUI code | None | None | Yes |
| U.S. major indices | Provider-native index points; futures and cash indices are distinct | Each KB row retains its own overseas `dt_tm`; some are previous U.S. close while futures may be live | No accepted history/finality contract; symbol coverage is a fixed snapshot list | KB current provider view | None | Yes for historical daily finals; no for labelled current snapshot |
| U.S. rates | Annual percent expected; no source | U.S. session/publication/vintage absent | No route | None | None | Yes |
| DXY | Index points expected; no licensed/defined source | Session/close absent | No route | None | None | Yes |

## 7. Other

### Provider endpoint and availability matrix

| economic_variable | KB endpoint / availability | LS endpoint / availability | Toss endpoint / availability | history_available | current_snapshot_available | daily_final_available | verdict |
|---|---|---|---|---|---|---|---|
| Gold | No Gold row in retained `IVSA0070.out4`; no verified catalog route | Commodity-derivatives investor TRs do not establish a Gold price series | None | No | No verified price snapshot | No | `NOT_COVERED` |
| WTI | `IVSA0070.out4` includes `NYM@CL` WTI near-month current row | No retained accepted WTI price route | None | No | Yes, KB | No accepted daily-final history | `CURRENT_ONLY` |
| Brent | No Brent row in retained KB slice/catalog evidence | No retained accepted Brent route | None | No | No | No | `NOT_COVERED` |
| ETF current data | Generic KB per-symbol quote/master may include ETFs, but ETF coverage/NAV/AUM semantics are not established | `POST /stock/etf`: `t1901` current price/NAV/disparity and `t1904` AUM/constituents; documented, not live-observed in this audit | Current stock universe/security type exists, but no retained ETF-specific NAV/AUM endpoint evidence | `t1903` per-current-symbol recent history is survivorship-unsafe for the ETF universe | Official LS candidate only | No accepted full-universe daily final | `UNVERIFIED` |

### Semantics and daily routing

| economic_variable | unit_semantics | date/session_semantics | known_limitations | recommended_daily_primary | recommended_secondary | external_source_still_required |
|---|---|---|---|---|---|---|
| Gold | No contracted unit/instrument | No source session | No verified route; do not assume a KB blank/global slot is Gold | None | None | Yes |
| WTI | KB provider-native near-month price; it is not a continuous-futures contract | Row-specific date/time; near-month roll/session rule undocumented | Current only; no OHLCV history or roll/PIT contract | KB `NYM@CL` labelled snapshot | None | Yes for daily history/continuous-series semantics |
| Brent | No contracted unit/instrument | No source session | No verified route | None | None | Yes |
| ETF current data | LS price/NAV/ratio fields are provider-native; `t1904` net assets/AUM is KRW 100 million | Capture x ETF and PDF application/source dates must remain separate | Two calls per ETF; about 2,320 calls/39+ minutes for ~1,160 ETFs at 1 request/sec; endpoints do not supply a historical PIT universe | LS `t1901+t1904` only after a bounded benchmark pilot; not yet primary | KB generic quote only after ETF eligibility/field audit | Yes for efficient full-market universe/history; not necessarily for a small forward watchlist after pilot |

## 8. Canonical history versus current observation

The recommended storage/use boundary is fixed as follows.

| Situation | Required treatment |
|---|---|
| Toss KOSPI/KOSDAQ investor or Treasury retained history | Keep provider-specific `*_daily` and the existing provider/availability fields. A latest finalized row can be displayed without creating a duplicate snapshot dataset. |
| KB IVSA0070 | Keep as seven provider-specific `*_snapshot` slices. `capture_date` is partition metadata; nullable slice-specific `market_date` is not inferred from `inq_dy_tm`. |
| LS t8428 / t1633 / t8462 retained data | Keep as Landing/Raw source observations until their independent contract, finality, unit, and PIT gates pass. Raw history is not a canonical daily dataset. |
| Provider overlap | Select a documented primary and show a separate secondary/cross-check. Never average, splice, or silently fill. |
| Derived values | Derive only inside a declared Derived contract from one compatible source/scope/session. Do not create PCR, FX crosses, basis, or market totals ad hoc in GUI code. |

## 9. Bounded pilot candidates after this audit

These are historical candidate designs, not current permission gates. Under the
standing Data authorization an agent may investigate, implement, schedule, and
promote a candidate when its current identity/schema/rights/finality contract
supports that step. Operations remain Landing-first, independently dated,
provider-rate-aware, idempotent, and prior-valid-data preserving.

| Priority | Provider / endpoint | Exact unresolved question | Minimal bounded candidate | PASS does not imply |
|---:|---|---|---|---|
| 1 | LS `t1901` + `t1904` | Can a small ETF benchmark set provide stable current price/NAV/disparity/AUM/constituent fields with explicit source/PDF dates? | Two known ETFs, exactly four business calls after one OAuth; preserve all field labels/units and source dates | Full-market adoption, historical universe, or a daily scheduler |
| 2 | LS `t2111` | Does the current KOSPI200 futures response expose a stable provider basis with contract/session/time and documented scaling? | One verified current outright contract, one business call | Historical basis or full contract-universe coverage |
| 3 | LS `t2214` or `t8466` | Can a separately verified expired contract be queried at a historical boundary without truncation or current-universe bias? | One expired contract, one bounded date/range call | A backfill or safe historical instrument universe |
| 4 | KB `IVS11560` domestic index | Does one officially resolved KOSPI200 index code return dated OHLCV/value rows with stable source/session semantics? | One OAuth plus one business call, as already designed in the retained KB audit | Bulk history or automatic Normalized promotion without verified terms/evidence |
| 5 | KB `IVA60190` / `IVSA0070.out4` FX | What are the exact quote directions, units/scales, and row timestamps for `KRWUSDCOMP` and `JPYUSDCOMP`? | Prefer offline workbook/provider clarification first; if still unresolved, one newly keyed bounded snapshot call under the standing Data runbook | USD/KRW, USD/JPY, or JPY/KRW canonical daily history |
| 6 | Toss exchange-rate point lookup | Which currency pairs and quote conventions are officially returned, and is the route a point observation or daily final? | Official schema capture first; at most two fixed pair/dateTime lookups under standing Data authority | A historical FX series or cross-rate derivation without an evidence-backed Derived contract |
| 7 | KB `IVU10430` | Does omitted `is_cd` have an officially documented market-aggregate meaning? | Only if documentation first establishes the mode; one short interval and one business call | Current-symbol fan-out or derivatives flow coverage |

No bounded pilot is proposed for Korea base rate, JPY/KRW, U.S. rates, DXY, Gold,
Brent, PCR, or VKOSPI because the retained KB/LS/Toss evidence does not first identify
a suitable endpoint at the required grain. Those are external-source research gaps,
not pilot-ready provider candidates in this historical audit. Agents may continue
public/existing-credential source research and establish a bounded route under
the standing Data runbook.

## 10. Evidence reviewed

The conclusion is grounded in the current authority and the retained provider
evidence below. Archived audits are cited as evidence only, never as execution
instructions.

- [Data Status](../data/DATA_STATUS.md),
  [Dataset Index](../data/DATASET_INDEX.md), and
  [Source Registry](../data/SOURCE_REGISTRY.md).
- KB retained official workbooks/samples under `docs/archive/data/evidence/2026-08-data-phase/kb/official/`,
  [KB API catalog](../archive/data/evidence/2026-08-data-phase/kb/api_catalog.md),
  [used endpoints](../archive/data/evidence/2026-08-data-phase/kb/used_endpoints.md),
  [current snapshot contract](../data/research/active/KBSEC_SNAPSHOT_CONTRACT.md), and
  [IVSA0070 derivatives audit](../archive/data/evidence/2026-08-data-phase/kb/KBSEC_IVSA0070_20260814_DERIVATIVES_AUDIT.md).
- [KB historical discovery audit](../archive/data/audits/2026-08-data-phase/KBSEC_HISTORICAL_API_DISCOVERY_AUDIT.md)
  and
  [KB/Toss integration audit](../archive/data/audits/2026-08-data-phase/KB_TOSS_HISTORICAL_INTEGRATION_AUDIT.md).
- [LS source inventory](../archive/data/evidence/2026-08-data-phase/ls/LS_OPENAPI_SOURCE_INVENTORY.md),
  [LS derivatives investor semantics](../data/research/active/LS_T8462_DERIVATIVES_SEMANTICS.md),
  and [semantic resolution audit](../archive/data/evidence/2026-08-data-phase/ls/SEMANTIC_RESOLUTION_AUDIT_20260817.md).
- [Toss historical candidate audit](../archive/data/audits/2026-08-data-phase/TOSS_HISTORICAL_CANDIDATE_AUDIT.md),
  Toss contracts/provider code, retained survivorship evidence, and the complete
  retained market-investor/Treasury artifact summaries.
- [Futures basis design](../archive/data/audits/2026-08-data-phase/C009_FUTURES_BASIS.md),
  [VKOSPI source audit](../archive/data/audits/2026-08-data-phase/VKOSPI200_SOURCE_AUDIT.md),
  and [Treasury availability audit](../archive/data/audits/2026-08-data-phase/A006_TREASURY_AVAILABILITY_AUDIT.md)
  for semantic boundaries only.

## 11. Final adoption decision

For a first provider-only Daily Dashboard, adopt the following display policy:

1. Use Toss latest finalized rows for KOSPI/KOSDAQ candles, market investor flow,
   and Korean Treasury 3Y/10Y, with provider and source date visible.
2. Use KB only as a provider-labelled current snapshot for KOSPI200, breadth,
   selected Korean derivatives, U.S. indices, and WTI; expose row/slice date and
   availability status and never promote unresolved rows to `*_daily`.
3. Use LS `t1633`, `t8428`, and `t8462` only as explicitly labelled source
   observations until their existing semantic/PIT gates close. Prefer LS over KB
   for the richer provider view, but do not call it canonical.
4. Leave 14 rows (`UNVERIFIED + NOT_COVERED`) out of the default dashboard. The
   six unverified rows require the bounded evidence above or a narrower declared
   watchlist grain; the eight not-covered rows require external-source research.

This yields **20/34 descriptively displayable rows, 7/34 credible daily-final
routes, and 0/34 fully unconditional rows** using KB + LS + Toss alone.
