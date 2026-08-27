# Phase 1 Backtest Architecture

Status: `TYPED_ATOMIC_FIVE_FILE_PUBLICATION_ACCEPTED / BOUNDED_DEVELOPMENT_ML_RUNNER_ADDED`

```text
frozen retained Data artifact
  -> additive backtest-input-readiness/v1 admission for new consumers
  -> market_features (pure deterministic feature frame)
  -> market_backtest labels (outcome-only frame)
  -> purged expanding walk-forward splits
  -> predefined small-grid descriptive rule replay on development coverage only
  -> pure self-financing close-proxy portfolio ledger on development coverage only
  -> coverage-only untouched final-five-calendar-year holdout boundary
  -> receipt-bound atomic five-file local result generation
  -> typed local validation/export service
  -> Backtest GUI NAV/drawdown consumption
```

`market_features` may depend on stable Data contracts but owns no provider,
collector, storage promotion, or GUI code. `market_backtest` owns labels,
evaluation splits, deterministic replay, result serialization, and the bounded
development-only close-proxy ledger. The ledger models hypothetical cash,
units, positions, and a fixed proportional cost only; it is not an executable
fill model and owns no order intent, live state, provider, or broker adapter.

The typed runner publishes exactly `bundle.json`, `experiments.json`,
`portfolio_ledger.json`, `result.json`, and `signals.csv` as one recoverable
directory generation. Its receipt binds the frozen input digest, sorted artifact
inventory, bytes, individual SHA-256 values, and aggregate bundle digest. The
local service revalidates exact bytes and semantics before the GUI may replace
its last accepted portfolio cards or curves; export copies only accepted
in-memory bytes to a new empty destination.

The accepted default local generation was published twice from the exact frozen
input with identical SHA-256 values for all five files. A fresh process validated
the `READY` receipt, frozen digest `a9229374...`, 8,165 portfolio curve rows, and
`results_reviewed=false`; the coverage-only holdout remains untouched.

The simulation decision clock sees a feature row only at `usable_from`. A label
is visible only after `label_available_at`; evaluation may join it after replay,
but strategy decisions never receive it. Network access is not an input option.

New versioned consumers call the pure `market_backtest.input_readiness`
boundary with only a declared v1 input/split identity, matching pre/post frozen
manifest evidence, the date-only retained calendar, development-only feature
rows, and `CoverageHoldout`. It returns a typed `READY`, `NOT_AVAILABLE`, or
`BLOCKED` receipt before consumer computation. It has no provider, Data write,
artifact write, label/outcome argument, or broker path. Date and sealed-holdout
checks run before any non-date feature value is read, so a holdout row cannot
enter consumer evaluation through a malformed value object.

The readiness receipt binds the full declared/pre/post manifest identities,
retained-calendar digest, feature metadata digest, exact feature schema,
T+1 clocks, source finality, PIT state, split identity, and sealed holdout. Its
canonical JSON and SHA-256 are deterministic and exclude feature values,
labels, predictions, metrics, paths, clocks from the wall time, and holdout
outcomes. Accepted replay modules and their semantic dependency manifests do
not include this additive module and remain unchanged.

The close-proxy portfolio starts with normalized cash 1.0 and holds only long
(1) or cash (0). A signal observed at final close T is usable only at 09:00 KST
on the exact next retained date T+1 and changes the position at that date's
final close. The new position first earns the T+1-close to T+2-close return.
Cash yield is zero. Every position change pays a fixed one-way 10 bp of traded
notional with exact self-financing conservation; there is no leverage, shorting,
forced final liquidation, open/intraday price, slippage, dividend, tax, FX, or
instrument-executability claim. The immutable daily ledger exposes assumptions,
cash, units, asset value, NAV, turnover, cost, exposure, and drawdown so later
layers do not need to reconstruct accounting.

The portfolio simulator accepts only the frozen coverage identity
`1990-01-03..2026-08-14`, its 2021-08-17 boundary, and its exact
8,225-development/1,222-holdout counts with `results_reviewed=False`. A
substitute boundary or equal-valued non-boolean review flag fails closed.
Holdout labels, metrics, rankings, and crisis outcomes are not computed or
inspected by the development replay. The predefined four-row threshold grid is
a baseline comparison, not parameter optimization, and never selects a winner.

## Frozen vertical slice

| Field | Value |
|---|---|
| Dataset | `kr_kospi200_index_daily` |
| Contract | version 1 |
| Coverage | 1990-01-03..2026-08-14 |
| Rows/files/bytes | 9,447 / 37 / 738,068 |
| Root manifest SHA-256 | `a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713` |
| Immutable root | `artifacts/backtest/frozen_inputs/kr_kospi200_index_daily/a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713/` |
| Decision rule | T close observation -> next retained trading-date decision |

The digest binds sorted relative Parquet paths and each file SHA-256. A changed
digest requires an explicit new frozen manifest and experiment identity.

## Bounded overnight ML development path

One retained local, at-most-eight-hour Phase-3 development experiment is
governed by [`OVERNIGHT_ML_RUNBOOK.md`](OVERNIGHT_ML_RUNBOOK.md). Project Status
authorizes additional versioned offline experiment identities under their own
contracts, splits, budgets, and tests; this historical operation is not a
one-shot phase ceiling.
It reuses the verified frozen loader but slices the source before the sealed
holdout boundary before feature or label construction. Only the six accepted
T+1 features and development outcomes enter the model study.

Optuna persists one single-process SQLite study and scikit-learn evaluates
logistic regression, histogram gradient boosting, and random forest candidates
through the existing 60-session-purged, 5-session-embargoed expanding
walk-forward policy. Each trial is a development diagnostic. The current best
trial is never a selected production model and no holdout, portfolio, GUI,
provider, account, or order boundary is crossed.
