# Leverage Evaluation and Safety Contract

Status: `DOCUMENTATION_ONLY / IMPLEMENTATION_NOT_SELECTED`

Contract version: `leverage-evaluation/v1`

Related accepted boundaries:

- [Project Goal](../project/PROJECT_GOAL.md)
- [Backtest Status](BACKTEST_STATUS.md)
- [Investment Policy Contract](INVESTMENT_POLICY_CONTRACT.md)

## Purpose and non-execution boundary

This contract defines a future Backtest-owned, read-only comparison between one
exact leveraged research case and one point-in-time-matched unlevered baseline.
It preserves daily reset and path dependence, attributes costs without double
counting, tests leverage-specific loss and liquidation paths, and applies an
accepted user policy or a clearly labelled generic scenario.

It does not select a product, strategy, leverage level, provider, account,
execution venue, optimizer, or GUI implementation. It is not a recommendation,
suitability decision, expected-return claim, order, paper-broker instruction, or
permission to use margin. The current close-proxy result and sealed holdout are
outside this contract and remain unchanged.

## Closed envelope

A `leverage-evaluation/v1` document has exactly:

| Field | Type | Rule |
| --- | --- | --- |
| `contract_version` | string | Exactly `leverage-evaluation/v1`. |
| `evaluation_id` | stable digest-bound string | Binds every case, policy/scenario, data, path, cost, stress, rule, and code identity. |
| `output_state` | enum | `POLICY_ELIGIBLE_RESEARCH`, `POLICY_INELIGIBLE_RESEARCH`, `SCENARIO_ONLY`, `UNAVAILABLE`, or `INVALID`. |
| `generated_at` | aware timestamp | Composition time only; never evidence as-of. |
| `policy_binding` | `PolicyBinding` | Exact accepted policy revision or exact generic-scenario identity. |
| `data_binding` | `DataBinding` | Immutable PIT-safe source and executable-series manifest. |
| `leveraged_case` | `CaseDefinition` | One exact leveraged ETF or margin-financed case. |
| `unlevered_baseline` | `CaseDefinition` | One exact unlevered comparator case. |
| `comparison_binding` | `ComparisonBinding` | Enforces identical clock, sample, capital, cash-flow, and measurement basis. |
| `path_result` | `PathResult` or null | Daily path and after-cost attribution; null unless fully valid. |
| `stress_results` | ordered array of `StressResult` | Same paired cases under exact historical or deterministic shocks. |
| `gate_results` | ordered array of `GateResult` | Every applicable policy/safety gate, evaluated conjunctively. |
| `uncertainty_codes` | ordered unique array of `UncertaintyCode` | Exact bounded qualifications. |
| `unavailable_reasons` | ordered unique array of `UnavailableReason` | Exact fail-closed causes. |
| `invalidation_conditions` | ordered array of versioned conditions | Conditions that retire the result. |

Unknown fields/enums, duplicate identities, nonfinite numbers, digest mismatch,
unstable ordering, mixed clocks, missing required components, or an internally
inconsistent state make the output `INVALID` before any comparative number is
displayed.

## Policy and scenario binding

`PolicyBinding` has exactly `binding_kind`, `policy_id`, `policy_revision`,
`scenario_assumption_id`, and `scenario_assumption_version`.

- `USER_POLICY` requires exact `investment-policy/v1` state
  `READY_FOR_RESEARCH`, a stable non-null ID/revision, and every leverage-related
  choice resolved. Only it may emit `POLICY_ELIGIBLE_RESEARCH` or
  `POLICY_INELIGIBLE_RESEARCH`; the latter means a fully measured applicable
  gate is `FAIL`, not that the result is missing. Both scenario fields are null.
- `GENERIC_SCENARIO` requires one immutable, versioned assumption identity and
  emits `SCENARIO_ONLY` only when every required input/path is available. A
  missing required result emits `UNAVAILABLE`. It cannot consume private account
  state or claim suitability. Both policy fields are null.
- The binding fixes investment horizon, benchmark/return basis/base currency,
  maximum loss/drawdown/volatility/gross leverage/concentration, cash floor,
  margin buffer, maximum forced-liquidation-risk tolerance, and cost/tax/FX
  identities. It does not own the product's actual margin, liquidation, or
  leverage-reduction rule.

No value may be inferred from a popular product, current holding, historical
winner, GUI default, or the accepted close-proxy assumptions. A policy revision
changes `evaluation_id`; old results are not silently relabelled.

## Exact product and exposure identity

