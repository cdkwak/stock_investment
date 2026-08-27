# Dashboard MVP Daily Source Routing

> Decision date: 2026-08-17 KST
> Scope: source-routing decision only. No API/OAuth call, refresh, collector,
> scheduler, canonical promotion, or GUI implementation was performed. Current Data
> authority remains [Data Status](../data/DATA_STATUS.md); this document does not
> relax any semantic, publication, revision, PIT, or provider-boundary blocker.
> Current GUI phase and implementation gating are owned by
> [GUI Status](GUI_STATUS.md).
> Missing operations are implementation work under standing Project/Data/GUI
> authority, not a requirement for another user/Lead phase approval.

## 1. Decision summary

The MVP has 19 variable rows. The daily display route is:

- **7 source-ready daily-final rows:** Toss KOSPI, KOSDAQ, their market volume,
  and the three market investor net-purchase rows.
- **1 current-snapshot-only row:** KB KOSPI/KOSDAQ breadth, displayed only as
  provisional with provider, capture time, and independently resolved market date.
- **11 existing-history refresh rows:** KOSPI/KOSDAQ trading value, three KOSPI200
  risk/derivatives rows, three U.S. indices, and three U.S. rate/spread rows.

`Source-ready` means no further source-discovery or semantic pilot is required to
choose the provider. It does **not** claim that an incremental collector or scheduler
already exists. In particular, the retained Toss historical runner is a completed
backfill workflow, not an implemented daily incremental operation. Agents may
create a bounded Landing-first daily path under standing authority and update
this routing when current evidence supports it.

The dashboard may show a provider-specific latest tile beside a different provider's
historical chart, but it must not splice the two into one unlabeled series. No KB, LS,
Toss, KRX, Yahoo, or FRED values are averaged, substituted, or gap-filled across
providers.

`CURRENT_SNAPSHOT` means a provider view captured for the current display and
does not become daily history. `LATEST_FINAL_DAILY` means the newest accepted
final observation already present in a retained daily artifact. The Dashboard
may use the latter for current trend context. USD/KRW (`FRED DEXKOUS`) and U.S.
10Y (`FRED DGS10`) route as `LATEST_FINAL_DAILY / PIT_LIMITED`; retained Yahoo
global indices route as `LATEST_FINAL_DAILY / USABLE_WITH_LIMITS`. Gold and WTI
may appear only as `PROVIDER_RAW_VIEW / NORMALIZED_REVIEW_REQUIRED`, and VIX
remains `N/A`. Every value must expose its own source, market date, freshness,
and status; differing dates must never be presented as one simultaneous state.

## 2. Shared daily call budgets

Call counts below are planned source interactions, not calls made by this audit.
They are stated once here to prevent double-counting shared responses.

| Route | Expected daily source calls | Variables sharing the route | Operational boundary |
|---|---:|---|---|
| Toss market-investor daily-final run | **1 OAuth + 2 business calls** | KOSPI investor trading, KOSDAQ investor trading | Implemented as one latest `1d` page per market with an exact intended-date gate, joint normalized/bridge promotion, rollback, and API-0 completed-date replay. KOSPI/KOSDAQ candle collection remains a separate future route. |
| KB `IVSA0070` snapshot | **1 OAuth + 1 business call per normal cycle** | Breadth primary-current view and optional cross-checks for Korean index/volume/trading value | Prefer the post-close window; use provider-aware bounded retry/backoff and durable idempotency. Seven slices retain independent dates. Normalized publication still requires its semantic/finality contract. |
| pykrx/KRX index refresh | **2 provider-method calls per normal cycle** | KOSPI/KOSDAQ OHLC, volume, trading value, market cap | One `get_index_ohlcv` call for each of KOSPI and KOSDAQ. Underlying HTTP count is library-internal and not contracted. Standing Data API policy applies. |
| Equity availability + breadth | **2 data.go.kr sentinel calls + 0 rebuild calls** | KOSPI/KOSDAQ breadth history | Shared stock-price and universe exact-date calls; only after both are adopted/promoted may the breadth rebuild run offline. |
| KOSPI200 futures/options refresh | **At least 2 business pages; exact total pagination-dependent** | Volume PCR and futures basis | One futures operation and one-or-more option pages for the date. Agents may create or update the Data-owned contract/runbook under standing authorization; unresolved identity/session/finality still blocks promotion claims, not research calls or Landing capture. |
| KRX futures-investor manual file | **0 API calls; 1 reviewed manual CSV acquisition** | Foreign KOSPI200 futures net purchase | Settings-bound official KRX CSV, then zero-network promoter. LS `t8462` Raw is not substituted. |
| Yahoo global current phase | **3 business calls** | S&P 500, Nasdaq Composite, Nasdaq-100 | Fixed active runbook: one sequential call per symbol, then offline reviewed promotion. |
| FRED yields phase | **3 business calls** | UST 2Y, UST 10Y, UST 10Y-2Y | Fixed active runbook calls DGS2, DGS10, and DGS30 together; spread rebuild is zero-network and promotes atomically with yields. |

