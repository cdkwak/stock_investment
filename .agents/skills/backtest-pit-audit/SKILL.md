---
name: backtest-pit-audit
description: Audit offline backtest inputs and experiments for point-in-time safety, label isolation, split integrity, and deterministic replay; use before extending or validating backtests.
---
# Backtest PIT Audit

Use this skill for offline Backtest work that creates or reviews features, labels, splits, replay artifacts, or experiment records.

Start from Backtest Status and read only the routed feature contract, backtest architecture, validation policy, and relevant frozen-input manifest. Backtest/feature code remains network-free: use retained Data-owned inputs rather than calling providers or mutating Data artifacts/state, and never infer availability from source dates alone. This boundary does not prevent versioned offline model, fill, accounting, portfolio, or local paper-simulation implementation.

Verify that every decision-time input has explicit `observation_time`, `available_at`, and `usable_from` semantics; EOD inputs must remain unavailable until the declared T+1 decision point. Keep labels and future outcomes isolated from feature/signal namespaces. Check purge and embargo against label horizon, retain the untouched holdout, and reject current-universe or revised-data leakage.

Bind experiments and replays to the frozen input digest, feature and label versions, split policy, thresholds, and code identity. Re-run deterministically before reporting results. Treat descriptive classification diagnostics as non-portfolio output unless the same implementation supplies tested fill, accounting, and performance semantics. No additional phase approval is needed for that offline implementation; never represent simulation as live performance or reach a broker mutation endpoint.
