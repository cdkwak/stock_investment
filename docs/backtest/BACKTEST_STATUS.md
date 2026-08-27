# Backtest Status

Read this after `AGENTS.md` and
[`PROJECT_STATUS.md`](../project/PROJECT_STATUS.md) for Backtest work. This is a
compact current routing view; experiment receipts and historical implementation
detail do not belong here.

## Current state

| Field | Current fact |
|---|---|
| Domain state | `DOMAIN_PARALLEL_OFFLINE_ENGINEERING_ACTIVE` |
| Foundation | Accepted deterministic close-proxy five-file generation and typed local GUI consumer |
| Frozen input | Content-addressed retained artifact, digest `a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713` |
| Final holdout | 1,222 observations beginning 2021-08-17; sealed and intentionally uninspected; `results_reviewed=false` |
| New-consumer readiness | Additive provider-free `backtest-input-readiness/v1` binds exact manifest/readback, retained calendar, clocks, split, PIT/finality, and sealed holdout; accepted replay paths are not retrofitted |
| Network/Data boundary | Backtest is provider-free and may not collect, refresh, or mutate Data artifacts |
| Development authority | Offline features, models, fills, costs, portfolio accounting, reporting, and unmistakably local paper simulation may proceed through versioned contracts |
| Financial boundary | No broker order endpoint, account mutation, live-performance claim, recommendation, or suitability claim |

## Phase route

| Phase | Current state |
|---|---|
| Foundation | `CLOSED_OFFLINE` |
| Signal backtest | `DEVELOPMENT_ONLY_PURGED_WALK_FORWARD_BASELINE / CLOSE_PROXY_FOUNDATION_ACCEPTED` |
| Models | `ACTIVE_OFFLINE_DEVELOPMENT` |
| Portfolio and GUI integration | `ACTIVE_VERSIONED_DEVELOPMENT`; accepted close-proxy service remains the baseline |

## Accepted boundaries

- Six versioned T+1 features have explicit lookback, missing-value, clock, and
  PIT rules. Labels and future outcomes remain isolated from inputs.
- Replay reads the content-addressed frozen input, not the mutable production
  root, and checks the exact semantic dependency manifest.
- New versioned consumers have a typed deterministic input-readiness receipt;
  this is an additive admission boundary and does not claim retroactive
  enforcement for the accepted Phase-1, indicator, or overnight-ML paths.
- Development splits preserve a 60-session purge and 5-session embargo for the
  accepted feature slice. The three-axis market-regime engine uses its stricter
  252-session purge and exact forward-outcome clocks.
- The accepted close-proxy ledger is development-only, self-financing,
  long/cash, normalized to cash 1.0, zero cash yield, and fixed one-way 10 bp
  hypothetical cost. It is not an obtainable-fill model.
- `historical-next-open/v1` implements one-instrument-plus-cash, long-only,
  fractional-unit next-retained-session-open execution with explicit one-way
  cost. It makes no volume, capacity, tax, financing, dividend, or fill claim.
- Predefined indicator study, threshold-band execution, and matched-hold
  comparison are implemented without parameter search, ranking, or winner
  selection.
- The fixed RSI14 scenario uses Wilder RSI14, fixed 30/70 thresholds, exact T+1
  clocks, next-open execution, matched hold, and a content-bound five-file
  result. It remains development-only and cannot inspect the final holdout.
- The GUI adapter accepts only typed validated local bundles and performs no
  Feature, signal, label, strategy, fill, or accounting calculation.
- `market-regime-validation/v1` fixes 63/126/252-session returns and true path
  drawdowns. Numeric production evaluation is blocked until price/technical,
  valuation, and PIT-safe Forward EPS/revision/ROE are all present; missing
  earnings evidence is never zero or neutral.
- `stock-candidate-research/v1` is the strict future three-axis validation
  boundary for dated-universe, oversold, Forward EPS revision and relative-value
  evidence. It is separate from the GUI's permissive descriptive current-data
  scanner and cannot run with substituted index/current-master evidence.

## Owning documents

Read only the document that owns the selected change.

