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

- Data dashboard refreshed at `572b37f`; current coverage is in
  [Data status](DATA_STATUS.md).
- Stock-issuance history is now a 152,676-row, zero-duplicate source-observation
  artifact for reference dates 2020-07-14..2026-08-12. Publication timing and
  canonical event identity remain blocked for predictive use.
- A single post-cooldown KRX Investor sentinel returned HTTP 200 with retry 0, proving
  access recovery for that scope, but again returned only the positive range-end row.
  Investor historical collection remains stopped on source semantics.
- Free KRX Open API coverage starts in 2010, but logged-in KRX Basic Statistics
  manually returned KOSPI200 futures from 1996-05-06 and options from 1997-07-07.
  Paid KRX/FnGuide sourcing is deferred; bulk-use terms and a bounded collector
  pilot are the remaining free-route gates recorded in the Data dashboard.
- OpenDART now has one retained known-positive combined paid/free-issue row with
  verified economic terms. Original and corrected receipt identities differ, so
  canonical corporate-action identity and adjustment accounting remain blocked.
- The 2026-08-12 equity integration finished offline with all five affected datasets
  through that date, primary-key duplicates zero, no network calls during adoption,
  and focused tests `75 passed, 1 skipped`.
- A post-integration read-only inventory reproduced the same input-tree and inventory
  hashes twice: 42 artifact roots, 51 registered contracts, 38 observed registered
  artifacts, zero unregistered artifacts, and 59 state files.
- The user-provided KRX futures net-purchase CSV history is now a dedicated normalized
  dataset: 33,670 rows across 6,734 dates, 1999-04-26..2026-08-13, with exact
  Landing-to-Normalized audit and no network calls. Broader investor-trading targets
  remain separate.
- KB daily snapshot ingestion is prepared as a one-attempt-per-trading-day 17:00 KST
  append-only task. A successful 2026-08-13 reference run proved access; the Rev1
  E021 sentinel used a different flat OAuth envelope. The official nested envelope
  is now canonical, with the next bounded daily capture still pending.
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
