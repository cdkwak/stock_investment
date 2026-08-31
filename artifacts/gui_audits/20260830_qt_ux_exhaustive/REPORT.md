# Stock Investment Rev1 — exhaustive native Qt UX audit

## Verdict: FAIL

The audit is **complete**, not `Incomplete`. All ten user surfaces, all five requested display sizes, the four Research pane configurations, 125% large-font states, pointer and keyboard paths, modal/detail behavior, heavy/empty/long-content states, and native accessibility/performance substitutes were exercised. A completion protocol then added 10/10 explicit post-action assertions, mid-form interruption/revisit, first-versus-second visit cost, three lifecycle positions, an A→B→A state round trip, Day 0/1/7/30 date horizons, Account table 0/1/100/1000 stress, and repeated performance samples. The product fails because core information or recovery controls disappear at supported desktop sizes.

### Persona lock

- **Role:** an individual investor checking market, data, and personal-asset state every day.
- **Technical comfort:** ordinary user; does not interpret dataset IDs or internal failure contracts.
- **Time pressure:** must understand anomalies and the next safe action within 3–10 minutes, often before or during market hours.
- **Emotional state:** highly sensitive to incorrect money/data and cautious around anything that resembles deletion, ordering, or transfer.
- **Device/context:** Korean-primary Windows desktop, mouse and keyboard, approximately 60 cm viewing distance, physical maximum 2560×1440, with 1280×720 as the narrow supported audit condition.

### Severity tally

- Critical: 0
- High: 5
- Medium: 10
- Low: 1
- Total final findings: 16

**Interaction Manifest: complete (60/60 required entry types; 6 per surface).**

### Hard-gate scorecard

| Hard gate | Result | Evidence |
|---|---|---|
| Qt console warnings/errors = 0 | PASS | `ledger.json`: 0 messages; `followup.json`: 0 messages |
| Provider/network side effects = 0 | PASS | `runtime.provider_refresh_injected=false`; provider-free fixture boundary |
| Layout collapse = 0 | **FAIL** | Dashboard at 1920↓, Net Worth at 1366/1280, Research all-open at 1280, Index value/control clipping at 1600↓ |
| High native accessibility defects = 0 | **FAIL** | UX-05: Account display-range selector is omitted from real Tab traversal. UX-07 is a separate Medium accessibility finding and does not drive this hard gate. |
| Metric-specific native performance budgets | PASS | Repeated samples: startup median/p95 1039.98/3185.35 ms (budgets 5000/6000), direct tab 14.47/64.30 ms (250/500), safe action 5.24/7.71 ms (200/500), resize 41.26/51.65 ms (250/500). Deliberate 700 ms audit waits were excluded. |
| Financial/account/scheduler safety boundary | PASS | No order, transfer, account mutation, provider refresh, scheduler activation, protected-data access, or product-code write |

## Measured coverage

- Physical display 2560×1440; maximized application client 2560×1334 because Windows reserved chrome/taskbar space.
- Display matrix: 2560×1440, 1920×1080, 1600×900, 1366×768, 1280×720.
- 10/10 surfaces and 117 controls reconciled: 95 executed, 13 disabled by prerequisites, 9 safety-skipped, 0 unresolved.
- 154 retained screenshots after the completion protocol.
- 20 Research pane captures (4 configurations × 5 sizes), 20 large-font captures, 11/11 scenario rows.
- 0/1/100/1000 synthetic table-volume runs on every applicable table, followed by state restoration.
- 438.936 seconds final interaction run, 85 material events, 1.054-second median event gap, clean close.
- Follow-up focus run: 10/10 surfaces, 0 direct-focus failures, 0 Qt messages. Index Graph reached all 21 controls by Tab; this supersedes the earlier inactive-window measurement artifact.
- Completion protocol: 10/10 explicit primary-action assertions passed; 5/5 missing scenario protocols passed; the visible Account table completed 0/1/100/1000 rows and restored; repeated performance budgets passed; 0 Qt messages.

