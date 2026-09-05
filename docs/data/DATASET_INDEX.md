# Dataset Index

Navigation view only. [Data Status](DATA_STATUS.md) decides current priority and
authorization; a dataset contract decides schema and semantics. Do not use a
path, an evidence record, or this index as permission to collect or promote.

## How to use this index

Select one row from the active route in Data Status, then read only its listed
contract, checkpoint/state, and active runbook if one is routed. Coverage is
the retained artifact boundary, not a publication-time or PIT claim.

The typed Dataset Universe contains 93 logical dataset records: 82 contracted
catalog rows plus 12 retained Raw/research records, with one overlapping identity.
Three ORATS U.S. option P/C schemas are deliberately unregistered
`contract_only_no_entitlement` drafts and are excluded from both counts until
subscription, root-scope, finality, and Data-operation gates are approved.
The typed operations registry contains 49 rows. Its post-baseline rows
are the KRX broad-index valuation row, the exact KOSPI200
constituent/price/breadth dependency chain, the Toss market-investor source,
the Toss Korean Treasury OHLC source, the BOK Treasury source observation, the
BOK USD/KRW daily source, the same-day provisional Korean equity price source,
the two current-list Korean ETF datasets, and the manual-ready KB transaction
ledger lane; all now have explicit operational ownership. The dated pre-ETF row-level audit is
[`artifacts/data_inventory/full_dataset_universe_multiaxis_20260818.csv`](../../artifacts/data_inventory/full_dataset_universe_multiaxis_20260818.csv).
It remains historical reconciliation evidence; the typed registry and this
navigation view contain the thirteen later rows.
Physical coverage does not imply daily automation: exactly 49 typed-universe
records are explicitly `automation_enabled=True` and all map to 22 active scheduler
lanes. The human-readable task-to-lane-to-dataset relationship and all 44
automation-disabled dispositions are in the
[Scheduler Data Map](SCHEDULER_DATA_MAP.md). The retained
`market_price_60m_observation` history is `STATIC_COMPLETE / NO_REFRESH` and is
not one of them. Its separate current-display operation writes no Normalized
history or Backtest data. Every other record remains disabled. The retained
33-row core health snapshot is at
`artifacts/daily_health/core_data_20260818.json`. `primary_classification` is a
deprecated compatibility projection and must not drive operations, scheduling,
GUI, or backtest eligibility.

자동화 비활성 44개는 하나의 “대체됨” 묶음이 아니다. 현재 구성은
수동 게이트 16개, 연구 전용 11개, 계약·의미 미확정 8개,
자동 갱신하지 않는 보존 자료 9개다. 특히
`kr_equity_*` 연구 계약 4개와 `kr_investor_flow_daily`는 완전 대체가 확인되지
않았고, 세 과거 구간 데이터셋은 현재 브리지의 상류 입력이므로 삭제 대상이 아니다.

Each of the 93 rows also carries an explicit consumer triad with a bounded
reason code. Display eligibility is evidenced only by `gui_use`; research
eligibility is evidenced only by an accepted contract and/or retained local
evidence; predictive eligibility is evidenced only by positive PIT safety.
The current totals are display `32 ELIGIBLE / 20 LIMITED / 41 BLOCKED`, research
`64 ELIGIBLE / 29 LIMITED / 0 BLOCKED`, and predictive
`9 ELIGIBLE / 0 LIMITED / 84 BLOCKED`. `PIT_LIMITED`, collection readiness,
automation, and freshness never imply predictive eligibility.

The typed 93-row registry in this document is the canonical dataset navigation
view. Its retained dated reconciliation inputs, which predate the two Korean
ETF rows, are
[`full_dataset_universe_20260818.csv`](../../artifacts/data_inventory/full_dataset_universe_20260818.csv)
and
[`full_dataset_universe_multiaxis_20260818.csv`](../../artifacts/data_inventory/full_dataset_universe_multiaxis_20260818.csv).
The Data-Status-selected Health V2 path projects current runtime truth. It is a
local mutable/regeneratable projection retained locally and intentionally
Git-ignored; its bytes are not canonical Git evidence. Physical Dashboard
routing remains owned by
[`DASHBOARD_DATA_MAP.md`](../gui/DASHBOARD_DATA_MAP.md). Historical 111-row GUI
audits and compact execution CSV views are not part of the canonical baseline
and must not be cited as active files. Navigation and audit views never
authorize collection, promotion, scheduling, GUI integration, or Backtest
execution.

