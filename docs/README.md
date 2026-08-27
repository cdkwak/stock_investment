# Documentation Router

This directory is organized for bounded agent reading. The default route is:

```text
AGENTS.md
  -> project/PROJECT_STATUS.md
  -> exactly one domain Status
  -> exactly one selected contract, checkpoint, research item, or operation
```

Do not scan all documentation at startup.

## Domain entry points

| Task | Read next |
|---|---|
| Cross-project status or priority | [Project Status](project/PROJECT_STATUS.md) |
| Data collection, refresh, sources, accounts, or promotion | [Data Status](data/DATA_STATUS.md) |
| GUI, Dashboard, Telegram summary, or display freshness | [GUI Status](gui/GUI_STATUS.md) |
| Offline Backtest, model, portfolio, or local simulation | [Backtest Status](backtest/BACKTEST_STATUS.md) |
| Scheduler inventory or consolidation | [Scheduler Status](project/SCHEDULER_STATUS.md) after Project Status |
| Scheduler task, lane, and dataset relationship | [Scheduler Data Map](data/SCHEDULER_DATA_MAP.md) after Data Status |
| File location or ownership | [Repository Map](project/REPOSITORY_MAP.md) |
| Architecture or long-term sequencing | [Project Roadmap](project/PROJECT_ROADMAP.md) only after current Status |

## Document roles

| Role | Answers | Default reading? |
|---|---|---|
| Status | What is true now, blocked, and next? | Yes, one routed domain only |
| Contract | What schema, identity, meaning, and invariants hold? | Only when selected |
| Checkpoint/state | Where did an operation stop and what may resume? | Only for that operation |
| Operation/runbook | How does a current authorized operation run? | Only when selected by Status |
| Research | What semantic, PIT, source, or finality question is unresolved? | Only for that question |
| Archive | What happened historically? | No; evidence only |

## Directory layout

```text
docs/
|-- project/    compact cross-project controls and routing
|-- data/       Data Status, indexes, active research, sources, and reusable operations
|-- gui/        GUI Status and presentation-owned contracts/maps
|-- backtest/   Backtest Status and offline research contracts/runbook
`-- archive/    completed, superseded, and pre-compaction historical evidence
```

Current Status files stay compact and replace stale facts. Detailed test
receipts, one-shot windows, completed pilots, and superseded procedures belong
in the [Documentation Archive](archive/README.md). Archive content never overrides a current Status, Contract,
checkpoint, or selected operation.

For cleanup or deletion, consult [Repository Map](project/REPOSITORY_MAP.md)
and the dated repository-usage audit linked there, then verify current code,
test, script, and active-document references.
