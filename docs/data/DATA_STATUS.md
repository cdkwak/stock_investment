# Data Status

Read this after `AGENTS.md` and
[`PROJECT_STATUS.md`](../project/PROJECT_STATUS.md) for Data work. This file is
the current routing view; detailed receipts and past operations belong in
`docs/archive/`, not here.

## Resume route

1. Select one dataset in [Dataset Index](DATASET_INDEX.md).
2. Confirm provider ownership in [Source Registry](SOURCE_REGISTRY.md) and, only
   when substitution is in question, [Source Fallback Policy](SOURCE_FALLBACK_POLICY.md).
3. Read only the contract, research item, checkpoint, or operation selected
   below.

Do not scan all of `operations/`, `research/`, `sources/`, or `docs/archive/`.
For sovereign yields and bond ETFs, also preserve
[Sovereign Yield and Bond ETF Semantics](SOVEREIGN_YIELD_BOND_ETF_SEMANTICS.md).

## Current state

| Field | Current fact |
|---|---|
| Status date | 2026-08-30 KST |
| Domain state | `AUTONOMOUS_PUBLIC_AND_EXISTING_CREDENTIAL_READONLY_OPERATIONS_ACTIVE` |
| Data flow | `Provider -> Landing -> Normalized -> Derived -> Published`; never merge layers |
| Registry | 40 operation entries, 80 universe rows, 39 automation-enabled datasets |
| Runtime health | Retained local health rows mark `kr_index_constituent_daily`, `kr_kospi200_constituent_price_daily`, and `kr_kospi200_breadth_daily` `STALE` at latest `2026-08-27` versus expected `2026-08-28`. Their causal chain is source constituents -> published constituent prices -> derived breadth. The GUI Health summary emits only the typed numeric-free `KOSPI200_BREADTH_DEPENDENCY_FRESHNESS_UNRESOLVED` decision hold while any member is `STALE` or `UNKNOWN`; no numeric conclusion from that chain is supported until the retained rows become acceptable. |
| Scheduler baseline | Thirteen enabled Data definitions remain; no extra Windows task was created. All 39 automation-enabled datasets map to 19 logical lanes. KR bundle contract v5 keeps 09:10/14:10/20:30 and adds 20:30 short-balance, short-investor, LS t8462 Raw, and Toss Treasury lanes while formalizing the existing liquidity/credit child. The BOK source observation stays on its correct 17:10 task and now exits API-zero because its three-batch gate is reached. The separate read-only KB account task remains outside the 80-row market universe. Stable relations are in [Scheduler Data Map](SCHEDULER_DATA_MAP.md). |
| BOK finality gate | `THREE_BATCH_GATE_REVIEWED / BOUNDED_THREE_BATCH_AVAILABILITY_CONFIRMED / PERMANENT_FINALITY_UNKNOWN`. The 2026-08-26..28 batches each selected the same-day provider-native date across all six tenors with six data calls, one separate UI call, retry zero, and no Normalized write. Both next-provider-day comparisons were field/byte `SAME`; all UI table markers were `N/N`. The 2026-08-29 scheduler receipt stopped at 3 to 3 with API calls zero. A separate daily-route contract may use only the runbook's provider-native common-date and preceding-date-unchanged gate; automatic expected latest, predictive use, promotion, and Dashboard numeric use remain disabled. |
| Scheduler expansion validation | The first natural v5 20:30 run exposed two bounded contract mismatches without partial canonical loss: exact KRX KOSPI200 membership returned 201 observed rows, and short-investor data was available same-day after 18:10. Contract/test fixes then atomically advanced the breadth chain through 2026-08-26 (201/201 prices; 144/54/3), short investor through 2026-08-27, and index fundamentals through 2026-08-26; each replay passed at API 0. Toss Treasury remains advanced through its T+1 target and LS t8462 Raw remains non-promoted. |
| Global current route | One unified 30-minute scheduler task covers 17 accepted routes. Thirteen use completed 30-minute bars; `^VIX`, `^FVX`, `^TNX`, and `^TYX` preserve provider-native 15-minute bars. The 03:02 occurrence retained all Landing responses but returned 1 after only `SP500_CURRENT_60M` raised a fallback invariant; its prior value was preserved and copied-state replay passed. The natural 03:32 recovery then passed all 17 routes with failures 0, retries 0, and history writes 0. |
| Equity current valuation observation | One retry-zero KRX `MDCSTAT03501` ALL-market observation for 2026-08-25 retained 2,719 unique source rows with zero duplicates. It is exact-date descriptive PER/PBR evidence only (`PIT_LIMITED_FIRST_OBSERVED_ONLY`), not canonical history, Forward EPS or a relative-value judgment. The 09:10 child introduced in v4 is retained unchanged in active bundle v5 and catches up one completed session at a time; valid-empty is expected provider lag and retries only at the next natural occurrence, while auth/network/schema failures remain typed lane failures. |
| Yahoo direct-30m switch gate | `PARTIAL_KEEP_NATIVE_15M` on 2026-08-27. Four retry-zero calls confirmed exact symbol/Cboe exchange/`America/Chicago`/`INDEX`/`dataGranularity=30m` identity. `^VIX` passed the `:00/:30` completed-grid, finite OHLC, retained-15m same-end close, and `:02/:32` selector checks. `^FVX/^TNX/^TYX` instead exposed provider-native start-minute offsets `:20/:50` plus an as-retrieved `:45` quote row, yielding zero `:00/:30` completed candidates and no same-end retained-15m comparison. No mixed interval or code/GUI/scheduler change is allowed from this result; all four routes remain native 15m and retained history remains `STATIC_COMPLETE / NO_REFRESH`. Evidence: `artifacts/agent_runs/yahoo_current_30m_switch/validation_20260827.json` and four immutable responses under `data/landing/yahoo_market_30m_validation/20260827/`. |
| Accounts | Toss read-only refresh is installed for 07:00. Its natural 2026-08-27 occurrence reconciled strict `TERMINAL_SUCCESS / SUCCEEDED` with token calls 1 and account calls 3: the scheduler last-result receipt is byte-identical to the date-keyed occurrence receipt, and its `normalized_sha256` matches the current Normalized snapshot. Its shared lifecycle boundary returns every concurrent contender immediately at API 0 (including a claimed daily occurrence, recorded as `SCHEDULE_CONCURRENT_REFRESH`), restores and reads back the exact prior projection before terminalizing a caught interruption (unknown counts stay `null`), and records an API-zero explicit `RECOVERY_REQUIRED` state rather than falsely terminalizing or silently replaying when exact recovery cannot be verified; the supported CLI publishes that strict receipt as its scheduler last result. Privacy removal invalidates the existing refresher after excluding in-flight writers. Next: observe the next date-keyed Toss occurrence for identifier-free terminal-receipt, digest-binding, and call-budget reconciliation. KB account data is outside the 80-row market-dataset universe. A configured KB account exists: all three approved runtime settings are present, provider-free construction is `ENABLED`, and an identifier-free `SSQM2952` snapshot was live-validated on 2026-08-26. `STOCK_DATA_KBSEC_ACCOUNT_DAILY` is now installed for 07:10 with one-call occurrence idempotency, exact receipt/digest binding, and prior-valid preservation; its first natural task occurrence is pending. The seven `kb_*_snapshot` universe rows are separate market snapshots. |
| Safety | Existing `.env` credentials may be used without disclosure. No orders, transfers, purchases, subscriptions, account mutation, or unsupported semantic/PIT/finality claim. |

