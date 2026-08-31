# Portfolio lab independent screenshot review

Reviewer scope: `Account`, `Net_Worth`, and `Backtest` only. This is a screenshot-led visual review of the five requested standard viewports plus the supplied 125% large-font captures (2560x1440 and 1366x768). No real account/provider data was inspected; the one populated Account capture is explicitly synthetic. No financial, account, queue, scheduler, or product state was changed.

## Evidence standard

- **Visible proof** means the behavior is directly visible in a named PNG. It overrides a contradictory automated candidate.
- **Automated candidate** means `ledger.json` / `inventory.json` reports geometry, focus, contrast, control disposition, or first-time-user heuristics. It is useful corroboration, not visual proof.
- The automated ledger reports `layout: []` at all five sizes for all three surfaces. That candidate is false for Net Worth at 1366x768 and 1280x720: the screenshots visibly clip the action row and show a horizontal scrollbar.
- The ledger reports no missing focus targets and no sub-24px controls. That supports keyboard reachability, but the static captures cannot prove complete focus order, modal escape behavior, or screen-reader output.
- 125% evidence exists only at 2560x1440 and 1366x768. No claim is made about 125% at 1920x1080, 1600x900, or 1280x720.

## Top 3

1. **Critical — Net Worth recovery and destructive controls overflow off-screen at common laptop widths.** At 1366x768 the `이 날짜 스냅샷 삭제` control is clipped and `로컬 새로 읽기` is entirely off-screen; at 1280x720 even more of the row is lost. The horizontal scrollbar makes recovery discoverable only after lateral scrolling. The 125% 1366x768 capture reproduces the same failure. Evidence: `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_1366x768.png`, `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_1280x720.png`, `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_largefont_1366x768.png`. Suspected area: `src/stock_data/gui/main_window.py:4544-4585` (`NetWorthPage` puts title, blank date selector, privacy, create, edit, delete, and reload into one non-wrapping `QHBoxLayout`).
2. **Warning — Account presents a read-only/privacy promise beside equally prominent local mutation and whole-history deletion controls.** `로컬 새로 읽기`, add/update, edit, delete, and `계좌 스냅샷·가치 이력 전체 삭제` all use the same neutral button treatment. The visible page does not provide a danger cue, separation, or consequence summary near the whole-history deletion action. Inventory marks the three state-changing controls `SKIPPED_SAFETY`; code indicates a default-No confirmation, but that dialog is an automated/code candidate and was not screenshot-proved. Evidence: all standard and large-font Account captures, most clearly `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_1366x768.png`. Suspected area: `src/stock_data/gui/main_window.py:3236-3310` and confirmation handler `src/stock_data/gui/main_window.py:14264-14283`.
3. **Warning — Backtest is developer-first and visually renders failure/empty data as a large black chart.** The page foregrounds `DEVELOPMENT ONLY`, `typed development`, `LOW30/HIGH70`, `SIGNAL COVERAGE`, `NEXT-OPEN LEDGER`, `CLOSE-PROXY`, and `NOT_EXECUTABLE_INSTRUMENT`. Its banner says revalidation failed and the last valid result was preserved, while the metric cards say no result and the dominant charts are empty black planes. A first-time user cannot tell whether there is a usable preserved result, what to do next, or whether the chart is broken. Evidence: every Backtest viewport and both large-font captures, e.g. `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_1280x720.png`. Suspected area: `src/stock_data/gui/main_window.py:10163-10300`, with state-render strings around `src/stock_data/gui/main_window.py:10505-10767`.

## Account

### Per-size verdicts