### Phase times and manifest completeness

| Phase | Measured result |
|---|---|
| Main interaction/viewport/stress filmstrip | 438.936 seconds; 85 events; median gap 1.054 seconds |
| Focus/accessibility follow-up | 10 surfaces; 0 direct-focus failures; clean close |
| Completion protocol | 5 startup samples, 10 post-actions, 5 missing scenarios, 1 dynamic Account table, 59 repeated performance samples |
| Independent visual review | 3 disjoint screenshot reviews, followed by one fresh audit-the-audit critique |
| Manifest | 10/10 surfaces, 117/117 controls, 5/5 display sizes per surface, 11/11 scenario families |

## Display-size summary

| Display | Product result | Principal evidence |
|---:|---|---|
| 2560×1440 | WARN | No breakpoint collapse, but Dashboard density/long-copy limits, Account legend crowding, Backtest empty-chart hierarchy, and raw diagnostic language remain. |
| 1920×1080 | FAIL | Dashboard indicator labels already truncate. |
| 1600×900 | FAIL | Dashboard controls collapse; Index right-edge price pill clips; Net Worth begins horizontal overflow. |
| 1366×768 | FAIL | Dashboard controls become ambiguous fragments; Index controls/value clip; Net Worth reload/destructive controls leave the viewport. |
| 1280×720 | FAIL | The 1366 failures worsen; Research all-open source/status content is cut off with no vertical recovery. |

## Ranked Top 5

1. **UX-01 Dashboard responsive control collapse** — highest reach and earliest breakpoint: it damages the default landing screen at 1920 px, so one shared responsive-toolbar fix improves both Dashboard and Index.
2. **UX-02 Net Worth recovery controls leave the viewport** — it hides the safest recovery action at common laptop widths; a small header-wrap patch yields high user and safety value.
3. **UX-03 Research source/status content is lost at 1280×720** — unlike visual density, this removes the explanation of data trust entirely and has no scroll recovery.
4. **UX-05 Account selector is skipped by Tab** — it blocks a complete keyboard path on a privacy-sensitive screen and is cheaper to fix than the broader content work.
5. **UX-04 Shared provenance/recovery language** — it affects Research, Data Status, Index detail, Account, and Backtest; one formatter plus one direct recovery route has unusually broad leverage.

## Findings

### UX-01 — Dashboard chart controls collapse below 1920 px

- **Layer:** Layout / responsive behavior
- **Severity:** High
- **Surface + viewport + panes:** Dashboard; 1920×1080, 1600×900, 1366×768, 1280×720; indicator panel expanded
- **Persona:** A user checking the market at a normal desktop/laptop width
- **Reproduce:**
  1. Open Dashboard.
  2. Expand `보조지표`.
  3. Resize successively to the four listed sizes.
- **Observed:** Indicator captions lose characters at 1920/1600. At 1366/1280 the chart title and market selector collide and controls are reduced to ambiguous one- or two-character fragments.
- **Expected:** Every indicator remains recognizable and operable, with controls wrapping, collapsing into a disclosure, or moving to a secondary row/menu.
- **Evidence:** `evidence/Dashboard_1920x1080.png`, `evidence/Dashboard_1600x900.png`, `evidence/Dashboard_1366x768.png`, `evidence/Dashboard_1280x720.png`, `evidence/Dashboard_largefont_1366x768.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:5597` and `src/stock_data/gui/main_window.py:5633`.
- **Smallest possible patch:** Replace the two fixed single-row `QHBoxLayout`s with a breakpoint-aware two-row/collapsible indicator toolbar and give title/selectors non-compressing size policies.

### UX-02 — Net Worth toolbar hides delete and reload actions