Permission-only language in older receipts does not block a newly keyed bounded
operation. Occurrence idempotency, semantic gates, provider rights, PIT,
finality, Landing-first capture, atomic promotion, and prior-valid preservation
still apply.

## Immediate checkpoints

1. Observe the next natural KR bundle v5 occurrences at 09:10 and 20:30.
   Confirm that the source-width and same-day-publication fixes remain API-zero
   when current, the 13-lane receipt completes, and managed Health stays `39/39`.
2. Observe the next date-keyed Toss account 07:00 occurrence; reconcile its
   identifier-free terminal receipt, Normalized digest, and call budget.
3. Observe the first natural KB `SSQM2952` account occurrence at 07:10 and
   verify its identifier-free receipt, exact snapshot digest, one-call budget,
   and prior-valid preservation. Keep the separate disabled legacy KB market
   task distinct; exact inventory is in [Scheduler Status](../project/SCHEDULER_STATUS.md).
4. Use [Source Fallback Policy](SOURCE_FALLBACK_POLICY.md) for every backup-source
   proposal. Only the exact FRED VIX same-upstream parser fallback is currently
   active; never substitute provider-specific Treasury, flow, derivative, or
   account values merely because a primary route failed.
5. Keep `ls_t1633_program_trading_candidate` out of the scheduler. Its
   2026-08-26 recheck used one bounded transient retry, stopped after three data
   calls, and promoted nothing; investigate the provider failure separately.