### Universe reconciliation

Each row independently records role, grain, refresh policy, operational
readiness/blocker, the display/research/predictive consumer triad and reason
codes, predictive/PIT evidence, automation policy, scheduler management, and
GUI use. In particular, a predictive block does not imply that Raw collection
is operationally blocked, and an operationally ready row does not gain consumer
eligibility by inference.

| Measure | Count | Definition |
|---|---:|---|
| Logical dataset universe | 93 | 82 contracted catalog rows plus 12 retained/research rows without a registered contract, with one overlapping identity |
| Economic-variable families | 60 | Same variable across provider-specific observations and canonical/bridge layers grouped once; their datasets remain separate rows |
| Typed physical-artifact scopes | 95 | Unique layer/Landing/state/local-user scopes declared by the typed registry; provider call/file counts are excluded. |
| Operations registry before/after | 48 / 49 | The KB SWQA2301 cash-flow lane is registered `MANUAL_READY`; its automation flag remains off pending the first coordinator live run |
| Typed universe registry before/after | 92 / 93 | 19 current operation-registry omissions and 25 intentional exclusions remain explicit catalog rows; the new KB transaction row is registered |
| Registry missing after reconciliation | 0 | Every logical dataset is known by the typed universe registry |

| Data role | Count |
|---|---:|
| `SOURCE` | 51 |
| `SOURCE_OBSERVATION` | 8 |
| `RAW_OBSERVATION` | 12 |
| `DERIVED` | 7 |
| `PUBLISHED_BRIDGE` | 5 |
| `SNAPSHOT` | 7 |
| `HISTORICAL_SEGMENT` | 3 |
| **Total** | **93** |

| Grain | Count |
|---|---:|
| `DAILY` | 72 |
| `WEEKLY` | 6 |
| `EVENT_DRIVEN` | 5 |
| `SNAPSHOT` | 7 |
| `INTRADAY` | 3 |
| **Total** | **93** |

| Operational readiness | Count |
|---|---:|
| `READY_WITH_FINALITY_GATE` | 19 |
| `READY_WITH_LIMITS` | 24 |
| `READY` | 7 |
| `MANUAL_ONLY` | 21 |
| `BLOCKED` | 8 |
| `NOT_APPLICABLE` | 14 |
| **Total** | **93** |