If all currently implemented selected routes were run on one day, the fixed known budget is 1 Toss OAuth
+ 2 Toss investor business calls, optionally 1 KB OAuth + 1 KB business call, 2 pykrx provider
method calls, 2 data.go.kr breadth-sentinel calls, 3 Yahoo calls, 3 FRED calls, and
one manual KRX file acquisition. KOSPI200 derivatives pages remain variable and are
not included in a false exact total.

## 3. Final source selection

### 3.1 Dashboard source and provider roles

| economic_variable | dashboard_display_source | daily_operational_primary | secondary_crosscheck | historical_source | current_snapshot_or_daily_final |
|---|---|---|---|---|---|
| KOSPI | Latest tile: Toss `getMarketIndicatorCandles(KOSPI)`; history chart: `kr_index_daily`; never splice without provider boundary | Toss KOSPI latest finalized `1d` candle | KB `IVSA0070.out2` `KGG01P`, provisional only | `kr_index_daily`, pykrx/KRX, 1975-01-04+ | Toss `DAILY_FINAL`; KB is optional `CURRENT_SNAPSHOT` |
| KOSDAQ | Latest tile: Toss `getMarketIndicatorCandles(KOSDAQ)`; history chart: `kr_index_daily` | Toss KOSDAQ latest finalized `1d` candle | KB `IVSA0070.out2` `QGG01P`, provisional only | `kr_index_daily`, pykrx/KRX, 1996-07-01+ | Toss `DAILY_FINAL`; KB optional snapshot |
| KOSPI200 | `kr_kospi200_index_daily.close`; no snapshot splice | Retained KRX/pykrx ticker `1028` EOD close | KB `IVSA0070.out2` current cross-check only | `kr_kospi200_index_daily`, 1990-01-03+ | `PIT_SAFE_EOD_T_PLUS_1`; do not substitute futures `spot_value` |
| KOSPI volume | Latest tile: volume from the same Toss KOSPI candle; history chart: `kr_index_daily.volume` | Toss KOSPI candle, shared with KOSPI level | KB `IVSA0070.out2.vlm`, provisional/current | `kr_index_daily`, pykrx/KRX | Toss `DAILY_FINAL`; do not average with KB |
| KOSPI trading value | `kr_index_daily.trading_value`; show the latest retained KRX date until refreshed | pykrx/KRX index refresh | KB `IVSA0070.out2.dl_tw_amt` only as raw provisional diagnostic, not the default KPI | `kr_index_daily`, pykrx/KRX | Existing `DAILY_FINAL`; KB field is snapshot with unresolved unit/date |
| KOSDAQ volume | Latest tile: volume from the same Toss KOSDAQ candle; history chart: `kr_index_daily.volume` | Toss KOSDAQ candle, shared with KOSDAQ level | KB `IVSA0070.out2.vlm`, provisional/current | `kr_index_daily`, pykrx/KRX | Toss `DAILY_FINAL`; do not average with KB |
| KOSDAQ trading value | `kr_index_daily.trading_value`; show retained KRX as-of date | pykrx/KRX index refresh | KB `IVSA0070.out2.dl_tw_amt` raw diagnostic only | `kr_index_daily`, pykrx/KRX | Existing `DAILY_FINAL`; KB snapshot is not canonical |
| Advances / declines / unchanged | Current tile: KB `kb_market_breadth_snapshot`; history chart: `kr_market_breadth_daily`; keep them visibly separate | KB `IVSA0070` breadth slice for current GUI only | `kr_market_breadth_daily` after canonical equity refresh is the final historical cross-check | `kr_market_breadth_daily`, derived from canonical price + PIT-safe universe | KB `CURRENT_SNAPSHOT`, always provisional until slice date is resolved; history is derived daily-final |
| Foreign net purchase | Latest tile: Toss market investor row; history chart: `kr_market_investor_net_purchase_bridge_daily` with provider columns | Toss KOSPI/KOSDAQ `getMarketIndicatorInvestorTrading` | KB `IVSA0070.out5` current cross-check, provisional | Published legacy+Toss bridge, 1999-01-04+ with provider segments preserved | Toss `DAILY_FINAL` |
| Institution net purchase | Same published bridge/latest Toss rule | Same two Toss investor responses; no extra call | KB `IVSA0070.out5`, provisional | Same published bridge | Toss `DAILY_FINAL` |
| Individual net purchase | Same published bridge/latest Toss rule | Same two Toss investor responses; no extra call | KB `IVSA0070.out5`, provisional | Same published bridge | Toss `DAILY_FINAL` |
| KOSPI200 volume PCR | `kr_kospi200_option_pcr_daily.volume_pcr`, latest retained final row | Existing KRX legacy + data.go.kr option source chain, then offline PCR rebuild | None among KB/LS/Toss; selected option quotes cannot form total PCR | Provider bridge inputs, 2010-01-04+ | Existing derived `DAILY_FINAL`; no accepted current snapshot substitute |
| KOSPI200 Raw Call/Put Wall | Read-only Option Wall feature from the published option bridge, explicitly same-date joined to `kr_kospi200_index_daily` | Existing accepted option bridge plus retained KRX/pykrx ticker `1028` spot artifact; offline computation only | None; do not synthesize from selected quotes or a KB current level | Option bridge and KOSPI200 spot inputs, common-date coverage 2010-01-04+ | Raw Wall `EOD_T_PLUS_1`; Active Wall is not routed until an evidence-backed threshold policy is contracted and tested |
| KOSPI200 futures basis | `kr_kospi200_futures_nearest_listed_daily.settlement_basis` with regular-session status filter | Existing futures provider bridge, then offline nearest-listed/basis rebuild | LS `t2111` remains an unobserved current cross-check candidate and is not adopted | KRX legacy + data.go.kr bridge, 2010-01-04+ | Existing derived `DAILY_FINAL`; LS current candidate is not used |
| Foreign KOSPI200 futures net purchase | `kr_kospi200_futures_investor_net_purchase_daily`, `session=ALL`, exact foreign source label | Reviewed official KRX screen 15007 manual CSV | LS `t8462` `U` is Raw-only and cannot replace or fill the official row; KB fields are unavailable | Same manual official KRX dataset, 1999-04-26+ | Official manual-file `DAILY_FINAL`; not a KB/LS snapshot |
| S&P 500 | `global_index_price_daily`, `symbol=SP500` | Yahoo capture-first global refresh | KB `IVSA0070.out4` S&P 500 cash/futures rows only as separately timestamped provisional cross-checks | Yahoo daily, 1928-01-03+ | Yahoo `DAILY_FINAL`; KB optional current snapshot |
| NASDAQ Composite | `global_index_price_daily`, `symbol=NASDAQ_COMPOSITE` | Yahoo capture-first global refresh | KB `IVSA0070.out4` Nasdaq Composite, provisional cross-check | Yahoo daily, 1971-02-05+ | Yahoo `DAILY_FINAL`; KB optional snapshot |
| NASDAQ-100 | `global_index_price_daily`, `symbol=NASDAQ100` | Yahoo capture-first global refresh | KB has Nasdaq futures, not a verified Nasdaq-100 cash-index equivalent; no numeric substitute | Yahoo daily, 1985-10-01+ | Yahoo `DAILY_FINAL` |
| UST 2Y | `fred_treasury_yield_daily.dgs2` | FRED yields phase | None among KB/LS/Toss | FRED DGS2, 1962-01-02+ | FRED daily observation/finalized current artifact under vintage limits |
| UST 10Y | `fred_treasury_yield_daily.dgs10` | FRED yields phase | None among KB/LS/Toss | FRED DGS10, 1962-01-02+ | FRED daily observation/finalized current artifact under vintage limits |
| UST 10Y-2Y | `us_treasury_spread_daily.spread_10y_2y` | Zero-network rebuild from the same promoted FRED DGS10 and DGS2 rows | None; never combine a KB rate with FRED | Derived FRED spread, 1962-01-02+ | Derived `DAILY_FINAL` only when both same-date input yields are present |

