# Explainable Action-State and Portfolio-Adjustment Contract

Status: `DOCUMENTATION_ONLY / IMPLEMENTATION_NOT_SELECTED`

Contract version: `action-state/v1`

## Purpose and non-execution boundary

This contract defines a future Backtest-owned, read-only output that can connect
validated descriptive/research evidence with an accepted sanitized holdings
projection. It describes items for human review; it is not an order,
recommendation, suitability decision, guaranteed-return claim, or permission to
trade.

Data and account domains own their exact local inputs and freshness. Backtest
owns rule/strategy identity, policy gates, portfolio arithmetic, and explanatory
output. A future GUI may render only the typed result. It must not calculate
signals, weights, costs, tax, FX, liquidity, concentration, or orders.

## Closed envelope

An `action-state/v1` document has exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `contract_version` | string | Exactly `action-state/v1`. |
| `vocabulary_version` | string | Exactly `action-state-vocabulary/v1`; binds every enum in the closed vocabulary section. |
| `output_id` | stable string | Binds all input, policy, rule, configuration, and code identities. |
| `output_state` | enum | `REVIEW_ELIGIBLE`, `SCENARIO_ONLY`, `UNAVAILABLE`, or `INVALID`. |
| `generated_at` | timezone-aware timestamp | Composition time only, never evidence as-of. |
| `decision_time` | timezone-aware timestamp | Exact decision boundary for all eligible evidence. |
| `policy_binding` | `PolicyBinding` | Exact accepted user policy/revision or versioned generic scenario. |
| `rule_binding` | `RuleBinding` | Exact accepted strategy/rule/model and validation identity. |
| `account_binding` | `AccountBinding` or null | Sanitized read-only holdings/NAV projection; never a raw broker payload. |
| `market_bindings` | ordered array of `MarketBinding` | Exact instrument price, FX, benchmark, risk, and other evidence identities. |
| `instrument_actions` | ordered array of `InstrumentAction` | One independent result per exact instrument identity. |
| `portfolio_adjustment` | `PortfolioAdjustment` or null | Conserved current/proposed weights and separate impacts. |
| `uncertainty_codes` | ordered unique array of `UncertaintyCode` | Output-level uncertainty from the exact closed enum; no free-form provider errors. |
| `invalidation_conditions` | ordered array of versioned conditions | Conditions that retire this output. |
| `unavailable_reasons` | ordered array of `UnavailableReason` | Closed reason vocabulary. |

Unknown fields, duplicate identities, unstable ordering, nonfinite numbers,
digest mismatch, an unknown vocabulary value, a missing vocabulary version, or
an unbound rule make the output `INVALID` before any action state or proposed
weight is displayed.

## Exact review states

`ActionState` is exactly one of:

- `BUY_REVIEW` (`매수 검토`)
- `HOLD` (`보유`)
- `REDUCE_REVIEW` (`비중 축소 검토`)
- `SELL_REVIEW` (`매도 검토`)
- `UNAVAILABLE` (`판단 불가`)

The four named states are explanatory outputs of one accepted, versioned rule;
they are not broker instructions. `HOLD` is not permission to ignore a policy
breach, and `BUY_REVIEW` is not a promise of positive return. An invalid,
missing, stale, PIT-blocked, policy-ineligible, or unreconciled input yields
`UNAVAILABLE`; the system does not borrow another instrument's state or retain
an earlier state without its original as-of and explicit stale warning.

## Policy and rule identity

`PolicyBinding` contains `binding_kind`, `policy_id`, `policy_revision`, and
`scenario_assumption_id`.

- `USER_POLICY` requires an accepted `investment-policy/v1` in
  `READY_FOR_RESEARCH`, with exact non-null policy ID and positive revision.
  Only it may produce `output_state=REVIEW_ELIGIBLE`.
- `GENERIC_SCENARIO` requires an immutable scenario assumption identity and
  always produces `SCENARIO_ONLY`; it cannot consume private holdings or claim
  suitability.
