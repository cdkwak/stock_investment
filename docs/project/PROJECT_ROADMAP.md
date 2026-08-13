# Project Roadmap

This repository is evolving into a personal quantitative investment platform that supports historical research, reproducible backtesting, realtime monitoring, account management, paper trading, and eventually guarded live trading.

Keep the project in **one repository for now**, while enforcing domain boundaries that allow components to be separated later without redesigning the core.

```text
Historical Data ──> Features ──┐
                               ├──> Strategy + Risk ──> Order Intent
Realtime Data ─────────────────┘                            │
                                                           ├──> Backtest Fill Model
                                                           │         └──> Simulated Account
                                                           │                    └──> Results
                                                           │
                                                           ├──> Paper Broker Adapter
                                                           │         └──> Paper Account
                                                           │
                                                           └──> Live Broker Adapter
                                                                     └──> Live Account

GUI ──> Application Services ──> Data / Backtest / Account / Trading
```

Strategy, risk, order-intent, and account semantics must remain reusable across backtest, paper, and live execution. The execution implementation changes; the strategy logic does not.

## Domain Boundaries

| Domain            | Responsibility                                                                                                                                                                        | Must not own                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `market_data`     | Historical and realtime provider adapters, raw Landing capture, request ledgers, contracts, normalization, canonical datasets, lineage, checkpoints, refreshes, and source validation | Research features, strategies, portfolio decisions, orders, or GUI behavior                          |
| `market_features` | Deterministic and point-in-time-safe feature calculations, availability rules, feature definitions, versioned feature marts, and feature validation                                   | Provider calls, order decisions, account state, or presentation logic                                |
| `market_trading`  | Reusable strategy interfaces, signals, risk policies, order intents, broker-neutral order models, execution interfaces, and later paper/live broker adapters                          | Historical fill simulation, portfolio accounting, provider response formats, or GUI rendering        |
| `market_account`  | Broker-neutral cash, positions, balances, transactions, realized and unrealized P&L, account events, reconciliation, and audit state                                                  | Strategy research, market-data normalization, historical fill models, or broker-specific UI behavior |
| `market_backtest` | Frozen dataset selection, simulation clock, historical fills, slippage, commissions, market-impact assumptions, experiment execution, metrics, reporting, and reproducibility         | External API dependencies, live broker state, realtime network calls, or duplicated strategy logic   |
| `market_gui`      | User workflows, visualization, configuration, monitoring, and orchestration through public application services                                                                       | Financial calculations, strategy rules, portfolio accounting, provider parsing, or order semantics   |

## Dependency Rules

Dependencies must point toward stable domain interfaces rather than provider-specific implementations.

```text
market_data
    ↓
market_features
    ↓
market_trading interfaces
    ↓
market_backtest / paper execution / live execution
    ↓
market_account state
```

The following rules are mandatory:

* Backtests must run entirely from retained, versioned local artifacts with network access disabled.
* Provider SDKs and raw response formats must remain inside provider adapters.
* Feature calculations must declare their input datasets, availability rules, lookback periods, and output versions.
* Strategy and risk logic must be identical across backtest, paper, and live execution wherever possible.
* Backtest execution models and live broker adapters must implement compatible order and execution contracts.
* Account state must be derived from an auditable event or transaction history, not only mutable in-memory values.
* The GUI must call application services rather than importing collectors, strategies, accounting code, or provider adapters directly.
* Domain packages must not depend on `market_gui`.
* Secrets must never be committed, logged, written to documentation, or included in generated artifacts.
* Repository splitting should occur only when release, deployment, ownership, or versioning boundaries clearly justify it.

## Development Sequence

### 1. Historical Data — Maintenance and Gap Filling

The core historical collection baseline is established.

Continue only high-value work:

* bounded refreshes;
* missing critical sources;
* provenance and immutable Landing capture;
* schema and contract validation;
* point-in-time availability metadata;
* resumable checkpoints;
* unresolved corporate-action and macroeconomic gaps.

Do not repeatedly reopen completed ranges or delay backtest development for low-value supplemental datasets.

### 2. Features — Immediate Foundation

Build deterministic, leakage-safe features over frozen dataset versions.

Initial feature families should include:

* price and momentum;
* volatility and drawdown;
* liquidity and turnover;
* market breadth;
* investor flow;
* lending and short selling;
* futures, basis, and option PCR;
* market-cap and universe attributes;
* interest-rate, currency, and macroeconomic regimes.

Every feature must define:

* source dataset version;
* observation timestamp;
* earliest usable timestamp;
* lookback and missing-value policy;
* transformation parameters;
* output schema and validation rules.

### 3. Backtest — Primary Active Phase

Backtest development is now the primary engineering focus.

The first usable engine should support:

* frozen local dataset snapshots;
* deterministic simulation time;
* point-in-time universe selection;
* signal scheduling;
* broker-neutral order intents;
* historical fill simulation;
* commissions, taxes, slippage, and liquidity constraints;
* cash and position accounting through account interfaces;
* benchmark comparison;
* CAGR, volatility, Sharpe, drawdown, turnover, and exposure metrics;
* reproducible experiment configuration;
* in-sample, validation, out-of-sample, and walk-forward evaluation.

The first milestone is not a comprehensive engine. It is one complete vertical path:

```text
Frozen Dataset
    → Feature Mart
    → Baseline Strategy
    → Backtest Service
    → Backtest Result
    → Reproducible Report
```

### 4. GUI — Early Vertical Integration

GUI work should begin once `BacktestService` and `BacktestResult` have stable minimal interfaces. It does not need to wait for every backtest feature to be complete.

The first GUI milestone should provide:

* dataset and universe selection;
* strategy and parameter configuration;
* test-period selection;
* backtest execution and progress;
* equity curve and drawdown;
* performance summary;
* trades and positions;
* warnings, validation failures, and run metadata.

The GUI remains a thin client. Data preparation, feature computation, strategy evaluation, accounting, and metrics stay in their domain services.

### 5. Realtime Data

Add realtime ingestion behind the same canonical market-data interfaces used by historical research.

Realtime work should include:

* connection lifecycle and reconnect handling;
* timestamp normalization;
* sequence and duplicate detection;
* stale-data detection;
* snapshot and stream reconciliation;
* bounded buffering;
* durable event capture where required;
* provider-health and latency monitoring.

Historical and realtime data may use different adapters, but downstream strategy interfaces should not depend on provider-specific formats.

### 6. Account Management

Introduce broker-neutral account state before live order automation.

Required capabilities include:

* cash and available buying power;
* positions and average cost;
* pending and completed orders;
* executions and partial fills;
* fees and taxes;
* realized and unrealized P&L;
* deposits, withdrawals, and corporate-action events;
* periodic reconciliation against the broker;
* mismatch detection and recovery.

The broker remains the external source of truth for live holdings. Internal state must be reconciled rather than blindly trusted.

### 7. Paper Trading

Paper trading must exercise the production strategy, risk, order, account, and monitoring interfaces using realtime data and simulated execution.

Required safeguards include:

* full order and decision audit trails;
* deterministic identifiers and idempotency;
* position and cash limits;
* stale-market-data blocking;
* session and market-hours controls;
* restart and state-recovery tests;
* simulated rejects, partial fills, disconnects, and delayed executions;
* continuous account reconciliation.

Paper trading is a validation phase, not a cosmetic demo.

### 8. Live Trading Bot

Live broker execution is the final phase.

It may begin only after paper-trading invariants and recovery behavior are proven. Initial deployment must use restricted capital, restricted instruments, and explicit operator controls.

Minimum live-trading protections include:

* hard position and loss limits;
* per-order and daily notional limits;
* duplicate-order prevention;
* idempotent submission and recovery;
* broker reconciliation;
* stale-data and connectivity interlocks;
* emergency stop and cancel-all controls;
* manual approval mode;
* immutable audit logs;
* safe restart behavior;
* shadow or read-only validation before automatic submission.

Progression should be:

```text
Historical Replay
    → Backtest
    → Realtime Shadow Mode
    → Paper Trading
    → Small-Capital Supervised Live
    → Guarded Automation
```

## Current Phase and Priority

| Area               | Current state                                                         | Priority |
| ------------------ | --------------------------------------------------------------------- | -------: |
| Historical data    | Core baseline established; maintenance and gap filling continue       |      20% |
| Features           | Immediate foundation work                                             |      25% |
| Backtest           | Primary active development phase                                      |      45% |
| GUI                | Begin minimal vertical integration after Backtest v0 interfaces exist |      10% |
| Realtime data      | Planned after first research workflow is operational                  |    Later |
| Account management | Architecture defined; implementation follows realtime integration     |    Later |
| Paper/live trading | Gated by backtest, realtime, reconciliation, and safety validation    |    Final |

The project must no longer operate in a Data-only mode. Data work should continue in the background without blocking the Feature, Backtest, and initial GUI phases.

## Persistent References

* [README](../../README.md) — repository setup, layout, and supported entry points.
* [Data status](DATA_STATUS.md) — authoritative dataset coverage, limitations, and blockers.
* [Data-phase handoff](../DATA_PHASE_HANDOFF_20260813.md) — current operational gates and recovery constraints.
* [Data API inventory](../DATA_API_INVENTORY.md) — provider, credential, and API boundaries.
* [Dataset inventory](../D001_DATASET_INVENTORY.md) — reproducible artifact inventory.
* [Runbooks](../runbooks/) — bounded collection, validation, recovery, and maintenance procedures.

Detailed schemas, provider limitations, API call budgets, dataset-specific decisions, and operational procedures belong in those references rather than this roadmap.
