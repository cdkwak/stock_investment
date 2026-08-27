# Project Roadmap

The durable user-owned project goal is defined in
[`PROJECT_GOAL.md`](PROJECT_GOAL.md).
This roadmap owns the architecture and sequence toward that goal.

Keep the project in **one repository for now**, while enforcing domain boundaries that allow components to be separated later without redesigning the core.

This roadmap is a sequencing view. [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
remains authoritative for the selected domain, current gate, blockers, and
next authorized work.

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

### 1. Historical Data — Selected Active Expansion and Automation

The core historical collection baseline and bounded daily-operation controls
are established. Data is now the primary operational domain alongside active
parallel GUI/Feature/Backtest engineering. The standing
autonomous source-onboarding runbook permits agents to call public APIs and use
existing `.env`-injected credentials for market, macro, realtime, and read-only
account data; then proceed through contract, Landing-first collection, validated
promotion, health, and scheduling without a new approval for each normal step.
Source-specific semantic, rights, finality, PIT, atomicity, and idempotency gates
remain mandatory. Secret disclosure, orders, transfers, purchases,
subscriptions, binding agreements, and broker-side mutations remain excluded.

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

Market valuation and earnings research must keep two independent axes instead
of compressing them into one unexplained score:

* `Valuation level`: exact current/trailing/forward identity, 5-year and
  10-year as-of-only percentiles, PBR with separately accepted ROE context,
  and an earnings-yield gap only when the matching forward horizon and Korean
  government-bond observation are both accepted;
* `Earnings momentum`: point-in-time forward EPS/BPS/ROE levels, 1-month and
  3-month estimate changes, revision breadth/up/down ratios, contributor and
  vintage lineage;
* `Price driver`: a versioned decomposition of index return into forward-EPS
  change and forward-multiple change only when price and estimate horizons,
  timestamps, aggregation and universes reconcile exactly.

Until those forward-estimate contracts exist, KRX provider-native PER/PBR may
show only descriptive own-history context. It cannot produce expected earnings
growth, expected book growth, PBR/ROE residuals, yield-gap values, `EARLY
RECOVERY`, `LATE BULL`, `TOP RISK`, low/high-point labels, or a neutral zero for
the missing earnings axis. Later validation must test the two axes and their
interaction against pre-registered 3/6/12-month returns and drawdowns using
purged walk-forward evaluation; the final holdout stays sealed during feature
selection.

Every feature must define:

* source dataset version;
* observation timestamp;
* earliest usable timestamp;
* lookback and missing-value policy;
* transformation parameters;
* output schema and validation rules.

### 3. Backtest — Accepted Foundation and Active Offline Expansion

The accepted Phase 1 Feature/PIT contracts, frozen retained input,
deterministic features/labels/walk-forward, descriptive signal replay, and typed
five-file close-proxy generation remain supported reproducible baselines. They
are not a ceiling on new work. Agents may build versioned offline models,
historical executable-instrument fills, richer costs/accounting, optimization
research, result services, and local paper simulation in parallel with Data.
Each new path must preserve PIT/leakage controls, keep the existing final holdout
sealed during development, and avoid provider or broker mutation calls from
Backtest code.

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

### 4. GUI — Implemented Local MVP and Typed Backtest Result Integration

The local read-only Dashboard and Index MVP is implemented and validated over
retained artifacts. The Backtest page now runs the fixed offline replay in a
background workflow and renders only the strict validated close-proxy result;
it does not own features, signals, labels, fills, accounting, or metrics.
Versioned executable-instrument simulation and richer portfolio semantics are
authorized follow-on work; the fixed close-proxy result remains supported.

Dashboard source preparation and runtime services must continue to preserve
`*_daily` versus `*_snapshot` boundaries, provider labels, provisional state,
and provider-aware call controls. They must not call providers, implement collectors,
calculate model/strategy/accounting values in presentation code, or imply that a
display-source decision authorizes a Data operation or predictive use.

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

The durable goal also includes eventual access away from the development laptop.
That remains a later deployment phase: an always-on machine may host a read-only
application service over published snapshots and health state, while provider
credentials and read-only account refresh remain behind sanitized services that
may operate under the standing API authorization. Order, transfer, withdrawal,
purchase, subscription, and binding-agreement capabilities remain prohibited.
A NAS may retain backups or published artifacts, but file sharing alone is not
the application-service boundary.

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

| Area               | Current state                                                         | Routing |
| ------------------ | --------------------------------------------------------------------- | ------- |
| Historical data    | Selected for autonomous public/existing-credential API operations, contract-valid storage, read-only refresh, health, and automation | Selected |
| Features           | Deterministic Phase 1 feature/PIT foundation implemented over the frozen input | Supporting |
| Backtest           | Accepted close-proxy baseline plus autonomous versioned offline model, fill, cost, accounting, and simulation engineering; existing final holdout sealed | Active parallel |
| GUI                | Implemented local surfaces plus autonomous typed GUI/application-service engineering | Active parallel |
| Realtime data      | Read-only collection, normalization, storage, and shadow consumption authorized | Active Data expansion |
| Account management | Existing-credential read-only integration and reconciliation engineering authorized; mutations prohibited | Active read-only parallel |
| Paper/live trading | Local paper simulation authorized; all real or paper-broker order endpoints and financial mutations prohibited | Simulation only |

The project is routed to Data for autonomous public and existing-credential API
operations, read-only refreshes, source expansion, and automation. GUI,
Features, offline Backtest/ML, portfolio simulation, and local paper simulation
may advance in parallel with non-overlapping scopes. No real or paper-broker
order execution, transfer, purchase, subscription, binding-contract, or
broker-side financial mutation is authorized by this roadmap.

## Persistent References

* [README](../../README.md) — repository setup, layout, and supported entry points.
* [Data status](../data/DATA_STATUS.md) — authoritative dataset coverage, limitations, and blockers.
* [Data API inventory](../archive/data/evidence/2026-08-data-phase/inventory/DATA_API_INVENTORY.md) — archived provider, credential, and API evidence.
* [Dataset inventory](../archive/data/evidence/2026-08-data-phase/inventory/D001_DATASET_INVENTORY.md) — archived reproducible artifact inventory.
* [Data runbooks](../data/operations/) — procedures currently routed by Data Status.
* [GUI status](../gui/GUI_STATUS.md) — active GUI engineering state, runtime boundaries, and typed service route.
* [Backtest status](../backtest/BACKTEST_STATUS.md) — authoritative accepted Backtest boundary and holdout gate.

Detailed schemas, provider limitations, API call budgets, dataset-specific decisions, and operational procedures belong in those references rather than this roadmap.