- Missing, retired, changed, or inconsistent policy evidence makes a
  user-specific output unavailable.

`RuleBinding` contains the exact rule/strategy/model ID and version, training
input/feature/label/split/code digests, validation decision, executable-
instrument semantics, supported universe, rebalance rule, and holdout/release
status. A development candidate, descriptive dashboard state, close-price
proxy, or unreviewed holdout result is not an accepted action rule.

The exact policy revision and rule identity are immutable parts of `output_id`.
Any change invalidates the old output rather than silently recalculating it
under a new assumption.

## Evidence, time, PIT, and freshness

Each `MarketBinding` and instrument-level `EvidenceRef` contains:

- exact `EvidenceRole`, exact `EvidenceUseRole`, and exact
  dataset/series/artifact ID;
- contract/schema version and content digest;
- exact instrument/market/currency identity;
- observation/as-of, publication, available, usable-from, and retrieval times
  where the source contract defines them;
- value unit, return/accounting basis, finality and revision/vintage state;
- `predictive_pit_status` of `PIT_SAFE`, `PIT_LIMITED`, `PIT_BLOCKED`, or
  `UNKNOWN`; and
- `freshness_state` of `CURRENT_FOR_DECISION`, `EXPECTED_LAG`, `STALE`, or
  `UNKNOWN` under the exact source policy.

Every numeric or factual reason must reference one or more eligible evidence
IDs and the registered rule component that used them. Composition/retrieval/file
times cannot replace an observation or publication time. A successful refresh
does not imply fresh or PIT-safe evidence.

Only `PIT_SAFE` evidence that is current for the decision or under an explicitly
accepted expected-lag policy may support `REVIEW_ELIGIBLE`. `PIT_LIMITED`,
`PIT_BLOCKED`, stale, conflicting, or unknown evidence stays visible as a reason
for `UNAVAILABLE`; it is not filled, spliced, averaged away, or treated as zero.

## Closed vocabulary registry

All values below are exhaustive for `action-state-vocabulary/v1`. They are
case-sensitive serialized identifiers. A future value requires a new vocabulary
version and a new compatible action-state contract; it cannot be added by a GUI,
provider adapter, model, or configuration file. Unknown, misspelled, duplicated,
or version-mismatched values make the entire output `INVALID`.

### EvidenceRole

`EvidenceRef.evidence_role` and `MarketBinding.evidence_role` are exactly one of:

| Value | Sole semantic use |
| --- | --- |
| `MARKET_PRICE` | Exact instrument or index price/return observation. |
| `MARKET_BENCHMARK` | Accepted benchmark identity and observation. |
| `MACRO` | Macroeconomic level, change, release, or regime evidence. |
| `INTEREST_RATE` | Policy, sovereign, credit, or funding-rate evidence with exact tenor/basis. |
| `VALUATION` | Valuation measure with exact horizon, aggregation, universe, and unit. |
| `TECHNICAL` | Price/volume-derived indicator under an exact formula and window. |
| `SENTIMENT` | Survey, positioning, or sentiment measure under an exact source definition. |
| `DERIVATIVES` | Futures/options measure with exact contract, expiry, session, and unit semantics. |
| `ACCOUNT_HOLDING` | Sanitized read-only position quantity, basis, or holding identity. |
| `ACCOUNT_CASH` | Sanitized read-only cash balance and currency identity. |
| `FX_RATE` | Exact currency pair, direction, fixing/quote basis, and observation. |
| `LIQUIDITY` | Volume, spread, capacity, or liquidation-time evidence. |
| `RISK` | Independently defined volatility, drawdown, stress, or factor-risk evidence. |
| `TRANSACTION_COST` | Commission, spread, slippage, or market-impact evidence/assumption. |
| `TAX` | Versioned jurisdiction/account/instrument tax evidence or assumption. |
| `FINANCING_AND_PRODUCT` | Financing, borrow/margin, cash yield, expense ratio, or tracking-error evidence. |
| `CONCENTRATION` | Issuer/sector/currency/factor exposure evidence under an exact formula. |
| `CORPORATE_ACTION` | Split, dividend, merger, delisting, or other instrument-event evidence. |
| `TRADABILITY` | Venue/instrument eligibility, suspension, market-hours, or execution-availability evidence. |

