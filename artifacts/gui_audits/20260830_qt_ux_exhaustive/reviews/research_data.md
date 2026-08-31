# Independent screenshot review — Research / Watchlist / Data Status

## Scope and verdict rules

- Desktop Qt Widgets application, Korean-primary UI, rectangular desktop displays, keyboard and pointer input.
- Independently inspected with `view_image`: all 66 matching screenshots for `Research_Workspace`, `Watchlist`, `Data_Status`, and every `Research_panes_*` configuration in `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence`, including the later-added focus/follow-up captures.
- Required viewports: 2560×1440, 1920×1080, 1600×900, 1366×768, and 1280×720. Large-font evidence exists at 2560×1440 and 1366×768 for the three named surfaces and was also inspected.
- `PASS` means no screenshot-proven collapse or clipping. `WARN` means the geometry survives, but a language, hierarchy, empty-state, or recovery defect remains. `FAIL` means content is visibly lost or unusable.
- The machine ledger and inventory were used only as supporting signals. Automated candidates are explicitly separated from screenshot proof below.

## Top 3

1. **[Critical] Default/all-open Research Workspace loses source-status text at 1280×720.** The bottom of the rightmost pane is cut off, and the page has no visible vertical recovery mechanism.
2. **[Critical] Research Workspace exposes internal failure tokens and gives no user-operable recovery path.** `LOCAL_CANDIDATE_INPUT_MISSING`, `recovery=Data`, and internal dataset identifiers are rendered verbatim; the only nearby action merely retries the same failed read.
3. **[Warning] Data Status exposes raw fixture/source provenance.** `준비됨 · empty fixture health` appears beside the filter controls across normal and large-font evidence.

## Confirmed visual findings

### RD-01 — [Critical] Research Workspace clips the source/status pane at 1280×720

**Screenshot proof**

- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_1280x720.png`: the final source/status sentence is visibly cut at the bottom edge (`exact typed view가 ...`), with no page scrollbar or alternate disclosure control visible.
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_all_open_1280x720.png`: the same all-open composition reproduces the loss.
- The failure is absent at `Research_Workspace_1366x768.png` and `Research_panes_all_open_1366x768.png`, so the breakpoint is constrained to the narrowest tested viewport.

**Automated corroboration**

- The matching ledger row reports a vertical `QLabel` candidate at 1280×720: needed 216 px, actual 176 px. This is corroboration, not the basis of the finding.

**Suspected code area**

- `src/stock_data/gui/main_window.py:12363` (`ResearchWorkspacePage`). The page is a plain `QWidget`, not a scroll area.
- `src/stock_data/gui/main_window.py:12395` hard-codes a 900×500 page minimum.
- `src/stock_data/gui/main_window.py:12501` gives the OHLCV pane a 420 px minimum width; `src/stock_data/gui/main_window.py:12561` gives every splitter pane a minimum width while the all-open preset keeps five panes visible.
- The likely fix area is the Research page's vertical overflow policy and narrow-breakpoint preset behavior, not the label text itself.

**Duplicate hint**

- `Research_Workspace_1280x720.png` and `Research_panes_all_open_1280x720.png` are two captures of the same underlying default/all-open defect. File one product issue, not two.

### RD-02 — [Critical] Research error and recovery copy leaks implementation details

**Screenshot proof**

- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_2560x1440_after.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_1366x768.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_1280x720.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_1280x720.png`

Every composition shows raw strings such as `LOCAL_CANDIDATE_INPUT_MISSING`, `recovery=Data`, `kr_equity_price_daily`, and `kr_equity_canonical_universe_daily`. The source pane additionally says `exact typed view`, an internal contract term. The message instructs the user to restore internal partitions but provides no direct recovery control; `현재 후보 새로고침` only repeats the failed read.

**Automated corroboration**

- The ledger marks internal tokens `LOCAL_` and `recovery=` and records `has_visible_recovery_control: false` for `Research_Workspace`.

**Suspected code area**

- `src/stock_data/gui/services.py:132` constructs the internal-coded failure detail.
- `src/stock_data/gui/main_window.py:12719` renders `view.unavailable_reason` verbatim.
- `src/stock_data/gui/main_window.py:12535` hard-codes `exact typed view` in the empty source state.

**Recommendation**

- Present a plain-language summary first (what is unavailable and whether existing views remain safe), then one concrete action such as `데이터 상태 열기` or `복구 방법 보기`. Keep dataset IDs and failure codes behind an expandable technical-details control.

**Duplicate hint**

- This is repeated state, not separate per-preset defects. It affects default, all-open, core-chart, minimal-chart, and research-rail captures because they share one candidate/status renderer.
- Raw `source=`, `freshness=`, and similar diagnostics are also rendered elsewhere in `main_window.py`; a shared user-facing provenance formatter may prevent sibling duplicates.

### RD-03 — [Warning] Research first-use state relies on a keyboard shortcut and leaves the primary canvas inert

**Screenshot proof**

- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_2560x1440.png`: the chart occupies most of the screen but offers only `Ctrl+K로 정확한 종목을 선택하세요.` as the next step.
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_core_chart_1920x1080.png`: both the table and chart are empty, while selection remains shortcut-only.
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_1600x900.png`: three large empty panes provide no direct select/open action.

