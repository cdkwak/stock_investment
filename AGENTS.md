# AGENTS.md

## Project
`Stock Investment Rev1` is the new main project.

`Stock Investment` is legacy/reference only.
- Never modify it.
- Do not import runtime code from it.
- Reuse only verified behavior, API handling, and edge cases.

## Project-control documents
- Read `docs/project/PROJECT_STATUS.md` first at session start.
- Read `docs/project/PROJECT_ROADMAP.md` only for prioritization or architecture decisions.
- Read only the status document for the domain currently being worked on.
- Only the lead agent may update `docs/project/PROJECT_STATUS.md`.
- Domain owners may update their own domain status document.
- Status documents represent current state; replace stale facts instead of appending logs.
- Keep detailed inventories, contracts, runbooks, checkpoints, and ledgers outside `docs/project/`.
- Do not scan deferred runbooks, archived handoffs, or raw provider documents by default.

## Current Scope
Current priority: Data Layer.

Do not implement unless explicitly requested:
- GUI
- Backtest
- Supervised / Reinforcement Learning
- Trading / Order execution

## Data Flow
Provider → Landing → Normalized → Derived → Published

- Landing: lossless source capture
- Normalized: stable source data schema
- Derived: indicators, PCR, features, calculated data
- Published: datasets for Research / ML / GUI

Do not mix these layers.

## Data Sources
Historical:
- KRX / pykrx: Korea
- Yahoo: overseas market
- FRED: macro

Realtime:
- KB Securities

KB Securities is not the primary historical data source.

## Rules
- Define dataset schema/key before implementation.
- Do not guess undocumented fields or units.
- Preserve valid zero/missing/source values.
- Distinguish valid empty responses from API failures.
- Avoid survivorship bias and data leakage.
- Never silently change schemas or storage formats.
- Never overwrite valid data after collection failure.
- Use atomic writes for persistent datasets.
- Keep secrets out of code, logs, and datasets.
- Keep dependencies minimal.
- Prefer small, focused changes.

## Testing
- Add tests for meaningful data logic changes.
- Do not use live API calls in normal unit tests.
- Run relevant tests after changes.
- Report failures and untested areas clearly.

## Uncertainty
If source behavior or schema meaning is unclear:
do not guess; preserve verified behavior and report what needs confirmation.이걸 이포
