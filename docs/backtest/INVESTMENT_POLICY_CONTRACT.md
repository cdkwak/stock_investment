# Investment Policy Contract

Status: `ACTIVE_USER_POLICY_BOUNDARY / OPTIONAL_FOR_GENERIC_RESEARCH_SCENARIOS`

Contract version: `investment-policy/v1`

This contract records choices that must come from the user before the project
describes one strategy or portfolio as preferable for that user. Agents may
compare clearly labelled generic research scenarios with explicit assumptions
without waiting for these personal choices. This is not an execution mandate or
permission to inspect the untouched holdout. The current close-proxy foundation
does not produce or consume this contract.

## Purpose and ownership

The user owns the policy values. A future application may validate and preserve
them, but must not infer them from retained datasets, current holdings, a model,
historical performance, GUI defaults, or the existing close-proxy experiment.

A policy is suitable for user-specific ranking only when every required choice
is explicitly resolved and the policy has a stable identity. Project/Domain
Status independently authorizes provider access, offline optimization,
read-only account access, and wider Backtest engineering. This policy never
authorizes real broker orders or external financial mutations.

## Envelope

Every policy document has these top-level fields:

| Field | Type | Rule |
|---|---|---|
| `contract_version` | string | Exactly `investment-policy/v1`. |
| `policy_id` | string or null | Stable user-assigned identity; null while unresolved. It must not be generated from policy values alone. |
| `revision` | positive integer | Increases whenever a confirmed choice changes. |
| `policy_state` | enum | `UNRESOLVED_USER_CHOICE`, `READY_FOR_RESEARCH`, or `RETIRED`. |
| `confirmed_at` | RFC 3339 timestamp or null | User-confirmation time; null unless `READY_FOR_RESEARCH`. |
| `base_currency` | choice | Currency used for loss, liquidity, cost, and comparison reporting. |
| `investment_horizon` | object | User-selected evaluation start and end or duration. |
| `benchmark` | object | Exact comparison identity and return semantics. |
| `risk_limits` | object | Loss, drawdown, volatility, leverage, and concentration decisions. |
| `liquidity_policy` | object | Cash floor and known cash-need boundary. |
| `cost_policy` | object | Transaction-cost, tax, FX, financing, and product-cost assumptions. |

An individual user choice is represented as:

```json
{
  "choice_state": "UNRESOLVED_USER_CHOICE",
  "value": null,
  "unit": null,
  "user_note": null
}
```

`choice_state` is one of `UNRESOLVED_USER_CHOICE`, `USER_CONFIRMED`, or
`NOT_APPLICABLE_BY_USER`. `USER_CONFIRMED` requires a value and the field's
exact unit. `UNRESOLVED_USER_CHOICE` requires `value=null` and blocks
`READY_FOR_RESEARCH`. `NOT_APPLICABLE_BY_USER` is accepted only where the field
table permits it; it records a decision and must never be treated as numeric
zero.

## Required choices

### Investment horizon

| Field | Unit/identity | Resolution rule |
|---|---|---|
| `start_date` | ISO date | Required and user-confirmed. It is a policy boundary, not the first available dataset date. |
| `end_date` | ISO date | Required and later than `start_date`. A rolling policy instead uses `duration_months`. |
| `duration_months` | positive integer | Required only for a rolling horizon; mutually exclusive with `end_date`. |
| `evaluation_cadence` | `DAILY`, `MONTHLY`, `QUARTERLY`, `ANNUAL`, `HORIZON_END` | Required; it does not imply a trading cadence. |

Exactly one of a fixed `end_date` or rolling `duration_months` is confirmed.
Data coverage may make a confirmed horizon unavailable, but must not shorten it
silently.

### Benchmark

| Field | Allowed content | Resolution rule |
|---|---|---|
| `benchmark_id` | Stable dataset/instrument/index identity | Required. Labels such as `market` are invalid. |
| `version_or_series_id` | Exact retained series or contract version | Required before research. |
| `return_basis` | `PRICE_RETURN` or `TOTAL_RETURN` | Required; the two must not be substituted. |
| `currency` | ISO 4217 currency | Required. |
| `fx_treatment` | `UNHEDGED`, `HEDGED`, or `BASE_CURRENCY_CONVERTED` | Required when benchmark currency differs from `base_currency`; otherwise `NOT_APPLICABLE_BY_USER` is allowed. |
| `rebalance_rule` | Exact rule or `NOT_APPLICABLE_BY_USER` | Required decision; no rule may be inferred from an index name. |

The current KOSPI200 close proxy is an experimental input boundary, not a
user-selected benchmark.

### Risk limits

All fractions are finite decimal values in `[0, 1]`; percentages in UI text are
converted explicitly and never stored ambiguously.

| Field | Unit | Resolution rule |
|---|---|---|
| `maximum_loss_fraction` | fraction of policy capital | Required. Define the measurement horizon in `user_note` when it differs from the full investment horizon. |
| `maximum_drawdown_fraction` | peak-to-trough fraction | Required. |
| `annualized_volatility_ceiling` | annualized standard-deviation fraction | Required, or explicitly `NOT_APPLICABLE_BY_USER`. |
| `maximum_gross_leverage` | gross exposure / NAV | Required, finite, and non-negative; this does not authorize leverage products. |
| `maximum_single_position_fraction` | fraction of NAV | Required, or explicitly `NOT_APPLICABLE_BY_USER`. |
| `maximum_forced_liquidation_risk` | named qualitative prohibition or quantified rule | Required before margin-based instruments; otherwise `NOT_APPLICABLE_BY_USER`. |

