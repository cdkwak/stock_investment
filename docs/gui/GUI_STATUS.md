# GUI Status

Read this after `AGENTS.md` and
[`PROJECT_STATUS.md`](../project/PROJECT_STATUS.md) for GUI or Dashboard work.
This file contains only current routing facts. Historical feature receipts are
retained under `docs/archive/gui/status_history/`.

## Current phase

`AUTONOMOUS_GUI_ENGINEERING_ACTIVE / WEB_DASHBOARD_PRIMARY / PYSIDE6_PARITY_BRIDGE / PROVIDER_ISOLATED / OTHER_COVERAGE_INCOMPLETE`

GUI and application-service work may proceed in parallel with Data and
Backtest. Presentation remains local and read-only; provider transport,
canonical promotion, and scheduler execution remain Data-owned.

## Current web dashboard

- Primary display: local FastAPI app under `src/stock_web`, with pages 홈 / 시장 /
  종목 / 내 계좌 / 데이터 at <http://127.0.0.1:8787>.
- Always-on runtime: Windows task `STOCK_WEB_DASHBOARD`; restart with
  `scripts/restart_web.cmd` and inspect logs under `artifacts/runtime_logs/web/`.
- Local settings: `artifacts/local_user/web_settings.json`.
- Local-user artifacts: `artifacts/local_user/watchlists.json`,
  `artifacts/local_user/watch_conditions.json`,
  `artifacts/local_user/cash_flows.json`, and
  `artifacts/local_user/trade_journal_manual.json`.
- PySide6 status: **유지 (웹 parity 이후 퇴역 예정, 4단계)**.

## Current state

| Area | Current fact |
|---|---|
| Web Dashboard | The local FastAPI dashboard is the primary display layer. Its 홈 / 시장 / 종목 / 내 계좌 / 데이터 pages consume local read-only services and preserve typed unavailable states. |
| Health | GUI consumes the retained 80-row Health V2 artifact. All 39 automation-enabled rows are acceptable (`CURRENT 13 / EXPECTED_LAG 26`); unmanaged gaps remain visible rather than weakening the managed gate. |
| Korean valuation | Accepted KRX KOSPI/KOSDAQ PER/PBR stays visible under `KRX_NEXT_TRADING_DAY_0910`; unpublished same-day data is not demanded early. |
| Derivatives | Basis, volume PCR, OI PCR, Call Wall, and Put Wall consume exact Health-resolved T+1 dates and fail numeric-free on missing or mismatched inputs. |
| Korean Treasury | Numeric display remains withheld while BOK 817Y002 publication finality is unknown. Yahoo quote indices, futures prices, and older curves are not substitutes for official BOK yields. |
| Market regime | Price/trend/volatility and KRX valuation are present; PIT-safe Forward EPS/revision/ROE is absent. VIX temperature prefers the accepted Yahoo `^VIX` completed 15-minute current observation when available and ranks that display-only value against the retained FRED VIXCLS completed-daily distribution; it never appends the quote to daily history or Backtest inputs. The visible state remains evidence `2/3` with high/low-point judgment withheld. |
| Flow | Korean foreign/institution/individual daily-final flows are descriptive KRX-market facts. No U.S. participant-flow number is fabricated from semantically different weekly CFTC data. |
| Research | Provider-free Research Workspace, local watchlists, chart coverage, layout presets, and `Ctrl+K` exact-identity symbol switching are implemented. The [practical partial-axis scanner](STOCK_EXPLORATORY_SCANNER_CONTRACT.md) starts asynchronously on first tab use and shows extreme original-price candidates at RSI14 <= 30 or close/SMA60 <= 80%. Exact-date, hash-bound KRX current PER/PBR is displayed independently when present and never changes inclusion/order; Forward EPS and strict relative-value judgment remain `N/A`. Activating a row revalidates it through the exact local catalog before opening the chart. |
| Accounts | Toss and KB views are read-only and sanitized. Verified holding fields include average/current price, correctly scaled provider returns, ordinary/after-cost/daily P/L and source-time detail. Unsupported cash, realized P/L and cross-currency totals remain absent. The natural 2026-09-02 07:10 KB task failed closed after one supplier call and preserved the prior snapshot; a separately keyed manual read-only refresh then succeeded with one call. GUI state receives neither failed provider detail nor any direct identifier, and the failed scheduled receipt is not rewritten. |
| Backtest | GUI consumes validated typed local result bundles and contains no Feature, Model, strategy, fill, risk, or accounting logic. |
| Release readiness | GUI startup/provider isolation, account-environment allowlisting, cockpit readability, and the exact ten-page contract pass focused coverage. The fresh 09:50 KST offline gate passed native GUI, file identity, schema, cache, freshness, and required scheduler-result checks with zero external calls/mutations, but full release remains `FAIL` on Health-receipt reconciliation, three retained nonzero scheduler results, and 9/10 due groups. The inaccessible derivatives dataset is a separate retained-audit gate. |
| PySide6 | **유지 (웹 parity 이후 퇴역 예정, 4단계)**. It remains the secondary local display while web parity is completed; it is not the primary run route. Its verified consumer-first Today page grouped market summary, Korean/US flow, accounts, and evidence links with typed unavailable states; the retained 1366x768 and 900x640 checks had zero horizontal scroll or overlap, correct Korean glyphs, a visible analysis entry point, and WCAG-AA secondary text on the checked backgrounds. |

