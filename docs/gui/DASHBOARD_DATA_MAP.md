# Dashboard Data Map

> Physical-artifact audit at 2026-08-17 KST. This is a GUI source-selection
> snapshot and evidence view, not current execution authority. Current routing
> remains in [Data Status](../data/DATA_STATUS.md). A missing collector,
> contract, runbook, schedule, or local query is implementable work under
> standing Project/Data/GUI authority; semantic, rights, finality, and PIT gates
> still control the dependent numeric display or promotion.

For the 19-variable Dashboard MVP, final display/daily/history provider ownership is
now fixed by [Dashboard Daily Source Routing](DASHBOARD_DAILY_SOURCE_ROUTING.md).
This file remains the physical-artifact view and must not override that routing.

## Reading status

- `DASHBOARD_READY`: retained, typed data can be displayed with its as-of date.
- `READY_AFTER_REFRESH`: retained data exists but is not current to the known
  completed market date; use only after its contract-valid Data-owned route is
  safely refreshed.
- `RAW_ONLY`: Landing evidence exists, but no accepted Normalized display
  dataset exists.
- `CURRENT_SNAPSHOT_ONLY`: only a provider current snapshot is usable.
- `LATEST_FINAL_DAILY`: the latest accepted finalized row of a retained daily
  artifact is displayable; it is not a realtime/current snapshot.
- `PIT_BLOCKED`: source date exists, but publication/revision timing blocks
  predictive use; descriptive display requires a typed GUI contract with source,
  as-of, finality, and PIT limitation visible, not a separate permission grant.
- `SEMANTIC_BLOCKED`: a required display transformation/unit is not contracted.
- `MISSING`: no retained dataset for the requested variable.
- `SOURCE_RESEARCH_REQUIRED`: a source candidate exists but its usable source,
  storage, semantics, or license gate remains open.

`GUI Usable = Yes` means descriptive GUI use only. It never overrides a
contract's PIT or backtest restriction.

## Candidate variables

