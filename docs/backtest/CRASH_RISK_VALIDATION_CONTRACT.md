# Multi-Factor Crash-Risk Validation Contract

Status: `DOCUMENTATION_ONLY / IMPLEMENTATION_NOT_SELECTED`

Contract version: `crash-risk-validation/v1`

## Purpose and claim boundary

This contract defines a future Backtest-owned, point-in-time research boundary
for describing observable market-risk states, historical stress validation,
current-account impact, and defensive research candidates. It does not label an
asset or market a bubble, predict every crash, estimate a guaranteed crash
probability, recommend a trade, or authorize an order.

Data owns source contracts, collection, finality, revision lineage, and PIT
availability. Backtest may consume only frozen, contract-validated local
inputs. A future GUI may display a validated typed result but must not calculate
factors, risk states, portfolio loss, or defensive actions.

## Closed result envelope

A `crash-risk-validation/v1` result has exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `contract_version` | string | Exactly `crash-risk-validation/v1`. |
| `research_run_id` | stable string | Binds exact inputs, code, configuration, policy, and validation split. |
| `result_state` | enum | `AVAILABLE`, `PARTIALLY_AVAILABLE`, `UNAVAILABLE`, or `INVALID`. |
| `decision_time` | timezone-aware timestamp | Historical/current decision boundary. |
| `usable_information_cutoff` | timezone-aware timestamp | Latest information the decision may use. |
| `policy_binding` | `PolicyBinding` | Exact accepted `investment-policy/v1` policy/revision or a versioned generic scenario. |
| `factor_bindings` | ordered array of `FactorBinding` | One independently validated binding for every required factor role. |
| `factor_states` | ordered array of `FactorState` | Observable factor evidence; no hidden composite inputs. |
| `market_risk_state` | `MarketRiskState` | Derived descriptive state or `UNKNOWN`. |
| `state_evidence` | ordered array of stable evidence IDs | Exact factor/rule references supporting the state. |
| `uncertainty` | ordered array of stable uncertainty codes | Missing, stale, conflicting, or low-coverage qualifications. |
| `invalidation_conditions` | ordered array of versioned rules | Conditions that make the current state no longer applicable. |
| `account_impact` | `AccountImpact` | Optional, sanitized, read-only projection; independently unavailable. |
| `defensive_candidates` | ordered array of `DefensiveCandidate` | Explanatory research candidates, never executable actions. |
| `validation_binding` | `ValidationBinding` | Frozen inputs, split/purge/embargo, stress windows, and sealed holdout identity. |
| `validation_metrics` | `ValidationMetrics` or null | Development/test evidence only; null when validation is unavailable. |
| `unavailable_reasons` | ordered array of `UnavailableReason` | Closed reason vocabulary. |

Unknown fields, duplicate identities, unstable ordering, nonfinite values,
unbound configuration, or digest mismatch makes the result `INVALID` before any
state or account impact is displayed.

## Market-risk states

`MarketRiskState` is exactly one of:

- `BASELINE`: complete required evidence triggers none of the four registered
  risk-state rules.
- `OVERHEATING_POSSIBLE`: pre-registered valuation and/or concentration evidence
  is elevated while the required trend/drawdown rule does not establish an
  ongoing break. This is not a bubble claim or crash forecast.
- `RISK_EXPANDING`: the registered combination of worsening breadth,
  credit/liquidity, volatility, derivatives, concentration, or macro evidence
  passes while an ongoing crash is not established.
- `TREND_DAMAGED`: the registered price/trend deterioration rule passes with
  exact PIT-safe market evidence; it does not assert that a crash must follow.
- `DRAWDOWN_IN_PROGRESS`: the registered contemporaneous drawdown/stress rule
  passes. It describes observed loss, not advance prediction.
- `UNKNOWN`: one or more required factor, timing, freshness, PIT, or rule
  identities are missing, blocked, stale beyond policy, or invalid.

The four named risk states are distinct descriptive classifications, not a
guaranteed linear sequence. Rules and precedence are versioned before a
validation run. A later outcome cannot relabel an earlier decision. The output
must expose the evidence, uncertainty, and invalidation conditions rather than
compressing them into an unexplained alarm score.

