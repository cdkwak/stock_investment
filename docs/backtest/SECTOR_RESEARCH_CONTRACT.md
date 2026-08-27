# PIT-Safe Sector Research Contract

Status: `DOCUMENTATION_ONLY / IMPLEMENTATION_NOT_SELECTED`

Contract version: `sector-research/v1`

## Purpose and boundary

This contract defines the future Backtest-owned research boundary for sector
rotation, participation, relative strength, and relative-value candidates. It
does not select a provider, invent a Data schema, collect a dataset, implement
a model, inspect the final holdout, optimize a portfolio, or produce an order
or recommendation.

Data owns source contracts, taxonomy and universe history, validation,
promotion, and point-in-time availability. Backtest may consume only frozen,
contract-validated local inputs and performs all research calculations offline.
A future GUI may consume a typed result but must not calculate membership,
factors, states, or rankings.

## Research envelope

A `sector-research/v1` result has exactly these top-level fields:

| Field | Type | Rule |
| --- | --- | --- |
| `contract_version` | string | Exactly `sector-research/v1`. |
| `research_run_id` | stable string | Binds the exact frozen inputs, code, configuration, and validation split. |
| `research_state` | enum | `SCENARIO_ONLY`, `CANDIDATE_ELIGIBLE`, `UNAVAILABLE`, or `INVALID`. |
| `decision_time` | timezone-aware timestamp | The historical or current decision boundary. |
| `usable_information_cutoff` | timezone-aware timestamp | No input known after this instant may contribute. |
| `policy_binding` | `PolicyBinding` | Exact accepted policy revision for candidate use, or a versioned generic scenario assumption. |
| `taxonomy_binding` | `TaxonomyBinding` | Point-in-time sector classification identity. |
| `universe_binding` | `UniverseBinding` | Point-in-time eligible instrument universe identity. |
| `membership_binding` | `MembershipBinding` | Historical membership identity and availability boundary. |
| `input_bindings` | ordered array of `InputBinding` | Exact frozen local inputs required by the configured research run. |
| `window_spec` | `WindowSpec` | Versioned session windows and minimum coverage. |
| `validation_binding` | `ValidationBinding` | Leakage-safe split, purge, embargo, and untouched-holdout identity. |
| `sector_results` | ordered array of `SectorResult` | Independent typed results; empty when unavailable. |
| `instrument_results` | ordered array of `InstrumentResult` | Point-in-time member candidates within the evaluated sectors; empty when unavailable. |
| `unavailable_reasons` | ordered array of `UnavailableReason` | Closed reason vocabulary; non-empty for `UNAVAILABLE` or `INVALID`. |

Unknown fields, duplicate identities, unstable ordering, nonfinite values, or a
digest/configuration mismatch invalidate the whole result before ranking.

## Policy binding

`PolicyBinding` contains `binding_kind`, `policy_id`, `policy_revision`, and
`scenario_assumption_id`.

- `USER_POLICY` requires an accepted `investment-policy/v1` document in
  `READY_FOR_RESEARCH`, with non-null `policy_id` and exact positive revision.
  Only this binding may produce `research_state=CANDIDATE_ELIGIBLE`.
- `GENERIC_SCENARIO` requires a versioned, immutable
  `scenario_assumption_id`; policy identity is null and the output remains
  `SCENARIO_ONLY` regardless of historical metrics.
- Missing, retired, changed, or inconsistent policy evidence produces
  `UNAVAILABLE`. A result never infers policy from holdings, past performance,
  GUI defaults, or the accepted close-proxy experiment.

## Point-in-time taxonomy, universe, and membership

`TaxonomyBinding` contains the stable taxonomy owner, taxonomy ID, version,
publication timestamp, effective-from/effective-through interval, and content
digest. Domestic and overseas taxonomies are separate identities; labels that
merely say `sector`, `industry`, or `market` are invalid.

`UniverseBinding` contains the universe ID/version, market, observation date,
publication timestamp, content digest, and inclusion/exclusion rule identity.
Historical evaluation must include instruments that later delisted, merged,
changed sector, or became inactive whenever they were eligible at that
historical decision time. Today's survivors cannot define an older universe.