- **Layer:** Layout / recovery discoverability
- **Severity:** High
- **Surface + viewport + panes:** 순자산·증감; 1600×900 incipient overflow, 1366×768 and 1280×720 collapse; 125% at 1366×768
- **Persona:** A laptop user trying to load or repair a local net-worth snapshot
- **Reproduce:**
  1. Open `계좌·순자산` → `순자산·증감`.
  2. Resize to 1366×768 or 1280×720.
  3. Inspect the single header row before horizontal scrolling.
- **Observed:** `이 날짜 스냅샷 삭제` is clipped and `로컬 새로 읽기` is off-screen. A bottom horizontal scrollbar is the only discovery mechanism.
- **Expected:** Recovery and safety-critical controls remain visible without horizontal scrolling.
- **Evidence:** `evidence/Net_Worth_1366x768.png`, `evidence/Net_Worth_1280x720.png`, `evidence/Net_Worth_largefont_1366x768.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:4544`–`4585`.
- **Smallest possible patch:** Stack title and actions at the breakpoint, then wrap safe actions and move edit/delete into a clearly labeled maintenance menu.

### UX-03 — Research all-open layout loses source/status content at 1280×720

- **Layer:** Layout / information loss
- **Severity:** High
- **Surface + viewport + panes:** Research Workspace; 1280×720; default/all-open panes
- **Persona:** A researcher validating why a candidate or chart is unavailable
- **Reproduce:**
  1. Open Research Workspace with the default/all-open preset.
  2. Resize to 1280×720.
  3. Read the rightmost `출처·상태` pane to its end.
- **Observed:** The final sentence is cut at the bottom and the page exposes no vertical scroll or alternate disclosure.
- **Expected:** The full status remains reachable; narrow layouts should scroll vertically or switch to a compact pane preset.
- **Evidence:** `evidence/Research_Workspace_1280x720.png`, `evidence/Research_panes_all_open_1280x720.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:12395`, `src/stock_data/gui/main_window.py:12501`, and `src/stock_data/gui/main_window.py:12561`.
- **Smallest possible patch:** Wrap the page in a vertical scroll area and auto-select a compact preset below the measured breakpoint while preserving the user's pane choices.

### UX-04 — Shared provenance exposes internal contracts and Research cannot recover

- **Layer:** Content / error recovery
- **Severity:** High
- **Surface + viewport + panes:** Research Workspace at all sizes/presets, plus related Data Status, Index detail, Account, and Backtest provenance surfaces
- **Persona:** A first-time user who wants to understand why research data is unavailable
- **Reproduce:**
  1. Open Research Workspace under the provider-free missing-candidate state.
  2. Read the candidate status and source pane.
  3. Attempt to find an action that repairs or explains the missing inputs.
- **Observed:** `LOCAL_CANDIDATE_INPUT_MISSING`, `recovery=Data`, internal dataset IDs, and `exact typed view` are rendered verbatim. Data Status also shows `empty fixture health`; Account and Backtest expose raw contract tokens. `현재 후보 새로고침` merely retries the same failed read.
- **Expected:** Plain-language summary, effect, and one operable next step (`데이터 상태 열기` or `복구 방법 보기`), with technical IDs behind disclosure.
- **Evidence:** `evidence/Research_Workspace_2560x1440_after.png`, `evidence/Research_Workspace_1366x768.png`, `evidence/Research_panes_minimal_chart_1280x720.png`, `evidence/Data_Status_largefont_1366x768.png`, `evidence/Index_Graph_detail.png`, `evidence/Backtest_1280x720.png`.
- **Suspected code location:** `src/stock_data/gui/services.py:132`, `src/stock_data/gui/main_window.py:12430`, `src/stock_data/gui/main_window.py:12535`, `src/stock_data/gui/main_window.py:12719`.
- **Smallest possible patch:** Introduce one shared user-facing failure/provenance formatter used by all named surfaces and add a direct Data Status/recovery action beside the Research summary.

### UX-05 — Account display-range selector is skipped by Tab