These roles are non-substitutable. For example, `MARKET_PRICE` cannot fill
`FX_RATE`, `VALUATION` cannot fill `TECHNICAL`, and `ACCOUNT_HOLDING` cannot fill
`ACCOUNT_CASH`. One retained artifact may appear in multiple refs only when its
accepted source contract independently defines each exact series/field for each
role; each use then has a distinct evidence ID, series/field identity, unit,
time/PIT record, and registered rule-component reference. A correlation,
derivation, shared filename, or convenient numeric scale never authorizes a role
change.

### EvidenceUseRole

`EvidenceRef.evidence_use_role` and `MarketBinding.evidence_use_role` are each
exactly one of:

| Value | Meaning |
| --- | --- |
| `FACT` | A source-grounded observation used without interpretive relabelling. |
| `RULE_INTERPRETATION` | A deterministic result of the exact registered rule component over cited facts. |
| `UNCERTAIN_INFERENCE` | A labelled inference whose method, uncertainty, and invalidation conditions are explicit. |

`OPINION` is intentionally not a member. A rule interpretation or inference
must cite its source evidence IDs and rule-component identity; it cannot replace
a missing `FACT` required by that rule.

### UncertaintyCode

Every output-, metric-, and instrument-level `uncertainty_codes` field is an
ordered unique array containing only:

| Value | Qualification |
| --- | --- |
| `SOURCE_PROVISIONAL` | Source finality is provisional or revision-prone. |
| `SOURCE_REVISION_POSSIBLE` | A final-looking value remains subject to the source's accepted revision policy. |
| `PIT_LIMITED` | Availability is bounded but not fully predictive-PIT-safe. |
| `PIT_BLOCKED` | Evidence is known to be unusable at the decision time. |
| `PIT_UNKNOWN` | Required availability/vintage semantics are unresolved. |
| `EXPECTED_LAG` | Currentness relies on an explicitly accepted expected-lag policy. |
| `STALE_EVIDENCE` | Evidence is older than its decision-time freshness policy permits. |
| `FRESHNESS_UNKNOWN` | Currentness cannot be established. |
| `PARTIAL_COVERAGE` | Universe, history, position, currency, or component coverage is incomplete. |
| `METHODOLOGY_LIMITED` | An accepted method has a documented, bounded limitation. |
| `MODEL_UNCALIBRATED` | Model uncertainty is not calibrated for the claimed use. |
| `ACCOUNT_VALUATION_ESTIMATE` | A sanitized account value is an estimate rather than broker truth. |
| `COST_ESTIMATE` | Cost input is an explicit estimate. |
| `TAX_ESTIMATE` | Tax input is an explicit estimate. |
| `FX_ESTIMATE` | FX conversion input is an explicit estimate. |
| `LIQUIDITY_ESTIMATE` | Liquidity/capacity input is an explicit estimate. |
| `CONCENTRATION_ESTIMATE` | Concentration impact contains an explicit estimate. |
| `INSTRUMENT_MAPPING_UNRESOLVED` | Evidence-to-executable-instrument mapping is unresolved. |

An uncertainty code is a qualifier, not permission to bypass eligibility.
`PIT_LIMITED`, `PIT_BLOCKED`, `PIT_UNKNOWN`, `STALE_EVIDENCE`,
`FRESHNESS_UNKNOWN`, `MODEL_UNCALIBRATED`, and
`INSTRUMENT_MAPPING_UNRESOLVED` block `REVIEW_ELIGIBLE` whenever they affect a
required input or gate. Other codes remain visible and follow the exact accepted
policy/rule; no code may be silently dropped to improve the output state.