## Required factor identities

Every factor role is independently bound. A `FactorBinding` contains
`factor_role`, exact dataset/series ID, contract version, content digest,
observation/as-of time, publication time, available time, usable-from time,
frequency, unit, aggregation/weighting rule where applicable, finality and
revision/vintage state, `predictive_pit_status`, and `freshness_state`.

Required `factor_role` values are:

| Factor role | Required semantic boundary |
| --- | --- |
| `VALUATION` | Exact trailing/forward/current meaning, forecast horizon, numerator/denominator, market/sector aggregation and weighting, currency, finality, and vintage. |
| `MARKET_BREADTH` | Point-in-time universe/membership denominator, numerator, formula, coverage, and survivorship-safe identity. |
| `INDUSTRY_CONCENTRATION` | Point-in-time taxonomy/membership, weight basis, concentration formula, and exact industry/issuer identities. |
| `CREDIT_LIQUIDITY` | Credit or liquidity meaning, unit, release/vintage timing, revision policy, and frequency; unlike series are not spliced. |
| `TREND` | Exact market/instrument price-return basis, calendar, lookback, corporate-action treatment, and decision timing. |
| `VOLATILITY` | Realized/implied identity, horizon, annualization, market session, and observation/finality semantics. |
| `DERIVATIVES` | Exact futures/options/basis/PCR/position meaning, contract/expiry or aggregation identity, unit, and publication timing. |
| `MACRO_ENVIRONMENT` | Exact growth/inflation/employment/policy/liquidity role, release time, reference period, frequency, seasonal adjustment, vintage and revision lineage. |

`predictive_pit_status` is exactly `PIT_SAFE`, `PIT_LIMITED`, `PIT_BLOCKED`, or
`UNKNOWN`. `freshness_state` is exactly `CURRENT_FOR_DECISION`,
`EXPECTED_PUBLICATION_LAG`, `STALE`, or `UNKNOWN`.

Every configured required factor must be `PIT_SAFE` and current or under an
explicit pre-registered expected-lag rule before a non-`UNKNOWN` composite
state is eligible. A blocked factor is never replaced by a similarly named
field, current revised macro value, market-wide proxy, forward fill, another
provider, or another factor role.

Current Backtest authority makes only Price and Volatility families evaluable.
FX, Breadth, Flow, and Derivatives remain unavailable under the accepted
ablation boundary; accepted predictive Valuation, Industry Concentration,
Credit/Liquidity, and Macro roles are also not established here. Therefore this
document creates no current numeric risk state. Missing families remain
`UNKNOWN`, not neutral and not zero.

## Independent factor states and composition

A `FactorState` contains the factor role/binding identity, registered rule
version, observation, normalized state (if the rule defines one), historical
as-of-only reference distribution, coverage, evidence IDs, uncertainty codes,
and an independent status of `ELEVATED`, `NORMAL`, `CONFLICTING`, or `UNKNOWN`.

- `NORMAL` requires complete PIT-safe evidence; missing data cannot look calm.
- `CONFLICTING` remains visible and cannot be averaged away.
- A successful refresh does not imply semantic availability or freshness.
- Factor normalization is allowed only under its pre-registered historical
  as-of-only transformation. Different units never share an unlabeled scale.
- The composite state is fail-closed `UNKNOWN` whenever a required factor is
  not eligible. Optional-factor rules, if a future version permits them, must be
  explicit in the registered configuration and cannot be selected after seeing
  outcomes.

## Policy binding

`PolicyBinding` contains `binding_kind`, `policy_id`, `policy_revision`, and
`scenario_assumption_id`.

- `USER_POLICY` requires an accepted `investment-policy/v1` document in
  `READY_FOR_RESEARCH`, with the exact stable ID and positive revision.
- `GENERIC_SCENARIO` requires an immutable versioned assumption identity and
  cannot produce a user-specific account defense or suitability claim.
- Missing, retired, or inconsistent policy evidence makes policy-gated
  defensive candidates unavailable. It does not alter historical market factor
  observations.

All applicable loss, drawdown, volatility, leverage, concentration, cash,
margin-buffer, benchmark, liquidity, transaction-cost, tax, financing, product,
and FX choices are conjunctive gates. Passing one never offsets another.