- **Layer:** Accessibility / keyboard navigation
- **Severity:** High
- **Surface + viewport + panes:** 계좌·보유; all sizes; populated and unavailable states
- **Persona:** A keyboard user selecting the account display scope
- **Reproduce:**
  1. Open `계좌·보유`.
  2. Activate the window and traverse controls using Tab.
  3. Observe which page-local control is omitted.
- **Observed:** Direct focus succeeds on all 8 controls, but the real Tab loop reaches only 7 and skips `식별정보 없는 계좌 표시 범위 선택`.
- **Expected:** Logical Tab order reaches the selector in the same visual order as the page.
- **Evidence:** `followup.json` (`Account.tab_missing`), `evidence/Account_focus.png`, `evidence/Account_1366x768.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:3236`–`3265`.
- **Smallest possible patch:** Set an explicit local Tab chain beginning at `source_selector`, then re-run the same 8-control Tab assertion.

### UX-06 — Account maintenance and deletion share the safe-read hierarchy

- **Layer:** Safety hierarchy / visual design
- **Severity:** Medium
- **Surface + viewport + panes:** 계좌·보유; all sizes
- **Persona:** A cautious user distinguishing harmless local rereads from local data mutation
- **Reproduce:**
  1. Open `계좌·보유`.
  2. Compare `로컬 새로 읽기` with add/update/edit/delete and whole-history deletion.
  3. Inspect the empty and synthetic populated states without activating deletion.
- **Observed:** Safe read, local mutations, and `계좌 스냅샷·가치 이력 전체 삭제` use similar neutral visual weight even though their consequences differ materially.
- **Expected:** Maintenance is separated from viewing, and destructive actions use danger treatment plus concise consequence text.
- **Evidence:** `evidence/Account_1366x768.png`, `evidence/Account_2560x1440_before.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:3266`–`3308`; default-No confirmation at `src/stock_data/gui/main_window.py:14264`–`14282` is positive but not visible hierarchy.
- **Smallest possible patch:** Move mutations into a `로컬 데이터 관리` group/menu and danger-style only the two deletion actions.

### UX-07 — Data Status lifecycle table has no semantic name

- **Layer:** Accessibility
- **Severity:** Medium
- **Surface + viewport + panes:** Data Status; all sizes; normal and large-font
- **Persona:** A screen-reader user or a user interpreting data health
- **Reproduce:**
  1. Open Data Status.
  2. Inspect the accessibility inventory for the `통합 갱신 상태` table.
  3. Query the focused widget's semantic name.
- **Observed:** The enabled lifecycle `QTableWidget` has no semantic name; its parent group name does not label the table itself.
- **Expected:** The table has a concise accessible name describing its rows.
- **Evidence:** `followup.json` (`Data_Status.unnamed_controls`), `evidence/Data_Status_focus.png`, `evidence/Data_Status_largefont_1366x768.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:10959` and `src/stock_data/gui/main_window.py:11487`–`11490`.
- **Smallest possible patch:** Call `setAccessibleName("화면별 갱신 상태 표")`; raw provenance text is handled once in UX-04.

### UX-08 — Index Graph clips the current-price pill

- **Layer:** Layout / chart readability
- **Severity:** Medium
- **Surface + viewport + panes:** Index Graph; 1600×900, 1366×768, 1280×720
- **Persona:** A user comparing indicators and the latest index value
- **Reproduce:**
  1. Open Index Graph with a loaded local series.
  2. Resize to 1600×900, then 1366×768 and 1280×720.
- **Observed:** The right-edge current-price pill extends outside the chart at 1600 px and below.
- **Expected:** The value label remains fully inside the plot at every supported width.
- **Evidence:** `evidence/Index_Graph_1600x900.png`, `evidence/Index_Graph_1366x768.png`, `evidence/Index_Graph_1280x720.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:8154`–`8159`.
- **Smallest possible patch:** Reserve a right plot margin equal to the measured badge width; shared indicator truncation is merged into UX-01.

