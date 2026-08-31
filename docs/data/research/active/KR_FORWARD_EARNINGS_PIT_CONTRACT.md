# Korean Forward Earnings and Revision PIT Contract

Status: `FREE_OBSERVATION_UNSUPPORTED / PAID_SEMANTIC_CANDIDATE_ONLY / RIGHTS_AND_HISTORICAL_PIT_BLOCKED / NUMERIC_USE_FORBIDDEN`

Decision date: `2026-08-30 KST`

This is a source-acceptance and future dataset contract. It calls no paid API,
creates no subscription, stores no vendor value, and grants no Dashboard,
derived-feature, Backtest, or redistribution right.

## Decision

FnGuide/FnSpace is the strongest Korea-specific semantic candidate found. Its
official catalog names a paid daily forward family containing 12M-forward EPS
and ROE, an `EPS1` one-week/one-month revision ratio, and at least a distinct
12M-forward EPS one-week adjusted change. The accessible public output list
truncates after that one-week field, so it does not prove a matching 12M-forward
one-month field. The current official Company Guide demonstrates a KOSPI
security and labels its per-security forward-EPS field
`EPS(Fwd.12M, 지배)`. It does not establish complete Korean-market coverage or
prove the revision identities equivalent.

For Forward EPS specifically, current official Help and the official 2019
FnSpace recipe close more than field existence. Help publishes an expression
using FY1 and FY2 estimated controlling-shareholder net income, `a/12` and
`(12-a)/12`, and reference-month-end issued shares. Crucially, the currently
rendered official text places a literal multiplication operator (`*`) between
the two weighted FY terms; it does not publish a plus sign. A weighted-sum
interpretation is contextually plausible but is not the provider's published or
confirmed formula. The recipe publishes field ID `E312060`, exact label
`EPS(Fwd.12M, 지배)`, unit `원/주`, sample endpoint/parameters, a one-year date
range and an example result shape.

That is **partial public expression/schema/reproducibility evidence**, not a
closed formula or current source contract. The apparent `*` inconsistency keeps
the high-level roll formula and semantic gate unresolved. The Help assigns only
`기준월말 발행주식수` to the Forward EPS denominator; common/preferred/treasury
composition wording appears in adjacent trailing EPS/BPS explanations and is
not attributed here to the Forward EPS denominator. Current-schema stability,
complete request/response and error semantics, contributor rules, exact
API-product Korean-universe coverage, publication timestamps, immutable
vintages and correction identity also remain open. Public formulas for the
revision and Forward ROE fields remain incomplete.

FnSpace is also not a free observation route. The official page labels the
product paid, requires subscription/API credentials, applies coin consumption
and daily limits, and its published license forbids building the data into the
subscriber's database or exposing it through an application or to a third
party. Public page visibility therefore does not grant automation, retention,
Dashboard display or redistribution rights. No existing credential or
entitlement for this route is assumed, and no sample value was collected.

No paid alternative is selected or assumed entitled by this free-source
decision. OpenDART can supply as-filed realized financial facts, not analyst
forward consensus, and must remain a separate trailing/actual axis. Business
Quant's free Analyst Estimates API documents US-listed ticker/CIK coverage and
fiscal annual/quarterly EPS, not Korean securities, a 12M-forward composite,
1W/1M revision fields, Forward ROE, or historical estimate vintages. Current
KRX weighted PER/PBR is descriptive and must never substitute for a forward
estimate.

## Public Forward EPS expression and recipe evidence

### Provider-published text

The current official Company Guide Help renders the consolidated-accounting
expression with a literal `*` between the FY1 and FY2 terms:

```text
12M Forward EPS =
  {(a / 12 * FY1 controlling-shareholder net-income estimate)
   * ((12 - a) / 12 * FY2 controlling-shareholder net-income estimate)}
  / reference-month-end issued shares
```

The provider text therefore supplies the FY1/FY2 variables and the denominator
label, but its literal operator does not close a usable high-level roll formula.
The Forward EPS line itself does not state that its denominator includes
common, preferred and treasury shares; that composition wording belongs to
adjacent trailing EPS/BPS explanations.

### Contextual inference, not source fact

A conventional 12M roll could interpret the two terms as a weighted sum:

```text
[(a / 12 * FY1 estimate) + ((12 - a) / 12 * FY2 estimate)]
/ reference-month-end issued shares
```

That `+` expression is an inference only. It must not replace the published `*`
without a current provider clarification or corrected primary formula. The
meaning of `a`, exact denominator composition, contributor eligibility,
stale-estimate handling, corporate-action restatement and availability time
remain semantic gates.

The official FnSpace recipe posted `2019-06-21` publishes the following
historical sample contract without requiring a provider-value observation:

- item-list request:
  `ItemListApi?key=sample&format=json&apigb=A000006`;
- field-list columns: `DISPORD`, `ITEM_CD`, `ITEM_NM_KOR`, `NAME_PATH`,
  `P_ITEM_CD`, `P_ITEM_NM_KOR`, `UNIT`;