`MembershipBinding` contains the exact membership-history contract/version and
digest. Each instrument-sector interval preserves instrument identity,
sector identity, effective-from/effective-through dates, source observation
time, publication/available time, and `usable_from`. A decision uses only the
membership version available by `usable_information_cutoff`. The current
taxonomy or current constituents must never be applied backward to historical
prices, fundamentals, breadth, flow, or validation labels.

Missing historical taxonomy, universe, membership, publication time, or
revision lineage makes the affected sector `UNAVAILABLE`; the system does not
borrow the nearest current classification.

## Required input roles

This contract names semantic roles, not Data dataset names or providers. Every
role must be resolved by a separately accepted Data-owned contract before a
future implementation may use it.

| Input role | Required semantics |
| --- | --- |
| `INSTRUMENT_RETURN_SERIES` | Exact instrument identity, price/total-return basis, corporate-action treatment, currency, observation/available/usable times, and historical coverage. |
| `MARKET_COMPARATOR_SERIES` | Exact market benchmark and the same return basis, currency treatment, and decision timing as the instrument/sector comparison. |
| `SECTOR_COMPARATOR_SERIES` | Point-in-time member aggregation rule, weighting rule, taxonomy version, and matching return semantics. |
| `SECTOR_BREADTH` | Point-in-time member numerator/denominator and formula identity sufficient to distinguish broad participation from narrow leadership. |
| `SECTOR_FLOW` | Exact participant/flow meaning, unit, aggregation window, finality, revision policy, and sector membership timing. |
| `VALUATION` | Explicit trailing/forward/current meaning, numerator/denominator, aggregation and weighting, currency, as-of, availability, and revision semantics. |
| `EARNINGS_QUALITY` | Earnings level/change and estimate/actual identity with report period, publication time, and revision/vintage lineage. |
| `CASH_FLOW_QUALITY` | Operating/free-cash-flow definition, report period, publication time, currency/unit, and revision lineage. |
| `BALANCE_SHEET_RISK` | Debt, leverage, liquidity, and solvency definitions with report period and availability time. |
| `STRUCTURAL_DECLINE_EVIDENCE` | Versioned rule inputs that distinguish persistent business deterioration from a low valuation multiple. |

Every `InputBinding` contains `input_role`, dataset/series ID, contract version,
content digest, observation/as-of time, available time, usable-from time,
frequency, unit, currency, finality/revision state, return or aggregation basis
where applicable, and `predictive_pit_status`.

`predictive_pit_status` is one of `PIT_SAFE`, `PIT_LIMITED`, `PIT_BLOCKED`, or
`UNKNOWN`. Only `PIT_SAFE` is eligible for candidate calculation. A role may not
be filled by another role, a market-wide proxy, current constituents, forward
fill, cross-provider splice, or a similarly named field.

Current Backtest authority makes only Price and Volatility families evaluable.
Breadth and Flow remain unavailable, and this contract additionally requires
accepted point-in-time taxonomy/membership, sector valuation, and fundamental
quality roles. Until those exact roles are `PIT_SAFE`, no implementation may
emit a sector research candidate; the missing inputs remain explicit rather
than being substituted.

## Windows and comparable calculations

`WindowSpec` contains a version ID, a non-empty ordered list of distinct
positive trading-session windows, minimum complete observations per window,
the exact exchange calendar identity, and a missing-session policy. Calendar
days and trading sessions are not interchangeable. Windows are fixed before a
validation run and cannot be chosen from later outcomes.

For every configured window, a future result must preserve independently:

- instrument and sector return, with exact price/total-return and FX basis;
- relative strength versus both the exact market comparator and sector
  comparator;
- sector breadth numerator, denominator, coverage fraction, and aggregation
  rule;
- flow value, unit, participant meaning, and aggregation rule; and
- coverage start/end plus any unavailable reason.

Incomplete windows are unavailable, not padded. Missing members are not dropped
silently from a breadth denominator. Return, breadth, and flow windows cannot be
called comparable unless their decision times, membership version, calendar,
currency treatment, and availability cutoff agree.

## Independent trend and relative-value states

Each `SectorResult` contains sector/taxonomy identity, exact as-of and usable
cutoff, per-window observations, `trend_state`, `relative_value_state`,
valuation comparators, quality flags, candidate eligibility, uncertainty,
invalidation conditions, and stable reason codes.

