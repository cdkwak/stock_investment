# Market core screenshot review

Scope: `Dashboard`, `Index_Graph`, `Equity`, and `US_ETF` only. Reviewed every assigned baseline viewport (2560×1440, 1920×1080, 1600×900, 1366×768, 1280×720), both 2560 before/after states, large-font evidence at 2560×1440 and 1366×768, long-content stress, the common global switcher, Dashboard preferences, Index source/detail, and the later authoritative `followup.json` focus/actionability run. Verdicts below separate defects visible in the evidence from measurement candidates. `followup.json` supersedes the earlier ledger where they conflict. No provider or financial-data correctness assessment was made.

## Top 3

1. **Critical — Dashboard chart controls collapse from 1920 px downward.** Labels lose meaningful characters at 1920/1600, then the chart title and selector header collide at 1366/1280. Large-font 1366 reproduces the same collision. Suspected area: non-wrapping `QHBoxLayout`s for header and indicator controls in `src/stock_data/gui/main_window.py:5597-5638`; the shared `IndicatorControlPanel` is squeezed instead of wrapping/collapsing. Evidence: `Dashboard_1920x1080.png`, `Dashboard_1600x900.png`, `Dashboard_1366x768.png`, `Dashboard_1280x720.png`, `Dashboard_largefont_1366x768.png` under the evidence directory. Duplicate hint: `DUP-INDICATOR-ROW-RESPONSIVE` (related, less severe manifestation on Index Graph).
2. **Critical — Long status copy is visibly clipped on Dashboard, Equity, and US ETF.** Dashboard's first market card clips expanded content inside a fixed-height body; both equity-style pages cut long result feedback to a single amber line. Suspected areas: `src/stock_data/gui/main_window.py:5535-5549` and `:9014-9019`/`:9085-9101`. Evidence: `Dashboard_long_content.png`, `Equity_long_content.png`, `US_ETF_long_content.png`. Duplicate hints: `DUP-FIXED-HEIGHT-STATUS-COPY`, `DUP-INDIVIDUAL-EQUITY-FEEDBACK-HEIGHT`.
3. **Warning — Equity and US ETF first-use guidance contradicts the resulting error state.** Equity advertises `삼성전자 005930` while showing an identification failure for input `005930`; US ETF advertises `SOXX` while showing “no match” for input `SOXX`. Recovery controls exist, but the guided example itself appears broken, damaging trust. Suspected area: guided example wiring at `src/stock_data/gui/main_window.py:8994-9003` plus feedback rendering at `:9014-9019`. Evidence: `Equity_2560x1440_after.png`, `Equity_1920x1080.png`, `US_ETF_2560x1440_after.png`, `US_ETF_1920x1080.png`. Duplicate hint: `DUP-INDIVIDUAL-EQUITY-GUIDED-ERROR` (same `IndividualEquityPage`).

## Per-surface viewport verdicts

Verdict meanings: **PASS** = no visible size-specific collapse; **WARN** = usable but degraded or a non-size-specific UX/accessibility defect remains; **FAIL** = visible clipping/collision or a core operability failure.