### 3.2 Timing, semantics, promotion, and update method

| economic_variable | latest expected timing | unit | date semantics | GUI provisional label required | canonical daily promotion possible | refresh/update method | expected API calls per day |
|---|---|---|---|---|---|---|---|
| KOSPI | After Korean close when Toss returns the intended latest finalized daily candle; exact publication cutoff is not documented | Index points | Toss candle source date; retrieval time is not original availability time | **No** for finalized Toss tile; always show `source=Toss` and `as_of`. KB cross-check: **Yes** | **No** into `kr_index_daily`; a separate Toss provider-daily contract/bridge would be required | Future bounded one-page Toss daily capture; history remains pykrx refresh | Shared Toss candle call: 1 business for KOSPI; OAuth shared across run |
| KOSDAQ | Same as KOSPI | Index points | Toss daily source date | No for finalized Toss; KB cross-check yes | No direct promotion into KRX canonical dataset | Same bounded Toss daily capture | 1 business for KOSDAQ; OAuth shared |
| KOSPI volume | Same response/timing as KOSPI candle | Provider-native integer; do not assert a stronger multiplier | Same Toss source date as the candle | No for Toss final; KB cross-check yes | No direct promotion into `kr_index_daily` | Same KOSPI candle response; no second request | **0 marginal** beyond KOSPI candle |
| KOSPI trading value | After successful pykrx/KRX EOD refresh; exact provider publication time not contracted | `kr_index_daily` source integer; contract does not assign a stronger unit label | KRX trading date | No for accepted `kr_index_daily`; KB diagnostic yes | **Yes**, through existing `kr_index_daily` atomic incremental collector | Manual-live incremental `collect_kr_index_daily`; preserve overlap/replacement validation | Shared pykrx call: 1 KOSPI provider-method call |
| KOSDAQ volume | Same response/timing as KOSDAQ candle | Provider-native integer | Toss candle source date | No for Toss final; KB cross-check yes | No direct promotion into `kr_index_daily` | Same KOSDAQ candle response | 0 marginal beyond KOSDAQ candle |
| KOSDAQ trading value | After successful pykrx/KRX EOD refresh | `kr_index_daily` source integer | KRX trading date | No for accepted daily; KB diagnostic yes | Yes, existing contract/collector | Same index incremental collector | Shared pykrx call: 1 KOSDAQ provider-method call |
| Advances / declines / unchanged | KB attempt 16:30-18:00 KST; historical final only after price+universe availability and offline breadth rebuild | Security counts | KB slice-specific market date may be null/`DATE_UNRESOLVED`; derived history uses canonical trading date | **Yes** for every KB current tile: `PROVISIONAL · KB · captured_at · market_date/status` | KB: **No**. Derived `kr_market_breadth_daily`: **Yes** after canonical inputs | Current: existing KB daily snapshot operation. History: 2-call availability sentinel, adoption/promotion, then zero-network breadth rebuild | KB shared 1 OAuth +1 business; history shared 2 data.go.kr calls +0 rebuild |
| Foreign net purchase | Post-close/evening after Toss returns the intended final daily market row; exact cutoff remains provider evidence, not an assumed clock time | KRW for Toss rows | Toss source date plus availability metadata; legacy bridge segment keeps its own unknown unit/PIT labels | No for accepted Toss daily final; show source/as-of. KB cross-check yes | **Yes** to existing Toss normalized dataset and published bridge after the implemented safe incremental path; never automatic from KB snapshot | `refresh_toss_market_investor_daily.py`: two-market one-page capture, exact-date validation, joint promotion and rollback | Shared investor calls: 2 business total for both markets; 0 marginal by investor class; OAuth shared |
| Institution net purchase | Same Toss market row/timing | KRW for Toss rows | Same | Same | Same | Same | 0 marginal beyond the two market investor calls |
| Individual net purchase | Same Toss market row/timing | KRW for Toss rows | Same | Same | Same | Same | 0 marginal beyond the two market investor calls |
| KOSPI200 volume PCR | After the official option daily input for a date has been captured, normalized, and the PCR rebuild passes; current provider lag must be observed, not guessed | Ratio; `put_volume / call_volume`; valid-empty yields null, not zero | Exchange trading date; total source scope; same-provider same-session aggregation only | No for accepted row; show `observation_status` and as-of date | **Yes**, by rebuilding the registered Derived dataset from accepted options inputs; no direct source append | Existing data.go.kr options collector followed by offline modern PCR rebuild; no active daily runbook currently routes it | Shared derivatives refresh: one-or-more option pages; exact daily total is pagination-dependent |
| KOSPI200 Raw Call/Put Wall | Only after both accepted option rows and the KOSPI200 final close for that same date are retained; usable EOD T+1 | Strike/index points, OI/volume contracts, and distance ratios; preserve source values | Explicit same KRX trading date and front maturity; no implicit/as-of join | No intraday fallback; show Raw status, tie evidence, warning, and as-of date | **Yes**, read-only recomputation from retained inputs; do not persist or promote an Active Wall implicitly | Existing option bridge and `kr_kospi200_index_daily`, then explicit join helper | 0 API for retained recomputation; refresh cost belongs to the contract-bound Data-owned source collectors |
| KOSPI200 futures basis | After accepted futures daily input and offline rebuild; use regular rows only | `source_native_price_difference`; do not relabel as verified index points | Trading date and provider/session segment; only `SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE` is displayable | No for accepted row; expose basis/price-unit status | **Yes**, by rebuilding the registered Derived dataset; never append an LS snapshot | Existing data.go.kr futures collector, provider bridge rebuild, then nearest-listed/basis rebuild; no active daily runbook | Shared derivatives refresh: normally at least one futures page; exact total pagination-dependent |
| Foreign KOSPI200 futures net purchase | After the official KRX ALL-session CSV is available and manually reviewed; exact cutoff is not encoded in the contract | Million KRW, exact reviewed source-selected unit | Official CSV date, `product=KOSPI200_FUTURES`, `session=ALL`; manual capture time is separate | No for promoted official row; show manual-source/as-of and `USABLE_WITH_LIMITS` | **Yes**, through the existing manual-file promoter only | Acquire reviewed KRX screen 15007 CSV, retain inventory/hash, run zero-network promoter/audit | 0 API; 1 manual file acquisition |
| S&P 500 | After U.S. session daily bar becomes available; in KST normally a later calendar morning, but rely on returned source date | Provider-native index points | Yahoo source-exchange date; KB row timestamps remain independent | No for Yahoo final; KB cross-check yes | **Yes**, via capture-first staging review and offline promotion | Active `GLOBAL_CURRENT_REFRESH` Yahoo phase, then separate reviewed promotion | 1 of 3 fixed Yahoo calls |
| NASDAQ Composite | Same U.S.-session rule | Provider-native index points | Yahoo source-exchange date | No for Yahoo; KB cross-check yes | Yes | Same Yahoo phase | 1 of 3 fixed Yahoo calls |
| NASDAQ-100 | Same U.S.-session rule | Provider-native index points | Yahoo source-exchange date | No | Yes | Same Yahoo phase | 1 of 3 fixed Yahoo calls |
| UST 2Y | After FRED publishes the observation; no synthetic fill on U.S. holidays and no original-vintage claim | Annual percent | FRED observation date; retrieval/promotion time separate; historical vintage/revision limitation retained | No for descriptive display; expose FRED/as-of and PIT limitation | **Yes**, through reviewed FRED candidate promotion | Active `GLOBAL_CURRENT_REFRESH` `fred_yields` phase, then offline promotion | Shared fixed phase: 3 FRED calls total; DGS2 is one call |
| UST 10Y | Same | Annual percent | Same FRED observation-date/vintage rule | No for descriptive display; retain PIT limitation | Yes | Same FRED phase | DGS10 is one of the same 3 calls |
| UST 10Y-2Y | Available only after same-date DGS10 and DGS2 are both present and promoted | Percentage points (`DGS10 - DGS2`) | Same FRED observation date for both inputs; null if either input is null | No for accepted derived row; show source/as-of | **Yes**, zero-network and atomic with yield promotion | Existing spread rebuild inside the FRED-yields promotion transaction | 0 marginal API calls |