- Forward EPS field: `E312060`, `EPS(Fwd.12M, 지배)`, unit `원/주`;
- data request template:
  `Consensus4Api?key=sample&format=json&consolgb={M|C|I}&code={code}&frdate={YYYYMMDD}&todate={YYYYMMDD}&item={field_ids}`;
- `consolgb` meanings: `M=MAIN`, `C=consolidated`, `I=separate`;
- demonstrated parameters: `code=A005930`, `consolgb=M`,
  `frdate=20180621`, `todate=20190620`, with fields batched ten at a time;
- response handling: convert the JSON `dataset` to DataFrames, merge on `DT`,
  and print a sample result head reported as `[5 rows x 34 columns]`.

This is dated sample schema and replay guidance, not a current entitlement,
current-schema guarantee, historical-vintage archive or permission to retain
the demonstrated values. No sample endpoint was called and no response value
was copied into this repository.

## Field-by-field decision

Each decision below is independent. Failure of one field is not imputed from
another, and success of one field would not promote the others.

| Requested field | Identity evidence | Gates still open | Decision |
|---|---|---|---|
| Korea Forward EPS | Company Guide publishes an FY1/FY2 expression with a literal `*` between the weighted terms and a reference-month-end-issued-shares denominator; the 2019 FnSpace recipe identifies `E312060`, `EPS(Fwd.12M, 지배)`, `원/주` and a sample date-range request/result shape | Resolve published `*` versus inferred `+`, `a`, denominator composition, current-schema stability, complete API-product Korean coverage, statistic/window and contributors, adjustment/correction rules, publication time, immutable vintages, entitlement and retention/display rights | `UNSUPPORTED_FREE_OBSERVATION` |
| Korea EPS revision, one week | FnSpace separately names `EPS1 이익조정비율(1주, 지배)` and `EPS(Fwd.12M) 변화율(1주, 조정, 지배)` | Which identity is intended, each exact formula/denominator, unchanged/fiscal-roll/corporate-action handling, comparable prior vintage, revision cause, Korean coverage, rights and replayability | `UNSUPPORTED_FREE_OBSERVATION` |
| Korea EPS revision, one month | FnSpace names `EPS1 이익조정비율(1개월, 지배)`; the accessible public list does not establish a corresponding 12M-forward one-month field | Exact identity/horizon/formula, one-month calendar/session selection, comparable prior vintage, revision cause, Korean coverage, rights and replayability | `UNSUPPORTED_FREE_OBSERVATION` |
| Korea Forward ROE | FnSpace publicly names paid daily `ROE(Fwd.12M, 지배)` and a separate unqualified 12M-forward ROE | Exact numerator, denominator, averaging convention, accounting scope, unit, contributors, Korean coverage, publication time, immutable vintages, entitlement and retention/display rights | `UNSUPPORTED_FREE_OBSERVATION` |

All four fields remain absent/null. None may be synthesized from trailing
actuals, current-page values, KRX PER/PBR, fiscal-period estimates with a
different horizon, or a later restated series.

## Candidate gate matrix

Official/public documentation was rechecked on `2026-08-30 KST`. A blank or
undocumented gate fails closed; absence is never inferred as permission.

| Gate | FnGuide/FnSpace | Business Quant | OpenDART |
|---|---|---|---|
| Identity and horizon | Help publishes FY1/FY2 variables but literally joins the weighted terms with `*`; `+` is only a contextual inference, so the roll formula remains unresolved. The 2019 recipe maps the field to `E312060`, `EPS(Fwd.12M, 지배)`, `원/주`. It also names 12M-forward ROE, EPS1 1W/1M revisions and a distinct 12M-forward 1W change; public-list truncation leaves a 12M-forward 1M field unproven, and revision/ROE formulas remain incomplete | Fiscal annual/quarterly EPS consensus only; no documented 12M composite, revision, or ROE field | Filed reports and financial statements; no analyst-consensus field |
| Korea coverage | Help describes the Company Guide population as KRX-listed and KOSDAQ-registered companies, and the recipe demonstrates one Korean security; that does not prove the exact API product's complete current/historical KOSPI/KOSPI200 coverage | Official API overview limits equity coverage to US-listed securities resolved by ticker or SEC CIK | Korean disclosing companies, but actual filings rather than estimates |
| PIT, revisions and availability | Daily generation and a recipe date-range query from `20180621` through `20190620` are documented; a historical-shaped response is not evidence of provider publication time, first availability, revision identity/cause or immutable/replayable vintages | Estimate request takes ticker/CIK and mode, not an as-of vintage; returned fiscal periods are forecast targets, not perspective dates | Filing search includes receipt/report identity, but that is PIT evidence for company filings only |
| Access | Paid subscription and API key | Free API key is documented | API key is documented; no value call was made |
| Retention | Catalog license expressly forbids building a subscriber database | Standard pages allow licensed internal analysis, but retention after licence end is explicitly agreement-specific; no project retention lifecycle is granted | Not evaluated further because the required fields do not exist |
| Display | Catalog license forbids application/third-party exposure; limited citation in print/electronic documents is not Dashboard permission | Internal dashboards are described for licensed internal use; external display requires redistribution licence | Not evaluated further because the required fields do not exist |
| Redistribution | Prohibited by the catalog license without another agreement | Enterprise/redistribution licence required; attribution and surfaces are agreement terms | Not evaluated further because the required fields do not exist |
| Cost and limits | Paid; public table documents plan coin allocations/daily limits of 50,000, 250,000 or 800,000 and per-item coin consumption | Free plan documents 30 API calls/day and 0.1 GB/month; higher usage and redistribution are paid | No semantic candidate, so free API access does not cure identity failure |
| Reproducibility | The 2019 recipe exposes `ItemListApi` with `apigb=A000006`; `Consensus4Api` parameters `key`, `format=json`, `consolgb`, `code`, `frdate`, `todate`, `item`; field-list columns; ten-item batching; `DT` merge logic; and a printed result head of `[5 rows x 34 columns]`. It is useful historical replay documentation, but not a promise that the current entitled schema, response envelope, errors or archive remain identical | Documented response fields are fiscal period, type, consensus/reported/high/low; no Korean ID or historical perspective-date replay | Reproducible filed actuals cannot reproduce analyst expectations |

