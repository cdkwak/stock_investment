# Project status

Read this document first at the start of a session. It is the concise routing
page for current project state; detailed evidence remains in domain status
documents, inventories, contracts, runbooks, checkpoints, and ledgers.

## Current focus

- Historical Data is transitioning from core collection to maintenance, bounded
  refreshes, validation, provenance, and high-value gap filling.
- Backtest is the primary active development phase. Its first milestone is an
  API-free, reproducible vertical path over retained local datasets.
- Features support Backtest and must be deterministic and point-in-time safe.
- GUI, realtime account integration, paper trading, and live trading remain
  later phases; they must use stable domain interfaces rather than redesigning
  the core.

## Domain routing

| Need | Read |
|---|---|
| Current data coverage, limits, or collection gates | [Data status](DATA_STATUS.md) |
| Current Backtest scope and gates | [Backtest status](BACKTEST_STATUS.md) |
| Architecture or prioritization decision | [Project roadmap](PROJECT_ROADMAP.md) |
| Detailed Data operations | [Active runbooks](../runbooks/active/) and [deferred runbooks](../runbooks/deferred/) |

`GUI_STATUS.md` does not yet exist because GUI implementation has not started.
Create a domain status document only when that domain begins substantive work.

## Control-document rules

- Only the lead agent may update this file.
- Domain owners may update only their active domain status document.
- Agents read only this file plus the status document for the domain they are
  actively working on. Read the roadmap only for prioritization or architecture
  decisions.
- Status documents describe current state. Replace stale facts instead of
  accumulating chronological logs.
- Keep detailed inventories, contracts, runbooks, checkpoints, ledgers, and
  provider evidence outside `docs/project/`.
- Files under `docs/archive/` and `docs/runbooks/archive/` are historical
  evidence and never active operating instructions.
