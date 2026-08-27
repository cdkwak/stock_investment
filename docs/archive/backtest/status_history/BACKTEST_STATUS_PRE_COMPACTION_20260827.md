# Archived Backtest Status Snapshot

> Historical snapshot captured before the 2026-08-27 agent-routing
> compaction. It is evidence only, not current authority. Relative links are
> preserved as historical text and may no longer resolve from this archive.

# Backtest Status

This is the authoritative entry point for current Backtest state, priority,
routing, and boundaries. Read it after `AGENTS.md` and
[`PROJECT_STATUS.md`](../project/PROJECT_STATUS.md) for Backtest work.

## Current state

| Field | Current value |
|---|---|
| Project routing | `DOMAIN_PARALLEL_OFFLINE_ENGINEERING_ACTIVE` |
| Next phase | `FOUNDATION_ACCEPTED / WIDER_OFFLINE_DEVELOPMENT_AUTHORIZED` |
| Implementation | `TYPED_ATOMIC_LOCAL_PORTFOLIO_RUNNER_AND_GUI_CONSUMER_IMPLEMENTED / ACCEPTED_CLOSE_PROXY_PRESERVED / FIXED_INDICATOR_NEXT_OPEN_GUI_SCENARIO_IMPLEMENTED / OFFLINE_EXECUTION_AND_INDICATOR_BOUNDARIES` |
| Active task | Agents may extend offline features, models, historical fills, costs, portfolio accounting, reporting, and local paper simulation in assigned non-overlapping scopes. Preserve the accepted five-file generation and keep its final holdout sealed. |
| Input boundary | Frozen, retained, contract-validated local artifacts only |
| External API dependency | Forbidden |
| GUI dependency | The fixed close-proxy contract and its accepted five-file bundle are available again. Generation and GUI validation share the explicit `phase1-code-dependencies/v1` manifest, while the artifact bytes and sealed holdout remain unchanged and unreviewed. |
| Known current regression | Closed in `RQ-20260826T025726-5467`: unrelated Backtest modules no longer alter Phase-1 code identity; a change to any declared semantic dependency still fails closed. |
| Investment-policy boundary | [`INVESTMENT_POLICY_CONTRACT.md`](INVESTMENT_POLICY_CONTRACT.md) governs user-specific ranking and recommendations; unresolved choices do not block clearly labelled research scenarios or engineering with explicit assumptions |

Phase 1 established contracts and one deterministic feature path. The completed
close-proxy slice adds only a development-only, self-financing ledger for the
existing deterministic signal. The typed runner atomically publishes an exact
five-file generation and the GUI validates that generation before rendering its
NAV and drawdown. This is not an executable fill model or an
investment-performance claim. The accepted artifact itself remains a
close-proxy result. The prior broad-code-identity regression is closed by one
sorted, versioned twelve-file semantic dependency manifest shared by replay
generation and GUI validation. The reviewed legacy digest is accepted only
when the current explicit digest equals its fixed migration anchor; any later
declared dependency change still fails closed. This recovery did not rewrite
the artifact or inspect the holdout. Models, optimization,
historical executable-instrument
simulation, richer accounting, and local paper simulation may now be built as
new versioned boundaries; broker mutations and claims of live or validated
investment performance remain excluded.

## Phase sequence

| Phase | Scope | Current state |
|---|---|---|
| Phase 1 Foundation | Feature/PIT contracts, frozen input, deterministic no-network feature slice | `CLOSED_OFFLINE` |
| Phase 2 Signal Backtest | Labels, historical replay, walk-forward evaluation, rule baseline, bounded close-proxy ledger | `DEVELOPMENT_ONLY_PURGED_WALK_FORWARD_BASELINE / ABLATION_AND_EXPERIMENT_REGISTRY_READY / CLOSE_PROXY_PORTFOLIO_FOUNDATION_IMPLEMENTED / HOLDOUT_UNTOUCHED` |
| Phase 3 Models | Logistic baseline, then bounded tree/probabilistic models | `ACTIVE_OFFLINE_DEVELOPMENT` |
| Phase 4 Portfolio / GUI Integration | Executable-instrument simulation, richer costs, typed service, GUI consumption | `ACTIVE_VERSIONED_DEVELOPMENT`; fixed close-proxy service remains a supported baseline |