### UX-09 — Equity and US ETF guided examples immediately fail

- **Layer:** First-use guidance / trust
- **Severity:** Medium
- **Surface + viewport + panes:** 종목 차트 and 미국 ETF; all sizes
- **Persona:** A first-time user following the built-in example
- **Reproduce:**
  1. Open either chart page.
  2. Use the advertised Samsung `005930` or `SOXX` example.
  3. Observe the result banner.
- **Observed:** The exact advertised example remains in the field while the page reports identification/no-match failure.
- **Expected:** The example is guaranteed by the currently loaded local catalog, or the UI explains that the catalog itself is unavailable.
- **Evidence:** `evidence/Equity_2560x1440_after.png`, `evidence/Equity_1920x1080.png`, `evidence/US_ETF_2560x1440_after.png`, `evidence/US_ETF_1920x1080.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:8994`–`9003` and `src/stock_data/gui/main_window.py:9014`–`9019`.
- **Smallest possible patch:** Populate the example from an actually available catalog item; if none exists, replace the example with a local-catalog recovery state.

### UX-10 — Backtest result state conflicts and empty charts look broken

- **Layer:** Information hierarchy / empty state
- **Severity:** Medium
- **Surface + viewport + panes:** Backtest; all sizes and large-font states
- **Persona:** A user attempting to understand whether a validation result exists
- **Reproduce:**
  1. Open Backtest in the retained failure/empty state.
  2. Read the banner, cards, and plots.
- **Observed:** Raw labels such as `DEVELOPMENT ONLY`, `NEXT-OPEN LEDGER`, and `NOT_EXECUTABLE_INSTRUMENT` dominate. The banner says a result was preserved while visible cards say no result; large black empty plots resemble rendering failures.
- **Expected:** Plain-language purpose, one primary recovery action, consistent preserved-result status, and a friendly plot empty-state overlay.
- **Evidence:** `evidence/Backtest_1280x720.png`, `evidence/Backtest_2560x1440_before.png`, `evidence/Backtest_largefont_1366x768.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:10163`–`10300` and `src/stock_data/gui/main_window.py:10505`–`10767`.
- **Smallest possible patch:** Put the user outcome first, move contract tokens into technical details, and hide/overlay plots until a validated series exists.

### UX-11 — Watchlist dialogs are only partially localized

- **Layer:** Localization / consistency
- **Severity:** Low
- **Surface + viewport + panes:** 관심종목; create and rename dialogs
- **Persona:** Korean-primary user
- **Reproduce:**
  1. Open Watchlist.
  2. Start creating or renaming a list.
- **Observed:** Prompt copy is Korean but actions are `OK` and `Cancel`.
- **Expected:** Dialog actions match the Korean application language.
- **Evidence:** `evidence/Watchlist_create_cancel.png`, `evidence/Watchlist_create_keyboard_cancel.png`, `evidence/Watchlist_followup_cancel.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:10107` and `src/stock_data/gui/main_window.py:10115`.
- **Smallest possible patch:** Replace the native convenience dialog with a localized dialog/button box or supply translated button text.

### UX-12 — Dashboard fixed-height cards clip expanded text

- **Layer:** Text reflow / readability
- **Severity:** Medium
- **Surface + viewport + panes:** Dashboard; 2560×1440 long-content and large-font states
- **Persona:** A user reading a translated, detailed, or unusually long market explanation
- **Reproduce:**
  1. Open Dashboard at 2560×1440.
  2. Replace a market-card title/body with the retained long Korean/English stress string.
  3. Observe the first card without changing the viewport.
- **Observed:** Long copy is visibly cut inside the fixed-height card even at the largest display.
- **Expected:** The card grows, wraps, or exposes a details action without losing text.
- **Evidence:** `evidence/Dashboard_long_content.png`, `evidence/Dashboard_largefont_2560x1440.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:5535`–`5549`.
- **Smallest possible patch:** Remove the fixed three-line body height and let the card use content-based height or an explicit expandable summary.