| Surface | Display | Verdict | Visual result and exact evidence |
|---|---:|---|---|
| Dashboard | 2560×1440 | WARN | Baseline and expanded indicator row fit, but information is extremely dense and multiple card/body labels remain micro-sized; long-content stress visibly clips the first KOSPI card. `evidence/Dashboard_2560x1440_before.png`, `evidence/Dashboard_2560x1440_after.png`, `evidence/Dashboard_largefont_2560x1440.png`, `evidence/Dashboard_long_content.png` |
| Dashboard | 1920×1080 | FAIL | Expanded indicator captions are already truncated (`MA20/60/120`, volume/EMA/BB labels lose characters); vertical scrolling is available, so lower content is not classified as lost. `evidence/Dashboard_1920x1080.png` |
| Dashboard | 1600×900 | FAIL | Indicator controls are compressed into ambiguous fragments across the row; the plot/insight split remains intact. `evidence/Dashboard_1600x900.png` |
| Dashboard | 1366×768 | FAIL | `KOSPI 차트 · 불러오는 중…` collides with `시장/지수`; nearly every indicator label is reduced to one or two characters. Large font reproduces the failure. `evidence/Dashboard_1366x768.png`, `evidence/Dashboard_largefont_1366x768.png` |
| Dashboard | 1280×720 | FAIL | Same collision becomes worse; title and controls are clipped while a large empty plot consumes the first viewport. `evidence/Dashboard_1280x720.png` |
| Index_Graph | 2560×1440 | PASS | Controls and chart fit; before/after measurement state is visible. The authoritative follow-up reaches all 21 page controls by direct focus and Tab, and `Index_Graph_focus.png` shows a visible focus outline on MA5. `evidence/Index_Graph_2560x1440_before.png`, `evidence/Index_Graph_2560x1440_after.png`, `evidence/Index_Graph_largefont_2560x1440.png`, `evidence/Index_Graph_focus.png` |
| Index_Graph | 1920×1080 | PASS | No visible layout collapse; chart and status line remain readable. Authoritative focus coverage is complete. `evidence/Index_Graph_1920x1080.png` |
| Index_Graph | 1600×900 | FAIL | The right-edge current-price pill is clipped by the viewport. `evidence/Index_Graph_1600x900.png` |
| Index_Graph | 1366×768 | FAIL | Indicator captions truncate (`MA120`, `EMA20`, `BB(20,2)`, RSI/OBV/BB-width), and the right-edge price pill is clipped. Large-font evidence increases chart labels but preserves the clipping. `evidence/Index_Graph_1366x768.png`, `evidence/Index_Graph_largefont_1366x768.png` |
| Index_Graph | 1280×720 | FAIL | Same control-label and price-pill clipping; summary wraps safely at the bottom. `evidence/Index_Graph_1280x720.png` |
| Equity | 2560×1440 | WARN | Geometry is stable before/after and under large font, but the failed search for the advertised example is contradictory; long feedback is forced into a one-line banner and visibly cut off. `evidence/Equity_2560x1440_before.png`, `evidence/Equity_2560x1440_after.png`, `evidence/Equity_largefont_2560x1440.png`, `evidence/Equity_long_content.png` |
| Equity | 1920×1080 | WARN | No clipping; error banner, search controls, empty-state guidance, and rail remain readable. The example/error contradiction remains. `evidence/Equity_1920x1080.png` |
| Equity | 1600×900 | WARN | Stable two-column layout; no visible size collapse. The shared first-use defect remains. `evidence/Equity_1600x900.png` |
| Equity | 1366×768 | WARN | Instruction text wraps safely; rail actions remain visible and large font remains geometrically stable. The shared first-use defect remains. `evidence/Equity_1366x768.png`, `evidence/Equity_largefont_1366x768.png` |
| Equity | 1280×720 | WARN | Narrowest composition remains usable without overlap or clipped actions. The shared first-use defect remains. `evidence/Equity_1280x720.png` |
| US_ETF | 2560×1440 | WARN | Geometry is stable before/after and under large font, but the advertised `SOXX` example and simultaneous “no match” state conflict; long feedback is visibly cut off. `evidence/US_ETF_2560x1440_before.png`, `evidence/US_ETF_2560x1440_after.png`, `evidence/US_ETF_largefont_2560x1440.png`, `evidence/US_ETF_long_content.png` |
| US_ETF | 1920×1080 | WARN | No visible size collapse; the example/error contradiction remains. `evidence/US_ETF_1920x1080.png` |
| US_ETF | 1600×900 | WARN | Stable two-column layout with readable controls and rail actions. The shared first-use defect remains. `evidence/US_ETF_1600x900.png` |
| US_ETF | 1366×768 | WARN | Body copy wraps safely and large font remains stable. The shared first-use defect remains. `evidence/US_ETF_1366x768.png`, `evidence/US_ETF_largefont_1366x768.png` |
| US_ETF | 1280×720 | WARN | Narrowest composition remains usable without overlap. The shared first-use defect remains. `evidence/US_ETF_1280x720.png` |

All `evidence/...` paths in this table are relative to `artifacts/gui_audits/20260830_qt_ux_exhaustive/`.

## Confirmed defects

### MC-01 — Dashboard responsive control collapse

- **Severity/category:** Critical — operability and recognition failure.
- **Confirmed visual defect:** At 1920 px the indicator names already lose characters. At 1366/1280 the title and `시장/지수` group occupy the same horizontal space and labels become ambiguous single-character controls. This is layout collapse, not merely a measurement candidate.
- **Large-font:** `Dashboard_largefont_1366x768.png` reproduces the collision and truncation.
- **Suspected code:** fixed single-row header and indicator `QHBoxLayout`s at `src/stock_data/gui/main_window.py:5597-5638`; use responsive row wrapping, a collapsible disclosure, or minimum-size-aware secondary menu.
- **Evidence:** `evidence/Dashboard_1920x1080.png`, `evidence/Dashboard_1600x900.png`, `evidence/Dashboard_1366x768.png`, `evidence/Dashboard_1280x720.png`, `evidence/Dashboard_largefont_1366x768.png`.
- **Duplicate hint:** `DUP-INDICATOR-ROW-RESPONSIVE`; Index Graph uses the same dense indicator pattern.