## Owning contracts and maps

Read only the document that owns the selected change.

| Change scope | Owning document |
|---|---|
| Dashboard physical dataset-to-surface mapping | [Dashboard Data Map](DASHBOARD_DATA_MAP.md) |
| Provider capability and unresolved coverage | [Dashboard Provider Coverage](DASHBOARD_PROVIDER_COVERAGE.md) |
| Final/current/history source selection | [Dashboard Daily Source Routing](DASHBOARD_DAILY_SOURCE_ROUTING.md) |
| Per-surface as-of, freshness, last success, and next eligibility | [GUI Refresh Status Contract](GUI_REFRESH_STATUS_CONTRACT.md) |
| Indicator names, units, meaning, and suppression | [Indicator Semantic Catalog](INDICATOR_SEMANTIC_CATALOG_CONTRACT.md) |
| Future Korean daily summary and Telegram-sized projection | [Daily Market Summary Contract](DAILY_MARKET_SUMMARY_CONTRACT.md) |
| Scanner liquidity and financial-health filters | [Scanner Filters Contract](SCANNER_FILTERS_CONTRACT.md) |
| Account activity derivation and manual trade entries | [Trade Journal Contract](TRADE_JOURNAL_CONTRACT.md) |
| Provider-free morning investment note | [Investing Journal Contract](INVESTING_JOURNAL_CONTRACT.md) |

Current collection gates and retained-data truth are owned by
[Data Status](../data/DATA_STATUS.md). Scheduler inventory is owned by
[Scheduler Status](../project/SCHEDULER_STATUS.md). Do not duplicate either in
GUI documents.

## Runtime boundary

- The primary entry is the always-on `STOCK_WEB_DASHBOARD` task serving
  <http://127.0.0.1:8787>; use `scripts/restart_web.cmd` to restart it.
- `app.py` starts the secondary PySide6 application. Its status is **유지 (웹
  parity 이후 퇴역 예정, 4단계)**; shared services must be separated before
  retirement.
- Startup and rendering are provider-free and local-read-only by default.
- A GUI action may request an allowlisted asynchronous Data-owned read-only
  operation through a typed service. Secrets and provider parameters never
  enter presentation state.
- GUI code does not collect, backfill, promote, or rewrite market data.
- Every numeric surface preserves source, identity, unit, as-of, freshness,
  and provisional/finality meaning. Invalid, stale, mismatched, or unsupported
  input clears the number rather than showing a fallback.