`CaseDefinition` has exactly `case_id`, `case_kind`, `instrument_id`,
`instrument_type`, `venue_id`, `currency`, `exposure_index_id`,
`return_basis`, `target_exposure_rule`, `reset_rule`, `rebalance_rule`,
`distribution_rule`, `corporate_action_rule`, `accounting_mode`,
`cost_assumption_ids`, `execution_assumption_id`, `path_rule`, `margin_rule`, and
`reduction_rule`.

`path_rule` is a non-null versioned `PathRule` for every case. `margin_rule` is a
non-null versioned `MarginRule` only for `MARGIN_FINANCED_POSITION` and is null
for `LEVERAGED_ETF` and `UNLEVERED_BASELINE`. `reduction_rule` is a non-null
versioned `ReductionRule` for a leveraged case and null for the baseline. These
objects are part of `evaluation_id`; no rule may be supplied outside the closed
case definition.

`case_kind` is exactly:

- `LEVERAGED_ETF`: a fund/share product with an exact stated daily exposure
  objective. Investor-level margin call and forced liquidation are
  `NOT_APPLICABLE`, but product expense, tracking residual, daily reset,
  distributions, corporate actions, gaps, and near-total loss remain required.
- `MARGIN_FINANCED_POSITION`: an exact financed instrument/exposure whose
  initial/maintenance margin, financing, margin call, and forced-liquidation
  rules are required.
- `UNLEVERED_BASELINE`: the exact unlevered instrument or reproducible
  benchmark-replication rule used only as the paired comparator.

A marketing name such as “2x ETF”, an index label, or a ticker alone is not
enough. Exact instrument class, venue, currency, exposure objective, index,
return basis, reset boundary, distribution/corporate-action treatment, and
tradable series identity are mandatory. Leveraged ETF and margin exposure never
substitute for each other.

Version 1 accepts a leveraged ETF only with
`reset_rule=DAILY_TARGET_EXPOSURE` and a proven reset boundary. A product with a
different or unresolved objective requires a new compatible contract rather
than being forced into this case kind.

`ReductionRule` has exactly `rule_id`, `rule_version`, `trigger_metric_id`,
`operator`, `threshold`, `unit`, `measurement_time`, `target_exposure_rule`,
`execution_timing`, `cost_assumption_ids`, and `assumption_kind`.
`assumption_kind` is `VALIDATED_CASE_RULE` or `GENERIC_SCENARIO_ASSUMPTION`; it
is never labelled a user-confirmed policy field under `investment-policy/v1`.
Missing threshold, timing, target, or cost identity makes the leveraged case
unavailable. The rule can reduce exposure only inside simulation; it grants no
order or broker action.

`target_exposure_rule` records signed target gross/net exposure, leverage unit,
eligible range, and source/rule version. Effective leverage is calculated from
the simulated daily state and reported separately; the target is never copied
as realized leverage.

## Immutable PIT data binding and matched comparator

`DataBinding` contains an immutable manifest ID/digest and, for every input,
exact dataset/series/instrument, schema/contract, observation, publication,
available/usable-from, retrieval, finality/revision/vintage, currency/unit,
calendar/session, and predictive-PIT status. Only retained `PIT_SAFE` data
available at each simulation decision boundary is eligible. Current
classifications, revised history, future constituents, later FX, or a convenient
proxy cannot fill a historical gap.

`ComparisonBinding` contains `baseline_case_id`, `leveraged_case_id`, exact
start/end decision instants, ordered session/calendar identity, initial capital,
base currency, cash-flow schedule, execution timestamps, price/return basis,
FX conversion rule, benchmark identity, missing-session rule, and measurement
frequency.

The paired cases must share the same:

1. decision instants, usable information cutoff, ordered market sessions, and
   retained manifest generation;
2. underlying exposure index/benchmark semantics and price-versus-total-return
   basis, except for the explicitly modelled leverage/product transformation;
3. starting capital, external cash flows, base currency, FX timestamps, and
   evaluation horizon;
4. execution timing and missing-session policy; and
5. metric formula/version and rounding-after-validation rule.

Any mismatch produces `COMPARATOR_NOT_MATCHED` and no relative result. The
baseline cannot be resampled, shifted, extended, or selected after seeing the
leveraged result. “Leverage multiple times unlevered cumulative return” is
forbidden.

## Daily reset and path accounting

`PathRule` is bound inside each case and specifies exact state order for every
completed session:

1. carry prior cash, units/exposure, accrued financing, and margin state;
2. apply corporate actions and distributions at their contracted effective
   times;