### UnavailableReason

Every output-, metric-, and instrument-level `unavailable_reasons` field is an
ordered unique array containing only:

| Value | Exact fail-closed cause |
| --- | --- |
| `POLICY_NOT_ACCEPTED` | No exact accepted user policy/revision exists. |
| `POLICY_INELIGIBLE` | The policy forbids the instrument, exposure, or proposed state. |
| `RULE_NOT_ACCEPTED` | Rule identity, validation, release, or executable semantics are not accepted. |
| `RULE_UNIVERSE_UNSUPPORTED` | Instrument is outside the exact supported rule universe. |
| `EVIDENCE_MISSING` | A required evidence ref is absent. |
| `EVIDENCE_CONFLICT` | Required evidence sources or identities conflict. |
| `EVIDENCE_ROLE_MISMATCH` | A required `EvidenceRole` was substituted or mislabelled. |
| `EVIDENCE_SCHEMA_MISMATCH` | Contract/schema/vocabulary version is incompatible. |
| `EVIDENCE_DIGEST_MISMATCH` | Retained content does not match its bound digest. |
| `PIT_NOT_SAFE` | Required evidence is limited, blocked, unknown, or late at decision time. |
| `FRESHNESS_NOT_ELIGIBLE` | Required evidence is stale or has unknown/unaccepted lag. |
| `ACCOUNT_NOT_AVAILABLE` | Required sanitized holdings/cash projection is absent. |
| `ACCOUNT_NOT_RECONCILED` | Holdings, cash, prices, FX, units, or NAV do not reconcile. |
| `PRICE_NOT_AVAILABLE` | Required `MARKET_PRICE` evidence is unavailable. |
| `FX_NOT_AVAILABLE` | Required `FX_RATE` evidence is unavailable. |
| `BENCHMARK_NOT_AVAILABLE` | Required `MARKET_BENCHMARK` evidence is unavailable. |
| `METHODOLOGY_NOT_AVAILABLE` | Required metric has no accepted exact method. |
| `COST_NOT_AVAILABLE` | Required transaction/financing/product cost is unavailable. |
| `TAX_NOT_AVAILABLE` | Required tax component is unavailable. |
| `LIQUIDITY_NOT_AVAILABLE` | Required liquidity/capacity component is unavailable. |
| `CONCENTRATION_NOT_AVAILABLE` | Required concentration component is unavailable. |
| `RISK_NOT_AVAILABLE` | Required risk/stress component is unavailable. |
| `CURRENCY_OR_UNIT_MISMATCH` | Required units, scale, currency, pair direction, or accounting basis conflict. |
| `PORTFOLIO_NOT_CONSERVED` | Current/proposed/cash weights or impacts fail accounting invariants. |
| `POLICY_GATE_UNKNOWN` | A required policy gate cannot be evaluated. |
| `POLICY_GATE_BREACHED` | A required policy gate fails. |
| `INSTRUMENT_NOT_ELIGIBLE` | Instrument is not permitted or cannot be mapped to an executable identity. |
| `TRADABILITY_UNKNOWN` | Required tradability/session/suspension state is unavailable. |
| `CORPORATE_ACTION_UNRESOLVED` | A required instrument event is unresolved. |
| `OUTPUT_INVALIDATED` | A registered invalidation condition has occurred. |

`UNAVAILABLE` requires at least one reason at the affected level. `INVALID`
requires the most specific applicable reason(s) when the envelope remains
parseable; unparseable or unknown enum values are rejected before display and
must not be converted into a fallback reason. Available numeric values cannot
coexist with a reason that declares that same required value unavailable.

### Goal-family and failure mapping