- `CURRENT_SNAPSHOT` and `LATEST_FINAL_DAILY` are different routes. A current
  snapshot is never appended or interpolated into canonical daily history.
- Providers are not averaged, merged, or silently substituted.
- Account refresh is read-only; real or paper-broker order submission,
  amendment, cancellation, transfer, withdrawal, and other mutation are absent.
- Presentation preferences may persist allowlisted layout and identity choices,
  never market values, account identifiers, credentials, or provider payloads.
- Generated GUI-validation artifacts are pruned only through the existing
  reference-aware dry-run/digest/apply maintenance boundary; archives and
  active evidence remain protected.

## Current gaps

| Gap | Effect | Owner / release condition |
|---|---|---|
| BOK Treasury finality gate incomplete | Korean sovereign-yield values remain numeric-free | Data; complete the three-batch 17:10 evidence gate and contract-valid promotion |
| PIT-safe Forward EPS/revision/ROE unavailable | Earnings-momentum axis and market high/low judgment remain unavailable | Data research contract; never substitute current KRX PER/PBR |
| Daily summary input/runtime unavailable | The closed contract is concise and source-bound, but registry revision 1 intentionally yields `NO_OUTPUT` because no accepted `MARKET_STATE` result is selected; no Telegram runtime exists | GUI; a later reviewed task must bind the exact local result before implementing the provider-free composer |
| Failed KB scheduled receipt and one inaccessible Data dataset | Full release cannot pass even though GUI-local checks and the 09:10 KR task pass | Data; preserve the failed receipt, and repair only the exact ACL after explicit approval before rerunning the full gate |
| Unmanaged retained-data gaps | Health remains visibly degraded outside the managed SLO | Data; fix each dataset through its own contract rather than masking the row |
| PySide6 parity retirement | The web app is primary, but some shared services and parity coverage still live under the Qt-era package boundary | GUI; move shared services to a neutral package and retire PySide6 only in phase 4 after parity |

## Exact next GUI actions

1. Keep the local web Dashboard stable at <http://127.0.0.1:8787>, use
   `scripts/restart_web.cmd` for restarts, and preserve the typed local-only
   boundary while web parity is completed.
2. Preserve the closed
   [Daily Market Summary Contract](DAILY_MARKET_SUMMARY_CONTRACT.md): its default
   projection is normally 3–4 lines and hard-limited to 6 lines/480 code points.
   A later reviewed implementation task must first bind an accepted local
   `MARKET_STATE` result; registry revision 1 remains `NO_OUTPUT`.
3. Expose Korean sovereign yields or curve views only after Data publishes a
   contract-valid typed result. Keep quote-index, futures-price, official-yield,
   curve-spread, and bond-ETF return semantics separate.
4. Continue ordinary GUI work through the owning contract and focused tests;
   do not add historical feature receipts back into this Status.

## Validation boundary

- Changes to visible financial semantics, account/privacy behavior, refresh
  status, or shared contracts require independent review under the queue rules.
- Ordinary layout/document routing changes use focused automated checks.
- Provider-free unit/service tests and an offscreen render are required for
  visible GUI behavior changes. Live API calls do not belong in GUI tests.
- Historical acceptance evidence for the phase-2 retired subsystem is preserved
  only on `backup/repo-cleanup-phase2-20260903`; it is not a current GUI
  validation route.
- Release-readiness evidence proves only its enumerated managed surfaces. It
  now covers the exact Toss account due outcome and all enabled Data task
  definitions, but not BOK due outcomes, Issue State, Telegram, or legacy KB.

## Resume route

```text
AGENTS.md
  -> docs/project/PROJECT_STATUS.md
  -> docs/gui/GUI_STATUS.md
  -> exactly one owning contract or map above
```

Use [Repository Map](../project/REPOSITORY_MAP.md) only to locate code and
ownership. Read [Project Roadmap](../project/PROJECT_ROADMAP.md) only for an
architecture or sequencing decision. Do not scan GUI history or Data
operations by default.
