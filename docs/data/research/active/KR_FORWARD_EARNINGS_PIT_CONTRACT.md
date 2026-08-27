# Korean Forward Earnings and Revision PIT Contract

Status: `SEMANTIC_CANDIDATE_IDENTIFIED / RIGHTS_AND_HISTORICAL_PIT_BLOCKED / NUMERIC_USE_FORBIDDEN`

Decision date: `2026-08-27 KST`

This is a source-acceptance and future dataset contract. It calls no paid API,
creates no subscription, stores no vendor value, and grants no Dashboard,
derived-feature, Backtest, or redistribution right.

## Decision

FnGuide/FnSpace is the strongest Korea-specific semantic candidate found for
constituent-level 12-month forward earnings data. Its official catalog names a
daily paid forward family containing 12M-forward EPS, BPS and ROE plus one-week
and one-month EPS revision fields. FnGuide's official Company Guide also defines
12M-forward EPS from FY1/FY2 consensus and the remaining fiscal-year months.

That evidence closes only **field existence and a high-level formula**. It does
not authorize this project to collect or retain the values. The published
FnSpace license limits use to the subscriber and says the data may not be built
into the subscriber's database or exposed through an application or to a third
party. Exact historical vintage timestamps, contributor counts, stale-estimate
rules, revision replacement policy, point-in-time KOSPI membership/weights,
index aggregation, and project-specific local retention/derived-display rights
also remain unresolved. No existing credential or entitlement for this route is
assumed.

LSEG I/B/E/S and FactSet remain credible licensed alternative source classes,
but their reviewed public material does not bind an entitled field/schema to
the exact KRX KOSPI universe or grant the required retention/display rights.
OpenDART can supply as-filed realized financial facts, not analyst forward
consensus, and must remain a separate trailing/actual axis. Current KRX weighted
PER/PBR is descriptive and must never substitute for a forward estimate.

## Required logical datasets

These names are proposed contracts, not registered production datasets.

| Dataset | Grain | Purpose | Current state |
|---|---|---|---|
| `kr_equity_forward_consensus_vintage` | security × provider vintage × metric × horizon | Preserve source analyst-consensus observations without overwrite | `RIGHTS_BLOCKED` |
| `kr_index_membership_weight_vintage` | index × security × effective interval × version | Exact historical KOSPI membership, float/share treatment and weights | `SOURCE_UNSELECTED` |
| `kr_market_forward_earnings_daily` | market × accepted vintage | Contracted aggregate EPS/BPS/ROE and matching forward multiples | `DERIVATION_BLOCKED` |
| `kr_market_earnings_revision_daily` | market × accepted vintage × lookback | 1M/3M changes, revision breadth and contributor coverage | `DERIVATION_BLOCKED` |

Do not overload `kr_index_fundamental_daily`, which remains the accepted KRX
provider-native trailing/descriptive PER/PBR series.

## Source-observation schema

One row represents one provider observation exactly as available in one
retained vintage. The minimum fields are:

```text
source_provider
source_product
source_field_id
source_request_id
source_landing_sha256
security_id
security_id_type
listing_market
accounting_scope                 # CONSOLIDATED / SEPARATE / PROVIDER_MAIN
metric                           # EPS / BPS / ROE / NET_INCOME / EQUITY
estimate_horizon                 # exact FWD_12M, FY1, FY2; never generic FWD
forecast_period_start
forecast_period_end
consensus_statistic              # MEAN / MEDIAN / other exact provider value
consensus_window
contributor_count
contributor_identity_policy
currency
unit
value
adjustment_basis                 # split/share/corporate-action treatment
provider_observation_date
provider_published_at_utc
provider_revision_id
provider_revision_at_utc
retrieved_at_utc
available_at_utc
vintage_id
is_first_seen
rights_profile_id
```

`provider_observation_date`, publication time, retrieval time and economic
forecast horizon are distinct. A reporting-period end, current web page date,
or file modification time may not replace publication or availability.

## PIT availability and revision rules

1. Landing bytes and request metadata are immutable. A later source response is
   a new `vintage_id`; it never overwrites a prior estimate.
2. If a provider supplies a documented publication timestamp,
   `available_at_utc` is that timestamp only after timezone and release-policy
   validation. Otherwise `available_at_utc=retrieved_at_utc` and the row is
   usable only from this project's first observation forward.
3. A vendor's current historical download is not historical PIT evidence by
   itself. Backfilled values remain `CURRENTLY_RESTATED_HISTORY` unless an
   immutable as-of archive or reproducible vintage service proves what was
   available then.
4. Split-adjusted or restated historical EPS/BPS may change without a new
   economic forecast. The adjustment basis and vendor revision identity must be
   retained; revision features cannot infer analyst action from a mechanical
   restatement.