## Phase 1 priorities

1. Review `FEATURE_CONTRACT.md`, `BACKTEST_ARCHITECTURE.md`, and
   `VALIDATION_POLICY.md` before changing their owned behavior.
2. Verify frozen manifest digest
   `a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713`
   before every replay. Replay reads the matching content-addressed artifact,
   never the newer mutable production root.
3. Do not rewrite the accepted close-proxy contract in place. Add versioned
   simulation contracts for executable instruments, fills, costs, or accounting.
4. Preserve the coverage-only holdout beginning 2021-08-17; its 1,222
   observations, outcomes, predictions, and crisis metrics remain uninspected
   during development.
5. Use the fixed `PRICE -> VOLATILITY -> FX -> BREADTH -> FLOW -> DERIVATIVES`
   ablation order. Only Price and Volatility are currently evaluable; the other
   families remain unavailable for dependent numeric evaluation and are never
   substituted. Their interfaces, tests, and Data-owned evidence work may
   continue without a phase or permission decision.

## Gates

| Gate | State | Required closure |
|---|---|---|
| Data dependency | Standing Data API operations active | Backtest remains deterministic and network-free; request needed retained inputs through Data-owned contracts rather than calling providers from replay code |
| GUI MVP prerequisite | Closed | Existing local read-only GUI is implemented and validated |
| Feature/PIT contract | Closed offline | Six versioned T+1 features with explicit lookback/missing/PIT rules |
| Frozen input/version | Closed for current artifact | Content-addressed KOSPI200 v1 root has 9,447 rows, 37 partitions, 738,068 bytes, and exact digest `a922...`; production remains separate at `e6d606...` |
| Deterministic feature slice | Closed offline | Feature/label namespace isolation, T+1 timing, 60-day purge, and 5-day embargo are tested; two independent restored-input replays produced byte-identical signals, results, and experiment records |
| Ablation and experiment identity | Closed offline | Cumulative families fail closed at unavailable/PIT-blocked inputs; records bind code-tree, threshold-value, signals, results, input, feature/label versions, split, 60-session horizon/purge, embargo, and exact Signal PIT status |
| Typed atomic result adapter | Implemented offline | Exact receipt-bound five-file service; GUI performs no feature/signal/label/accounting calculation and renders only the validated close-proxy curve |
| Stable portfolio foundation | Implemented offline | Pure deterministic ledger, metrics, atomic publication/recovery, exact export, background GUI workflow, and last-accepted-result preservation are test-verified |
| Next-open execution boundary | Implemented offline | `historical-next-open/v1` maps a close-observed long/cash decision only to the exact next retained session open, charges explicit one-way basis-point cost, conserves cash/assets/cost on every row, reports return/volatility/drawdown/turnover/exposure metrics, rejects outcome columns and invalid identities, and makes no capacity or obtainable-fill claim; 19 focused tests including a seeded 252-session conservation stress pass |
| Predefined indicator study | Implemented offline | `predefined-indicator-study/v1` compares exact predeclared LOW/HIGH thresholds against outcome-only development labels in input order, requires exact next-retained-session feature usability, rejects feature outcome namespaces and inputs outside declared coverage or crossing the untouched holdout, emits typed insufficiency, and never ranks or selects a winner; 17 focused tests pass |
| Threshold-band execution scenario | Implemented offline | `predefined-threshold-band/v1` turns one exact predeclared enter/exit band into sparse hysteresis decisions, requires exact PIT-safe next-session usability and one matching instrument, rejects inputs outside declared coverage or crossing the untouched holdout before price/indicator inspection, then delegates all fills and accounting to `historical-next-open/v1`; 13 focused tests pass |
| Matched hold comparator | Implemented offline | `threshold-band-matched-hold/v1` enters the baseline at the exact same first strategy decision/fill, holds through the identical clock and price series, and reports return/NAV, annualized volatility, drawdown, turnover and transaction-cost differences without ranking or choosing a winner; no-entry is typed rather than inventing a baseline; 4 focused tests and the full Backtest unit suite (`401 passed, 1 skipped`) pass |
| Fixed RSI14 scenario replay | `IMPLEMENTED_OFFLINE / INDEPENDENT_TECHNICAL_REVIEW_ACCEPTED / NOT_A_PROJECT_BLOCKER` | [`indicator-scenario-replay/v1`](INDICATOR_SCENARIO_REPLAY_CONTRACT.md) builds exact Wilder RSI14 with T+1 clocks, evaluates only fixed LOW30/HIGH70 candidates, executes only the fixed 30-entry/70-exit next-open scenario plus matched hold, rejects holdout dates before numeric inspection, and atomically publishes a five-file content-bound bundle. Interrupted promotion now restores journal-declared prior bytes or prior absence before any retry, even when the interrupted live bundle is structurally valid. Readback recomputes the current eight-file code digest, validates exact result/RSI/study/scenario schemas and clocks, then deterministically replays every strategy and baseline ledger from its retained market/decision rows and requires exact full execution equality before matched-hold reconciliation. Independent review accepted fresh generation `16204260059abc7ef318958e8424b870`: two actual frozen development runs were five-file byte-identical, all nine rebound/semantic forgery families failed closed, the owning atomic suite passed 19/19, and the wider Backtest/Feature suite passed `452 passed, 1 skipped`. The accepted result remains development-only and does not authorize holdout inspection, ranking, recommendation, or live use. |
| Fixed scenario GUI adapter | `IMPLEMENTED_PROVIDER_FREE / DEVELOPMENT_INPUT_ONLY / REVIEW_REQUIRED` | `backtest-gui-scenario-adapter/v1` accepts only the exact typed RSI14 development input envelope, preflights every date key against the sealed boundary before identity, clock, price, indicator, label, outcome, or metric values, then delegates the fixed LOW30/HIGH70 study and 30-entry/70-exit next-open plus matched-hold evaluation to the accepted pure engines. It returns frozen result views and preserves caller frames. Missing inputs and no-entry/insufficient states stay typed and numeric-free; threshold search, ranking, recommendation, provider/account access and Data writes are absent. Focused service/GUI validation passes 17 tests, the owning execution/indicator regressions pass 63 tests, and a provider-free 1600x900 offscreen render has no horizontal overflow or running worker. |
| Three-axis market-regime validation | `V1_ENGINE_IMPLEMENTED / NUMERIC_RUN_BLOCKED_PENDING_COMPLETE_EARNINGS_AXIS` | [`market-regime-validation/v1`](MARKET_REGIME_VALIDATION_CONTRACT.md) fixes 63/126/252-session return and true path-MDD labels, requires separate PIT-safe price/technical, valuation, and forward-earnings states, reconciles every label clock to the exact 252nd retained successor session, applies a 252-session purge plus embargo on development-only folds, proves training-label availability before test decisions, and exposes no winner/ranking. Missing or PIT-blocked Forward EPS/revision/ROE rejects evaluation rather than becoming zero or neutral. Eleven focused tests pass; no production data, provider, GUI result, accepted five-file generation, or sealed holdout was read or changed. |