## Active operations

The links below are the only default operational reading set. A linked file is
authority for its schema, state, and replay rules only after this Status selects
the corresponding task.

| Scope | Selected operations |
|---|---|
| Standing onboarding and orchestration | [Public-source onboarding](operations/AUTONOMOUS_PUBLIC_SOURCE_ONBOARDING.md), [current-observation supervisor](operations/CURRENT_OBSERVATION_ACQUISITION_SUPERVISOR.md) |
| Korean daily core | [Market daily incremental](operations/MARKET_DAILY_INCREMENTAL.md), [Dashboard core daily](operations/DASHBOARD_CORE_DAILY_INCREMENTAL.md), [KRX index fundamentals](operations/KRX_INDEX_FUNDAMENTAL_DAILY.md), [KOSPI200 breadth](operations/KOSPI200_CONSTITUENT_BREADTH_INCREMENTAL.md), [KOSPI200 derivatives](operations/KOSPI200_DERIVATIVES_DAILY_INCREMENTAL.md), [VKOSPI](operations/KRX_VKOSPI_DAILY_INCREMENTAL.md), [LS program trading](operations/LS_T1633_PROGRAM_TRADING_DAILY.md), [LS t8462 raw](operations/LS_T8462_DAILY_RAW_COLLECTION.md) |
| Canonical equity and events | [Canonical daily contract](operations/CANONICAL_EQUITY_DAILY.md), [canonical incremental](operations/CANONICAL_EQUITY_DAILY_INCREMENTAL.md), [dividend append](operations/DIVIDEND_OBSERVATION_APPEND.md) |
| Global/current display | [Global current refresh](operations/GLOBAL_CURRENT_REFRESH.md), [global 30-minute route](operations/GLOBAL_MARKET_CURRENT_60M.md), [SOXX onboarding](operations/GLOBAL_ETF_SOXX_ONBOARDING.md), [FDR future display](operations/FDR_FUTURE_DISPLAY_DAILY.md), [Yahoo historical/native-lane evidence](operations/YAHOO_MARKET_15M.md) |
| Macro and availability | [BOK Treasury source observation](operations/BOK_ECOS_TREASURY_DAILY.md), [Toss Korean Treasury daily](operations/TOSS_KR_TREASURY_DAILY.md), [FRED availability](operations/FRED_OBSERVATION_AVAILABILITY.md), [data.go.kr availability sentinel](operations/DATA_GO_KR_EQUITY_AVAILABILITY_SENTINEL.md) |
| Read-only accounts and provider snapshots | [Toss account](operations/TOSS_ACCOUNT_SNAPSHOT_READONLY.md), [KB account](operations/KBSEC_ACCOUNT_SNAPSHOT_READONLY.md), [family holding prices](operations/FAMILY_ACCOUNT_HOLDING_CURRENT_PRICES.md), [KB market snapshot](operations/KBSEC_DAILY_MARKET_SNAPSHOT.md), [Toss short watchlist](operations/TOSS_SHORT_WATCHLIST_DAILY.md) |
| Intraday current | [Toss recurring 30-minute](operations/TOSS_DOMESTIC_UR246_RECURRING_30M.md) |
| Raw evidence research | [OpenDART corporate actions](operations/OPENDART_CORPORATE_ACTION_INCREMENTAL_PILOT.md), [OpenDART statements](operations/OPENDART_FINANCIAL_STATEMENT_PILOT.md), [Yahoo option/PCR](operations/YAHOO_SYMBOL_OPTION_PCR_PILOT.md) |

### Runtime identity documents

These files remain beside operations because code/tests bind their exact paths;
they are not separate scheduler jobs:

