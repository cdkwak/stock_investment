# Interaction manifest — Qt GUI exhaustive audit

Audit target: `src/stock_data/gui/main_window.py` in provider-free native Qt mode.

Physical display: 2560×1440, DPR 1.0. The maximized Windows client area measured 2560×1334 (the OS reserved window chrome/taskbar space); the audit still treats this as the requested 2560×1440 display condition.

## Coverage totals

- 10/10 user surfaces.
- 117 visible actionable controls reconciled: 95 executed and verified, 13 disabled by an unmet prerequisite, 9 safety-skipped, 0 unresolved.
- 5 display conditions per surface: 2560×1440, 1920×1080, 1600×900, 1366×768, 1280×720.
- 154 retained evidence images, including before/after, focus, modal/detail, pane, large-font, long-content, table-volume, lifecycle, round-trip, and seasoning states.
- 20 Research pane captures (4 configurations × 5 sizes).
- 20 large-font captures (10 surfaces × 2 sizes at 125%).
- 11/11 scenario rows assessed.
- 0 Qt warnings/errors during the final 438.936-second interaction run; median event gap 1.054 seconds; clean close.
- 10/10 explicit post-action assertions passed in `supplemental.json`; each binds a primary action to its expected visible preservation/change contract.

## Per-surface interaction record

| Surface | Pointer primary | Keyboard primary / focus | Text input | Modal, detail, or pane | 5 sizes | Result |
|---|---|---|---|---|---|---|
| Dashboard | Local reload and indicator controls | 7/7 page controls directly focused; Tab trail complete | Ctrl+K typed `KOSPI` | Preferences opened and closed | Yes | Executed |
| Index Graph | Reload, selectors, measurement controls | 21/21 directly focused; 21/21 reached by Tab | Ctrl+K typed `KOSPI` | Source/detail opened and closed | Yes | Executed; one safety-skipped detached-window action |
| Equity | Search/guided input and rail actions | 11/11 directly focused; Tab trail complete | Query field and Ctrl+K | Detail/empty transition observed | Yes | Executed; unavailable actions recorded disabled/safety-skipped |
| Research Workspace | Candidate refresh and preset/pane controls | 14/14 directly focused; Tab trail complete | Preset name and Ctrl+K | 4 pane configurations | Yes | Executed |
| US ETF | Search/guided input and rail actions | 11/11 directly focused; Tab trail complete | Query field and Ctrl+K | Detail/empty transition observed | Yes | Executed; unavailable actions recorded disabled/safety-skipped |
| Watchlist | Safe list workflow | 4/4 directly focused; Tab trail complete | Create-name field and Ctrl+K | Create dialog cancelled by pointer and keyboard | Yes | Executed; mutations cancelled/safety-skipped |
| Data Status | Filters, reload, reset, disclosures | 7/7 directly focused; Tab trail complete | Filter field and Ctrl+K | Disclosure/detail state | Yes | Executed |
| Account | Local read-only reload/privacy controls | 8/8 directly focused; 7/8 reached by Tab | Ctrl+K | Nested account surface | Yes | Account source selector Tab omission recorded; mutations safety-skipped |
| Net Worth | Privacy/local controls | 4/4 directly focused; Tab trail complete | Ctrl+K | Nested net-worth surface | Yes | Executed; mutations safety-skipped/disabled |
| Backtest | Offline/local bundle actions | 3/3 directly focused; Tab trail complete | Ctrl+K | Validation dialog/state | Yes | Executed; export safety-skipped/disabled |

## Stress matrix

- Research panes: `all_open`, `core_chart`, `research_rail`, `minimal_chart` at all five sizes.
- Large font: every surface at 2560×1440 and 1366×768.
- Tables: every applicable QTableWidget was rendered with 0, 1, 100, and 1000 synthetic rows, row counts verified, then restored.
- Long content: Korean, English, apostrophe, CJK, and Arabic stress content injected into safe local widgets, captured, then restored.
- Scenarios: first contact, interrupted workflow, wrong-turn recovery, returning user, keyboard-only, heavy data, destructive confidence without mutation, second-user role (native single-user N/A), lifecycle position, round trip, and data seasoning.
- Completion details: Equity mid-form text survived A→B→A; Research second visit cost was 1.013× first visit; Account rendered distinct current/stale/empty positions; a Dashboard indicator state survived A→B→A and was restored; Net Worth exposed Day 0/1/7/30 selector horizons; the dynamically visible Account table completed 0/1/100/1000 rows and restored its original row count.
- Repeated performance with budgets fixed before measurement: startup median/p95 1039.98/3185.35 ms (5000/6000 budgets), direct tab 14.47/64.30 ms (250/500), safe action 5.24/7.71 ms (200/500), resize 41.26/51.65 ms (250/500). All pass. The earlier 700 ms paced observations are retained but marked advisory and excluded.
- Native substitutes for web-only checks: Qt message handler instead of browser console; provider injection flag instead of network panel; direct focus/accessibility inventory instead of axe; widget geometry and screenshots instead of DOM layout; startup/tab/action/resize timing instead of web vitals.

## Safety record

The audit did not call providers, access protected data, place or modify orders, transfer funds, mutate an account, activate a scheduler, alter Queue state, publish externally, or write product code. Destructive-looking controls were inspected but not activated. Synthetic account/table fixtures lived only in the audit process and task-local temporary directory.

Machine-readable evidence: `ledger.json`, `inventory.json`, `stress.json`, and `followup.json` in this directory.