### UX-13 — Equity and US ETF long result feedback is cut off

- **Layer:** Text reflow / error feedback
- **Severity:** Medium
- **Surface + viewport + panes:** 종목 차트 and 미국 ETF; long-content stress
- **Persona:** A user needing the full reason a local identity could not be resolved
- **Reproduce:**
  1. Open either chart page.
  2. Render the retained long Korean/English feedback string.
  3. Read the amber result banner.
- **Observed:** The one-line feedback banner cuts the message with no disclosure or reliable full-text path.
- **Expected:** Failure text wraps to its full height or offers a technical-details expansion.
- **Evidence:** `evidence/Equity_long_content.png`, `evidence/US_ETF_long_content.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:9014`–`9019` and wrapped-label fitting at `src/stock_data/gui/main_window.py:9085`–`9101`.
- **Smallest possible patch:** Include `search_feedback` in the wrapped-label height fitting pass.

### UX-14 — Account populated charts lose legend and date meaning

- **Layer:** Data visualization / readability
- **Severity:** Medium
- **Surface + viewport + panes:** 계좌·보유; synthetic populated 2560×1440 state
- **Persona:** A user comparing holdings and account value history
- **Reproduce:**
  1. Render the retained synthetic multi-holding Account state.
  2. Inspect allocation legend labels, pie labels, and the history x-axis.
- **Observed:** Multiple legend labels collapse to repeated `Synthetic Holding ...`; pie labels crowd one another; date labels use ellipses and do not communicate time.
- **Expected:** Distinct holding names and readable dates remain available at the maximum display.
- **Evidence:** `evidence/Account_2560x1440_before.png`, `evidence/Completion_lifecycle_populated_current.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:2871`–`3029`.
- **Smallest possible patch:** Truncate legend labels with unique suffix/tooltips, limit direct pie labels, and use a sparse formatted date axis.

### UX-15 — Research empty canvas offers only a keyboard shortcut

- **Layer:** Onboarding / empty state
- **Severity:** Medium
- **Surface + viewport + panes:** Research Workspace; `core_chart`, `minimal_chart`, and `research_rail` at all sizes
- **Persona:** A first-time pointer user trying to begin research
- **Reproduce:**
  1. Open Research Workspace with no exact instrument selected.
  2. Switch among the three compact presets.
  3. Look for a visible action that selects an instrument.
- **Observed:** Large empty canvases say only `Ctrl+K로 정확한 종목을 선택하세요`; the prominent refresh button is unrelated to exact selection.
- **Expected:** The empty canvas includes a visible `종목 선택` action while retaining Ctrl+K as a shortcut.
- **Evidence:** `evidence/Research_panes_minimal_chart_2560x1440.png`, `evidence/Research_panes_core_chart_1920x1080.png`, `evidence/Research_panes_research_rail_1600x900.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:12430`, `src/stock_data/gui/main_window.py:12444`, and `src/stock_data/gui/main_window.py:12481`.
- **Smallest possible patch:** Add one shared `종목 선택` button to the empty chart/table state that opens the existing global switcher.

### UX-16 — Backtest loses page context after interaction

- **Layer:** Wayfinding / scroll position
- **Severity:** Medium
- **Surface + viewport + panes:** Backtest; post-action states at all sizes
- **Persona:** A user returning from bundle validation and trying to understand where they are
- **Reproduce:**
  1. Open Backtest and note the page title.
  2. Use the retained safe validation/reload path.
  3. Observe the resulting scroll position.