| Required evidence/failure family | Required enum identity |
| --- | --- |
| Market / benchmark | `MARKET_PRICE`, `MARKET_BENCHMARK` |
| Macro / rates | `MACRO`, `INTEREST_RATE` |
| Valuation / technical / sentiment | `VALUATION`, `TECHNICAL`, `SENTIMENT` |
| Derivatives | `DERIVATIVES` |
| Holdings / cash / multi-currency valuation | `ACCOUNT_HOLDING`, `ACCOUNT_CASH`, `FX_RATE` |
| Risk / liquidity / concentration | `RISK`, `LIQUIDITY`, `CONCENTRATION` |
| Cost / tax / financing and product | `TRANSACTION_COST`, `TAX`, `FINANCING_AND_PRODUCT` |
| Corporate action / executable availability | `CORPORATE_ACTION`, `TRADABILITY` |
| Missing, conflict, role, schema, digest | `EVIDENCE_MISSING`, `EVIDENCE_CONFLICT`, `EVIDENCE_ROLE_MISMATCH`, `EVIDENCE_SCHEMA_MISMATCH`, `EVIDENCE_DIGEST_MISMATCH` |
| PIT / freshness | `PIT_NOT_SAFE`, `FRESHNESS_NOT_ELIGIBLE` |
| Policy / rule / universe | `POLICY_NOT_ACCEPTED`, `POLICY_INELIGIBLE`, `RULE_NOT_ACCEPTED`, `RULE_UNIVERSE_UNSUPPORTED` |
| Account / price / FX / benchmark | `ACCOUNT_NOT_AVAILABLE`, `ACCOUNT_NOT_RECONCILED`, `PRICE_NOT_AVAILABLE`, `FX_NOT_AVAILABLE`, `BENCHMARK_NOT_AVAILABLE` |
| Method / costs / risk gates | `METHODOLOGY_NOT_AVAILABLE`, `COST_NOT_AVAILABLE`, `TAX_NOT_AVAILABLE`, `LIQUIDITY_NOT_AVAILABLE`, `CONCENTRATION_NOT_AVAILABLE`, `RISK_NOT_AVAILABLE`, `POLICY_GATE_UNKNOWN`, `POLICY_GATE_BREACHED` |
| Accounting / eligibility / event / invalidation | `CURRENCY_OR_UNIT_MISMATCH`, `PORTFOLIO_NOT_CONSERVED`, `INSTRUMENT_NOT_ELIGIBLE`, `TRADABILITY_UNKNOWN`, `CORPORATE_ACTION_UNRESOLVED`, `OUTPUT_INVALIDATED` |

## Sanitized read-only account binding

`AccountBinding` may consume only an accepted local sanitized projection. It
contains an opaque local snapshot identity, source kind, snapshot as-of,
projection version, base currency, independently timestamped price/FX
references, reconciled NAV status, cash and position weights, and coverage.

Broker account numbers, credentials, tokens, raw responses, order history,
unnecessary personal fields, and free-form broker errors are forbidden. This
contract does not call an account provider or refresh an account.

Current weights require a reconciled NAV whose holdings, cash, prices, FX, and
units are all valid at their separately preserved timestamps. If any required
component is invalid, affected current weights and every dependent proposed
weight are null and the action is `UNAVAILABLE`. Broker holdings remain the
external source of quantity/cash truth; an internal valuation is labelled an
estimate and never overwrites them.

Private account data is not a training feature, label, threshold input, or
historical-validation input. It is applied only after the accepted rule output
is fixed, to compute current explanatory portfolio impact.

## Instrument action

Each `InstrumentAction` has exactly:

- exact instrument ID, instrument type, market, and currency;
- policy/rule/output identity references;
- `action_state`;
- current and proposed weight (fraction of reconciled NAV) or null;
- signed weight change or null;
- ordered unique evidence refs with exact `EvidenceRole` and
  `EvidenceUseRole` (`FACT`, `RULE_INTERPRETATION`, or
  `UNCERTAIN_INFERENCE`); `OPINION` is forbidden in this typed result;