## 4. Daily operational rules

1. The latest tile and the history chart may have different providers and different
   latest dates. Both source and `as_of` must be visible; the tile is not appended to
   the chart merely because it is newer.
2. Toss KOSPI/KOSDAQ candle volume comes from the same response as the index level.
   Do not make duplicate calls per field.
3. One Toss investor response supplies foreign, institution, and individual values.
   Compute each net purchase only as buy minus sell within that same provider row,
   or use the already published provider-preserving bridge. Do not combine investor
   components from KB and Toss.
4. KB breadth is a `*_snapshot` current observation. `capture_date` is not
   `market_date`; a null or unresolved date remains visible and prevents canonical
   promotion.
5. KB trading value is not selected as the display primary because its retained
   unit/multiplier and slice finality are unresolved. It may appear only in a raw
   diagnostic/cross-check view.
6. LS `t2111` does not replace the retained futures basis. LS `t8462` does not
   replace official foreign futures net purchase. Their current blockers remain.
7. PCR and basis are rebuilt only from their declared provider-bridge inputs. The GUI
   never calculates them ad hoc from selected current quotes.
8. Yahoo and FRED candidates are displayed only after their capture-first review and
   offline promotion. A Landing response is not a dashboard dataset.

## 5. MVP intersection with the six `UNVERIFIED` audit rows

