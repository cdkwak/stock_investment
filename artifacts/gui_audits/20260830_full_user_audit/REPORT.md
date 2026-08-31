# Full GUI user audit — 2026-08-30

## Bounded PM acceptance checkpoint — scope corrected

On HEAD `8085ceb1fa693feba2c2526d33fd96309d0da89a`, the isolated Windows QFont
regression passed. Two fresh native Windows 1600×900 cycles, 01 and 02, each
completed all 10 page contracts, reconciled 138 stable controls, recorded 108
executed / 16 disabled-prerequisite / 14 safety-skip dispositions, repeated the
provider-free local candidate refresh twice, emitted zero negative QFont
warnings, drained managed workers, and closed cleanly.

Cycle 02 initially rejected one lower-axis assertion because the audit hook
forcibly showed a hidden plot and measured whichever stale indicator had
rendered last. The task-local hook was corrected to apply one explicit
volume-on/RSI14-panel state, measure all eight primary lower axes, and restore
the exact prior state; the canonical cycle 02 rerun passed. This is an audit
artifact, not reproducible product flakiness.

No final ten-cycle GUI acceptance is claimed and no immutable acceptance
generation was frozen. PM clarified that the user's ten-count belongs to ten
distinct Canonical Queue problems traversing the complete lifecycle, not ten
repetitions of this GUI harness; cycle 03 was interrupted under that scope
correction and retained only as invalidated partial evidence.

## Outcome

The read-only native Windows audit covered **10 page contracts**, **138 control/tab/dialog dispositions**, **10 user-facing dataset groups**, and **16 retained redacted screenshots**. Dispositions reconcile to **104 EXECUTED_VERIFIED**, **18 SKIPPED_SAFETY**, and **16 DISABLED_PREREQUISITE**; all managed workers were quiescent before a clean close.

Orca computer-use was attempted first and returned `runtime_unavailable`. The fallback used the repository's real PySide6 application with native logical 1600×900 rendering and real `QTest.mouseClick` / `keyClick` events; no provider refresher was injected.

## Evidence

- Full action and dataset matrix: [action_matrix.json](action_matrix.json)
- Screenshot manifest: [screenshots/index.json](screenshots/index.json)
- Screenshots: [screenshots/](screenshots/)
- Immutable acceptance contract: `.unlazy/gui-full-audit-20260830/PLAN.md`

## Findings for PM deduplication/triage

### F-GUI-20260830-001 — P2 — Primary chart lower-panel y-axis labels clip at the left edge

- Fingerprint: `gui:primary-chart:left-axis-label-clipping:win1600x900`
- Reproduction: On native Windows at logical 1600x900, open Dashboard, Index Graph, 종목 차트 (005930), and 미국 ETF (SPY); inspect the left edge of volume/indicator lower panels.
- Expected: Every rotated y-axis title and tick label is fully readable inside the viewport.
- Actual: The first characters and/or tick labels are cut by the left viewport boundary; the defect repeats on multiple primary chart surfaces.
- Confidence: HIGH
- Likely code scope: src/stock_data/gui/main_window.py: chart layouts/margins for DashboardPage, IndexPage, IndividualEquityPage
- Likely test scope: tests/unit/gui/test_gui_backtest.py: 1600x900 primary-chart fit and pixel-bound assertions
- Dedup hint: Search Canonical Queue for left-axis, volume label, chart clipping, 1600x900.
- Screenshots: screenshots/00_Dashboard.png, screenshots/01_Index_Graph.png, screenshots/02_종목_차트.png, screenshots/03_미국_ETF.png

### F-GUI-20260830-002 — P2 — Data Status summary-card detail text is vertically clipped

- Fingerprint: `gui:data-status:summary-card-body-vertical-clipping:win1600x900`
- Reproduction: Open Data Status at native logical 1600x900 after the local status reread completes; inspect the four summary cards above 통합 갱신 상태.
- Expected: Each card shows its complete summary/status text or uses an explicit expandable/ellipsis affordance.
- Actual: Second/third lines are visibly cut at the fixed card bottom, obscuring current counts/status context.
- Confidence: HIGH
- Likely code scope: src/stock_data/gui/main_window.py: DataStatusPage summary-card sizing/layout
- Likely test scope: tests/unit/gui/test_gui_health.py; tests/unit/gui/test_gui_backtest.py: Windows 1600x900 pixel/layout regression
- Dedup hint: Search Canonical Queue for Data Status summary card clipping or fixed height.
- Screenshots: screenshots/06_Data_Status.png

### F-GUI-20260830-003 — P2 — Selected-equity detail text and right-rail controls clip at 1600x900

- Fingerprint: `gui:equity-workspace:identity-detail-and-right-rail-clipping:win1600x900`
- Reproduction: Search 005930 or SPY with real mouse/key events, select the exact result, click 차트 보기, and inspect the identity card, source text, and bottom right-rail buttons at 1600x900.
- Expected: Identity/source text and all button captions remain fully readable without overlap or truncation.
- Actual: Identity/source lines are cut vertically; the bottom of the right rail clips the 아래 control caption. The selected-symbol error state remains understandable but materially harder to scan.
- Confidence: HIGH
- Likely code scope: src/stock_data/gui/main_window.py: IndividualEquityPage header/detail/right-rail sizing
- Likely test scope: tests/unit/gui/test_gui_backtest.py: primary chart pages at 1600x900 with selected identity
- Dedup hint: Search Canonical Queue for IndividualEquityPage clipping, right rail, identity detail.
- Screenshots: screenshots/02_종목_차트.png, screenshots/03_미국_ETF.png

### F-GUI-20260830-004 — P3 — Research empty chart looks numeric and OHLCV columns are clipped

