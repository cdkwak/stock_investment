# Stock Investment Rev1 — Qt GUI UX First Wave

> Verdict: **INCOMPLETE, with five actionable findings.** Five primary surfaces were exercised with native Qt input, but the full exhaustive audit contract is not satisfied because Orca computer-use remained unavailable and Account/Backtest keyboard focus was not proven. This is a completed first-wave product audit, not a claim that the whole GUI passed.

- Persona lock: `시간 제약이 큰 개인 투자자` (`PERSONA.md`)
- Audited revision: `19503fa65bf91b45a2e363f86489c1b1d4893401`
- Target: native PySide6 desktop application
- Baseline: Windows, 1600×900, Korean, keyboard + mouse
- Constrained size: 1366×768
- Scope: Dashboard, Research Workspace, Data Status, Account/Net Worth, Backtest
- Safety fence: read-only account inspection; no orders, transfers, account changes, provider refreshes, live scheduler activation, or protected-data access
- Evidence: `ledger.json`, `INTERACTION_MANIFEST.md`, and 16 screenshots under `evidence/`

## Executive summary

The application is technically much healthier than its product hierarchy suggests: all five pages stayed open at both sizes, native interactions returned, Qt emitted zero messages, background workers drained, and the window closed cleanly. The main weakness is not raw stability. It is that important user workflows still expose internal engineering language, developer-only controls, and unavailable-data detail at the same visual level as the user's next safe action.

The highest-impact first wave is therefore:

1. isolate Account deletion controls from read-only inspection;
2. give Research and Dashboard one plain-language recovery action;
3. repair the constrained Research rail and Account legends;
4. separate Backtest user results from development tools;
5. normalize and group the mixed-language navigation.

## Interaction evidence

| Surface | Actual interactions | Result | Important observation |
|---|---|---|---|
| Dashboard | Changed nested market tab; opened and explicitly closed screen preferences | Returned normally | No single recovery action when most market cards are unavailable |
| Research Workspace | Typed and verified `005930`; toggled both panes; reread local candidates | Returned normally; button re-enabled | Internal error codes and dataset identifiers dominate the main content |
| Data Status | Typed and verified `CURRENT`; reset filters; selected a row; reread local lifecycle | Returned normally | Clearer than the other pages; “problems first” framing works |
| Account / Net Worth | Visited both nested tabs; toggled privacy in memory and restored it | Restored before capture | Read-only promise conflicts with nearby destructive data-management controls |
| Backtest | Reread validated local bundle; toggled details panel | State changed | Developer laboratory controls and user-facing results share one hierarchy |

Coverage details are in `INTERACTION_MANIFEST.md`. Strict manifest completion is 2/5 because three pages contain no safe text field; keyboard focus was not proven on Account and Backtest.

## Findings

### M2 — Research and Dashboard make recovery cognitively expensive

**Observed.** The primary Research surface displays `LOCAL_CANDIDATE_INPUT_MISSING`, `recovery=Data`, `kr_equity_price_daily`, and `kr_equity_canonical_universe_daily`. It does contain a recovery sentence, but at 1366×768 that sentence and the diagnostics wrap into several lines while the `출처·상태` rail becomes too narrow. Dashboard expresses the same underlying problem differently: ten equal-weight `확인 필요` cards, a blank numeric plot under `KOSPI 차트 · 불러오는 중…`, and repeated `표시 불가` detail without one dominant recovery action.

**Why it matters.** The recovery route exists, but the user must extract it from implementation detail and repeated unavailable-state signals. A direct action is more efficient than asking the user to translate storage contracts.

**Reproduce.** Open Research Workspace with the validated local fixture, type `005930`, and use `현재 후보 다시 읽기`. Compare both Research screenshots. Then open Dashboard and compare `Dashboard_1600_after.png` and `Dashboard_1366x768.png`.

**Smallest useful correction.** Add a typed `user_summary` and `recovery_label` to the Research service/view-model result. Show one sentence plus one recovery button in the main panel; move raw codes and dataset IDs into `진단 상세`. On Dashboard, replace the blank plot with one unavailable overlay and a `데이터 상태에서 원인 확인` action; collapse repeated unavailable cards into a count/summary.

**Suspected code.** `src/stock_data/gui/services.py:132` and `src/stock_data/gui/main_window.py:5430`, `:5449`, `:5600`, `:5615`, and `:12375`.

### M3 — Backtest gives development controls the same hierarchy as investor results

**Observed.** `BACKTEST / SIGNAL REPLAY`, `DEVELOPMENT ONLY`, `NEXT-OPEN LEDGER`, `MATCHED-HOLD DIFFERENCE`, and `CLOSE-PROXY ... NOT EXECUTABLE` appear alongside enabled-looking `오프라인 실행`, `검증 번들 새로 읽기`, and `정확한 번들 내보내기`. The page also reports that bundle revalidation failed while preserving the previous result, but does not give a plain next step.

**Why it matters.** Existing labels such as `DEVELOPMENT ONLY`, `NOT EXECUTABLE`, and `추천 아님` do provide caution. The remaining problem is hierarchy: warning labels, user results, execution controls, and failure recovery compete at the same level.

**Reproduce.** Open Backtest, choose `검증 번들 새로 읽기`, then toggle the details area. Observe the unchanged empty chart and mixed production/development hierarchy in both Backtest screenshots.

**Smallest useful correction.** Make `검증 결과 불러오기` the single primary action. Put run/export and fixed RSI scenario controls inside a collapsed `개발 도구` section that is disabled until typed development input exists. Translate the four English metric titles into Korean user concepts and retain technical labels only in details.