- exact ordered unique `UnavailableReason` and `UncertaintyCode` arrays;
- exact as-of/usable cutoff and freshness qualification;
- expected portfolio/risk impact references;
- separate cost/tax/FX/liquidity/concentration impact references; and
- versioned invalidation conditions.

An instrument outside the accepted rule universe or the account/policy's
permitted instrument set is `UNAVAILABLE`. The state cannot be inferred from a
name, sector, current holding, model score, or prior action. Proposed weight is
null unless the complete portfolio adjustment passes every invariant below.

## Conserved portfolio adjustment

`PortfolioAdjustment` contains ordered `current_weights`, ordered
`proposed_weights`, explicit cash weights, signed deltas, base currency,
valuation as-of references, gross/net exposure, and `conservation_tolerance`.

The same exact instrument universe (including explicit zero weights) is used on
both sides. Within the fixed numeric tolerance:

1. current instrument weights plus current cash equal `1`;
2. proposed instrument weights plus proposed cash equal `1`;
3. every signed delta equals proposed minus current;
4. deltas including cash sum to `0`;
5. gross/net exposure and leverage reconcile from the displayed weights; and
6. no weight, short exposure, borrowing, or cash deficit violates the exact
   accepted policy and executable-instrument rule.

Rounding occurs only after validation for display. Residual cash, fees, taxes,
FX conversion, and financing are explicit components, not hidden adjustments
used to force conservation. If the system cannot conserve weights and costs
under one exact accounting identity, `portfolio_adjustment` is null and every
dependent action is unavailable.

## Separate portfolio and risk impacts

Before/after/delta values remain independent for:

- cash fraction, gross and net exposure, and effective leverage;
- single-position, issuer, sector, industry, currency, and correlated-factor
  concentration under exact formula identities;
- benchmark active weight and tracking-risk estimate;
- volatility, drawdown/stress-loss, and loss-limit estimates under accepted
  research methods;
- liquidity capacity and liquidation-time estimate under exact volume/spread
  semantics; and
- every confirmed policy risk, concentration, cash, margin, and loss gate.

Missing methodology or evidence makes only the affected metric unavailable but
blocks a proposal when that metric is a confirmed policy gate. A favorable
return or one passed limit cannot offset another breached/unknown gate.

## Separate cost, tax, FX, liquidity, and concentration components

Each component reports amount, base currency, fraction of NAV, input/rule
identity, as-of, uncertainty, and availability independently:

- `TRANSACTION_COST`: commissions, spread/slippage assumption, and market impact
  are separately attributable under the registered execution model.
- `TAX`: exact versioned jurisdiction/account/tax-policy identity; unknown tax
  is unavailable, never zero.
- `FX`: currencies, conversion direction, exact retained FX series/as-of, and
  conversion cost; no silent currency netting.
- `LIQUIDITY`: tradable capacity, participation assumption, days/time to trade,
  spread/impact overlap, and stale-volume qualification.
- `CONCENTRATION`: current/proposed/delta exposure under each exact concentration
  formula; it is an impact, not a monetary fee.
- `FINANCING_AND_PRODUCT`: financing rate, borrow/margin cost, cash yield,
  expense ratio, and tracking error where applicable.

No component may be omitted because it is unfavorable or unknown. Monetary
components may be summed only after each is available under the same base
currency and horizon; the typed output still preserves every component. Costs,
tax, FX, liquidity, concentration, and risk are never compressed into an
unexplained score.

## Uncertainty and invalidation

Uncertainty uses closed codes and binds the affected evidence/metric/action.
Required invalidation conditions include, where applicable:

- new source observation or accepted revision;
- evidence becoming stale or PIT-ineligible;
- account, position price, FX, or reconciled NAV generation changing;
- policy or rule revision changing;
- instrument eligibility, corporate action, liquidity, or tradability changing;
- any risk/cost/tax/FX/concentration gate becoming unknown or breached; and
- validation/release status being withdrawn.

After invalidation the output is not silently retained as current. A GUI may
show the prior result only as stale historical evidence with its original
timestamps and no proposed weight/action eligibility.