### MC-02 — Dashboard text does not tolerate content expansion or large-font intent

- **Severity/category:** Critical — WCAG reflow/text-scaling risk; Warning for ordinary desktop readability.
- **Confirmed visual defect:** `Dashboard_long_content.png` shows the first market card’s long Korean/English copy clipped inside a fixed-height card. Dense lower cards and metadata remain visually tiny even in 2560 large-font evidence.
- **Suspected code:** `src/stock_data/gui/main_window.py:5535-5549` explicitly sets 10 px styles and a fixed three-line body height. These hard limits prevent large-font and translated/expanded text from reflowing.
- **Evidence:** `evidence/Dashboard_long_content.png`, `evidence/Dashboard_largefont_2560x1440.png`, `evidence/Dashboard_2560x1440_after.png`.
- **Duplicate hint:** `DUP-FIXED-HEIGHT-STATUS-COPY` with MC-06 on the equity feedback banner.

### MC-04 — Index Graph right-edge value and dense indicator row clip at narrower widths

- **Severity/category:** Warning — data-label legibility and control recognition.
- **Confirmed visual defect:** The blue current-price pill runs beyond the right chart boundary at 1600/1366/1280. At 1366/1280 several indicator controls truncate enough to obscure their setting names.
- **Suspected code:** non-wrapping control rows at `src/stock_data/gui/main_window.py:8065-8074`; plot viewport/margin handling at `:8154-8159` does not reserve space for the right-edge label.
- **Evidence:** `evidence/Index_Graph_1600x900.png`, `evidence/Index_Graph_1366x768.png`, `evidence/Index_Graph_1280x720.png`, `evidence/Index_Graph_largefont_1366x768.png`.
- **Duplicate hint:** control-row portion relates to `DUP-INDICATOR-ROW-RESPONSIVE`.

### MC-05 — Equity/US ETF guided examples appear broken in the resulting UI

- **Severity/category:** Warning — first-time-user trust and recovery.
- **Confirmed visual defect:** On both pages, the typed query exactly matches the prominently advertised guided example, while the banner reports failure/no match. The controls offer a path to retry, but repeating the same example is not meaningful recovery.
- **Suspected code:** static guided examples at `src/stock_data/gui/main_window.py:8994-9003` and feedback at `:9014-9019`. If catalog readiness can invalidate examples, the empty state should choose a currently available example or explain that the local catalog is unavailable rather than presenting a canonical no-match.
- **Evidence:** `evidence/Equity_2560x1440_after.png`, `evidence/Equity_1920x1080.png`, `evidence/US_ETF_2560x1440_after.png`, `evidence/US_ETF_1920x1080.png`.
- **Duplicate hint:** `DUP-INDIVIDUAL-EQUITY-GUIDED-ERROR` (shared `IndividualEquityPage`).

### MC-06 — Equity/US ETF long result feedback is clipped

- **Severity/category:** Critical under text expansion/large-font WCAG reflow; Warning in the default state.
- **Confirmed visual defect:** Both long-content screenshots show a long Korean/English status message cut off in a one-line amber banner with no wrap, ellipsis, or expansion. The ledger independently measures the feedback `QLabel` as 54 px needed vs 27 px actual.
- **Suspected code:** `search_feedback` is word-wrapped at `src/stock_data/gui/main_window.py:9014-9019` but omitted from the wrapped-label height fitting list at `:9085-9101`.
- **Evidence:** `evidence/Equity_long_content.png`, `evidence/US_ETF_long_content.png`.
- **Duplicate hint:** `DUP-INDIVIDUAL-EQUITY-FEEDBACK-HEIGHT`; shared class, same defect.

### MC-08 — Index source/detail dialog exposes raw implementation tokens

- **Severity/category:** Warning — first-time comprehension and hierarchy.
- **Confirmed visual defect:** The dialog is a flat block of `key=value` tokens (`dataset`, `identity`, `source_session_date`, `expected_as_of`, `freshness`) mixed with prose. It is readable but not plain-language or scan-friendly for a user asking “출처·기준 상세”. The ledger’s `internal_tokens=[]` classification misses what is plainly visible.
- **Suspected code:** detail-string assembly and generic message box at `src/stock_data/gui/main_window.py:8610-8679`.
- **Evidence:** `evidence/Index_Graph_detail.png`.
- **Duplicate hint:** `DUP-RAW-DETAIL-TOKENS` if other source/detail dialogs use the same key/value pattern.

## Empty, error, recovery, hierarchy, and focus notes