## Operational boundaries

- Backtest must never call an external API, run a collector/backfill, depend on
  provider credentials, or mutate Data artifacts/state.
- Availability and PIT rules come from Data Status and the exact selected Dataset
  Contract; Backtest must not reinterpret source semantics.
- Unknown or `PIT_LIMITED/PIT_BLOCKED` availability fails closed for predictive use.
- Labels and future outcomes must remain outside Data/Feature inputs available to
  the simulation clock.
- Strategy and risk logic should remain reusable across historical and local
  simulated execution through broker-neutral interfaces.
- Historical fill/accounting belongs in `market_backtest`, not provider or GUI
  code, and may be implemented now as a versioned offline boundary.
- GUI code must not contain Feature, Model, strategy, fill, risk, or accounting logic.
- Supported Phase-1 CLI and GUI-service terminal failures emit only the strict
  dependency-neutral `runtime-diagnostic/v1` projection under
  `artifacts/runtime_logs/application/`. This does not change exceptions, exit
  status, replay artifacts, frozen inputs, or results; `market_backtest` imports
  neither GUI nor Data implementation.

## Next Backtest action

The typed five-file close-proxy foundation and its independent review are
accepted. Preserve the exact published generation, frozen input digest
`a922...`, normalized cash 1.0, long/cash-only exposure, exact T+1 timing, zero
cash yield, fixed one-way 10 bp hypothetical cost, and the untouched
1,222-observation holdout. `results_reviewed=false` continues to mean that the
final holdout is intentionally uninspected, not that artifact review is pending.

