# Sovereign Yield and Bond ETF Semantics

Status: `DOCUMENTATION_ONLY / FUTURE_CROSS_DOMAIN_BOUNDARY`

Contract version: `sovereign-yield-semantics/v1`

This contract defines how a future read-only research or application service
may describe sovereign rates, yield curves, equity linkage, and bond ETFs. It
does not select a provider, create a Data contract, authorize collection, or
make a predictive or portfolio claim.

## Core rule

Every displayed or researched value must retain its exact instrument identity,
economic quantity, unit, observation time, publication/retrieval time, cadence,
finality, source identity, and point-in-time status. Values that differ on one
of those dimensions remain separate unless a versioned alignment rule accepts
the difference explicitly.

The following states are mandatory:

| State | Meaning |
|---|---|
| `VALUE` | Exact identity and all required semantic/time gates pass. |
| `STALE` | Identity is valid but the governing freshness rule fails. |
| `INCOMPATIBLE` | Inputs cannot be aligned for the requested comparison or curve. |
| `UNSUPPORTED` | The requested component, formula, or product field has no verified source or method. |
| `PIT_BLOCKED` | Current evidence exists but is not safe for historical/predictive use. |
| `UNAVAILABLE` | Required evidence is missing, malformed, or ambiguous. |

Only `VALUE` contains a numeric result. None of the other states is numeric
zero, an unchanged value, or permission to substitute a nearby instrument.

## Instrument identity

Every observation uses `rate-observation/v1` with these fields:

| Field | Rule |
|---|---|
| `country` | ISO 3166 country identity. |
| `issuer` | Exact sovereign or monetary authority; an ETF issuer is separate. |
| `instrument_kind` | `POLICY_RATE`, `SOVEREIGN_YIELD`, `SOVEREIGN_SECURITY_PRICE`, `SOVEREIGN_FUTURES_PRICE`, `BREAKEVEN_RATE`, `REAL_YIELD`, `TERM_PREMIUM_ESTIMATE`, or `BOND_ETF`. |
| `instrument_id` | Stable series, security, contract, index, or share-class identity. A label such as `10Y` is insufficient. |
| `tenor` | Exact maturity tenor for a rate, or `NOT_APPLICABLE` for a product whose duration is separately reported. |
| `quantity` | The economic quantity being measured, such as yield, clean price, futures quote, effective duration, or distribution yield. |
| `unit` | Exact unit including percent versus decimal, price currency, years, or index points. |
| `quote_convention` | Yield compounding/day-count basis or price quotation convention. |
| `observation_as_of` | Time or reference date to which the value applies. |
| `published_at` | Source publication time when available; otherwise explicit `UNAVAILABLE`. |
| `retrieved_at` | Retrieval time, never silently promoted to observation time. |
| `cadence` | Source-native frequency and whether the observation is intraday, daily-final, release-period, or model vintage. |
| `finality` | `PROVISIONAL`, `FINAL`, `AS_RETRIEVED`, `REVISED`, or source-specific verified equivalent. |
| `source_id` | Exact retained source/contract identity; a provider name alone is insufficient. |
| `vintage_id` | Required for revisable series and model estimates used historically. |
| `pit_status` | `PIT_SAFE`, `PIT_LIMITED`, `PIT_BLOCKED`, or `NON_PREDICTIVE`. |

An on-the-run sovereign yield, a constant-maturity series, a specific bond
yield, a cash security price, a Treasury-futures quote, and a bond ETF are six
different identities. Their values must never be silently joined as one rate
history.

## Yield and price semantics

- A yield is a rate and carries its compounding and day-count convention.
- A cash security or futures quote is a price. It is never labelled or stored as
  a yield without a separately verified conversion contract containing the
  deliverable basket, conversion factors, accrued interest, settlement, coupon,
  maturity, and calculation method.
- Price and yield generally move in opposite directions for a fixed cash-flow
  bond, all else equal. This is an interpretation constraint, not an exact
  one-for-one transformation and not a claim that every bond ETF move has a
  single rate cause.
- Percent, percentage-point, and basis-point changes remain distinct. One basis
  point is exactly `0.01 percentage point`; it is not a relative percent return.
- Yield change and price return are reported as separate quantities with
  separate units.

The current Yahoo/Cboe quote-index and Treasury-futures observations remain
provider-native prices or quote indices. This contract does not upgrade them to
official sovereign yields.

## Compatible curve construction

A curve request has a versioned `curve-policy/v1` identity. A numeric curve or
spread is valid only when every point has:

1. the same country and sovereign curve family;
2. the same economic quantity, compounding, day-count, source methodology, and
   finality class;
3. exact requested tenor identities;
4. observation times accepted by the policy's explicit alignment window; and
5. compatible PIT/vintage status for the intended descriptive or historical
   use.

The alignment window is a required configured value; this contract provides no
default. A daily-final observation and an intraday delayed quote are
`INCOMPATIBLE` unless a later approved policy explicitly defines and labels an
asynchronous comparison. Missing tenors are not interpolated unless a separate
versioned interpolation method, input set, and validation are selected.

Supported measure identities are explicit formulas:

| Measure | Formula |
|---|---|
| `slope_long_short` | `yield(long tenor) - yield(short tenor)` in percentage points |
| `butterfly` | `2 * yield(middle tenor) - yield(short tenor) - yield(long tenor)` in percentage points |
| `parallel_change` | Named aggregation of same-tenor changes under an exact method identity; no default aggregation |
| `curve_change` | Current valid measure minus the immediately prior compatible measure |