| Evidence | Verdict | Visible result |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_2560x1440_after.png` | Warning, no collapse | All controls and account-status cards fit, but the action cluster is detached at the far right, the empty panel is mostly unused space, destructive controls have neutral styling, and raw recovery tokens (`RUNTIME_CONFIG_REQUIRED`, `ACCOUNT_SNAPSHOT_MISSING`) are visible. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_1920x1080.png` | Warning, no collapse | Same hierarchy problem; all actions remain visible. The page is readable but action density is high and the personalized `아빠 CSV로 계좌 추가·갱신` label is not general-user language. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_1600x900.png` | Warning, no collapse | Three action rows still fit without horizontal clipping. The warning/recovery state is visible, but the button hierarchy remains flat. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_1366x768.png` | Warning, no collapse | All actions and three status cards remain visible. The whole-history deletion action is prominent and visually equivalent to safe actions. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_1280x720.png` | Warning, vertically scrollable | No horizontal collapse; all top actions remain reachable. The last status card reaches the viewport floor and the vertical scrollbar is visible, which is acceptable for content continuation but should remain covered by keyboard tests. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_largefont_2560x1440.png` | Warning, large-font layout holds | No visible text clipping or overlap. The same privacy/destructive hierarchy defect remains. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_largefont_1366x768.png` | Warning, large-font layout holds | No visible text clipping or horizontal overflow at the supplied 125% size. The button density and neutral deletion treatment remain. |

### Detailed checks

- **Privacy/read-only hierarchy — mixed.** The title copy clearly says only verified local read-only snapshots are shown and that there are no order/transfer functions. The `금액 숨김` control is visible and account labels are identifier-free. However, that promise shares the same first screen with add/update/edit/delete controls, so “read-only display” and “local data maintenance” are not visually separated.
- **Destructive confidence — warning.** The whole-history delete button is permanently enabled in the empty/stale state and uses the same styling as refresh and edit. Inventory correctly fenced it as `SKIPPED_SAFETY`. The confirmation handler defaults to No, but the retained screenshots do not prove the dialog wording, focus default, or cancellation path.
- **Legend/data readability — warning with direct populated-state evidence.** In `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_2560x1440_before.png`, the explicitly synthetic allocation legend truncates five labels to repeated `Synthetic Holding ...` forms, and donut labels crowd one another around the chart. The adjacent history chart uses ellipses for dates, so the x-axis does not communicate time. This is a real readability defect even at the largest viewport. Suspected area: `AccountChartsOverview._render_allocation` and `_render_history`, `src/stock_data/gui/main_window.py:2871-3029` (default Qt legend plus always-visible pie labels).
- **Empty/error/recovery — warning.** The amber banner explains that aggregate display is blocked because some accounts are unavailable/stale, and `로컬 새로 읽기` is always visible. The three cards also offer `확인 필요`. But English implementation tokens are exposed as the reason, and the screen does not translate each token into the next user action.
- **Focus cues — visible but incomplete proof.** The standard/large captures show a blue outline or text-selection-like cue on the active top/sub-tab. `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_global_switcher.png` shows a clearly focused input underline and privacy notice. Ledger focus traversal (`missing: []`) is corroborating automation, not proof that every visible action has a distinct focus ring.
- **First-time comprehension — warning.** `아빠 CSV`, mixed Korean/English provider labels, and raw status tokens assume project-specific knowledge. The ledger's `plain_language: true` candidate misses these visible strings.
- **Positive visible proof.** Standard and 125% views do not horizontally collapse; the long-content stress capture wraps safely: `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Account_long_content.png`.

## Net Worth

### Per-size verdicts

| Evidence | Verdict | Visible result |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_2560x1440_after.png` | Warning, no collapse | All actions fit. The blank date selector has no visible label, and the empty panel is overwhelmingly large. The focused `금액 숨김` control has a clear blue cue. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_1920x1080.png` | Warning, no collapse | All actions fit, but the single-row toolbar already dominates the title area and the blank selector is unexplained. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_1600x900.png` | Warning, incipient overflow | All controls are still visible, but a horizontal scrollbar appears at the viewport floor. This is the earliest visible sign that the page's minimum content width exceeds the viewport. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_1366x768.png` | **Critical, collapsed** | The action row is cut off to the right; the delete label is incomplete and `로컬 새로 읽기` is absent. A horizontal scrollbar is required to discover recovery. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_1280x720.png` | **Critical, collapsed** | The clipping worsens; right-side actions are off-screen and the recovery button is hidden. The horizontal scrollbar thumb visibly indicates undisclosed content to the right. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_largefont_2560x1440.png` | Warning, large-font layout holds | No visible clipping at the largest supplied 125% viewport, though the same blank-selector and flat action hierarchy remain. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_largefont_1366x768.png` | **Critical, large-font collapse** | The 125% capture reproduces the clipped toolbar and hidden reload action. |

### Detailed checks

- **Visual collapse/clipping — critical.** This is direct screenshot proof and must not be downgraded because the ledger lists no layout candidates. The toolbar should wrap, move maintenance actions into a secondary menu, or stack the title and controls below a breakpoint.
- **Privacy/read-only hierarchy — mixed.** `금액 숨김` is prominent, the copy says this exact-date local asset/debt view is separate from brokerage Account, and the empty detail says it does not call an external provider. However, the page never clearly names the display as local-only/read-only, and create/edit/delete share the same hierarchy as reload.
- **Destructive confidence — warning.** In the empty screenshots the exact-date edit/delete controls appear disabled, reducing immediate risk. When enabled, the delete action uses the same neutral treatment as non-destructive controls. Code contains a default-No exact-date confirmation; screenshots do not prove that interaction.
- **Legend/data readability — unproven, not passed.** Every retained Net Worth capture is an empty state; no legend, values, time axis, or multi-series chart is available for visual judgment.
- **Empty/error/recovery — critical at narrow widths.** The empty explanation is concise and privacy-aware, but it is visually tiny inside a huge panel and the recovery action is off-screen at 1366/1280. The blank selector can be mistaken for a failed render.
- **Focus cues — partial visible proof.** The focused privacy checkbox has an obvious blue frame at large widths, and the active tabs are visually distinct. Static evidence cannot prove focus after horizontally scrolling to hidden actions. Ledger `missing: []` is only an automated candidate because it does not detect off-screen discoverability.
- **First-time comprehension — warning.** The mixed-language phrase `brokerage Account` and unlabeled blank selector are avoidable ambiguity. A novice has no visible explanation of how to create the first valid snapshot versus merely reload local data.
- **Positive visible proof.** Long text wraps without truncation at 2560: `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Net_Worth_long_content.png`.

## Backtest