Each `InstrumentResult` contains the exact historical instrument identity, its
point-in-time sector membership interval, the same as-of/usable cutoff and
independent trend/relative-value fields, instrument-versus-sector and
instrument-versus-market observations, own-history valuation comparison,
quality flags, candidate eligibility, uncertainty, invalidation conditions,
and stable reason codes. It cannot appear unless that instrument was present in
the exact historical universe and membership bindings at the decision time.

`TrendState` is exactly:

- `BROAD_UPTREND`: the pre-registered price/relative-strength rule passes and
  independently accepted breadth confirms broad participation.
- `NARROW_UPTREND`: price/relative strength passes but breadth shows leadership
  concentration under the registered rule.
- `NEUTRAL`: complete evidence satisfies neither uptrend nor downtrend rule.
- `DOWNTREND`: the pre-registered downtrend rule passes.
- `UNAVAILABLE`: required price, comparator, breadth, membership, or timing
  evidence is absent, blocked, or invalid.

`RelativeValueState` is exactly:

- `RELATIVELY_UNDERVALUED_REVIEW`: all required market, same-sector, and own-
  history comparisons pass a pre-registered rule and no required value-trap
  flag is present or unknown.
- `FAIR_RANGE`: complete comparable evidence passes neither cheap nor expensive
  rule.
- `RELATIVELY_EXPENSIVE`: the pre-registered expensive rule passes.
- `VALUE_TRAP_RISK`: a cheap valuation comparison coexists with one or more
  present value-trap flags.
- `UNAVAILABLE`: valuation comparators, quality evidence, membership, timing,
  or revision lineage is incomplete or blocked.

Trend never implies undervaluation, and undervaluation never implies an
uptrend. Low PER or PBR alone cannot produce
`RELATIVELY_UNDERVALUED_REVIEW`.

The three mandatory valuation comparators are:

1. the exact market at the same decision time and valuation semantics;
2. point-in-time peers in the same taxonomy sector; and
3. the sector or instrument's own as-of-only historical distribution.

Forward and trailing ratios are distinct inputs. Negative/zero denominators,
provider nulls, incomparable currencies, missing weights, and different
forecast horizons remain independently unavailable; they are not repaired or
silently exchanged.

## Value-trap and quality evidence

Each of these flags is independently one of `PRESENT`, `NOT_DETECTED`, or
`UNKNOWN`:

- `EARNINGS_DETERIORATION`
- `CASH_FLOW_WEAKNESS`
- `BALANCE_SHEET_LEVERAGE`
- `LIQUIDITY_STRESS`
- `STRUCTURAL_DECLINE`

`NOT_DETECTED` requires complete PIT-safe evidence and a pre-registered rule;
missing data is `UNKNOWN`, never a reassuring zero. Any `PRESENT` flag prevents
an undervalued-review state and yields `VALUE_TRAP_RISK`. Any `UNKNOWN` required
flag makes relative value `UNAVAILABLE`.

## Candidate eligibility and output meaning

`candidate_eligibility` is exactly `RESEARCH_CANDIDATE`, `SCENARIO_ONLY`,
`EXCLUDED`, or `UNAVAILABLE`.

The field applies independently to each sector and instrument result. A sector
candidate never promotes all of its current members, and an instrument
candidate cannot borrow the sector's trend, valuation, or quality state to fill
missing instrument evidence.

`RESEARCH_CANDIDATE` requires all of the following conjunctively:

1. exact `USER_POLICY` binding in `READY_FOR_RESEARCH`;
2. PIT-safe taxonomy, universe, membership, price, comparators, breadth, flow,
   valuation, and quality input roles;
3. complete configured windows and matching decision/availability boundaries;
4. independently valid trend and relative-value states under the registered
   candidate rule;
5. every applicable policy risk, concentration, liquidity, benchmark, cost,
   tax, and FX gate is measurable and passed; and
6. leakage-safe validation eligibility under the exact run identity.

Failure of a confirmed policy gate is `EXCLUDED`. Missing or blocked evidence
is `UNAVAILABLE`, not a pass. A generic scenario is always `SCENARIO_ONLY`.
No candidate state means buy, sell, suitability, guaranteed return, target
weight, or permission to trade. It is an auditable research item for human
review only.