The page explains the shortcut, but recognition is weaker than a visible `종목 선택` action in the empty canvas. `현재 후보 새로고침` is visually prominent but is unrelated to selecting the exact instrument needed to make the chart useful.

**Suspected code area**

- `src/stock_data/gui/main_window.py:12430` and `src/stock_data/gui/main_window.py:12481` define shortcut-only empty-state copy.
- `src/stock_data/gui/main_window.py:12444` places `현재 후보 새로고침` as the only visible action in the candidate block.

**Duplicate hint**

- Treat the same empty-state component across chart-only and chart+OHLCV presets as one onboarding defect.

### RD-04 — [Warning] Data Status renders raw source/test provenance in the main control row

**Screenshot proof**

- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_2560x1440_after.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_1366x768.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_largefont_1366x768.png`

The small label to the right of `초기화` reads `준비됨 · empty fixture health`. This is implementation/test vocabulary and is especially confusing on a page that otherwise claims to prioritize understandable problem states.

**Suspected code area**

- `src/stock_data/gui/main_window.py:11487-11490` concatenates `view.source` directly into `report_state`.

**Duplicate hint**

- The same direct-source rendering pattern appears in other `main_window.py` status surfaces. This review confirms only Data Status screenshots; consider a shared sanitized provenance label.

### RD-05 — [Warning] Watchlist create/rename modal is only partially localized

**Screenshot proof**

- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_create_cancel.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_create_keyboard_cancel.png`
- `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_followup_cancel.png`

The prompt label is Korean (`새 목록 이름`), but its actions are English (`OK`, `Cancel`) inside an otherwise Korean workflow.

**Suspected code area**

- `src/stock_data/gui/main_window.py:10107` and `src/stock_data/gui/main_window.py:10115` use native `QInputDialog.getText`, inheriting untranslated platform button labels.

**Duplicate hint**

- Creation and rename share the same native-dialog mechanism; file one localization issue covering both.

### RD-06 — [Opportunity] Empty states are safe but visually passive

**Screenshot proof**

- Watchlist: `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_1280x720.png` and `Watchlist_2560x1440_after.png` show an actionable sentence, but the rest of the viewport is an empty table with no in-canvas action.
- Data Status: `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_table1_0.png` shows an empty data table; the explanation is relegated to the separate selected-detail strip.

Neither is a collapse or blocking failure. A centered empty-state message plus a directly operable action would improve first-use hierarchy: `종목 찾기` for Watchlist and `필터 초기화`/`전체 데이터 보기` in the empty Data Status table.

**Suspected code area**

- Watchlist notice: `src/stock_data/gui/main_window.py:9967`.
- Data Status table and filters: `src/stock_data/gui/main_window.py:11055-11120`.

## Automated candidates not promoted to screenshot-proven visual findings

1. **Data Status accessibility name:** the ledger reports one enabled `QTableWidget` with no semantic name. The likely widget is `refresh_lifecycle_table` at `src/stock_data/gui/main_window.py:10959`, which has no `setAccessibleName`. This cannot be proven from pixels, so it remains an automated accessibility candidate pending a screen-reader/accessibility-tree check.
2. **Long-content clipping:** ledger `clipping_candidates` arrays are empty for all three named surfaces. Independent screenshot inspection agrees for Watchlist and Data Status; their injected long copy wraps and expands. Research long copy also wraps in `Research_Workspace_long_content.png`. No long-content clipping issue is confirmed beyond RD-01's narrow-viewport source pane.
3. **Row-volume stress:** 1,000-row evidence uses ordinary scrolling and cell elision. No pane collapse is visible in `Research_Workspace_table0_1000.png`, `Research_Workspace_table1_1000.png`, `Watchlist_table0_1000.png`, `Data_Status_table0_1000.png`, or `Data_Status_table1_1000.png`.
4. **Focus captures:** `Research_Workspace_focus.png`, `Watchlist_focus.png`, and `Data_Status_focus.png` add no collapse or clipping. A single screenshot cannot establish a complete logical focus order, so keyboard-focus correctness remains governed by the ledger's interaction trail rather than being claimed from pixels.