| Category | Display Name | Economic Variable | Dataset | Provider | Columns | Unit | Coverage | Latest Date | Status | GUI Usable | Refresh Route | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Index | KOSPI | KOSPI index close | `kr_index_daily` | KRX via pykrx | `date,symbol,close` (`symbol=KOSPI`) | index points | 1975-01-04.. | 2026-08-07 | READY_AFTER_REFRESH | No | equity availability sentinel / `kr_index_daily` maintenance | KRX request was intentionally not run; do not use a KB snapshot as replacement. |
| Index | KOSDAQ | KOSDAQ index close | `kr_index_daily` | KRX via pykrx | `date,symbol,close` (`symbol=KOSDAQ`) | index points | 1996-07-01.. | 2026-08-07 | READY_AFTER_REFRESH | No | same | Same KRX concurrency boundary. |
| Index | KOSPI200 | KOSPI200 spot index close | `kr_kospi200_index_daily` | KRX via pykrx ticker `1028` | `date,symbol,close` (`symbol=KOSPI200`) | index points | 1990-01-03.. | 2026-08-07 | DASHBOARD_READY | Yes | read-only retained artifact | `PIT_SAFE_EOD_T_PLUS_1`; one preserved 1995-01-04 source OHLC anomaly. |
| Index | S&P 500 | U.S. large-cap index close | `global_index_price_daily` | Yahoo | `date,symbol,close` (`SP500`) | provider-native index points | 1928-01-03.. | 2026-08-14 | DASHBOARD_READY | Yes | `GLOBAL_CURRENT_REFRESH` Yahoo phase | Current value is descriptive; historical Raw provenance remains limited. |
| Index | NASDAQ Composite | U.S. composite index close | `global_index_price_daily` | Yahoo | `date,symbol,close` (`NASDAQ_COMPOSITE`) | provider-native index points | 1971-02-05.. | 2026-08-14 | DASHBOARD_READY | Yes | same | Same Yahoo limitation. |
| Index | NASDAQ-100 | U.S. large non-financial index close | `global_index_price_daily` | Yahoo | `date,symbol,close` (`NASDAQ100`) | provider-native index points | 1985-10-01.. | 2026-08-14 | DASHBOARD_READY | Yes | same | Same Yahoo limitation. |
| ETF | SOXX | iShares Semiconductor ETF market price | `global_etf_price_daily` | Yahoo adapter, offline only | `date,symbol,close,volume` (`SOXX`) | provider-native unadjusted OHLCV; adjusted close separate | — | — | DATA_NOT_AVAILABLE | No | standing Data onboarding authorized; production rows absent | Generic contract/service path exists; onboard through Data and never substitute SOX. |
| Rates | BOK base rate | Korean policy rate | — | BOK ECOS candidate | — | percent | — | — | SOURCE_RESEARCH_REQUIRED | No | no accepted artifact/route | Treasury-yield observation is not the policy-rate series. |
| Rates | Korea Treasury 3Y | KOFIA final-quotation yield | `bok_ecos_kr_treasury_yield_source_observation` | BOK ECOS | `date,tenor,yield_percent` (`3Y`) | annual percent | 1998-11-13.. | 2026-08-13 | PIT_BLOCKED | Provisional | no current active refresh runbook | Immutable source observation; publication/revision timing unresolved. |
| Rates | Korea Treasury 10Y | KOFIA final-quotation yield | same | BOK ECOS | `date,tenor,yield_percent` (`10Y`) | annual percent | 1998-11-13.. | 2026-08-13 | PIT_BLOCKED | Provisional | same | Do not merge with Toss yield candles. |
| Rates | Korea 10Y–3Y | term spread | no derived artifact | BOK ECOS inputs | `10Y yield_percent - 3Y yield_percent` | percentage points | inputs 1998-11-13.. | 2026-08-13 | PIT_BLOCKED | No | proposal only | Do not create an ad-hoc persisted derived dataset in GUI work. |
| Rates | U.S. Treasury 2Y | constant-maturity yield | `fred_treasury_yield_daily` | FRED (`DGS2`) | `date,dgs2` | annual percent | 1962-01-02.. | 2026-08-13 | DASHBOARD_READY | Yes | `GLOBAL_CURRENT_REFRESH` FRED-yields phase | Provider vintage/revision limitation remains; no predictive claim. |
| Rates | U.S. Treasury 10Y | constant-maturity yield | same | FRED (`DGS10`) | `date,dgs10` | annual percent | 1962-01-02.. | 2026-08-13 | LATEST_FINAL_DAILY / PIT_LIMITED | Yes | same | Descriptive latest-final display only; show source and observation date. |
| Rates | U.S. 10Y–2Y | term spread | `us_treasury_spread_daily` | derived from FRED | `date,spread_10y_2y` | percentage points | 1962-01-02.. | 2026-08-13 | DASHBOARD_READY | Yes | rebuilt atomically with FRED-yields promotion | Derived only from the retained FRED source; no provider merge. |
| FX | USD/KRW | FRED DEXKOUS observation | `fred_usd_fx_daily` | FRED (`DEXKOUS`) | `date,dexkous` | provider-labelled DEXKOUS value | 1971-01-04.. | 2026-08-07 | LATEST_FINAL_DAILY / PIT_LIMITED | Yes | `GLOBAL_CURRENT_REFRESH` FRED-FX phase | Display without inversion, with source and observation date; not a current snapshot. |
| FX | USD/JPY | requested quote convention | raw `fred_usd_fx_daily` exists | FRED (`DEXJPUS`) | `date,dexjpus` | source unit not contracted here | 1971-01-04.. | 2026-08-07 | SEMANTIC_BLOCKED | No | same | Same direction/unit gate. |
| FX | JPY/KRW | cross rate | no derived artifact | FRED inputs candidate | `DEXJPUS,DEXKOUS` | requested cross unit | shared input range | 2026-08-07 | SEMANTIC_BLOCKED | No | proposal only | Requires confirmed quote direction and an explicit derived-data policy; never silently compute in the GUI. |
| FX | Dollar Index (DXY) | USD index | — | ICE DXY / other candidate | — | index points | — | — | SOURCE_RESEARCH_REQUIRED | No | no accepted source/artifact | No DXY series is retained in Yahoo/FRED artifacts; a new source/license audit is required. |
| Commodity | Gold | Yahoo vendor-continuous gold future | `global_commodity_futures_daily` Landing only | Yahoo (`GC=F`) | source-native close in retained raw response | vendor-native | 2000-08-30.. | 2026-08-14 | PROVIDER_RAW_VIEW / NORMALIZED_REVIEW_REQUIRED | Yes, Raw-labelled | contract/finality-gated promotion | Raw provider view only; never represent it as Normalized or PIT-safe. Continuous roll semantics remain review-required. |
| Commodity | WTI | Yahoo vendor-continuous WTI future | same | Yahoo (`CL=F`) | source-native close in retained raw response | vendor-native | 2000-08-23.. | 2026-08-14 | PROVIDER_RAW_VIEW / NORMALIZED_REVIEW_REQUIRED | Yes, Raw-labelled | same | Raw provider view only; includes retained negative observations and performs no correction. |
| Commodity | Brent | Yahoo vendor-continuous Brent future | same | Yahoo (`BZ=F`) | source-native OHLCV | vendor-native | 2007-07-30.. | 2026-08-14 | RAW_ONLY | No | same | Same Raw/roll/PIT blocker. |
| Internals | KOSPI volume | market-level volume | `kr_index_daily` | KRX via pykrx | `date,symbol,volume` (`KOSPI`) | source integer volume | retained index coverage | 2026-08-07 | READY_AFTER_REFRESH | No | KRX index maintenance only | Direct market-level field; no equity-Parquet aggregation. |
| Internals | KOSDAQ volume | market-level volume | `kr_index_daily` | KRX via pykrx | `date,symbol,volume` (`KOSDAQ`) | source integer volume | retained index coverage | 2026-08-07 | READY_AFTER_REFRESH | No | same | Direct market-level field. |
| Internals | KOSPI trading value | market-level turnover | `kr_index_daily` | KRX via pykrx | `date,symbol,trading_value` (`KOSPI`) | source integer value | retained index coverage | 2026-08-07 | READY_AFTER_REFRESH | No | same | Direct market-level field; retain provider unit. |
| Internals | KOSDAQ trading value | market-level turnover | `kr_index_daily` | KRX via pykrx | `date,symbol,trading_value` (`KOSDAQ`) | source integer value | retained index coverage | 2026-08-07 | READY_AFTER_REFRESH | No | same | Direct market-level field. |
| Internals | Advances / declines / unchanged | market breadth | `kr_market_breadth_daily` | derived canonical price + universe | `date,market,advancing,declining,unchanged,total` | security counts | KOSPI 1995-05-03..; KOSDAQ 1996-07-02.. | 2026-08-12 | READY_AFTER_REFRESH | No | rebuild after equity increments | Equivalent A/D is `advancing - declining`; compute only at query time from this small aggregate. |
| Internals | Foreign net purchase | market investor flow | `kr_market_investor_net_purchase_bridge_daily` | legacy pykrx + Toss bridge | `date,market,foreign_net_purchase,value_unit,provider_segment` | provider-native value unit | 1999-01-04.. | 2026-08-11 | DASHBOARD_READY | Yes | no active standalone refresh route | Display its date and `provider_segment`; no cross-provider averaging or gap fill. |
| Internals | Institution net purchase | market investor flow | same | same | `institution_net_purchase` plus provenance columns | provider-native value unit | 1999-01-04.. | 2026-08-11 | DASHBOARD_READY | Yes | same | Same provider-boundary/PIT limitation. |
| Internals | Individual net purchase | market investor flow | same | same | `individual_net_purchase` plus provenance columns | provider-native value unit | 1999-01-04.. | 2026-08-11 | DASHBOARD_READY | Yes | same | Same provider-boundary/PIT limitation. |
| Risk | VIX | FRED-distributed VIX daily close plus separately labelled Yahoo `^VIX` current display | `fred_vix_daily`; `market_price_15m_current` | FRED (`VIXCLS`) daily authority; Yahoo provider-native completed 15-minute current observation | `date,vixcls` history; current `value,provider_timestamp`; query-time current-value rank against at most 250 completed FRED days | index points | FRED 1990-01-02..; current observation is non-historical | current display + LATEST_FINAL_DAILY / PIT_LIMITED | Yes | FRED daily collector and unified Yahoo 30-minute polling task remain independent | Prefer the accepted Yahoo completed bar for the visible current VIX temperature and label it `Yahoo15m`; use FRED only for the daily comparison distribution. Never append/resample/promote the current quote into daily history or Backtest. |
| Risk | VKOSPI | KOSPI 200 volatility option index | `kr_vkospi_daily` | KRX official code `1300`, direct `MDCSTAT01201` | `market_date,close` plus query-time 1D change/change %, 20D/60D/250D percentiles | index points | 2003-01-02..2026-08-14 | 2026-08-14 | LATEST_FINAL_DAILY / PIT_LIMITED | Yes | finalized-date bounded incremental only; Landing-first and offline atomic promotion | Show KRX source/date/freshness; VIX is never substituted or combined. |
| Risk | KOSPI200 volume PCR | total KOSPI200 option put/call volume ratio | `kr_kospi200_option_pcr_daily` | KRX legacy + data.go.kr inputs | `date,volume_pcr,put_volume,call_volume,observation_status` | ratio | 2010-01-04.. | 2026-08-07 | DASHBOARD_READY | Yes | no current active refresh route | `volume_pcr = put_volume / call_volume`; total scope, `market_scope=unspecified_by_source`. `VALID_EMPTY` yields null ratio, not zero. |
| Risk | KOSPI200 OI PCR | total KOSPI200 option put/call open-interest ratio | same | same | `date,open_interest_pcr,put_open_interest,call_open_interest,observation_status` | ratio | 2010-01-04.. | 2026-08-07 | DASHBOARD_READY | Yes | same | `open_interest_pcr = put_open_interest / call_open_interest`; separate from volume PCR. |
| Risk | KOSPI200 Raw Call/Put Wall | front-maturity strikes with maximum source open interest | read-only `kospi200_option_walls` feature over the published options bridge, explicitly joined to `kr_kospi200_index_daily` | legacy KRX + data.go.kr options; KRX/pykrx ticker `1028` spot | `date,underlying_close,front_maturity,call_wall_strike,put_wall_strike,call_distance_pct,put_distance_pct,call_open_interest,put_open_interest,call_oi_change,put_oi_change,wall_status,warning` | strike/index points and ratios | 2010-01-04.. | 2026-08-07 | DASHBOARD_READY_RAW_EOD | Yes, computed read-only | no active refresh route; rebuild only from accepted retained inputs | Same-date explicit EOD T+1 join only. `NO_OPEN_INTEREST` produces null Wall; ties remain evidence; `EXTREME_MONEYNESS` warns without deletion/correction. Active Wall threshold is unset. |
| Derivatives | KOSPI200 futures basis | nearest listed, same-row regular-session basis | `kr_kospi200_futures_nearest_listed_daily` | provider bridge | `date,session,settlement_basis,basis_status,price_unit_status` | source-native index-point difference | 2010-01-04.. | 2026-08-07 | DASHBOARD_READY | Yes | no current active refresh route | Select `session=REGULAR_DAY` and `basis_status=SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE`; no continuous/back-adjusted inference. |
| Derivatives | Foreign KOSPI200 futures net purchase | official investor net purchase | `kr_kospi200_futures_investor_net_purchase_daily` | settings-bound KRX CSV | `date,product,session,investor_type_source,net_purchase_trading_value,trading_value_unit_source` | source-selected `백만원` | 1999-04-26.. | 2026-08-13 | DASHBOARD_READY | Yes | settings-bound manual-file maintenance | Select `product=KOSPI200_FUTURES`, `session=ALL`, foreign investor type; other full-flow datasets remain blocked. |