## Leakage-safe validation

`ValidationBinding` contains the frozen input manifest digest, code/config
digest, ordered split identity, training/validation/test coverage, label
horizon, purge sessions, embargo sessions, taxonomy/membership vintage rule,
and final-holdout identity with `holdout_results_reviewed=false`.

- Source rows, taxonomy, universe, and memberships are sliced by information
  availability before features, peers, aggregates, or labels are constructed.
- Training labels must be available before the earliest validation/test
  decision. Purge is at least the maximum label horizon and embargo is fixed
  before results.
- Feature thresholds, factor selection, windows, valuation rules, and
  value-trap rules are fit or selected only in the training boundary. Crisis
  dates and later sector outcomes cannot tune them.
- Historical peer groups and breadth denominators use only members eligible at
  that decision time, including later delisted/inactive names when applicable.
- The existing final 1,222-observation holdout remains untouched. Its labels,
  predictions, rankings, sector outcomes, and metrics are not constructed or
  inspected during development.
- Validation must separately report coverage/unavailability, turnover and
  costs, benchmark-relative results, drawdown, false discovery/stability, and
  sensitivity to taxonomy revisions. A favorable aggregate metric cannot hide
  missing sectors or failed policy gates.

Historical validation describes what the registered rule did in retained
periods. It is not a prediction of future sector performance.

## Unavailable reasons

The version-1 `UnavailableReason` vocabulary is exactly:

- `POLICY_UNRESOLVED`
- `TAXONOMY_UNAVAILABLE`
- `UNIVERSE_UNAVAILABLE`
- `MEMBERSHIP_UNAVAILABLE`
- `MEMBERSHIP_NOT_YET_KNOWN`
- `INPUT_ROLE_UNRESOLVED`
- `PIT_BLOCKED`
- `SOURCE_STALE`
- `TIMING_MISMATCH`
- `RETURN_BASIS_MISMATCH`
- `CURRENCY_OR_FX_MISMATCH`
- `INSUFFICIENT_WINDOW`
- `BREADTH_UNAVAILABLE`
- `FLOW_UNAVAILABLE`
- `VALUATION_UNAVAILABLE`
- `QUALITY_EVIDENCE_UNAVAILABLE`
- `VALUE_TRAP_EVIDENCE_UNKNOWN`
- `POLICY_GATE_FAILED`
- `VALIDATION_INELIGIBLE`
- `IDENTITY_OR_DIGEST_MISMATCH`

Free-form errors, unknown reason codes, and provider exception text fail
validation.

## Project Goal requirement map

| Project Goal sector requirement | Contract field or invariant |
| --- | --- |
| Multiple-period returns and relative strength | Versioned trading-session `WindowSpec` and per-window market/sector comparisons. |
| Broad participation versus narrow leadership | PIT membership denominator, `SECTOR_BREADTH`, and independent `BROAD_UPTREND`/`NARROW_UPTREND` states. |
| Flow evidence | Separate non-substitutable `SECTOR_FLOW` role with participant, unit, window, finality, and timing semantics. |
| Market/sector/own-history relative valuation | Three mandatory as-of-matched valuation comparators. |
| Earnings and cash-flow quality | Independent PIT-safe earnings and cash-flow roles and tri-state flags. |
| Financial health, debt, and liquidity | `BALANCE_SHEET_RISK` plus leverage and liquidity flags. |
| Structural decline/value-trap evidence | Mandatory tri-state structural-decline evidence and `VALUE_TRAP_RISK`; unknown evidence fails closed. |
| Trend and undervaluation shown separately | Independent `TrendState` and `RelativeValueState`; neither implies the other. |
| PIT taxonomy, universe, and membership | Versioned publication/effective/usable identities; no current-classification backfill and no survivor-only universe. |
| Candidate is not an automatic recommendation | Typed research eligibility, exact policy binding, explanatory-only output, and no order/target-weight meaning. |

## Document boundary

This document authorizes no runtime implementation, Data contract, provider,
collector, scheduler, account access, GUI change, optimization, holdout review,
or trading action. A separate claimed task must establish accepted Data-owned
input contracts and a versioned offline implementation with tests before any
numeric sector result exists.