**Suspected code.** `src/stock_data/gui/main_window.py:10180`, `:10190`, `:10214`, `:10221`, `:10231`, `:10240`, `:10277`, and `:10401`.

### M1 — Account's read-only promise conflicts with adjacent deletion actions

**Observed.** The page correctly states that it reads validated local snapshots and does not order or transfer. A small `개인 데이터 관리` label is present, but `선택 수동계좌 삭제` and `계좌 스냅샷·가치 이력 전체 삭제` still use the same neutral visual treatment near `로컬 새로 읽기`.

**Why it matters.** Safe inspection and destructive local-data management should not look equivalent. This audit did not activate deletion or test its confirmation, so it establishes a trust/affordance defect rather than a proven data-loss path.

**Reproduce.** Open `계좌·순자산` → `계좌·보유` and compare the reload and deletion controls in `Account_Net_Worth_1600_after.png`.

**Smallest useful correction.** Strengthen the existing `개인 데이터 관리` separation with a danger style or overflow menu, then verify that confirmation names the affected scope and recovery boundary. Keep local reread in the primary safe group.

**Suspected code.** `src/stock_data/gui/main_window.py:3238`, `:3258`, `:3282`, and `:3298`.

### M4 — Research's side rail and Account legends degrade at constrained width

**Observed.** Research's `출처·상태` rail collapses into narrow wrapped text. Account chart legends truncate repeated synthetic holding names even at 1600×900 and become harder to scan at 1366×768. The ledger reported possible table-header width deficits, but the screenshots do not visibly prove those defects, so they are not published as findings.

**Why it matters.** Narrow diagnostic text and repeated truncated labels make the affected views slower to scan.

**Reproduce.** Compare the Research and Account screenshots at 1600×900 and 1366×768.

**Smallest useful correction.** At constrained width, stack Research source/status below the chart or allow it to collapse. For account charts, show top holdings plus `기타` and reveal complete labels in tooltip/detail rather than truncating every legend entry.

**Suspected code.** `src/stock_data/gui/main_window.py:12375` and the Research/Account table construction near the corresponding page builders.

### L1 — Global navigation has nine equal-weight, mixed-language destinations

**Observed.** The top navigation gives nine pages identical visual weight and mixes Korean labels with `Dashboard`, `Research Workspace`, `Data Status`, and `Backtest`.

**Why it matters.** The labels shift between user tasks and engineering modules. This audit did not run a first-contact task-completion study, so grouping is a coherence opportunity rather than a proven workflow blocker.

**Reproduce.** Inspect the top bar on any 1600×900 evidence image.

**Smallest useful correction.** Normalize visible labels to Korean and group destinations by task: 시장, 조사, 자산, 시스템. Preserve keyboard shortcuts and current page objects so this can begin as a navigation-only change.

**Suspected code.** `src/stock_data/gui/main_window.py:13196` through `:13212`.

## Positive patterns to preserve

- Data Status says that problems are shown first and a normal provider publication wait is not an error.
- Account explicitly states its read-only boundary and separates net worth from brokerage balances.
- Research gives an exact-symbol shortcut and an explicit unavailable-chart explanation.
- Dashboard preferences says it changes only display/order and offers an explicit Close action.
- Both tested sizes remained usable through wrapping or scrolling; no full-page layout collapse was observed.
- Native interaction produced zero Qt warnings/messages, no lingering managed worker, and a clean close.

## Validation and limitations

- Native interaction harness: **PASS** — 5 surfaces, 12 safe actions, two verified text entries, 16 screenshots, zero Qt messages, clean close.
- Focused pytest rerun: **INCONCLUSIVE** — one case passed and six cases failed during fixture setup because Windows denied access to the task-local pytest temp directory. A second bounded attempt reproduced the same ACL failure; no product assertion failed.
- Orca status: runtime ready on app 1.4.192, but computer-use calls returned `runtime_unavailable`. The audit used native PySide6/QTest input rather than restarting active orchestration.
- No screen-reader pass, native accessibility-tree pass, production-provider/network run, order path, transfer path, account mutation, or live scheduler test was performed.
- Dashboard and Backtest did not execute data-producing actions; the audit remained within the user's safety boundary.

## Recommended implementation order

| Wave | Change | Why first | Acceptance evidence |
|---|---|---|---|
| 1 | M1 Account danger-zone separation | Small trust/safety improvement | Safe reload visibly separated; scoped confirmation is verified |
| 1 | M2 Research recovery copy + Dashboard recovery CTA | Common recovery-state contract | Each affected surface gives one plain next action at both sizes |
| 1 | M4 Research rail + Account legend repair | Directly visible constrained-layout defects | Diagnostic rail remains readable; full legend meaning is available |
| 2 | M3 Backtest user/developer hierarchy | Larger information architecture change | Default page contains no development-only primary control |
| 3 | L1 grouped/normalized navigation | Cross-page coherence change | Visible labels are consistent and destinations are task-grouped |

## Independent self-critique

A fresh reviewer classified the six draft findings as **KEEP 5, GENERIC 0, DUPLICATE 1**. Dashboard's unavailable-state finding was merged into the same recovery-state contract as Research. The reviewer also removed three unsupported High severities, dropped visually unproven table-header clipping claims, and downgraded navigation to an opportunity because first-contact task completion was not tested. The final five findings above reflect that critique.

## Hold-this paragraph

The first wave shows no broad crash or full-page collapse in the five tested surfaces; it does not prove broad UX health. Fix Account danger-zone separation and the shared Research/Dashboard recovery contract before visual restyling. Then repair the visible constrained-width rail/legend defects and separate Backtest development tools. Do not treat the lack of Qt warnings as UX completion, and do not claim full accessibility or keyboard coverage until Account/Backtest focus and the native accessibility tree are exercised.