3. apply opening gap and executable price under the chosen execution rule;
4. mark exposure and equity before any rebalance;
5. test margin call and forced-liquidation triggers at every contracted test
   point, never only at close;
6. execute required liquidation or the versioned rebalance rule with explicit
   volume, slippage, tax, FX, and transaction-cost treatment;
7. apply the session return path and product tracking transformation;
8. accrue financing, expense, borrow, and cash yield on exact day-count bases;
9. compute closing NAV, cash, gross/net/effective leverage, margin headroom, and
   policy gates; and
10. conserve all positions, cash, liabilities, costs, and P/L before rounding.

The exact product contract may require a different evidenced event order; that
order must be versioned and replaces the template above rather than being
silently rearranged. Missing intraday information cannot prove that a margin
call did not occur. If trigger ordering materially affects the result and cannot
be established, the affected path is `UNAVAILABLE`.

Daily-reset exposure compounds recursively from the prior session NAV. The
contract reports volatility drag and path dependence from that actual simulated
path; it never applies one leverage multiple to a horizon return. Identical
start/end underlying returns may produce different leveraged results and must
not be collapsed.

## Cost and performance attribution

Each case has a same-horizon, same-base-currency `CostAttribution` with separate
daily and cumulative values for:

- `FINANCING_INTEREST`;
- `PRODUCT_EXPENSE`;
- `TRACKING_RESIDUAL` (signed performance attribution, not automatically a fee);
- `TRANSACTION_COST`, with commission, spread/slippage, and market impact
  independently attributable;
- `TAX`;
- `FX_CONVERSION`;
- `BORROW_AND_MARGIN`;
- `CASH_YIELD`; and
- `OTHER_UNSUPPORTED`, which is numeric-free and blocks a complete after-cost
  claim.

Every component binds formula/assumption ID, source, unit, currency, accrual and
deduction timing, sign convention, as-of, and availability. Components are never
compressed into an unexplained “all-in cost”.

`accounting_mode` is exactly:

- `OBSERVED_NET_PRODUCT_RETURN`: observed product prices/total returns already
  embed product expense and tracking outcomes. They are attributed when an
  accepted decomposition exists but are not deducted a second time.
- `SYNTHETIC_GROSS_EXPOSURE`: gross underlying exposure is transformed by the
  exact reset rule and each separately available cost is deducted once.

Mixing modes or double-counting embedded expense/tracking makes the result
`INVALID`. Unknown tax, FX, financing, product expense, or required cost is
unavailable, never zero. Gross return, each attribution, net return, and
unlevered relative difference must reconcile within a fixed numeric tolerance.

## Margin, buffer, and forced-liquidation semantics

A `MARGIN_FINANCED_POSITION` requires `MarginRule` with exact collateral
eligibility/haircuts, initial and maintenance requirements, excess-equity
formula, test frequency/timestamps, call deadline, cure mechanics, financing
rate/day count, liquidation priority/quantity/price, gap/slippage/cost/tax/FX,
negative-equity treatment, and rule version/effective dates.

The path reports initial/lowest/final margin headroom, first call time, deficit,
cure action, liquidation time/price/fraction, realized loss, residual exposure,
and negative equity independently. No close-only bar may assume a favorable
intraday trigger or execution. Unknown rule, missing trigger-time price, or
unsupported liquidation ordering produces `MARGIN_PATH_UNAVAILABLE`, not “no
liquidation”.

For `LEVERAGED_ETF`, holder margin fields are explicitly `NOT_APPLICABLE`; they
are never populated with zeros or borrowed margin rules. Fund-level leverage
and counterparty mechanics may be documented as product risk but do not become
investor margin-call arithmetic without an accepted source contract.

## Required paired stress cases

Every `StressResult` binds a stable `stress_id`, `stress_kind`, input
manifest/scenario version, exact shock values and units, event order/duration,
leveraged and unlevered path results, cost attribution, gate results, and
limitations. It is descriptive research, not a probability forecast.

The registry must cover, when the selected case is exposed:

- retained PIT-safe historical crash windows, without using later-known
  constituents or revisions;
- deterministic large decline and opening-gap paths;
- high-volatility alternating-return paths that expose daily-reset decay;
- correlation and tracking-residual shifts;
- FX shock and conversion-cost paths for cross-currency cases;
- financing/borrow/expense increases;
- liquidity loss, spread/impact widening, and constrained rebalance;
- margin deficit, missed cure, forced liquidation, and negative-equity paths for
  margin cases; and
- leverage reduction/rebalance sequences with explicit ordering and costs.