- Fingerprint: `gui:research-workspace:empty-chart-and-ohlcv-clipping:win1600x900`
- Reproduction: Open Research Workspace at 1600x900 with no selected symbol and inspect the chart/OHLCV panels.
- Expected: The chart has an explicit unavailable overlay and the OHLCV table exposes all column meanings without clipped headers.
- Actual: The blank chart renders default numeric axes (0.2–0.8), while the fixed-width OHLCV panel cuts headers after high and relies on a tiny horizontal scrollbar.
- Confidence: HIGH
- Likely code scope: src/stock_data/gui/main_window.py: ResearchWorkspacePage empty-state overlay and splitter/table sizing
- Likely test scope: tests/unit/gui/test_gui_backtest.py; tests/unit/gui/test_stock_candidate_discovery_gui.py
- Dedup hint: Search Canonical Queue for Research Workspace empty chart axes or OHLCV header clipping.
- Screenshots: screenshots/04_Research_Workspace.png

### F-GUI-20260830-005 — P2 — Current candidate refresh terminates in LOCAL_CANDIDATE_READ_FAILED

- Fingerprint: `gui:research-candidate-scan:local-candidate-read-failed:20260830-current-state`
- Reproduction: Open Research Workspace, click 현재 후보 새로고침 with a real mouse event, wait until the managed candidate worker finishes.
- Expected: The retained local scan loads candidates, or a precise missing/corrupt input reason identifies the recovery action.
- Actual: The terminal feedback is '현재 후보를 읽지 못했습니다 (LOCAL_CANDIDATE_READ_FAILED)' with zero candidate rows; it does not identify the failing retained input.
- Confidence: HIGH
- Likely code scope: src/stock_data/gui/main_window.py: refresh_candidate_scan / candidate worker; src/stock_data/gui/services.py: LocalExploratoryCandidateScanner
- Likely test scope: tests/unit/gui/test_stock_candidate_discovery_gui.py; tests/unit/gui/test_gui_backtest.py
- Dedup hint: Search Canonical Queue for LOCAL_CANDIDATE_READ_FAILED, exploratory candidate scanner, retained scan path.
- Screenshots: screenshots/04_Research_Workspace.png

### F-GUI-20260830-006 — P2 — Windows full-page navigation emits negative QFont point-size warnings

- Fingerprint: `gui:windows-navigation:qfont-negative-point-size-warning:qt-dpr1`
- Reproduction: Run test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning with QT_SCALE_FACTOR=1 and QT_ENABLE_HIGHDPI_SCALING=0 on native Windows.
- Expected: All page/chart fonts retain positive point sizes and the focused Windows navigation test passes without QFont warnings.
- Actual: The focused test fails after navigation with repeated 'QFont::setPointSizeF: Point size <= 0 (-0.750000)' messages; six sibling focused GUI tests pass.
- Confidence: HIGH
- Likely code scope: src/stock_data/gui/main_window.py: account chart font propagation; src/stock_data/gui/font_policy.py
- Likely test scope: tests/unit/gui/test_gui_backtest.py::test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning
- Dedup hint: Search Canonical Queue for QFont setPointSizeF -0.75, Windows navigation, account chart fonts.
- Screenshots: screenshots/07_Account.png

## Dataset-quality review

Every dataset row in the matrix records grain, value presence, as-of handling, freshness/next eligibility, required missingness, sentinel/invalid handling, source identity, explanatory empty/unavailable behavior, table shape, and screenshot evidence. Important observed boundaries were preserved: Dashboard numeric suppression, Data Status managed/unmanaged distinction, exact equity identity/date mismatch, US ETF authorized-scope failure, account source separation and privacy, Net Worth empty state, and Backtest development-only/holdout-sealed labelling.

## Usability review

Discoverability, feedback, latency, layout/clipping, terminology, keyboard/focus, recovery, privacy, stale/error handling, and clean close were all inspected. 6 reproducible findings are frozen above; no P0/P1 was observed.

## Focused validation

- Artifact/schema/digest validator: PASS — 10 pages, 138 controls, 10 datasets, 6 findings, 16 screenshots.
- Focused native GUI pytest selection: 6 passed, 1 failed. The failure is frozen as F-GUI-20260830-006; it does not invalidate the observed audit matrix, but it requires PM deduplication/triage.

## Safety and retries

Backtest execution, scenario execution, export, broker/paper-order endpoints, transfers, account/user-data mutations, provider refresh, scheduler activation, and Queue/Git mutations were never invoked. The audit used 1 failed Orca-computer attempt and 10 native harness attempts: 3 pre-evidence harness failures/interruptions exposed DPR/modal/worker-drain issues, followed by 7 successful refinement passes; only the final clean pass feeds the durable matrix and screenshots.

## Four-pass completion record

1. Audit pass: inventoried and exercised pages, tabs, dialog controls, selectors, filters, inputs, privacy controls, local rereads, and validation-only actions.
2. Expert reread: reconciled 10 page contracts and dataset semantics against GUI Status.
3. Defect hunt: pixel-reread at 1600×900, edge/error/disabled states, worker quiescence, and privacy capture checks.
4. Polish: fixed audit-evidence gaps, re-ran exact-identity chart workflows, deduplicated stable control ids, remeasured counts, and validated clean close.

## PM-owned remainder

C5–C7 / G5–G7 remain pending exactly as required. PM must deduplicate these 6 fingerprints against the Canonical Queue, triage accepted findings into disjoint OWNS, require focused tests and fresh review, and run the final affected-control regression; this audit does not register, triage, claim, implement, review, commit, or mutate Queue state.
