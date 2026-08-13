# Backtest status

Status: `ACTIVE_FOUNDATION / IMPLEMENTATION_NOT_STARTED`.

Backtest is the primary active development phase, but no backtest package or
engine has been implemented yet. The first approved implementation milestone is
one deterministic, API-free vertical path:

```text
Frozen local dataset -> point-in-time feature -> baseline strategy
-> simulated execution/accounting -> reproducible result
```

Current gates:

- define stable boundaries for `market_features`, `market_backtest`,
  `market_trading`, and `market_account` before implementation;
- prohibit external API access during a backtest;
- select frozen local dataset versions and availability rules explicitly;
- reuse strategy and risk interfaces for later paper/live execution;
- keep GUI concerns outside financial and simulation logic.

Architecture and sequencing are in the [project roadmap](PROJECT_ROADMAP.md).
Data readiness and limitations remain in [Data status](DATA_STATUS.md).

