# Project Status

Read this document first after `AGENTS.md`. Only the lead agent may update it.

## Active task

**Backtest v0 foundation:** define and implement one deterministic, API-free vertical
path from a frozen local dataset through a point-in-time feature, baseline strategy,
simulated execution/accounting, and reproducible result. Implementation has not yet
started; current scope is in [Backtest status](BACKTEST_STATUS.md).

## Next three priorities

1. Freeze the first local input dataset/version and its availability rules.
2. Establish reusable feature, strategy, order, execution, portfolio, and result
   boundaries, then implement the minimal vertical slice.
3. Add reproducibility and no-network tests; expose stable service/result interfaces
   before any GUI implementation begins.

## Blockers

| Area | Current blocker | Effect |
|---|---|---|
| Backtest | Package and minimal domain interfaces do not yet exist | First vertical slice remains unimplemented |
| Data selection | Corporate actions, historical vintages, and some target histories remain incomplete | Select only datasets whose documented limitations fit the first test |
| GUI | Backtest service/result interfaces are not stable | GUI implementation and `GUI_STATUS.md` remain deferred |
| Realtime/trading | KB authorization is blocked; live-account interfaces are not designed | No realtime account, paper, or live execution work |

## Do not run

- No external API or network access from Backtest.
- No KRX Investor retry, KB token retry, or other paused/deferred Data probe without
  new evidence and explicit authorization.
- No GUI business logic, realtime account integration, paper trading, live orders,
  supervised learning, or reinforcement learning in the current milestone.
- Do not treat archived handoffs or runbooks as active instructions.

## Latest validation state

- Data dashboard refactored and committed at `8a4b7ef`; current coverage is in
  [Data status](DATA_STATUS.md).
- The 2026-08-12 equity integration finished offline with all five affected datasets
  through that date, primary-key duplicates zero, no network calls during adoption,
  and focused tests `75 passed, 1 skipped`.
- A post-integration read-only inventory reproduced the same input-tree and inventory
  hashes twice: 42 artifact roots, 51 registered contracts, 38 observed registered
  artifacts, zero unregistered artifacts, and 59 state files.
- Inventory tests: `23 passed, 1 skipped`. Main-worktree Markdown links: 38 checked,
  zero broken; stale pre-consolidation control/runbook paths: zero.

## Routing

| Need | Read |
|---|---|
| Current Data coverage, limits, or collection gates | [Data status](DATA_STATUS.md) |
| Current Backtest scope and gates | [Backtest status](BACKTEST_STATUS.md) |
| Architecture or prioritization decision | [Project roadmap](PROJECT_ROADMAP.md) |
| Actionable Data procedure | [Active runbooks](../runbooks/active/) |

`GUI_STATUS.md` does not exist because GUI implementation has not started. Status
documents describe current state and must not become append-only logs. Archive,
deferred runbooks, raw provider guides, and the full data tree are non-default context.