5. Amendments, contributor additions/removals, accounting-scope changes and
   fiscal-year rollovers are separate revision causes. A missing cause is
   `UNKNOWN`, not analyst-up or analyst-down.
6. Backtest joins use `available_at_utc <= decision_time_utc` and the latest
   accepted vintage at that time. Calendar dates alone are insufficient.

## Market aggregation contract

No market aggregate may be calculated until the source closes all of the
following for the exact KRX KOSPI identity:

- effective-dated constituents, security/share-class mapping and float or index
  weights for the same accepted vintage;
- price timestamp, currency, shares/float and corporate-action basis that match
  the forecast denominator;
- treatment of negative, zero, missing and stale earnings/book values;
- aggregation formula, including whether the provider uses sums of market cap
  and forecast earnings/book, weighted ratios, harmonic aggregation, or a
  provider-native index field;
- minimum forecast coverage and contributor count, with coverage reported both
  by constituent count and weight/market-cap share;
- a single accounting scope and forecast horizon across all included rows.

Per-security EPS must not be averaged across securities. A provider-native
index aggregate and a project-reconstructed aggregate are different identities
and must have separate dataset IDs and validation.

## Earnings revision contract

Revision features compare two accepted vintages only when provider, metric,
horizon, accounting scope, adjustment basis, security identity and aggregation
method are identical.

```text
eps_fwd_change_1m = current_forward_eps / prior_forward_eps_1m - 1
eps_fwd_change_3m = current_forward_eps / prior_forward_eps_3m - 1
revision_up_ratio = upgraded_covered_weight / comparable_covered_weight
revision_down_ratio = downgraded_covered_weight / comparable_covered_weight
revision_breadth = revision_up_ratio - revision_down_ratio
```

The exact lookback selection, unchanged tolerance, minimum comparable coverage,
weighting basis and fiscal-roll handling must be pre-registered. A missing prior
vintage, horizon rollover or mechanical restatement yields null, never zero.
Provider-supplied revision fields and project-derived changes are separate
identities and require an exact reconciliation before either is promoted.

## Forward ROE and valuation linkage

Forward ROE is accepted only as a provider field with an exact numerator,
denominator, accounting scope, horizon and unit, or as a separately versioned
derivation over matching forward net income and equity vintages. Current ROE,
trailing ROE and Forward ROE are never interchangeable.

The following remain blocked until matching horizons and vintages reconcile:

- expected earnings growth from trailing versus forward PER;
- expected book growth from current versus forward PBR;
- `PBR / Forward ROE` or a PBR-on-ROE residual;
- forward earnings yield and the Korean 10Y yield gap;
- price decomposition into forward-EPS change and multiple expansion;
- `EARLY RECOVERY`, `LATE BULL`, `TOP RISK`, high/low-point labels or a combined
  market-temperature score.

## Acceptance gates

Reclassify one source to `SELECTABLE_CANDIDATE` only after written primary or
contractual evidence closes all of these gates:

1. exact entitled field IDs, request schema, response sample, frequency,
   history depth, pagination/rate limits and error semantics;
2. exact formulas, horizon, accounting scope, statistic/window, units,
   contributors, adjustment and revision behavior;
3. publication/activation timestamps and immutable or replayable historical
   vintages suitable for PIT use;
4. exact KOSPI historical membership/weights and market aggregation treatment;
5. written rights for project-local Landing, normalized database retention,
   derived research, local Dashboard display and any later remote display;
6. applicable price and limits accepted by the user through a separate purchase
   decision; agents must not subscribe or accept terms autonomously;
7. Landing-first collector, atomic promotion, secret-safe logging, idempotent
   replay, coverage/revision validation and holdout-isolation tests.

Until every gate passes, the Dashboard may show only `선행 실적 축: 데이터 계약
미완료` and must keep the earnings axis absent rather than assigning a neutral
zero.

## Primary evidence

- FnGuide/FnSpace paid forward-field catalog and published license:
  <https://www.fnspace.com/DataMart/RequestInfo?aid=A000006&cid_p=C001&pid=P0003>
- FnGuide Company Guide field/formula help:
  <https://comp.fnguide.com/SVO2/ASP/SVD_help10.asp>
- FnGuide forward API field-enumeration example:
  <https://www.fnspace.com/Community/CommunityView?rno=172>
- Existing exact-KOSPI forward PER/PBR source decision:
  [KOSPI_FORWARD_PER_PBR_SOURCE_DECISION.md](../../sources/forward_valuation/KOSPI_FORWARD_PER_PBR_SOURCE_DECISION.md)