- **Observed:** The post-action captures begin at the action row; the `BACKTEST / SIGNAL REPLAY` title has scrolled above the viewport, weakening page context.
- **Expected:** Focus/scroll returns to a meaningful result status while the page identity remains available.
- **Evidence:** `evidence/Backtest_2560x1440_before.png`, `evidence/Backtest_2560x1440_after.png`, `evidence/Backtest_1280x720.png`.
- **Suspected code location:** `src/stock_data/gui/main_window.py:10138`–`10179`.
- **Smallest possible patch:** Preserve the scroll value during local validation or move focus to an in-view result heading without scrolling the page title away.

## Positive results

- No Qt warning/error appeared in the final or follow-up run.
- The Index Graph keyboard issue from the first measurement was proven false after explicitly reactivating the main window; all 21 controls are reachable.
- Direct focus acquisition succeeded on every audited page-local control.
- All long-content stress states restored cleanly; Watchlist and Data Status remained scrollable under 1000 rows.
- Research `core_chart`, `minimal_chart`, and `research_rail` presets did not collapse at any tested size.
- Account, Watchlist, Data Status, and Backtest retain clear local/offline safety language; no broker action exists in the audited workflow.

## Roadmap

### Now — release-blocking layout and recovery

1. Implement responsive Dashboard/Index indicator controls.
2. Stack/wrap Net Worth header actions and keep reload visible.
3. Add Research vertical recovery plus a compact 1280 preset.
4. Replace shared raw provenance output with a friendly formatter and add a direct Research recovery route.

### Next — accessibility and safety hierarchy

1. Repair the Account local Tab chain.
2. Name the Data Status lifecycle table.
3. Separate safe read actions from add/edit/delete actions and danger-style destructive controls.
4. Reserve Index plot margin for the current-price pill.
5. Re-run keyboard-only and 125% large-font tests at all five display sizes.

### Later — comprehension and polish

1. Make Equity/ETF examples catalog-aware.
2. Simplify Backtest hierarchy and empty plots.
3. Repair long-copy reflow, Account legends/date axes, Research pointer onboarding, Backtest scroll return, and Watchlist dialog localization.

## Independent review and corrections

Three fresh reviewers inspected disjoint screenshot sets: `reviews/market_core.md`, `reviews/research_data.md`, and `reviews/portfolio_lab.md`. One early reviewer classified the Index focus chain as broken from the first ledger. The authoritative follow-up, which explicitly reactivated the main window, proved 21/21 Tab reachability and the review was corrected before this report. Visual proof also overrode an automated false negative that reported no Net Worth overflow. The fresh audit-the-audit pass classified the 10-item draft as KEEP 9 / GENERIC 0 / DUPLICATE 1 / WRONG 0. Its requested split/merge and five omitted reviewer findings produced the final 16-item set; a second fresh classification is recorded in `CRITIQUE.md`.

## Hold this while fixing

Preserve the provider-free/read-only boundary, identifier-free account rendering, disabled trading/scheduler behavior, canonical local-data provenance, explicit unavailable states, and fresh focus/performance evidence. Do not “fix” empty states by silently calling providers or by hiding the reason data is unavailable. Fix layout, language, focus order, and recovery affordances while retaining the current safety contracts.

## Hold this in your hands

At 2560×1440 the application already feels like a serious, unusually transparent personal workstation: it shows uncertainty, keeps trading disabled, and exposes where evidence came from. I would want to keep using that object. I would not yet want to live with it every morning on a laptop, because its densest tools stop behaving like crafted controls and begin behaving like a compressed engineering console. The right product direction is therefore preservation, not reinvention: keep the safety and evidentiary character, but make the responsive hierarchy, recovery paths, and plain-language states feel deliberate at every supported size.

## Audit artifacts

- `INTERACTION_MANIFEST.md` — completed interaction and stress coverage.
- `ledger.json`, `inventory.json`, `stress.json`, `followup.json`, `supplemental.json` — machine evidence.
- `reviews/market_core.md`, `reviews/research_data.md`, `reviews/portfolio_lab.md` — independent visual reviews.
- `evidence/` — 154 retained images.