A stress not applicable to the exact product is `NOT_APPLICABLE` with a typed
reason. A required but unmeasurable stress is `UNAVAILABLE` and blocks a complete
safety claim. Favorable stresses cannot offset a failed required stress.

## Metrics and conjunctive safety gates

`PathResult` reports leveraged and baseline gross/net/after-cost returns,
relative return, annualized volatility, maximum drawdown and duration, maximum
loss over the policy horizon, downside and recovery measures, daily target and
effective gross/net leverage, turnover, cash fraction, path-dependent drag,
tracking residual/error, all cost components, and margin/liquidation measures
where applicable. It also reports every `ReductionRule` trigger evaluation,
target exposure, simulated execution, and cost as separate events. Each metric
or event has exact formula/rule version, unit, sample/time, and availability; no
future return guarantee or “optimal” label is allowed.

Each `GateResult` contains exact `gate_id`, `gate_owner_kind`,
`gate_owner_id`, `gate_owner_version`, measured metric/reference,
limit/operator/unit, `PASS`, `FAIL`, `UNAVAILABLE`, or `NOT_APPLICABLE`,
supporting path/stress evidence, and reason code. `gate_owner_kind` is
`POLICY_FIELD` for an exact `investment-policy/v1` field/revision or
`SCENARIO_ASSUMPTION` for an exact generic scenario assumption/version, or
`CASE_SAFETY_RULE` for an exact case `ReductionRule`/product safety invariant;
the three never substitute. Required gates include:

- maximum gross/effective leverage;
- maximum horizon loss and maximum drawdown;
- annualized volatility when confirmed;
- minimum cash floor and dated cash need;
- minimum margin buffer and maximum forced-liquidation risk for margin cases;
- position/concentration limits;
- exact case leverage-reduction trigger/target/timing/cost compliance;
- every required cost, tax, FX, financing, liquidity, and product-evidence
  availability gate; and
- required historical/deterministic stress completion.

All applicable gates are conjunctive. One pass, higher return, or lower average
cost cannot offset another `FAIL` or `UNAVAILABLE`. `POLICY_ELIGIBLE_RESEARCH`
requires every applicable gate `PASS`. A fully measured `FAIL` yields
`POLICY_INELIGIBLE_RESEARCH`; any required `UNAVAILABLE` yields output
`UNAVAILABLE`. Neither state may carry a proposal/ranking claim.

Output-state precedence is deterministic:

| Condition | Exact `output_state` |
| --- | --- |
| Envelope/schema/enum/digest/accounting inconsistency or cost double count | `INVALID` |
| Otherwise, either binding kind has any required unavailable input, path, cost, stress, or gate | `UNAVAILABLE` |
| Otherwise, `USER_POLICY` has one or more fully measured gate failures | `POLICY_INELIGIBLE_RESEARCH` |
| Otherwise, `USER_POLICY` has every applicable gate pass | `POLICY_ELIGIBLE_RESEARCH` |
| Otherwise, fully available `GENERIC_SCENARIO`, regardless of displayed scenario gate passes/failures | `SCENARIO_ONLY` |

The rows are evaluated top to bottom. A structural invalidity is never converted
to an unavailable reason; an unavailable result is never converted to a policy
failure; and a fully measured policy failure is never described as missing.

## Closed reasons, uncertainty, and invalidation

`UnavailableReason` is exactly `POLICY_UNRESOLVED`, `PRODUCT_IDENTITY_UNRESOLVED`,
`EXPOSURE_RULE_UNRESOLVED`, `DATA_NOT_PIT_SAFE`, `COMPARATOR_NOT_MATCHED`,
`PATH_RULE_UNRESOLVED`, `REDUCTION_RULE_UNRESOLVED`, `COST_COMPONENT_UNAVAILABLE`,
`FX_UNAVAILABLE`, `MARGIN_RULE_UNAVAILABLE`, `MARGIN_PATH_UNAVAILABLE`,
`STRESS_UNAVAILABLE`, `POLICY_GATE_UNAVAILABLE`, `HOLDOUT_PROTECTED`, or
`OUTPUT_INVALIDATED`.

Cost double counting, non-conserved accounting, and an unknown structural field
are validation defects and therefore `INVALID`, not `UnavailableReason` values.
A fully measured failed policy gate produces `POLICY_INELIGIBLE_RESEARCH`, not
`POLICY_GATE_UNAVAILABLE`.

