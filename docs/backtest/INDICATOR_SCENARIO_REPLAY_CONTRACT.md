# PIT-Safe RSI14 Indicator Scenario Replay Contract

Status: `IMPLEMENTED_OFFLINE / DEVELOPMENT_ONLY / HOLDOUT_UNTOUCHED`

Contract ID: `indicator-scenario-replay/v1`

## Purpose and claims

This replay answers one bounded research question: how a predeclared Wilder
RSI14 low/high interpretation and one fixed 30-entry/70-exit hysteresis scenario
behave on retained KOSPI200 development coverage. It does not search a
threshold, rank a policy, recommend a trade, inspect the final holdout, or claim
that the KOSPI200 index open was an obtainable instrument fill.

The replay is offline and provider-free. It reads only the exact content-
addressed `kr_kospi200_index_daily` v1 frozen root whose manifest digest is
`a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713`.
It reads no credentials, account data, mutable production Data, or provider and
writes neither Data state nor the accepted Phase-1 generation.

## Frozen and sealed boundary

The exact coverage policy is:

- coverage: `1990-01-03..2026-08-14`;
- development rows: `8,225`, strictly before `2021-08-17`;
- sealed holdout rows: `1,222`, beginning `2021-08-17`;
- `results_reviewed=false`.

Manifest verification may read immutable file bytes and date keys. After that,
only canonical dates are inspected to form the development slice. A requested
or observed scenario row at or after the holdout boundary is rejected before
open, close, RSI, label, outcome, execution, or metric values are inspected.
Feature and label builders receive only development rows.

## Wilder RSI14 feature

`market_features.rsi.build_wilder_rsi14` defines version 1:

1. `change_t = close_t - close_(t-1)`;
2. `gain_t = max(change_t, 0)` and `loss_t = max(-change_t, 0)`;
3. the first averages are arithmetic means of the first 14 gains and losses;
4. later averages use Wilder recurrence
   `(prior_average * 13 + current_value) / 14`;
5. RSI is `100 - 100 / (1 + average_gain / average_loss)`;
6. gain-only is 100, loss-only is 0, and an all-flat window is explicitly 50;
7. no missing, nonfinite, nonpositive, duplicate, unsorted, wrong-identity, or
   outcome-namespace input is imputed or accepted.

An observation is final at 15:30 Asia/Seoul on retained session T and is usable
only at 09:00 Asia/Seoul on the exact next retained session. The last source row
is omitted because no T+1 use exists. Every feature carries observation time,
available time, usable-from time, source/contract/feature version, and exact
`PIT_SAFE_EOD_T_PLUS_1` status.

## Fixed study and scenario

The descriptive study has exactly two candidates, in this order:

- `RSI14_LOW_30`: `rsi_14 <= 30`, 20-session outcome horizon;
- `RSI14_HIGH_70`: `rsi_14 >= 70`, 20-session outcome horizon.

Each requires at least 20 development observations. Outcome labels remain in
the label namespace, become available before the holdout, and are joined only
after feature decisions. `winner_selected=false` is invariant.

The execution policy is exactly `RSI14_30_70`: start in cash, enter when RSI is
at or below 30, retain the position through neutral values, and exit when RSI is
at or above 70. Decisions observed at T close fill only at the next retained
session open through `historical-next-open/v1`, using normalized cash 1.0 and
fixed 10 bp one-way hypothetical cost. The matched-hold comparator enters at
the same first decision/fill and uses the same session/open/close series.

Early frozen index history contains preserved zero-open source observations.
They are not repaired. Execution begins only after the last nonpositive-open
development observation and keeps the following contiguous retained calendar.
RSI construction and descriptive study still retain the full valid close-based
development coverage. The result records the exact execution-proxy coverage
and labels `KRX:1028` as `INDEX_OPEN_PROXY_NOT_OBTAINABLE_INSTRUMENT`. No ETF,
dividend, tax, financing, capacity, slippage, or live-performance claim is made.

## Deterministic atomic bundle

The output directory contains exactly `bundle.json`, `result.json`,
`rsi14.csv`, `scenario.json`, and `study.json`. Canonical JSON uses sorted keys,
compact separators, UTF-8, one trailing newline, and rejects NaN. CSV uses a
fixed column order and LF endings. The bundle binds the frozen digest, exact
30/70 thresholds, holdout policy, explicit eight-file semantic code digest,
and byte count/SHA-256 for every other file. Readback rejects missing, extra,
moved, linked, malformed, or content-mismatched files.

Publication stages and journals only under `.tmp/agents/root/`, atomically
replaces the result directory on the same volume, verifies promoted bytes, and
restores the exact prior directory—or exact prior absence—on failure. Journal
truth wins during interrupted recovery: when backup and an apparently valid new
live directory coexist, the new directory is discarded and the journal-bound
prior bytes are restored; a first-publish interruption restores absence. This
restoration completes before a later attempt, so a second pre-promotion failure
cannot strand the interrupted live output.

Readback never trusts a self-declared 64-hex code identity or artifact hashes
alone. It recomputes the current digest over all eight declared code files,
rejects duplicate JSON keys, validates the exact result identity and fixed
30/70 policy, validates RSI CSV columns/count/identity/canonical timezone-aware
PIT clocks/range, and validates exact nested study, decision, execution-ledger,
execution-metric, and matched-hold schemas and accounting before reconciling
bundle receipts. Unexpected fields, malformed clocks, strings in numeric metric
slots, decision/execution misalignment, or self-inconsistent accounting fail
closed even when all artifact hashes have been rebound. Readback reconstructs
the retained market and decision frames, replays them through
`historical-next-open/v1`, and requires exact equality for the complete strategy
and baseline execution objects; trade direction, position continuity,
cash/units, costs, NAV, and annualized metrics therefore cannot be independently
forged. Interrupted state is
recovered before a later run. The supported CLI is
`scripts/run_indicator_scenario_replay.py`; it accepts only project/output paths
and emits no values, holdings, credentials, provider arguments, or order action.

## Consumer boundary

This bundle is eligible only for a separately validated read-only Backtest GUI
consumer. The GUI must validate the exact bundle first and may explain the
fixed scenario, feature clock, study availability, NAV, drawdown, and matched
comparison. It may not calculate RSI, select thresholds, call a provider, read
the sealed holdout, imply a recommendation, or reach a broker/order endpoint.