- [Dashboard current projector](operations/DASHBOARD_CURRENT_READINESS_LOCAL_PROJECTOR.md)
- [UR-233 identity manifest](operations/DASHBOARD_CURRENT_READINESS_UR233.md)
- [UR-242 identity manifest](operations/DASHBOARD_CURRENT_READINESS_UR242.md)
- [Account privacy boundary](operations/ACCOUNT_LOCAL_PRIVACY_BOUNDARY.md)

## Active research gates

Uncertainty blocks only the affected claim, promotion, or predictive use.
Continue independent evidence work.

| Question | Current research route |
|---|---|
| Corporate-action identity and Korean event sources | [Canonical identity](research/active/CORPORATE_ACTION_CANONICAL_IDENTITY.md), [cash dividend](research/active/KOREAN_CASH_DIVIDEND_EVENT_SOURCE.md), [split](research/active/KOREAN_SHARE_SPLIT_SOURCE.md), [ticker identity](research/active/KOREAN_SECURITY_TICKER_IDENTITY_SOURCE.md) |
| Pre-2010 derivatives | [Source decision](research/active/KRX_PRE2010_DERIVATIVES_SOURCE_DECISION.md), [terms](research/active/KRX_PRE2010_DERIVATIVES_TERMS.md), [permission gate evidence](research/active/KRX_DERIVATIVES_INVESTOR_PERMISSION_GATE.md) |
| LS semantics | [Source boundaries](research/active/LS_SOURCE_BOUNDARIES.md), [t8462 semantics](research/active/LS_T8462_DERIVATIVES_SEMANTICS.md) |
| ETF and futures PIT | [KRX ETF PIT](research/active/KRX_ETF_PIT.md), [Yahoo commodity futures normalization](research/active/YAHOO_COMMODITY_FUTURES_NORMALIZATION.md) |
| U.S. historical/PIT | [Security master](research/active/US_SECURITY_MASTER_PIT_UNIVERSE.md), [primary OHLCV](research/active/US_EQUITY_ETF_HISTORICAL_OHLCV_PRIMARY_SOURCE.md), [source addendum](research/active/US_EQUITY_ETF_HISTORICAL_OHLCV_PRIMARY_SOURCE_ADDENDUM.md), [SEC fundamentals](research/active/SEC_US_FUNDAMENTAL_PIT.md) |
| Forward earnings | [KR forward-earnings PIT contract](research/active/KR_FORWARD_EARNINGS_PIT_CONTRACT.md) (`FREE_OBSERVATION_UNSUPPORTED / NUMERIC_USE_FORBIDDEN` for Korea Forward EPS, 1W/1M revisions and Forward ROE) |
| Release timestamps and sector history | [CFTC release PIT](research/active/CFTC_RELEASE_DATE_PIT.md), [sector taxonomy feasibility](research/active/SECTOR_TAXONOMY_MEMBERSHIP_PIT_FEASIBILITY.md) (`sector-input-feasibility/v1`, `NUMERIC_CONSUMER_NOT_READY`; shared Data/Backtest feasibility boundary) |
| Provider/account semantics | [KB snapshot contract](research/active/KBSEC_SNAPSHOT_CONTRACT.md), [SOX source research](research/active/SOX_SOURCE_RESEARCH.md) |
| Additional free sources | [Historical free-source discovery](research/active/HISTORICAL_FREE_SOURCE_DISCOVERY.md) |

## Supported boundary

- KRX/pykrx remains the historical Korea default, Yahoo the overseas-market
  default, FRED the macro default, and KB the realtime baseline unless a
  dataset contract selects otherwise.
- Never average or silently merge providers. Preserve source identity, units,
  valid zero/missing values, session meaning, and source timestamps.
- Current snapshots are not canonical daily history. Promotion needs a
  contract-defined and tested rule.
- `EXPECTED_LAG` is not stale; compare each dataset with its own publication
  policy and completed-session calendar.
- Account projections are local, sanitized, identifier-free, and read-only.
- Backtest consumers receive retained, contract-valid data only; Data never
  inspects or changes the sealed Backtest holdout.

## Lifecycle

- Current facts and exact next actions stay here.
- Reusable executable procedures stay in `docs/data/operations/`.
- Unresolved evidence stays in `docs/data/research/active/`.
- Completed, one-shot, expired-window, and superseded detail moves to
  `docs/archive/data/` and never becomes default authority.
- Registry, Dataset Index, or Source Registry rows must be updated when an
  active path changes.