The five `UNVERIFIED` rows in
[Dashboard Provider Coverage](DASHBOARD_PROVIDER_COVERAGE.md) are total market
capitalization, securities lending, USD/KRW, USD/JPY, and ETF current
data. **None is in the MVP 1st-wave list.**

Result: **0 bounded pilots are required for an MVP variable from the five
`UNVERIFIED` rows.** Their existing pilot candidates were outside this historical
MVP wave, but may now be researched or implemented under standing authority. This does not
upgrade KB trading value or LS current basis: those are not members of the six-row
intersection, and their documented semantic/current-evidence restrictions are
handled by the routing above.

## 6. Final A / B / C classification

These groups classify the selected **daily display/update route**, not the existence
of historical data.

### A. Daily automatic/manual source route ready

No additional source-discovery pilot is required. Agents may add the bounded
daily operation now under standing authority; this historical audit did not
itself create it.

- KOSPI — Toss daily-final candle
- KOSDAQ — Toss daily-final candle
- KOSPI volume — shared Toss KOSPI candle
- KOSDAQ volume — shared Toss KOSDAQ candle
- Foreign net purchase — Toss daily-final market investor row
- Institution net purchase — same Toss response
- Individual net purchase — same Toss response

Planned combined budget: **1 Toss OAuth + 4 Toss business calls per trading day**.
Canonical promotion remains contract/finality-evidence gated as stated above.