## Read-only account impact boundary

Account data is never a training feature, threshold input, market-risk-state
input, or validation label. `AccountImpact` is a separate current projection
that consumes only an accepted sanitized read-only account/NAV view after the
market state is fixed.

Its allowed fields are:

- opaque local snapshot identity and source kind, never a broker account number;
- account snapshot as-of, each position-price as-of, FX as-of, and projection
  composition time as separate timestamps;
- base currency and independently validated NAV, cash, gross/net exposure,
  position/sector/industry weights, and leverage;
- concentration measures under an exact versioned formula;
- stress-window loss estimates by position/currency/factor plus portfolio total;
- coverage, uncertainty, stale/missing components, and policy-limit results.

Raw provider responses, credentials, account identifiers, order history,
free-form broker errors, and unnecessary personal fields are forbidden. An
invalid price, FX rate, holding identity, NAV component, or timestamp makes the
affected exposure/loss unavailable. If an exact NAV cannot reconcile, the
portfolio total is not displayed as a confirmed amount. Account impact may be
`UNKNOWN` while the independently evidenced market-risk state remains valid.

## Defensive candidate boundary

A `DefensiveCandidate` has a `candidate_kind`, rationale/evidence IDs,
preconditions, expected risk effect, uncertainty, invalidation conditions,
separately attributed costs, policy-gate results, and
`candidate_state=RESEARCH_ONLY|EXCLUDED|UNAVAILABLE`.

Allowed `candidate_kind` values are:

- `EXPOSURE_REDUCTION_REVIEW`
- `DIVERSIFICATION_REVIEW`
- `CASH_BUFFER_REVIEW`
- `HEDGE_RESEARCH_REVIEW`
- `NO_CHANGE_REVIEW`

A candidate may be `RESEARCH_ONLY` only when the exact user policy revision,
account impact, instrument/product semantics, liquidity, costs, tax, FX, and
risk gates required by that candidate are valid. Otherwise it is excluded or
unavailable. The output contains no order side, quantity, executable price,
broker route, target account mutation, or automatic action. A hedge candidate
does not claim effectiveness until its exact instrument and costs pass separate
PIT-safe historical validation.

## Versioned stress windows

Each `StressWindow` contains a stable window ID/version, start/end decision
times, market/calendar identity, selection rationale recorded before the run,
input-coverage requirements, and whether it is a development diagnostic or
locked test window. Named episodes such as the dot-com decline, global financial
crisis, or COVID decline are diagnostic examples only after their exact dates
and selection rule are versioned.

Known crisis dates, drawdown bottoms, later labels, and rebounds cannot choose
features, thresholds, state precedence, factor roles, windows, purge, embargo,
or policy limits. Rolling non-event windows must also be included so evaluation
does not condition only on known crashes.

## Validation metrics

`ValidationMetrics` reports every metric with exact horizon, denominator,
coverage, benchmark, cost basis, and confidence/uncertainty. It must include:

- state coverage and time spent in each state;
- event definition, true-event count, false-alarm count and rate;
- warning lead/lag distribution without discarding late or missed events;
- early-exit count and cost under the registered defensive rule;
- rebound opportunity-cost return after each defensive exit;
- strategy and benchmark maximum drawdown, volatility, and downside loss;
- avoided loss and missed upside reported separately, never netted invisibly;
- turnover and separately attributed transaction cost, tax, FX, financing,
  product/hedge expense, slippage assumption, and cash drag;
- concentration and stressed account-loss change where sanitized account
  scenario inputs are valid; and
- sensitivity/stability across factor vintages, taxonomy revisions, thresholds,
  and stress/non-event windows.

`false_alarm` means the registered warning state occurred but the pre-registered
drawdown event definition did not occur within its exact evaluation horizon.
`early_exit_cost` and `rebound_opportunity_cost` use the registered executable-
instrument research proxy and benchmark; close-proxy results cannot be
presented as executable portfolio savings.

No aggregate accuracy, return, or avoided-loss number may hide unavailable
factors, missing events, failed policy limits, or cost components.

## PIT-safe split and holdout invariants