Every output records its input observation identities and as-of times. A graph
may show incompatible points separately, but must not draw a continuous curve
through them as if they were simultaneous.

## Rate components

Policy rate, nominal yield, real yield, breakeven inflation, and term-premium
estimate are independent typed components:

- `POLICY_RATE` is an administered target/rate identity, not a sovereign tenor.
- `SOVEREIGN_YIELD` is nominal unless its source contract says otherwise.
- `REAL_YIELD` requires an exact inflation-protected instrument or verified
  model series.
- `BREAKEVEN_RATE` requires compatible nominal and real identities and an exact
  formula; it is not identical to expected inflation.
- `TERM_PREMIUM_ESTIMATE` is model-dependent and requires model version,
  vintage, inputs, and revision semantics. It must not be inferred as a
  residual from unrelated current displays.

If any component lacks verified evidence, that component is `UNSUPPORTED` or
`UNAVAILABLE`; other components may remain independently visible. The service
must not force components to sum to a nominal yield or invent a missing
residual.

## Equity and sector linkage

A future `rate-equity-linkage/v1` result is descriptive research evidence, not
a causal or predictive signal. It binds:

- exact rate/curve measure and equity or sector return identities;
- common observation calendar and explicit lag convention;
- sample start/end, frequency, currency and total-versus-price return basis;
- point-in-time taxonomy, universe, membership, and data vintages;
- regime definition and version, fixed before evaluation;
- statistic identity, window, uncertainty interval, observation count, and
  missing-data rule; and
- out-of-sample or untouched evaluation status.

Growth-versus-value, discount-rate, or stock-bond-correlation explanations are
allowed only as labelled interpretations linked to those measurements. A
positive or negative historical correlation may change by regime and must not
be described as permanent, causal, or guaranteed to persist. Incompatible or
PIT-blocked inputs produce no linkage number.

## Bond ETF boundary

A bond ETF is a fund share, not an individual sovereign bond. A future
`bond-etf-semantics/v1` record requires:

| Field | Rule |
|---|---|
| `fund_id` and `share_class_id` | Exact legal fund/share-class identity; ticker alone is insufficient. |
| `benchmark_index_id` | Exact tracked index and version. |
| `price_as_of` and `nav_as_of` | Separate timestamps and currencies. |
| `effective_duration_years` | Source-labelled effective duration and as-of date; not inferred from the ticker name. |
| `weighted_average_maturity_years` | Separate from duration. |
| `convexity` | Verified value/method or `UNSUPPORTED`. |
| `distribution_policy` | Frequency, ex-date/pay-date semantics, and whether a return series reinvests distributions. |
| `yield_measure` | Exact measure such as SEC yield or distribution yield; measures are never substituted. |
| `expense_ratio` | Share-class expense ratio and effective/as-of date. |
| `tracking_difference` | Fund return minus benchmark return under matched dates, currency, and return basis. |
| `tracking_error` | Exact dispersion method, frequency, window, and observation count. |
| `currency_and_hedging` | Trading currency, underlying exposure currency, and verified hedging policy. |
| `liquidity_fields` | Exact volume/spread/premium-discount semantics or `UNSUPPORTED`. |

Duration-based price sensitivity is an approximation that requires the rate
shock definition, duration as-of, convexity treatment, and limitations. Fund
flows, distributions, fees, tracking, curve-shape changes, option effects, and
currency can make ETF returns differ from a simple sovereign-yield move.

`TLT` may be displayed only after its exact fund and share-class identity is
verified. Its price, NAV, duration, distribution yield, expense ratio, and
tracking fields keep independent as-of times. No field may be copied from a
similarly named long-duration product.

## Fail-closed rules

- Do not compare or subtract unlike units, tenor families, countries, finality
  classes, or asynchronous observations without a selected alignment policy.
- Do not infer a yield from a futures price or quote index.
- Do not infer real yield, breakeven, term premium, duration, convexity, tax,
  fee, distribution, or tracking semantics from a label.
- Do not forward-fill a current display into historical research or use a
  revised latest value as though it were the original vintage.
- Do not turn descriptive linkage into a trade instruction, causal story, or
  prediction.
- A missing component suppresses only dependent outputs; independent valid
  observations remain available with their own provenance.

## Requirement map

| Project Goal requirement | Contract evidence |
|---|---|
| Domestic and overseas sovereign tenors | exact country, issuer, curve family, tenor, and source identities |
| Price-yield inverse meaning | separate quantities plus constrained inverse interpretation |
| Policy, nominal, real, breakeven, term premium | independent component types and unsupported states |
| Compatible curve changes | versioned alignment policy, exact formulas, and input identity binding |
| Equity valuation and sector linkage | PIT-safe descriptive linkage schema and non-causal interpretation rule |
| Regime-varying stock-bond correlation | versioned regime/sample/statistic identity and uncertainty |
| TLT and other bond ETFs | fund/share class, duration, distribution, fee, tracking, NAV, currency, and liquidity boundary |
| Honest missing or mixed evidence | mandatory fail-closed states and independent-component preservation |

## Explicitly deferred

Provider selection, licensing, Data schemas, collection, normalization,
historical promotion, formulas not named above, GUI implementation, predictive
features, TLT portfolio simulation, optimization, recommendations, and orders
remain outside this documentation-only contract. Each requires separate current
authority, an owning domain contract, and proportionate tests.