No candidate closes all gates. FnSpace remains a paid semantic lead only;
Business Quant and OpenDART are rejected as substitutes for these four Korean
fields.

## Required logical datasets

These names are proposed contracts, not registered production datasets.

| Dataset | Grain | Purpose | Current state |
|---|---|---|---|
| `kr_equity_forward_consensus_vintage` | security × provider vintage × metric × horizon | Preserve source analyst-consensus observations without overwrite | `RIGHTS_BLOCKED` |
| `kr_index_membership_weight_vintage` | index × security × effective interval × version | Exact historical KOSPI membership, float/share treatment and weights | `SOURCE_UNSELECTED` |
| `kr_market_forward_earnings_daily` | market × accepted vintage | Contracted aggregate EPS/BPS/ROE and matching forward multiples | `DERIVATION_BLOCKED` |
| `kr_market_earnings_revision_daily` | market × accepted vintage × lookback | 1W/1M changes, revision breadth and contributor coverage | `DERIVATION_BLOCKED` |

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

This section governs only a future aggregate of the earnings/ROE observations
defined here. It does not decide or duplicate provider-native Forward PER/PBR
identity, licensing or aggregation; that exact scope belongs to
`RQ-20260826T012440-3679`.

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
eps_fwd_change_1w = current_forward_eps / prior_forward_eps_1w - 1
eps_fwd_change_1m = current_forward_eps / prior_forward_eps_1m - 1
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

Reclassify one field/source pair to `SELECTABLE_CANDIDATE` only after written
primary or contractual evidence closes all of these gates for that field. A
field-level constituent route may close independently, but any market aggregate
must additionally pass gate 4:

1. exact entitled field IDs, request schema, response sample, frequency,
   history depth, pagination/rate limits and error semantics;
2. exact formulas, horizon, accounting scope, statistic/window, units,
   contributors, adjustment and revision behavior;
3. publication/activation timestamps and immutable or replayable historical
   vintages suitable for PIT use;
4. for an aggregate only, exact KOSPI historical membership/weights and market
   aggregation treatment;
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

- FnGuide/FnSpace paid forward-field catalog, license, cost and limits (retrieved
  `2026-08-30 KST`):
  <https://www.fnspace.com/DataMart/RequestInfo?aid=A000006&cid_p=C001&pid=P0003>
- FnSpace API terms, effective `2018-11-20` and still presented as current when
  retrieved `2026-08-30 KST`:
  <https://policy.fnguide.com/FnSpace/Terms>
- FnGuide Company Guide KOSPI-security and Forward-EPS identity page (retrieved
  `2026-08-30 KST`; no numeric value retained here):
  <https://wcomp.fnguide.com/CompanyInfo/Consensus>
- Current FnGuide Company Guide Help formula and coverage guide (retrieved
  `2026-08-30 KST`):
  <https://wcomp.fnguide.com/Help/Guide?cmp_cd=466690>
- FnGuide/FnSpace Forward API field/schema/date-range/result-shape recipe,
  posted `2019-06-21` and retrieved `2026-08-30 KST`:
  <https://www.fnspace.com/Community/CommunityView?rno=172>
- Business Quant API overview and Analyst Estimates schema, last updated
  `2026-08-16` when retrieved `2026-08-30 KST`:
  <https://businessquant.com/docs/api/>,
  <https://businessquant.com/docs/api/estimates>
- Business Quant pricing, data-source/right summary, commercial licensing and
  terms (retrieved `2026-08-30 KST`):
  <https://businessquant.com/pricing>,
  <https://businessquant.com/data-sources>,
  <https://businessquant.com/commercial-licensing>,
  <https://businessquant.com/terms-of-use>
- OpenDART official disclosure and periodic-financial-statement API catalogs
  (retrieved `2026-08-30 KST`):
  <https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001>,
  <https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003>
- Out-of-scope Forward PER/PBR decision path owned by
  `RQ-20260826T012440-3679` (not modified here):
  [KOSPI_FORWARD_PER_PBR_SOURCE_DECISION.md](../../sources/forward_valuation/KOSPI_FORWARD_PER_PBR_SOURCE_DECISION.md)