| Scope | Owning document |
|---|---|
| Feature schema, clock, missingness, PIT | [Feature Contract](FEATURE_CONTRACT.md) |
| Package and service architecture | [Backtest Architecture](BACKTEST_ARCHITECTURE.md) |
| Splits, leakage, holdout, acceptance | [Validation Policy](VALIDATION_POLICY.md) |
| Fixed RSI14 replay | [Indicator Scenario Replay Contract](INDICATOR_SCENARIO_REPLAY_CONTRACT.md) |
| Three-axis regime validation | [Market Regime Validation Contract](MARKET_REGIME_VALIDATION_CONTRACT.md) |
| Strict stock-candidate validation | [Stock Candidate Discovery Contract](STOCK_CANDIDATE_DISCOVERY_CONTRACT.md) |
| User-specific ranking prerequisites | [Investment Policy Contract](INVESTMENT_POLICY_CONTRACT.md) |
| Sector research inputs and states | [Sector Research Contract](SECTOR_RESEARCH_CONTRACT.md); Data-owned [PIT-safe taxonomy, membership, and input feasibility](../data/research/active/SECTOR_TAXONOMY_MEMBERSHIP_PIT_FEASIBILITY.md) (`sector-input-feasibility/v1`, `NUMERIC_CONSUMER_NOT_READY`) |
| Crash-risk descriptive states | [Crash-Risk Validation Contract](CRASH_RISK_VALIDATION_CONTRACT.md) |
| Explainable action-state vocabulary | [Action-State Contract](ACTION_STATE_CONTRACT.md) |
| Leverage comparison and safety gates | [Leverage Evaluation Contract](LEVERAGE_EVALUATION_CONTRACT.md) |
| Supported recurring offline ML execution | [Overnight ML Runbook](OVERNIGHT_ML_RUNBOOK.md) |

Documentation-only contracts define future boundaries; they do not by
themselves select a runtime, provider, product, recommendation, or holdout
inspection.

## Gates

| Gate | State | Required handling |
|---|---|---|
| Frozen input identity | Closed for accepted generation | Reject any digest or declared semantic-dependency mismatch |
| Final holdout | Sealed | Do not inspect outcomes, predictions, metrics, or crisis slices during development |
| PIT and label isolation | Mandatory | Unknown or `PIT_LIMITED/PIT_BLOCKED` inputs fail closed for dependent predictive use |
| Data dependency | Data-owned | Request retained inputs through Data contracts; never call providers from Backtest |
| Sector research inputs | `UNAVAILABLE / NUMERIC_CONSUMER_NOT_READY` | Follow Data-owned `sector-input-feasibility/v1`; do not calculate candidates until both markets' taxonomy/membership and every required role are PIT-safe and frozen |
| Forward earnings axis | Missing | Continue interface/test work, but do not run the three-axis numeric evaluation on partial evidence |
| Investment policy | Unresolved for user-specific ranking | Clearly labelled scenario engineering may continue; ranking/recommendation may not |
| GUI consumption | Typed local bundle only | GUI validates and renders; it does not recompute domain logic |

## Exact next actions

1. Preserve the accepted five-file generation, frozen digest, and sealed
   holdout; require `backtest-input-readiness/v1` before adding a new versioned
   consumer, without changing accepted replay semantics.
2. Continue PIT-safe Forward EPS/revision/ROE evidence work through the Data
   research contract. Run the three-axis regime evaluation only after all three
   axes are contract-valid.
3. For new model or portfolio work, predeclare identity, inputs, clocks, split,
   costs, and acceptance criteria before numeric comparison. Keep winner
   selection and user-specific ranking out until their contracts are closed.
4. Reuse broker-neutral strategy/risk interfaces for historical and local
   simulation, but ensure local simulation can never reach a broker mutation
   endpoint.

## Operational boundaries

- Backtest code imports no provider credentials or Data collector code.
- Data source semantics and availability come from the selected Data contract;
  Backtest does not reinterpret them.
- Historical fill/accounting belongs in `market_backtest`, not GUI or provider
  code.
- Live private account state is not a research input. Any future account-impact
  view must consume a separate sanitized projection.
- A descriptive state is not a recommendation, order, executable instrument,
  or validated performance claim.
- Normal unit tests do not make live API calls.

## Resume route

```text
AGENTS.md
  -> docs/project/PROJECT_STATUS.md
  -> docs/backtest/BACKTEST_STATUS.md
  -> exactly one owning contract above
```

Read Data Status and one selected Dataset Contract only when a Backtest task
needs retained-input semantics. Read Project Roadmap only for architecture or
sequencing and Repository Map only for placement. Do not scan archives,
provider guides, runbooks, or the full Data tree by default.