Wider offline Backtest engineering is authorized. Agents may implement open or
intraday historical fills, models, optimization research, executable-instrument
simulation, richer costs/accounting, and local paper simulation with versioned
contracts and proportionate tests. Backtest code still must not call providers,
inspect the existing final holdout during development, use live private account
state as research input, or reach any broker order/mutation endpoint.

The pure `market_backtest.execution` boundary now implements the first bounded
executable-instrument scenario without changing the accepted close-proxy path.
It is explicitly `DEVELOPMENT_ONLY_EXECUTION_MODEL`: one instrument plus cash,
long-only, fractional units, next-retained-session open, fixed one-way cost, and
no dividends, tax, financing, volume/capacity or obtainable-fill claim. Sparse
decisions hold the prior target; a decision on the final retained session fails
closed rather than filling on the same day. The module performs no I/O, imports
no provider or account code, and has not read the frozen final holdout.

The pure `market_backtest.indicator_study` boundary now supports descriptive
overbought/oversold candidate research without parameter optimization. It
requires exact PIT-safe feature clocks, identity-aligned outcome-only labels,
predefined finite thresholds and an untouched holdout policy. It reports signal
coverage, conditional and unconditional return/positive-rate/drawdown summaries
and their mean-return difference, preserving candidate order and setting
`winner_selected=false`. A result that becomes available in the holdout is
rejected before indicator or outcome numeric values are inspected.

The pure `market_backtest.indicator_strategy` boundary connects one predeclared
LOW-entry/HIGH-exit hysteresis scenario to the next-open execution ledger. It
does not search thresholds or select a policy. Repeated LOW or HIGH observations
do not create duplicate trades, a neutral path remains cash, and the exact
instrument/date/usable-clock mapping is validated before execution. Prices and
indicator values at or beyond the untouched holdout are not inspected.

The same module also exposes a matched buy-and-hold comparator. Both paths stay
cash until the strategy's first entry observation and buy at the same retained
next-session open with identical starting capital, assumptions, instrument,
currency, sessions and price series. The comparison reports differences only;
it has no winner, rank, recommendation, or policy-eligibility field.

The implemented
[`PIT-Safe RSI14 Indicator Scenario Replay Contract`](INDICATOR_SCENARIO_REPLAY_CONTRACT.md)
connects those accepted generic boundaries to one production RSI feature and
one deterministic runner. Its thresholds are fixed at 30/70 and never searched.
The sealed 2021-08-17 holdout boundary is checked before numeric feature,
outcome, price, or metric inspection; only 8,225 development source rows enter
the builders. Preserved zero-open early index rows are not repaired, so the
next-open index-proxy scenario starts only after the last such row and records
that bounded coverage explicitly. The result is development-only,
`INDEX_OPEN_PROXY_NOT_OBTAINABLE_INSTRUMENT`, and carries no winner,
recommendation, ETF, capacity, or live-performance claim. Its five-file bundle
is deterministic and exact-byte bound. Journal recovery restores the exact
pre-transaction bundle or exact prior absence before a later attempt; a valid
interrupted new live bundle never supersedes that prior state. Readback
recomputes the current eight-file semantic code digest and validates the closed
result, RSI CSV, study, and scenario schemas/identities instead of trusting
self-declared artifact hashes. Nested validation rejects noncanonical or naive
timestamps, unexpected study/comparison metric fields, nonnumeric execution
metrics, broken decision-to-next-session alignment, and ledger or matched-hold
accounting that does not reconcile. Each execution is regenerated through the
accepted next-open engine and must match every retained row and metric exactly,
closing contradictory trade side, position continuity, cash/unit mechanics,
and annualized metric claims. This exact bundle may be consumed by the
independently reviewed read-only GUI boundary. The exact-bundle technical review
is accepted; this does not relax the untouched holdout, development-only,
non-ranking, non-recommendation, or non-live-use boundaries.