### Per-size verdicts

| Evidence | Verdict | Visible result |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_2560x1440_after.png` | Warning, no horizontal collapse | Controls/cards fit and both empty plots are visible, but the page title has scrolled above the viewport after interaction. The black empty plots dominate the page. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_1920x1080.png` | Warning, vertically scrollable | No horizontal clipping. Only part of the first plot is visible without scrolling; recovery is visible at the top. Developer vocabulary dominates. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_1600x900.png` | Warning, vertically scrollable | No horizontal collapse. The chart begins below the fold and the title remains scrolled out in the post-interaction state. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_1366x768.png` | Warning, vertically scrollable | Cards reflow cleanly and text is not clipped. The page still presents five separate “no result” surfaces and a large black empty chart. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_1280x720.png` | Warning, vertically scrollable | No horizontal clipping. The black chart consumes the remaining viewport, while the more technical detail/recovery context lies elsewhere in the scroll. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_largefont_2560x1440.png` | Warning, large-font layout holds | No visible text collision at 125%; the same empty-chart and developer-first hierarchy remain. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_largefont_1366x768.png` | Warning, large-font layout holds | The two-column cards and banners fit without horizontal clipping at 125%. Vertical scrolling remains necessary and expected. |

### Detailed checks

- **Developer-vs-user hierarchy — warning.** Safety boundaries are commendably explicit, but raw contract identifiers are treated as primary labels. Replace or subordinate them with plain-language outcomes (for example, “실거래에 사용할 수 없는 개발용 결과”) and move exact contract tokens into `기술 근거`.
- **Empty/error/recovery — warning and internally inconsistent.** The top amber banner says bundle revalidation failed and the last validation result was preserved; all visible metrics then say there is no execution result. `검증 번들 새로 읽기` is visibly available in every requested viewport, contradicting the ledger's `has_visible_recovery_control: false` candidate. The recovery action needs a one-line consequence (“local bundle only; preserves current result on failure”) and a success/error timestamp.
- **Legend/data readability — unproven for populated data; poor empty treatment is proven.** There is no plotted series or legend in any retained Backtest capture, so populated legend readability cannot be certified. The empty black canvas with axes but no empty-state overlay looks like a rendering failure. Hide the plots or overlay a plain-language empty state until a validated series exists.
- **Focus cues — visible but unusual.** The top `Backtest` tab consistently shows a blue rectangle with text-selection-like highlighting after focus traversal. `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_global_switcher.png` shows a clear focused input underline. The ledger's complete traversal and Escape-close result are automated candidates; screenshots alone do not prove all chart/tool-button focus cues.
- **Wayfinding/scroll recovery — warning.** `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_2560x1440_before.png` visibly contains `BACKTEST / SIGNAL REPLAY`, while every requested post-interaction viewport begins at the action row with that title scrolled away. The repeated before/after difference suggests focus traversal or action handling retained a non-zero `QScrollArea` position. Suspected area: `BacktestPage` QScrollArea setup and focus behavior around `src/stock_data/gui/main_window.py:10138-10179`.
- **Destructive/privacy boundaries — positive visible proof.** There is no broker/account mutation control. `오프라인 실행` and `NOT EXECUTABLE` make the non-trading boundary visible, and export is disabled in this state. This does not establish confirmation behavior for local file export when enabled.
- **First-time comprehension — warning.** The page lacks a plain-language page-level purpose, three-step path, or single primary next action. The automated `internal_tokens: ["NOT EXECUTABLE"]` candidate materially undercounts the many visible internal tokens.
- **Positive visible proof.** Long text wraps within the amber banner without horizontal clipping: `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Backtest_long_content.png`.

## Duplicate hints

- Treat the Net Worth clipping at standard 1366/1280 and large-font 1366 as **one root defect**, not three findings: the single non-wrapping header layout has a minimum width larger than the viewport.
- Treat Account's neutral local-deletion styling across all seven captures as **one cross-size hierarchy defect**. Net Worth's neutral delete styling is a related design-system duplicate, but its toolbar overflow is a separate root issue.
- Treat Backtest's raw developer labels, empty black plots, and missing post-interaction page title as three behaviors repeated across all sizes; do not file one duplicate per viewport.
- Account and Net Worth global-switcher evidence is byte-identical according to the ledger (same SHA-256) and visually identical; one shared global-switcher issue/fix should cover both.
- The long-content screenshots show safe wrapping and no visible clipping on all three assigned surfaces; they should not be converted into false positive findings merely because the stress text is intentionally repetitive.

## Automated candidates still requiring interactive proof

- Full tab order and a persistent, distinguishable focus ring on every enabled action, especially the hidden Net Worth controls after horizontal scrolling.
- Default focus and cancellation behavior of Account whole-history deletion and Net Worth exact-date deletion confirmations.
- Populated Net Worth legend/axis/data readability and populated Backtest legend/series readability.
- Empty-to-success recovery after `로컬 새로 읽기` / `검증 번들 새로 읽기`, including whether focus returns to a meaningful status message.
- Privacy behavior when `금액 숨김` is toggled while populated, including charts, tooltips, accessible descriptions, and table content.