## Remaining missing-risk source

- No retained DXY series exists. Do not use a convenient Yahoo/other ticker as a
  substitute without a source, license, and semantics audit.
- Retained FRED VIX and official KRX VKOSPI are separate descriptive series as
  recorded in the table above. Both remain `PIT_LIMITED` and are not eligible
  for predictive Backtest use until the evidence gate is closed.

## Lightweight query boundary (implementation allowed)

Agents may implement and test the listed local read-only interfaces under
standing GUI authority. They are feasible without a whole-equity scan if they read
only the relevant year/market/symbol partition and project only required
columns. `date` is a row filter inside a yearly Parquet partition, not a
date-directory partition; the current-year partitions are nevertheless small.

| Future interface | Minimal inputs | Safe read plan | Constraint |
|---|---|---|---|
| `get_latest_market_metrics()` | global index, FRED yield/spread, index market aggregate, BOK observation, derivatives | read 2026 partitions, requested columns only; find last dated row per source | Never assume sources share a latest date. |
| `get_market_breadth(date)` | `kr_market_breadth_daily` | `market=KOSPI/KOSDAQ/year=YYYY`, filter `date`, project count columns | Rebuild is required after equity increments. |
| `get_market_investor_flow(date)` | published investor bridge | `market=.../year=YYYY`, filter `date`, retain provider provenance columns | Preserve segment/date/PIT labels. |
| `get_risk_sentiment(date)` | PCR, nearest futures basis, FRED VIX, KRX VKOSPI | year partition, filter date + declared `scope/session/status` | VIX/VKOSPI may be served descriptively with source/date/`PIT_LIMITED`; exclude them from predictive Backtest use until availability/revision gates close. |
| `get_index_series(index,start_date,end_date)` | Korean or Yahoo index root; dedicated KOSPI200 root | select `market`/`symbol` partition and years in range; project `date,close` | KOSPI200 uses `kr_kospi200_index_daily`, never futures `spot_value`. |
| `get_kospi200_option_walls(start_date,end_date,policy)` | published option bridge plus dedicated KOSPI200 spot root | compute Raw Wall, then call the explicit same-date join helper; project Wall, OI/volume/change, distance, status, ties, and warnings | No automatic date/source join. EOD T+1 only; `policy` may configure warnings but may not define an Active Wall threshold implicitly. |
| `get_macro_series(series,start_date,end_date)` | FRED / BOK | year partitions and named source column/tenor only | Do not implement FX inversion/cross-rate or BOK spread silently. |

No new aggregate, service, cache, normalized dataset, or GUI code is created by
this map.