| Dataset / group | Primary source | Coverage | Contract | Artifact / Landing path | High-level state |
|---|---|---|---|---|---|
| `kr_equity_price_daily`, market cap, provider/canonical universe | marcap + KRX Open API + data.go.kr | 1995-05-02..2026-08-25 | `kr_equity.py` | `data/{landing,normalized,published}/` | `LIVE_VALIDATED_THROUGH_20260825 / BOUNDED_CATCHUP_ACTIVE / API_ZERO_REPLAY`; the exact atomic states contain the same eight accepted dates through 2026-08-25; revision policy remains unresolved, no expanded historical PIT claim |
| `kr_equity_price_provisional_daily` | KRX via pykrx 1.2.8 market-wide OHLCV | no retained rows before the first bounded human run | `kr_equity_provisional.py` v1; canonical OHLCV columns plus `provisional`, `observed_at` | `data/landing/pykrx/kr_equity_provisional_daily/`; `data/normalized/kr_equity_price_provisional_daily/` | `AUTOMATION_ENABLED / KR_EQUITY_PROVISIONAL_DAILY / DISPLAY_AND_CONDITION_ALERTS_ONLY / NON_PREDICTIVE`; same-session 20:30 target, two calls, canonical overlap always wins in readers; see [runbook](operations/KR_EQUITY_PROVISIONAL_DAILY.md) |
| `kr_equity_investor_flow_daily` | KRX via pykrx 1.2.8 per-symbol trading value | no retained rows before the first bounded human run | `kr_equity_investor_flow.py` v1; `(date,symbol)`, signed participant net-purchase amounts in KRW | `data/landing/kr_equity_investor_flow_daily/`; `data/normalized/kr_equity_investor_flow_daily/symbol=<code>/year=<yyyy>/` | `AUTOMATION_ENABLED / KR_EQUITY_INVESTOR_FLOW_DAILY / KRX_POST_CLOSE_2030 / DESCRIPTIVE_ONLY / PIT_BLOCKED`; `on="순매수", detail=False`, watchlist plus retained-symbol union, five-session repair window, max 40 symbols and one call per symbol; see [runbook](operations/KR_EQUITY_INVESTOR_FLOW_DAILY.md) |
| `kr_index_daily`, `kr_market_breadth_daily` | KRX/pykrx; derived canonical equity inputs | index and breadth through 2026-08-25 | `kr_index_daily.py`, `kr_market.py` | `data/normalized/kr_index_daily/`, `data/derived/kr_market_breadth_daily/` | `AUTOMATION_ACTIVE_WITH_DATASET_LIMITS`; both 2026 index partitions contain 158 rows through 2026-08-25 with zero duplicate primary keys and the exact index state agrees; breadth is `COMPLETE` with no pending date for the same eight canonical-equity dates and advances only inside that transaction; predictive claims remain contract-limited |
| `kr_kospi200_index_daily` | KRX via pykrx ticker `1028` | 1990-01-03..2026-08-25; 9,453 rows | `kospi200_index_daily.py` | `data/normalized/kr_kospi200_index_daily/` | `AUTOMATION_ACTIVE / PIT_SAFE_EOD_T_PLUS_1`; exact lane state is `SUCCEEDED` and lane-atomic with KOSPI/KOSDAQ through 2026-08-25; all 37 partitions have duplicate date 0; one preserved 1995-01-04 source OHLC anomaly |
| `fred_vix_daily` | FRED `VIXCLS`; exact same-upstream FDR 0.9.202 parser fallback accepted only for typed primary `SCHEMA_ERROR`; direct Cboe route excluded | 1990-01-02..2026-08-26 | `global_market.py`; `fred_vix_fallback.py` | immutable provider-specific Landing, `data/normalized/fred_vix_daily/` | direct FRED advanced through the verified publication target; primary remains authoritative, scheduler cadence is unchanged, and predictive use remains `PIT_BLOCKED_PENDING_VINTAGE_RESOLVER` |
| `kr_vkospi_daily` | KRX code `1300`; direct `MDCSTAT01201` | 2003-01-02..2026-08-19; 5,832 rows | `vkospi_daily.py` | `data/raw/kr_vkospi_daily/`, `data/normalized/kr_vkospi_daily/` | bounded empirical 18:30 KST finality; `AUTOMATION_ACTIVE / PIT_LIMITED`; actual trigger and API-0 replay passed |
| KOSPI200 futures/options bridges, nearest-listed, PCR, option walls | legacy KRX + data.go.kr; KRX/pykrx ticker `1028` for Wall distance | Source futures 11,382 / options 2,722,082; Bridge futures 38,643 / options 3,812,160; Basis 6,544; persisted PCR total 4,233 (modern recovery segment 1,626); all through 2026-08-25; recent-250 Wall ends with one same-date 2026-08-25 row | `derivatives_price_authority.py`, bridge, basis, and `kospi200_option_walls.py` contracts | `data/{normalized,published,derived}/` bridge roots; recent-250 review at `artifacts/analysis/kospi200_option_wall_recent_250.csv` | `AUTOMATION_ACTIVE / CURRENT_20260825 / T_PLUS_1_EXPECTED_LAG / RETAINED_RECOVERY_API0_REPLAY`; all seven runtime probes validate, the replay is API 0, and the 20:30 v2 lane catches up oldest-first within three-session/six-call/600-second bounds; provider revision/predictive finality and pre-2010 coverage remain open |
| `kr_kospi200_futures_investor_net_purchase_daily` | settings-bound official KRX CSV | 1999-04-26..2026-08-13; 33,670 rows | `krx_derivatives_investor.py` | `data/landing/manual/krx_derivatives_investor/`; normalized artifact | `DATA_COMPLETE / USABLE_WITH_LIMITS` |
| `kr_short_selling_{trading,balance,investor}_daily` | authenticated KRX/pykrx official Primary | trading through 2026-08-19; balance through 2026-08-13 with accepted 2026-08-14 KOSPI valid-empty stop; investor through 2026-08-14 with a retained, unpromoted 2026-08-17 KOSPI-volume valid-empty placeholder | `kr_short_selling.py` | `data/landing/pykrx/short_selling/`; normalized artifacts | trading KOSPI/KOSDAQ production atomicity and API-0 replay passed; trading scheduler active; Balance/Investor remain separate manual gates and neither valid-empty stop may be retried under its completed approval; no fallback |
| `kr_stock_lending_daily`, market, participant | data.go.kr | 2021-04-01..2026-08-14 | `data_v1.py` | `data/landing/data_go_kr/`; normalized artifacts | official D+1 business day after 13:00 KST policy; all three scheduler/replay paths passed; `AUTOMATION_ACTIVE` |
| `kr_market_{liquidity,credit_balance}_daily` | data.go.kr | 2021-10/11..2026-08-06 | `data_v1.py` | `data/landing/data_go_kr/`; normalized artifacts; per-date finality state | two-pass scheduler active at 20:30/09:10; credit rechecks one prior 1–3-session date after same-day valid-empty and still requires two identical complete observations; retained live advancement remains pending |
| Market-investor provider bridge | legacy pykrx + Toss | 1999-01-04..2026-08-19; 9,784 rows | `investor_bridge.py`; `toss_market_investor_daily.py` | `data/published/kr_market_investor_net_purchase_bridge_daily/` | `CURRENT / provider-boundary / two-market atomic daily promotion`; same-date replay API 0 |
| Korean Treasury / credit | BOK ECOS; Toss; KRX credit candidate | BOK 1998..; Toss 2019..; credit 2002.. | `bok_ecos_treasury.py`, `global_market.py` | respective Landing/normalized roots | retained; publication/vintage limits remain |
| Corporate-action source observations | data.go.kr / OpenDART | dividends snapshot; rights partial; issuance 2020-07-14.. | observation contracts | `data/landing/data_go_kr/`; normalized observations | `SOURCE_OBSERVATION_ONLY / PREDICTIVE_USE_BLOCKED` |
| `kr_corp_code_map`, `kr_fundamentals_quarterly` | OpenDART `corpCode.xml`, `fnlttSinglAcntAll.json` | no retained fundamentals coverage until the first human-run two-step collection | `kr_fundamentals.py` v1; identity map `(corp_code)` and revision-preserving quarterly key `(symbol,bsns_year,reprt_code,fs_div,rcept_no)` | immutable `data/landing/opendart/kr_fundamentals_quarterly/`; candidate staging; `data/normalized/{kr_corp_code_map,kr_fundamentals_quarterly}/` after reviewed promotion | `MANUAL_TWO_STEP / AUTOMATION_DISABLED / DISPLAY_AND_SCANNER_ONLY / PIT_BLOCKED`; CFS-first with OFS only after CFS `013`, Q4 de-cumulation, exact local call ledger; [source contract](sources/opendart/README.md) |
| `global_index_price_daily` | Per-symbol registry: Yahoo chart for SP500/NASDAQ_COMPOSITE/NASDAQ100/SOX/DOW_JONES/DOLLAR_INDEX/NIKKEI225/TAIEX/EURO_STOXX50/HANG_SENG/DAX; Cboe public daily-history CSV for VIX9D/VIX3M/VIX6M/SKEW | Existing Yahoo broad indices retained; NIKKEI225/TAIEX/EURO_STOXX50/HANG_SENG/DAX registered with exact currency, Yahoo exchange, exchange timezone, exchange-calendar identity and `ohlc_fill_from_close` (Yahoo's pre-2000 rows carry close only); full histories promoted 2026-09-05: NIKKEI225 1985-01-02~, TAIEX 1997-08-01~, EURO_STOXX50 2007-04-02~, HANG_SENG 1986-12-31~, DAX 1987-12-30~ (one Yahoo call each); Cboe term-index full-history onboarding/promote path active from 2026-09-04 | `global_market.py`; [Cboe index history](sources/cboe/INDEX_HISTORY.md) | Yahoo: `data/landing/global_current_refresh/`; Cboe: `data/landing/cboe_index_history/<run_id>/<symbol>.csv`; `data/normalized/global_index_price_daily/` | mixed-provider `06:20 GLOBAL_INDEX_DAILY`; one retry-zero request per due symbol; Yahoo strict endpoints use the registered exchange calendar; Cboe files are full history and use source ticker equal to the Cboe symbol, nullable volume, hash-bound Landing, and isolated CAS promotion |
| `us_vix_term_structure_daily` | FRED `VIXCLS` + Cboe `VIX9D/VIX3M/VIX6M/SKEW` | builds after the four Cboe histories and FRED VIX are retained | `global_market.py` v1; `vix_term_structure.py`; [Cboe index history](sources/cboe/INDEX_HISTORY.md) | `data/derived/us_vix_term_structure_daily/year=YYYY/`; state-bound dual-source lineage | `GLOBAL_INDEX_DAILY` dependency refresh after the Cboe index phase; ratios `vix/vix3m`, `vix9d/vix`, contango/backwardation regime, and trailing full-window 252-observation percentile rank; descriptive/PIT-blocked; public/guest display permitted; `VX=F` unavailable (HTTP 404 on 2026-09-04) |
| FRED yields/FX, Treasury spread | FRED | DEXKOUS FX through 2026-08-28 as verified 2026-09-03; yields/spread retain their independent coverage | `global_market.py` | `data/landing/global_current_refresh/`; normalized/derived artifacts | DEXKOUS is the weekly Federal Reserve H.10 release and is no longer the sole current display/account valuation reference; `FRED AUTOMATION_ACTIVE / predictive PIT blocked` |
| `fred_treasury_yield_ext_daily` | FRED fredgraph CSV (`DGS3`, `DGS5`, `DTB3`) | registered, not yet collected; DGS3/DGS5 requested from 1962-01-02 and DTB3 from 1954-01-04 | `global_market.py` v1 sibling contract; `date,dgs3,dgs5,dtb3` | immutable `data/landing/global_current_refresh/`; candidate/Normalized whole-root CAS through `fred_yields_ext` | values are percent and holiday/non-observation `.` markers remain nullable; DTB3 is the 3-month Treasury-bill secondary-market discount rate used only as a cash-yield proxy; predictive use remains PIT-blocked. The sibling preserves all `fred_treasury_yield_daily` readers. Central health/operations registration is pending a coordinator change to the out-of-scope `contracts/registry.py`, `orchestration/daily_operations.py`, and `orchestration/dataset_universe.py`. |
| `bok_ecos_usd_krw_daily` | BOK ECOS `731Y001/D/0000001` | no retained rows before the first bounded human backfill | `bok_ecos_fx.py` v1; `date` key and native KRW-per-USD value | immutable `data/landing/bok_ecos_usd_krw_daily/`; `data/normalized/bok_ecos_usd_krw_daily/year=YYYY/` | `AUTOMATION_ENABLED / BOK_FX_DAILY / DISPLAY_AND_ACCOUNT_VALUATION_ONLY / PIT_BLOCKED`; 20:30 KR bundle, with project weekday target today after 16:00 KST else previous weekday; target absence is expected provider lag; publication time/finality remain [unverified](sources/bok_ecos/731Y001_USD_KRW_DAILY.md) |
| `global_etf_price_daily` | Yahoo chart | SOXX 2025-08-18..2026-08-18; EWY first collected/promoted 2026-09-02; SOXL/TQQQ/QLD/TLT/QQQ/SPY/SGOV/VGLT plus VNQ/IEF/SHY registered, not yet collected; VNQ onboarding starts 2004-09-29 and IEF/SHY 2002-07-30 | `global_etf.py` | `data/normalized/global_etf_price_daily/`; capture-first Landing/state retained | all thirteen symbols are in the 06:10 registry; VNQ is NYSE Arca CUSIP 922908553, IEF is NASDAQ CUSIP 464287440, and SHY is NASDAQ CUSIP 464287457; each has explicit leverage multiple 1. The explicit-period Yahoo route uses one call per symbol (no 5-year chunking); identity/currency/exchange/daily granularity fail closed; predictive use remains blocked. |
| `global_equity_price_daily` | Yahoo chart | SKHY retained 2026-07-13..2026-09-03 | `global_equity.py` v1; SKHY is `EQUITY` / `DEPOSITARY_RECEIPT`, NASDAQ, ISIN `US78392B2060`, underlying `000660`; ADR ratio is unknown and remains `None` | `data/landing/global_current_refresh/`; `data/normalized/global_equity_price_daily/symbol=SKHY/year=YYYY/` | `HEALTH_REGISTERED / GLOBAL_EQUITY_DAILY / GLOBAL_DAILY_FINAL`; one-call under-one-year onboarding via `yahoo_equity`; Yahoo identity requires USD and exchange `NMS/NGM/NASDAQ/NasdaqGM`; predictive use remains blocked |
| `tossinvest_us_quote_30m` | Toss Securities Open API `/api/v1/prices` | first retained observation 2026-09-04 22:21 KST | inline v1 contract in `tossinvest_us_quotes.py`; `(retrieved_at,symbol)` run key; positive finite USD quote | `data/landing/tossinvest/us_quotes_30m/`; `data/normalized/tossinvest_us_quote_30m/date=YYYY-MM-DD/`; `artifacts/intraday/tossinvest_us_quotes_latest.json` | `HEALTH_REGISTERED / TOSSINVEST_US_QUOTES_30M / GLOBAL_30M`; `PT30M` during `[17:00,06:00)` KST; one call, retry zero, valid-empty/prior-valid preservation, descriptive pre/regular/after/closed hint; never an official bar or close |
| `cboe_daily_pcr_daily` | Cboe Daily Market Statistics public daily CSV/JSON; Historical Options Data URL is a coordinator-verification placeholder | no retained rows before the first human-run collection | `us_option_pcr.py` v1; `(date,scope)`; scopes `TOTAL/INDEX/ETP/EQUITY/VIX` plus optional `SPX_SPXW`; non-negative contract counts; volume/OI P/C = put ÷ call and null for zero call | sha256-bound `data/landing/cboe/daily_pcr/date=YYYY-MM-DD/<run_id>/`; `data/normalized/cboe_daily_pcr_daily/year=YYYY/`; `artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json` | `MANUAL_READY / AUTOMATION_DISABLED / CBOE_DAILY_PCR`; at most one request per date; private personal non-commercial display only; exact label `Cboe 거래소 합계 · 지수 · ETP · 개별주 · VIX`; guest/public, redistribution, and predictive use forbidden; live lane requires one coordinator curl plus `--endpoint-verified` |
| `kbsec_transactions_daily` | KB Securities read-only `SWQA2301` transaction history | no retained rows before the first coordinator live run | `kbsec_transactions.py` v1; raw-row SHA-256 key; identifier-free date/direction/category/amount/tax/summary/type-code projection | identifier-free `data/landing/kbsec/transactions/date=YYYY-MM-DD/<run_id>/`; `data/state/kbsec_transactions_daily/state.json`; `artifacts/local_user/cash_flows.json`; `artifacts/scheduler_logs/STOCK_DATA_KB_TRANSACTIONS_DAILY_last.json` | `MANUAL_READY / AUTOMATION_DISABLED / KB_TRANSACTIONS_DAILY / 07:20 KST`; prior-day seven-day overlap plus any retained-row gap, initial 2025-01-01, six rows/page via `nxt_key`, at most 40 calls, Landing-first, `kb_auto` row-hash merge, manual ledger entries untouched, `OTHER` excluded from return cash flows |
| `kr_etf_master`, `kr_etf_price_daily` | KRX via pykrx current ETF endpoints | `123320`, `243880` retained for 2026-08-24..2026-09-02; five-call verified first run | `kr_etf.py` | `data/landing/pykrx/kr_etf_daily/`; `data/normalized/{kr_etf_master,kr_etf_price_daily}/`; exact state | `AUTOMATION_ACTIVE / KR_ETF_PRICE_DAILY / DAILY / DISPLAY_ONLY / PIT_BLOCKED`; selected-symbol union of local KRX/ETF watchlist and retained master, max 10 symbols, per-symbol 30-XKRX-session catch-up, target-missing valid-empty is `EXPECTED_PROVIDER_LAG`; see [runbook](operations/KR_ETF_DAILY.md) |
| `market_price_60m_observation` | exact Yahoo registry | 457 finalized bars across `KRW=X`, `ZT=F`, `ZN=F`, `ZB=F`; latest 2026-08-19 12:00 UTC | `market_60m.py` v2 retained-history parser | `data/normalized/market_price_60m_observation/`; immutable Landing and state retained | `STATIC_COMPLETE / NO_REFRESH / AUTOMATION_DISABLED / PIT_BLOCKED`; the separate [current-display operation](operations/GLOBAL_MARKET_CURRENT_60M.md) writes no Normalized history or Backtest data and does not reactivate this retained dataset; Treasury rows are futures prices, not yields; equity 1Y backfill remains blocked |
| `market_price_15m_observation` | exact Yahoo seven-symbol registry | retained Normalized segment 2026-08-19..2026-08-21; four accepted native-grid series | `market_15m.py` v1 active | `data/normalized/market_price_15m_observation/`; immutable Landing remains under `data/landing/global_market_15m/` | `STATIC_COMPLETE / INDICATIVE_DELAYED / PIT_BLOCKED`; this old retained segment is not the active polling dataset. The unified `STOCK_DATA_YAHOO_MARKET_30M` current-only task polls every 30 minutes, preserving provider-native 15-minute `^VIX/^FVX/^TNX/^TYX` bars without resampling; Treasury quote indices are not official yields. |
| `global_commodity_futures_daily` | Yahoo chart API | NQ=F/GC=F/CL=F retained 2025-08-18..2026-08-18; SP500_FUTURES (`ES=F`) and DOW_FUTURES (`YM=F`) first collected/promoted 2026-09-02; GOLD full-history window registered for 2000-08-30..2026-09-03 and not yet promoted | `global_market.py` active; legacy name retained | `data/normalized/global_commodity_futures_daily/` plus immutable Landing | `yahoo_dashboard_futures --symbols GOLD` uses one explicit-period daily call; `range=max` is prohibited because Yahoo coerces it to monthly. Daily lane remains unchanged; descriptive/PIT-blocked, individual expiry and official settlement use prohibited, and volume is not a trusted activity signal. |
| CFTC COT Legacy, TFF, Disaggregated Raw | CFTC Historical Compressed archives | Legacy 1986/1995..; TFF/Disaggregated 2006.. | no accepted normalized contract | `data/landing/cftc*/` | `RAW_BACKFILL_COMPLETE / PIT_BLOCKED` |
| `kr_equity_foreign_ownership_daily` Raw | KRX/pykrx `MDCSTAT03701` | 2000-01-05..2026-08-12; 6,558 dates / 13,910,258 rows | contract-only Raw metadata; [publication/finality policy](sources/krx/MDCSTAT03701_FOREIGN_OWNERSHIP_FINALITY.md) | `data/landing/pykrx/high_value_raw/kr_equity_foreign_ownership_daily/` | `RAW_BACKFILL_COMPLETE / OFFLINE_INCREMENTAL_READY / PUBLICATION_FINALITY_UNDOCUMENTED`; [non-executable candidate plan](queues/KRX_FOREIGN_OWNERSHIP_RAW_DAILY_REVIEW_REQUIRED.md) |
| `kr_equity_fundamental_daily` Raw | KRX/pykrx `MDCSTAT03501` | 2008-01-03..2026-08-12; 4,589 dates / 9,925,137 rows | contract-only Raw metadata; [publication/revision/duplicate policy](sources/krx/MDCSTAT03501_EQUITY_FUNDAMENTAL_FINALITY.md) | `data/landing/pykrx/high_value_raw/kr_equity_fundamental_daily/` | `RAW_BACKFILL_COMPLETE / OFFLINE_INCREMENTAL_READY / PUBLICATION_REVISION_FINALITY_UNDOCUMENTED`; provider duplicates retain response-local ordinals; [non-executable candidate plan](queues/KRX_EQUITY_FUNDAMENTAL_RAW_DAILY_REVIEW_REQUIRED.md) |
| `kr_etf_universe_daily`, `kr_etf_ohlcv_daily` Raw | KRX/pykrx `MDCSTAT04301` | 2008-01-02..2026-08-12; 4,590 dates / 1,700,421 shared rows | none | `data/landing/pykrx/high_value_raw/kr_etf_universe_daily/`; OHLCV state references the same bytes | `RAW_BACKFILL_COMPLETE / PREDICTIVE_USE_BLOCKED` |
| `kr_index_fundamental_daily` | KRX `MDCSTAT00702`, tickers `1001`/`2001` | 2000-01-04..2026-08-26 | `kr_index_fundamental_daily.py` v1; `(date,index_code)`, response-SHA-bound | immutable Landing; `data/normalized/kr_index_fundamental_daily/`; exact state | bounded two-call promotion and API-zero replay passed; `09:10_AUTOMATION_ACTIVE / NON_PREDICTIVE`; [active runbook](operations/KRX_INDEX_FUNDAMENTAL_DAILY.md) |
| `kr_credit_benchmark_yield_daily` Raw | KRX/pykrx | 2002..2026-08-12 | none | retained diagnostic Landing roots | `RAW_BACKFILL_COMPLETE / PREDICTIVE_USE_BLOCKED` |
| `kr_index_constituent_daily`, `kr_kospi200_constituent_price_daily`, `kr_kospi200_breadth_daily` | KRX `MDCSTAT00601` ticker `1028` retained observation + exact local equity prices | retained exact 2026-08-26 observation; 201 members/prices; breadth 144/54/3 | `kospi200_constituent_breadth.py` | `data/normalized/kr_index_constituent_daily/`, `data/published/kr_kospi200_constituent_price_daily/`, `data/derived/kr_kospi200_breadth_daily/` | source-width-aware atomic promotion/read-back and API-zero replay passed; exact-date membership and accepted same-date canonical prices only, with no interval inference or backprojection |
| `ls_t8412_kospi200_constituent_15m_pilot` unregistered Raw pilot | LS OpenAPI `/stock/chart` t8412 | exact 2026-08-12 only; `000660`/`005930`; 52 native-15m rows | `kospi200_intraday_pilot.py` v1, `review_required_not_registered` | immutable `data/landing/ls_openapi/t8412_kospi200_constituent_15m/`, `data/raw/ls_t8412_kospi200_constituent_15m_pilot/`, exact checkpoint | `LIVE_VALIDATED_EXACT_RAW_20260812 / PIT_BLOCKED / MANUAL_ONLY / UNREGISTERED`; one OAuth plus two retry-zero calls, atomic readback and API-0 replay passed; no full fan-out, promotion, scheduler, or predictive use |
| Sector classification | KRX/pykrx | bounded pilots only | none | diagnostic Landing roots | `PIT_BLOCKED`; no daily backfill |
| LS `t8428`, `t1633`, `t8462` Raw candidates | LS OpenAPI | t8428 2006..; t1633 2001/2003..; t8462 observed 2025-07-18..2026-08-14 | `ls_t8428.py`; operational-with-empirical-finality `ls_t1633.py`; t8462 [analysis feature policy](config/LS_T8462_ANALYSIS_FEATURES.md) only | provider-specific Landing roots; t1633 2026-08-19 has one accepted KOSPI amount response plus redacted second-scope HTTP 500 provenance, with no Normalized root | t1633 uses a reviewed T+1 descriptive policy and four-scope joint transaction; the first bounded run stopped retry-free before promotion/replay and remains scheduler-disabled; t8462 historical Raw remains non-predictive |
| KB IVSA0070 snapshots | KB Securities | Normalized capture dates: breadth 2026-08-17..18; other six slices retained at 2026-08-17 | `kbsec_snapshot.py`, `current_snapshot.py` | `data/landing/kbsec/daily_snapshot/` | `CURRENT_SNAPSHOT / DATE_SEMANTICS_REVIEW_REQUIRED`; capture date is not accepted market date |

## Index rules

The two U.S. datasets above are registered in the authoritative Contract,
operations, and typed Health universe registries. `global_equity_price_daily`
uses the `GLOBAL_EQUITY_DAILY` / `GLOBAL_DAILY_FINAL` expectation. The
`tossinvest_us_quote_30m` row uses each `TOSSINVEST_US_QUOTES_30M` / `GLOBAL_30M`
boundary in `[17:00,06:00)` KST and preserves the last in-window expectation
outside that window without classifying the closed interval as delayed.

- `RAW_BACKFILL_COMPLETE` does not imply a DatasetContract, PIT safety, or
  Normalized/Canonical promotion.
- A source-specific current snapshot never fills historical daily data.
- Provider values are not averaged, spliced, rescaled, or silently substituted.
- Detailed closed investigations live in archive evidence; unresolved work lives
  only in `research/active/` and is routed by Data Status.