The [`Investment Policy Contract`](INVESTMENT_POLICY_CONTRACT.md) defines
the user-owned horizon, benchmark, risk, liquidity, cost, tax, and FX choices
that must be resolved before any later strategy ranking or optimization. Its
unresolved state does not change the fixed close-proxy assumptions. It blocks
only user-specific ranking or recommendation claims that depend on missing
choices, not clearly labelled scenario research or implementation work.

The documentation-only
[`PIT-Safe Sector Research Contract`](SECTOR_RESEARCH_CONTRACT.md) defines the
future Backtest consumer boundary for point-in-time taxonomy, universe and
membership, multi-window trend/relative strength, breadth, flow, relative
valuation, and value-trap evidence. Trend and relative value remain independent
states, current classifications are never backfilled into history, and a
candidate is explanatory research rather than a recommendation or order. The
linked Data-owned
[`sector-input-feasibility/v1`](../data/research/active/SECTOR_TAXONOMY_MEMBERSHIP_PIT_FEASIBILITY.md)
boundary inventories candidate authorities without selecting a provider or
claiming implementation readiness. It leaves both markets' historical
taxonomy/membership and the sector comparator, Breadth, Flow, and structural-
decline roles unavailable; market breadth, current classifications, ETFs, and
cross-taxonomy labels cannot substitute. Only the narrow retained KRX KOSPI200
market price comparator is supported independently, which cannot make a sector
candidate evaluable. This limits only dependent use; interfaces/tests and
Data-owned evidence work may continue. The final holdout remains untouched.

The documentation-only
[`Multi-Factor Crash-Risk Validation Contract`](CRASH_RISK_VALIDATION_CONTRACT.md)
defines the future Backtest boundary for the descriptive `OVERHEATING_POSSIBLE`,
`RISK_EXPANDING`, `TREND_DAMAGED`, and `DRAWDOWN_IN_PROGRESS` states, separate
sanitized account-impact projection, non-executable defensive candidates, and
PIT-safe stress validation. It does not claim a bubble or predict every crash.
Current blocked factor families stay `UNKNOWN`, no account data becomes a model
input, and the final holdout remains untouched. The contract selects no runtime
implementation, Data source, account refresh, or GUI behavior.

The documentation-only
[`Explainable Action-State and Portfolio-Adjustment Contract`](ACTION_STATE_CONTRACT.md)
defines the future Backtest-owned typed boundary for `BUY_REVIEW`, `HOLD`,
`REDUCE_REVIEW`, and `SELL_REVIEW` plus fail-closed `UNAVAILABLE`. It binds an
accepted investment-policy revision, validated evidence, a sanitized reconciled
account projection, conserved current/proposed/cash weights, and separate risk,
cost, tax, FX, liquidity, and concentration impacts. It is not an order,
recommendation, suitability or guaranteed-return claim; current close-proxy
results are neither executable holdings nor proposals, and the final holdout
remains untouched. Its `action-state-vocabulary/v1` registry now exhaustively
closes semantic evidence roles, reasoning-use roles, uncertainty codes, and
unavailable reasons. Unknown codes and role substitution fail `INVALID`; each
Project Goal evidence family and fail-closed cause has an exact enum route. The
contract still selects no runtime or GUI implementation.

The documentation-only
[`Leverage Evaluation and Safety Contract`](LEVERAGE_EVALUATION_CONTRACT.md)
defines `leverage-evaluation/v1` for a future PIT-matched comparison between one
exact leveraged ETF or margin-financed case and an exact unlevered baseline. It
keeps daily reset/path dependence, financing, product expense, tracking,
transaction cost, tax, FX, margin/liquidation paths, paired stress cases, and
policy gates independently attributable and fail-closed. ETF and margin
semantics never substitute, embedded costs are not double-counted, and all
applicable leverage/loss/drawdown/cash/buffer gates are conjunctive. It selects
no product, runtime, provider/account call, optimization, GUI behavior, order,
or holdout inspection; the current close-proxy generation remains unchanged.

## Bounded reading route

```text
AGENTS.md
  -> docs/project/PROJECT_STATUS.md
  -> docs/backtest/BACKTEST_STATUS.md
```

Read Data Status, one Dataset Index row, and one Dataset Contract only when the
Phase 1 task selects or validates the frozen input. Read the Project Roadmap only
for architecture/sequencing and the Repository Map only for placement. Do not
scan archives, provider guides, runbooks, or the full Data tree by default.