- **Dashboard:** Empty/unavailable states do have a visible `로컬 새로고침` control and a status banner. Hierarchy is weakened by ten equal-weight top cards, a very large empty plot, and micro-text status panels. `Dashboard_preferences.png` is operable, but the right list uses both vertical and horizontal scrollbars and long section labels are partially hidden; this is a **Warning**, not collapse.
- **Index_Graph:** Loaded hierarchy is clear (controls → summary → chart → volume → status). The authoritative follow-up directly focuses and Tabs to all 21 page controls with no misses. `Index_Graph_long_content.png` wraps safely. `Index_Graph_detail.png` needs a friendly field hierarchy.
- **Equity/US_ETF:** Search field, Search button, result selector, chart action, rail, and two guided empty-state actions remain visible at every size. The very large blank canvas is an **Opportunity** to center/constrain the empty state and explain what will appear next, not a confirmed layout defect. The failure banner plus still-visible guided example is the actual first-use defect.
- **Visible focus cues:** Dashboard after evidence shows a focus outline on an indicator checkbox; `Index_Graph_focus.png` visibly outlines MA5 and the authoritative follow-up reports `direct_focus_failures=[]`, `tab_missing=[]`, and `tab_reached_page_controls=21`. Equity and US ETF follow-up rows likewise report no focus misses across their 11 actionable controls; their stills do not independently establish every widget's focus-ring styling.
- **Global switcher:** In all four `*_global_switcher.png` stills, the query field has a visible focus cue but the results area is blank and the footer still reads the generic pre-search instruction. Because a still cannot establish whether asynchronous rendering had completed, this is a measurement candidate (MC-C02 below), not a confirmed no-results/hang defect.

## Measurement candidates / non-defects

### MC-C01 — Dashboard automated clipping candidates need targeted crops

The Dashboard ledger flags several lower-card labels as vertically short even at 2560 (for example Basis and option P/C captions). Some are below the first viewport or too small to prove character loss in the supplied full-screen stills. Keep as targeted-crop/large-font measurement candidates. Do not merge them into MC-01 unless a close-up confirms lost user-visible text.

### MC-C02 — Global switcher status timing

All four common switcher stills show a typed query, blank results, and the generic `승인된 로컬 식별정보만 검색합니다.` footer rather than pending/no-match/results feedback: `evidence/Dashboard_global_switcher.png`, `evidence/Index_Graph_global_switcher.png`, `evidence/Equity_global_switcher.png`, `evidence/US_ETF_global_switcher.png`. The code has explicit pending/no-match render text at `src/stock_data/gui/main_window.py:12326-12353`, so measure time-to-status and capture a post-render still before filing. Duplicate hint: `DUP-GLOBAL-SWITCHER-ASYNC-STATE` across every surface.

### MC-C03 — Equity/US ETF ledger recovery heuristic is a false positive

The matching ledger rows say `has_visible_recovery_control=false`, but the screenshots visibly provide the search field, `검색`, guided example, and `직접 검색어 입력`. Do not file “no recovery control” from the ledger alone. The valid issue is MC-05: the offered guided example appears to fail.

### MC-C04 — Internal QLineEdit clear-button accessibility candidate (MC-07 reclassified)

The earlier ledger exposed a generated 30×26 `QToolButton` for the visible `×` inside each search field and treated it as an unnamed action. The authoritative follow-up filters internal widgets, reports `unnamed_controls=[]`, and reaches all 11 actionable Equity/US ETF controls by direct focus and Tab. The screenshot plus `QLineEdit.setClearButtonEnabled(True)` at `src/stock_data/gui/main_window.py:8729-8737` prove only that Qt renders an internal clear affordance, not that a user-facing actionable control is missing. Keep as a screen-reader/platform-behavior candidate requiring a targeted assistive-technology check; do not count it as a confirmed defect or per-size warning. Duplicate hint: `DUP-QT-LINEEDIT-CLEAR-A11Y-CANDIDATE`.

### MC-C05 — Large-font geometry passes on the shared equity pages

`Equity_largefont_2560x1440.png`, `Equity_largefont_1366x768.png`, `US_ETF_largefont_2560x1440.png`, and `US_ETF_largefont_1366x768.png` show no new overlap or action loss. Do not generalize Dashboard/Index large-font failures to `IndividualEquityPage`.

### MC-C06 — Retracted MC-03: Index focus was a legacy measurement artifact

The earlier ledger claimed all Index page controls were missing, but the later authoritative `followup.json` supersedes it: `direct_focus_failures=[]`, `tab_missing=[]`, and `tab_reached_page_controls=21`. `evidence/Index_Graph_focus.png` also visibly outlines MA5. Remove the former Critical finding and close duplicate hint `DUP-INDEX-FOCUS-CHAIN`; there is no confirmed Index keyboard-access failure in this audit.