### B. GUI current snapshot only

- Advances / declines / unchanged — KB `IVSA0070` breadth slice, always
  `PROVISIONAL` until its market date/finality is resolved. Historical breadth stays
  in the separate derived dataset.

Planned combined budget: **1 KB OAuth + 1 IVSA0070 call per trading day**, shared
with any optional KB cross-check tiles. No snapshot is appended to history.

### C. Existing historical-source refresh required for now

- KOSPI trading value — pykrx/KRX `kr_index_daily`
- KOSDAQ trading value — pykrx/KRX `kr_index_daily`
- KOSPI200 volume PCR — KRX legacy/data.go.kr options + Derived rebuild
- KOSPI200 Raw Call/Put Wall — retained option bridge + explicit same-date KOSPI200 EOD T+1 join; no Active Wall threshold
- KOSPI200 futures basis — KRX legacy/data.go.kr futures bridge + Derived rebuild
- Foreign KOSPI200 futures net purchase — reviewed manual official KRX CSV
- S&P 500 — Yahoo capture-first refresh
- NASDAQ Composite — Yahoo capture-first refresh
- NASDAQ-100 — Yahoo capture-first refresh
- UST 2Y — FRED yields refresh
- UST 10Y — FRED yields refresh
- UST 10Y-2Y — zero-network rebuild from promoted FRED yields

KB/LS/Toss current candidates do not replace these rows because the retained
daily-final, unit, complete-market, session, or Normalized-promotion evidence is not
strong enough. Continue showing each retained dataset's own latest date until its
contract-valid refresh path succeeds.

## 7. Adoption decision

This routing records the Dashboard MVP source-selection decision; it is a
versioned default, not a ceiling on later evidence-backed onboarding:

- Toss owns the seven selected provider daily-final rows.
- KB owns only the provisional current breadth tile and optional labelled
  cross-checks.
- LS owns no default MVP display row at this stage.
- Existing KRX/pykrx, data.go.kr, manual KRX, Yahoo, and FRED datasets remain the
  display primaries for the eleven Group C rows.

A future collector may change ownership only through a new versioned contract
and evidence-backed source-routing update under standing authority. It must
never append a snapshot to canonical daily history or merge providers without
explicit compatible semantics and an atomic promotion rule.