`UncertaintyCode` is exactly `SOURCE_REVISION_LIMITED`, `PIT_LIMITED`,
`TRACKING_DECOMPOSITION_LIMITED`, `COST_ESTIMATE`, `TAX_ESTIMATE`, `FX_ESTIMATE`,
`LIQUIDITY_ESTIMATE`, `MARGIN_INTRADAY_LIMITED`, `STRESS_SCENARIO_ONLY`, or
`MODEL_UNCALIBRATED`. Unknown codes make the output `INVALID`; a code never
authorizes a missing required value.

Invalidation conditions include any data/code/contract digest change; new or
revised evidence; policy/scenario, product, exposure, cost, tax, FX, margin, or
stress version change; corporate action; tradability/liquidity change; and any
gate becoming unknown or breached. A retired result may be shown only as dated
historical research with its original identities and no current eligibility.

## GUI read-only projection

A future GUI may render only a validated typed result. The concise view shows
case identity/type, target and observed effective leverage, after-cost result
versus the matched unlevered baseline, maximum loss/drawdown, total cost with
expandable components, margin/liquidation headroom or `해당 없음`, failed or
unknown gates, exact as-of/coverage, and `정책 연구`/`가정 시나리오` status.
The displayed reduction condition comes from the exact case `ReductionRule` and
is labelled `검증 규칙` or `가정 시나리오`; it is not misrepresented as a
user-confirmed policy choice.

The GUI performs no leverage, compounding, cost, tax, FX, margin, liquidation,
stress, gate, or ranking calculation. It exposes no order side, quantity,
executable price, broker action, or “safe/guaranteed” claim. Missing values stay
`확인 불가`; they are not zero or replaced by the baseline.

## Separation from the accepted close-proxy foundation

| Accepted foundation fact | Required separation |
| --- | --- |
| KOSPI200 retained close series | Not a selected exposure index, executable product, or unlevered benchmark. |
| Normalized initial cash `1.0` | Not user capital, margin collateral, cash floor, or buffer. |
| Long/cash-only exposure | Not a leverage rule or permission to borrow. |
| Fixed one-way 10 bp hypothetical cost | Not a leverage transaction, tax, FX, financing, expense, borrow, or tracking assumption. |
| Zero cash yield | Not an accepted financing/cash-yield policy. |
| Development metrics | Not a leveraged comparison, product result, safety validation, or future expectation. |
| Untouched 1,222-observation holdout | Remains uninspected; this document grants no access or result-generation authority. |

No existing artifact changes meaning or validity merely because this document
exists. A separate claimed implementation task must create a new versioned
result family without overwriting the accepted five-file generation.

## Project Goal requirement map

| Project Goal leverage requirement | Contract field or invariant |
| --- | --- |
| Leverage ETF is optional, never assumed superior | Exact `LEVERAGED_ETF` case, paired baseline, after-cost result, no optimal/recommendation claim. |
| Distinguish ETF from margin trading | Closed case kinds and non-substitutable product/margin semantics. |
| Daily reset and path dependence | Recursive `PathRule`, exact event order, effective leverage, volatility-drag/path metrics. |
| Product expense and tracking error | Separate `PRODUCT_EXPENSE` and signed `TRACKING_RESIDUAL`, with embedded-cost double-count prevention. |
| Compare unlevered, periodic-buy, and simple-hold alternatives | This contract closes the exact PIT-matched unlevered baseline; periodic-buy/simple-hold require separately versioned baseline case/rules before inclusion and cannot be inferred here. |
| Deduct financing, fees, trading cost, tax, and FX | Independent once-only components with source, timing, currency, mode, and reconciliation. |
| Crash, volatility/correlation, gap, margin, liquidation, rebalance stress | Required paired `StressResult` registry and explicit event/path order. |
| Maximum leverage, drawdown, loss, cash/margin buffer | Exact accepted policy limits and conjunctive `POLICY_FIELD` gate results. |
| Reduction condition | Exact case-owned `ReductionRule` and `CASE_SAFETY_RULE` gate, explicitly not an `investment-policy/v1` field. |
| GUI shows effective leverage, cost, amplified loss, liquidation room, reduction condition | Typed read-only projection from path/cost/stress/gate/reduction results; no GUI calculation or execution. |
| Reproducible backtest, benchmark, opportunity cost, no guarantee | Digest-bound identities, exact matched baseline, gross/net/relative metrics, sealed holdout, non-predictive wording. |

## Document boundary

This contract authorizes no code/schema implementation, Data/provider/account
call or mutation, product acquisition, optimization, model release, holdout
inspection, GUI behavior, scheduler, order, amendment, cancellation, transfer,
withdrawal, or external financial action. Future implementation requires a
separate claimed task, exact local retained inputs, strict validators, atomic
new-generation publication, proportional tests, and independent review.