## Fail-closed eligibility

`REVIEW_ELIGIBLE` requires all of the following conjunctively:

1. exact accepted `USER_POLICY` ID/revision;
2. an accepted rule with supported executable-instrument semantics and required
   validation/release status;
3. exact reconciled sanitized account, price, FX, benchmark, and other required
   input identities;
4. PIT/freshness eligibility for every required item;
5. conserved current/proposed weights and accounting;
6. independently available cost, tax, FX, liquidity, concentration, and risk
   components required by the policy/rule; and
7. every applicable policy gate passes.

Any missing, stale, blocked, conflicting, invalid, or breached required item
fails closed to `UNAVAILABLE` or `INVALID`. A generic scenario remains
`SCENARIO_ONLY` and cannot use private holdings. No higher score, expected
return, or historical result overrides these gates.

## Separation from the accepted close-proxy foundation

The accepted Phase-1/close-proxy result is not action-state evidence:

| Close-proxy fact | Required interpretation here |
| --- | --- |
| KOSPI200 retained close series | Not an executable instrument, current holding, price quote, or selected benchmark. |
| Normalized initial cash `1.0` | Not account NAV, cash balance, or portfolio weight evidence. |
| Long/cash-only exposure | Not a user policy, proposed allocation, or permission to trade. |
| Fixed one-way 10 bp hypothetical cost | Not current execution cost, tax, spread, FX, liquidity, or user assumption. |
| Zero cash yield | Not financing, opportunity-cost, or cash-policy evidence. |
| Development metrics | Not strategy release, suitability, or expected future performance. |
| Untouched 1,222-observation holdout | Remains uninspected; this contract does not authorize action generation from it. |

Therefore no current accepted artifact may be presented as an executable
holding or proposed portfolio adjustment merely by adopting this document.

## Project Goal requirement map

| Project Goal action/adjustment requirement | Contract field or invariant |
| --- | --- |
| Four Korean review states | Exact `BUY_REVIEW`, `HOLD`, `REDUCE_REVIEW`, and `SELL_REVIEW` plus fail-closed `UNAVAILABLE`. |
| Connect market/macro/rates/valuation/technical/sentiment/derivatives/holdings/cash evidence | Exact non-substitutable `EvidenceRef`/`MarketBinding` roles with rule identity; unavailable roles remain unavailable. |
| Show source time, evidence, uncertainty, and invalidation | Independent evidence timestamps/PIT/freshness, uncertainty codes, and versioned invalidation conditions. |
| Current and proposed weights | Reconciled sanitized account binding and conserved ordered current/proposed/cash weights. |
| Explain risk and portfolio impact | Separate before/after/delta exposure, concentration, benchmark, volatility, drawdown/stress, liquidity, and policy gates. |
| Show transaction cost, tax, FX, liquidity, and concentration | Independent typed components with units, sources, availability, and no hidden net score. |
| Bind user horizon/benchmark/risk/cash/cost choices | Exact accepted `investment-policy/v1` ID and revision; all applicable gates are conjunctive. |
| Exploration is not automatic recommendation | Human-review semantics, no executable fields, no order/guarantee/suitability claim, and final user decision. |
| Preserve current close-proxy meaning and sealed holdout | Explicit non-executable separation table and untouched holdout invariant. |

## Document boundary

This document authorizes no runtime implementation, provider/account call,
Data/account mutation, model/rule release, optimization, holdout inspection,
GUI calculation, order, transfer, or external financial action. A separate
claimed task must establish an accepted executable-instrument rule and strict
local implementation/tests before any numeric action state or portfolio
adjustment is available.

`action-state-vocabulary/v1` is inseparable from `action-state/v1`. Any enum
addition, deletion, rename, semantic broadening, or role reassignment requires
a new vocabulary and compatible contract version plus a separate claimed
implementation task. Configuration aliases and unknown-code passthrough are
forbidden.
