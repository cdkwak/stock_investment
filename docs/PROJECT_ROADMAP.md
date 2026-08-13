# Project roadmap

This repository is evolving into a personal quantitative investment platform.
Keep it as one repository for now, while enforcing domain boundaries that allow
components to be separated later without redesigning the core.

```text
Historical Data -> Features -> Backtest -> GUI
                                      |
Realtime Data -> Account Management -> Paper Trading -> Live Trading Bot
                                      ^
                         reusable strategy logic
```

## Domain boundaries

| Domain | Responsibility | Must not own |
|---|---|---|
| `market_data` | Provider adapters, Landing capture, contracts, normalization, derived/published datasets, lineage, checkpoints, and validation | Strategies, portfolio simulation, orders, or GUI behavior |
| `market_features` | Deterministic, point-in-time-safe feature calculations over versioned market data | Provider calls, execution decisions, or presentation logic |
| `market_backtest` | Offline portfolio simulation, fills, costs, constraints, metrics, and reproducible experiments | External API dependencies or live account state |
| `market_account` | Broker-neutral balances, positions, cash, reconciliation, and account events | Strategy research, market-data normalization, or broker-specific UI logic |
| `market_trading` | Reusable strategy interfaces, signals, risk checks, order intents, simulated execution adapters, and later live broker adapters | GUI rendering or direct dependence on provider response formats |
| `market_gui` | User workflows, visualization, configuration, and orchestration of public application services | Financial calculations, strategy rules, portfolio accounting, or order semantics |

Dependencies should point toward stable domain interfaces. Strategy and risk
logic must be reusable by both simulated and live execution; only the execution
adapter changes. The backtest must run entirely from retained local artifacts
with network access disabled. The GUI remains a thin client over domain services.

## Development sequence

1. **Historical Data — maintenance/gap filling.** The core collection baseline is
   established. Continue bounded refreshes, missing-source work, provenance,
   contracts, and validation without reopening completed ranges unnecessarily.
2. **Features — next foundation.** Add deterministic, leakage-safe features with
   explicit inputs, availability rules, and reproducible outputs.
3. **Backtest — primary active phase.** Build an API-free engine against frozen
   local datasets, then add costs, execution models, portfolio constraints, and
   experiment reporting.
4. **GUI.** Expose research and backtest workflows without moving business logic
   into the presentation layer.
5. **Realtime Data and Account Management.** Add provider adapters behind the same
   data and account interfaces, preserving offline research behavior.
6. **Paper Trading.** Exercise the production strategy, risk, account, and order
   interfaces with simulated execution and full audit trails.
7. **Live Trading Bot.** Add broker execution only after paper-trading invariants,
   reconciliation, failure recovery, and operator controls are proven.

## Persistent references

- [README](../README.md) — repository setup, layout, and supported entry points.
- [Data status](DATA_STATUS.md) — authoritative dataset coverage and blockers.
- [Data-phase handoff](DATA_PHASE_HANDOFF_20260813.md) — current operational gates.
- [Data API inventory](DATA_API_INVENTORY.md) — provider and API boundaries.
- [Dataset inventory](D001_DATASET_INVENTORY.md) — reproducible artifact inventory.
- [Runbooks](runbooks/) — bounded collection, recovery, and maintenance procedures.

Detailed schemas, source limitations, call budgets, and operational procedures
belong in those references rather than this orientation page.