## Per-surface and per-configuration viewport verdicts

### Research Workspace — default surface

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_2560x1440_after.png` | WARN | Geometry intact; RD-02/RD-03 present. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_1920x1080.png` | WARN | Geometry intact; technical recovery copy wraps. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_1600x900.png` | WARN | Geometry intact; narrower side panes remain legible. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_1366x768.png` | WARN | No clipping; source text wraps heavily. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_1280x720.png` | **FAIL** | RD-01: source/status content is cut off. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_largefont_2560x1440.png` | WARN | Large-font capture adds no collapse; RD-02 remains. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_Workspace_largefont_1366x768.png` | WARN | Large-font capture remains intact at 1366×768. |

### Research panes — all open

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_all_open_2560x1440.png` | WARN | Five panes intact; RD-02/RD-03 present. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_all_open_1920x1080.png` | WARN | Five panes intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_all_open_1600x900.png` | WARN | Five panes intact; rail text wraps. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_all_open_1366x768.png` | WARN | No clipping. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_all_open_1280x720.png` | **FAIL** | RD-01 duplicate: rightmost source/status content is cut off. |

### Research panes — core chart

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_core_chart_2560x1440.png` | WARN | Chart and OHLCV panes intact; empty hierarchy remains. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_core_chart_1920x1080.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_core_chart_1600x900.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_core_chart_1366x768.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_core_chart_1280x720.png` | WARN | Intact at the narrowest size; shortcut-only empty state. |

### Research panes — minimal chart

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_2560x1440.png` | WARN | No collapse; very large inert canvas. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_1920x1080.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_1600x900.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_1366x768.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_minimal_chart_1280x720.png` | WARN | Intact; RD-02/RD-03 remain. |

### Research panes — research rail

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_2560x1440.png` | WARN | Three panes intact; `exact typed view` leak. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_1920x1080.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_1600x900.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_1366x768.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Research_panes_research_rail_1280x720.png` | WARN | Intact at narrowest size; text wraps without loss. |

### Watchlist

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_2560x1440_after.png` | WARN | No collapse; passive empty-state hierarchy. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_1920x1080.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_1600x900.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_1366x768.png` | WARN | Intact. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_1280x720.png` | WARN | Intact at narrowest size. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_largefont_2560x1440.png` | WARN | No large-font collapse. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Watchlist_largefont_1366x768.png` | WARN | No large-font clipping. |

### Data Status

| Evidence | Verdict | Notes |
|---|---|---|
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_2560x1440_after.png` | WARN | Geometry intact; RD-04 present. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_1920x1080.png` | WARN | All major regions visible; dense hierarchy. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_1600x900.png` | WARN | Vertical scrolling appears; no content loss. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_1366x768.png` | WARN | Vertical scrolling preserves access; lower table is below fold. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_1280x720.png` | WARN | Vertical scrolling preserves access; filters sit at the fold. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_largefont_2560x1440.png` | WARN | No large-font collapse; raw source remains. |
| `artifacts/gui_audits/20260830_qt_ux_exhaustive/evidence/Data_Status_largefont_1366x768.png` | WARN | Scrollable and intact; raw source remains. |

## Coverage conclusion

- **Confirmed hard failures:** 1 unique defect (RD-01), reproduced by 2 screenshots.
- **Confirmed visual/UX findings:** 2 Critical, 3 Warning, 1 Opportunity.
- **Pane configurations:** all-open fails only at 1280×720; core-chart, minimal-chart, and research-rail do not collapse at any tested viewport.
- **Large-font:** no additional clipping or collapse is visible in the supplied large-font evidence.
- **Automated-only unresolved candidate:** 1 missing semantic name in Data Status; it needs non-visual accessibility verification.