If more than one risk limit applies, a candidate must satisfy all of them.
Passing one limit never offsets breaching another. Missing data or a method that
cannot measure a confirmed limit fails closed.

### Liquidity and cash needs

| Field | Unit | Resolution rule |
|---|---|---|
| `minimum_cash_floor_fraction` | fraction of NAV | Required in `[0, 1]`. |
| `known_cash_need_amount` | `base_currency` amount | Required, or explicitly `NOT_APPLICABLE_BY_USER`; zero is valid only when user-confirmed. |
| `known_cash_need_date` | ISO date | Required when a positive cash need is confirmed. |
| `margin_buffer_fraction` | fraction of NAV | Required for margin exposure; otherwise `NOT_APPLICABLE_BY_USER`. |

The normalized cash value `1.0` in the current ledger is an accounting unit, not
this cash-floor choice.

### Costs, taxes, and FX

| Field | Unit/identity | Resolution rule |
|---|---|---|
| `transaction_cost_bps_one_way` | basis points | Required, finite, and non-negative. |
| `tax_policy_id` | versioned jurisdiction/account/tax assumption | Required, or explicitly `NOT_APPLICABLE_BY_USER`; null never means zero tax. |
| `fx_conversion_cost_bps` | basis points | Required for cross-currency comparison; otherwise `NOT_APPLICABLE_BY_USER`. |
| `fx_rate_series_id` | exact retained series/contract identity | Required when currency conversion is used. |
| `financing_rate_series_id` | exact retained series/contract identity | Required for leverage or borrowing; otherwise `NOT_APPLICABLE_BY_USER`. |
| `product_expense_ratio_source` | versioned source/assumption identity | Required for fund or ETF comparison; otherwise `NOT_APPLICABLE_BY_USER`. |

The close-proxy ledger's fixed one-way 10 bp hypothetical cost and zero cash
yield are experiment assumptions only. They do not resolve any user choice in
this contract.

## State transition and validation

`UNRESOLVED_USER_CHOICE` is the only valid initial state. A validator may emit
`READY_FOR_RESEARCH` only when:

1. `policy_id`, `revision`, and `confirmed_at` are present and valid;
2. every required choice is `USER_CONFIRMED` or an explicitly permitted
   `NOT_APPLICABLE_BY_USER`;
3. cross-field horizon, currency, cash-need, leverage, and cost conditions are
   consistent;
4. all referenced benchmark, FX, financing, tax, and product-cost identities
   are exact rather than descriptive placeholders; and
5. no numeric value was filled from a project default or historical result.

Changing a confirmed choice creates a new revision. Prior revisions remain
identifiable so experiments can bind to the exact policy revision. `RETIRED`
prevents new research runs but does not rewrite prior experiment evidence.

## Fail-closed research boundary

- An unresolved or invalid policy cannot rank strategies, choose a portfolio,
  claim suitability, or define an optimization objective.
- A future experiment must bind both `policy_id` and `revision` in its identity.
- Risk and cost results must be reported separately; they must not be collapsed
  into an unexplained score.
- A result outside any confirmed risk or liquidity limit is ineligible, even if
  it has higher return.
- Insufficient point-in-time data, benchmark coverage, cost evidence, or risk
  measurement is `UNAVAILABLE`, not a pass and not zero.
- Research output remains explanatory and non-executable. The user makes the
  final decision.

## Separation from the accepted close-proxy foundation

| Existing foundation fact | Policy meaning |
|---|---|
| KOSPI200 retained close-proxy input | Not a benchmark selection or investment-universe choice. |
| Available sample dates | Not the user's investment horizon. |
| Normalized initial cash `1.0` | Not capital, cash need, or cash-floor policy. |
| Long/cash-only exposure | Not a user risk or leverage decision. |
| Zero cash yield | Not a financing or opportunity-cost choice. |
| Fixed one-way 10 bp hypothetical cost | Not the user's transaction-cost assumption. |
| Untouched holdout | Remains untouched; this contract does not authorize inspection. |

## Requirement map

| Project Goal requirement | Contract evidence |
|---|---|
| User-selected investment period | `investment_horizon` and its fixed/rolling exclusivity rule |
| Exact benchmark | `benchmark` identity, return basis, currency, FX treatment, and rebalance decision |
| Maximum loss and volatility | `risk_limits`, conjunctive enforcement, and unavailable fail-closed rule |
| Required cash and buffer | `liquidity_policy`, dated cash need, and margin buffer |
| Transaction costs, tax, and FX | `cost_policy` with exact units and referenced identities |
| No premature optimization claim | lifecycle gate and fail-closed research boundary |
| Reproducible policy use | `policy_id`, monotonic `revision`, confirmation time, and experiment binding |

## Document boundary

This document does not itself define schema implementation, persistence, GUI,
strategy, simulation, provider, account, or Data behavior. Current Project and
domain Status provide standing authority for offline engineering, read-only
account/Data work, and local simulation, each through its owning contract and
tests. Unresolved policy fields block only user-specific ranking,
recommendation, or optimization claims that depend on them. Real broker orders
and external financial mutations remain prohibited.