`ValidationBinding` contains the frozen manifest digest, code/config digest,
factor-rule versions, ordered split identity, train/validation/test coverage,
event and metric horizons, purge sessions, embargo sessions, stress-window
registry digest, policy binding, and final-holdout identity with
`holdout_results_reviewed=false`.

1. Inputs are sliced by `usable_information_cutoff` before factors, states,
   account scenarios, defensive candidates, or labels are constructed.
2. Original release vintages are used. Later-revised values cannot appear in an
   earlier decision unless that exact revision was then available.
3. Training labels must be available before the earliest validation/test
   decision. Purge is at least the maximum event/label horizon and embargo is
   fixed before evaluation.
4. Preprocessing, thresholds, state precedence, candidate rules, and any model
   fit only inside each training boundary.
5. Stress windows are diagnostic slices, never an optimization shortcut.
6. The existing final 1,222-observation holdout remains untouched. Its factors,
   labels, predictions, risk states, crisis outcomes, rankings, account impacts,
   and metrics are not constructed or inspected during development.
7. The same frozen inputs, identities, configuration, and code must reproduce
   byte-identical typed results before later implementation acceptance.

Historical validation reports how a registered rule behaved in retained
periods. It does not promise to identify or prevent a future crash.

## Unavailable and uncertainty vocabulary

The version-1 `UnavailableReason` vocabulary is exactly:

- `POLICY_UNRESOLVED`
- `FACTOR_BINDING_MISSING`
- `PIT_BLOCKED`
- `SOURCE_STALE`
- `PUBLICATION_TIME_UNKNOWN`
- `VINTAGE_OR_REVISION_UNKNOWN`
- `TIMING_MISMATCH`
- `UNIT_OR_SEMANTIC_MISMATCH`
- `FACTOR_CONFLICT`
- `INSUFFICIENT_COVERAGE`
- `RULE_IDENTITY_MISSING`
- `ACCOUNT_PROJECTION_UNAVAILABLE`
- `ACCOUNT_NAV_UNRECONCILED`
- `PRICE_OR_FX_UNAVAILABLE`
- `POLICY_GATE_FAILED`
- `COST_EVIDENCE_UNAVAILABLE`
- `STRESS_WINDOW_INELIGIBLE`
- `VALIDATION_INELIGIBLE`
- `IDENTITY_OR_DIGEST_MISMATCH`

Unknown codes and free-form provider/account/exception text fail validation.

## Project Goal requirement map

| Project Goal crash-defense requirement | Contract field or invariant |
| --- | --- |
| Do not declare an AI/market bubble or promise crash prediction | Descriptive `MarketRiskState`, evidence/uncertainty, and explicit non-prediction boundary. |
| Distinguish overheating, expanding risk, trend damage, and drawdown in progress | Four exact named states with pre-registered rules plus `BASELINE`/`UNKNOWN`. |
| Combine valuation, breadth, concentration, credit/liquidity, trend, volatility, derivatives, and macro | Eight non-substitutable PIT/freshness-bound `factor_role` identities. |
| Show account concentration and expected loss impact | Separate sanitized read-only `AccountImpact` with timestamped NAV/price/FX/exposure and stress-loss coverage. |
| Review reduction, diversification, cash, and defensive tools with reason and cost | Typed non-executable `DefensiveCandidate` kinds, policy gates, uncertainty, invalidation, and separate costs. |
| Validate dot-com/GFC/COVID and non-event periods without leakage | Versioned stress windows, vintage slicing, non-event controls, purge/split rules, and diagnostic-only known episodes. |
| Report false alarms, early exits, rebound opportunity cost, drawdown, and costs | Mandatory separately defined `ValidationMetrics`. |
| Preserve reproducible test/final-holdout evidence | Frozen identities, byte reproducibility, purge/embargo, and untouched 1,222-observation holdout. |
| Respect user loss/risk/cash/leverage limits | Exact accepted policy revision and conjunctive fail-closed gates. |

## Document boundary

This document authorizes no implementation, provider call, Data contract or
mutation, account refresh, GUI behavior, strategy optimization, holdout review,
order, transfer, or other external financial action. A separate claimed task
must establish accepted Data inputs and a versioned offline implementation with
tests before any numeric risk state, account impact, or defensive candidate is
available.
