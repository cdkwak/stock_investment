# Local Daily Korean Market Summary Contract

Status: `DOCUMENTATION_ONLY / INPUT_REGISTRY_CLOSED / RUNTIME_NOT_IMPLEMENTED`

Contract ID: `daily-market-summary/v1`

## Purpose and authority

This GUI-owned application-service contract defines one deterministic Korean
daily market summary over accepted local typed results. Its default Telegram
projection is intentionally short: a typical message is four lines and may
never exceed six lines or 480 Unicode code points.

The contract owns no provider or external-AI call, Data/account mutation,
scheduler, GUI calculation, strategy, recommendation, or order. It authorizes
no runtime by itself. A future implementation must be separately claimed and
must consume only the reviewed input registry below.

## Current release boundary

Input registry `daily-market-summary-input-registry/v1`, revision `1`, is
closed as follows. `BOUND` requires the exact accepted contract and deterministic
result identity shown. `UNAVAILABLE_IN_V1` is an intentional numeric-free state,
not permission to select a similar file, card, dataset, or provider response.

| Input role | Requirement | Revision-1 selection | Exact accepted identity |
|---|---|---|---|
| `MARKET_STATE` | `REQUIRED_CORE` | `UNAVAILABLE_IN_V1` | No accepted local application-service result selected |
| `OVERBOUGHT_OVERSOLD` | `OPTIONAL` | `UNAVAILABLE_IN_V1` | No accepted local application-service result selected |
| `VALUATION` | `OPTIONAL` | `UNAVAILABLE_IN_V1` | No accepted local application-service result selected |
| `MACRO_SOVEREIGN_RATES` | `OPTIONAL` | `UNAVAILABLE_IN_V1` | No accepted local application-service result selected |
| `DERIVATIVES_VOLATILITY_FLOW` | `OPTIONAL` | `UNAVAILABLE_IN_V1` | No accepted local application-service result selected |
| `SECTOR_BREADTH_CONCENTRATION` | `OPTIONAL` | `UNAVAILABLE_IN_V1` | No accepted local application-service result selected |
| `CRASH_RISK` | `OPTIONAL` | `CONTRACT_ONLY_NO_RESULT` | [`crash-risk-validation/v1`](../backtest/CRASH_RISK_VALIDATION_CONTRACT.md); documentation only, no accepted result |
| `REFRESH_STATUS` | `REQUIRED_SYSTEM` | `BOUND` | [`gui-refresh-status/v1`](GUI_REFRESH_STATUS_CONTRACT.md), produced only by the accepted local projector; result ID is SHA-256 of the canonical validated payload |
| `ACCOUNT_VIEW` | `OPTIONAL` | `UNAVAILABLE_IN_V1` | No accepted sanitized account/NAV application-service result selected |

Therefore revision 1 intentionally produces `NO_OUTPUT`. It cannot emit a
market headline until a reviewed registry revision binds `MARKET_STATE` to one
exact accepted local result. A future implementation task may not choose an
input implicitly; selection requires a reviewed registry revision with contract
ID/version, result-ID rule, owner, semantic role, and digest rule.

## Closed result envelope

A result has exactly these top-level fields:

| Field | Rule |
|---|---|
| `contract_id` | Exactly `daily-market-summary/v1` |
| `summary_id` | Stable content digest of the registry revision/digest, versioned rules/templates, boundaries, ordered bindings, structured claims/account/watch items, and compact projection; excludes `composition_time_utc` |
| `summary_state` | `AVAILABLE`, `PARTIALLY_AVAILABLE`, `NO_OUTPUT`, or `INVALID` |
| `market_scope` | Exactly `KR_EQUITY_MARKET` |
| `composition_time_utc` | Aware instant normalized to `Z`; never a source as-of |
| `current_boundary` | Exact `ObservationBoundary` |
| `previous_boundary` | Comparable `ObservationBoundary` or null |
| `input_registry_ref` | Exact registry ID, revision, and content digest |
| `input_bindings` | Nine bindings in the registry order above, including unavailable roles |
| `sections` | Seven sections in the fixed order below |
| `account_relevance` | Sanitized typed result or typed unavailable state |
| `watch_items` | Evidence-bound observations to watch, never actions |
| `reason_codes` | Closed ordered reason codes |
| `compact_projection` | `TELEGRAM_COMPACT_V1` projection or null |

Unknown/duplicate fields, roles, claims, unstable ordering, naive timestamps,
nonfinite values, invalid units, identity/digest mismatch, or free-form provider
errors make the whole result `INVALID`.

`composition_time_utc` is occurrence metadata and never contributes to
`summary_id`. Recomposition of identical evidence and semantic content must
produce the same `summary_id` even when only the composition instant changes.

`ObservationBoundary` contains `market_date`, `decision_time_utc`,
`usable_information_cutoff_utc`, `calendar_id`, and `boundary_kind`.
`boundary_kind` is `INTRADAY_AS_OF`, `REGULAR_CLOSE`, or
`LAST_COMPLETED_SESSION`. Instants are aware and serialized as `Z`; Korean text
may render KST with an explicit label.

## Input binding and eligibility

Every `InputBinding` contains `input_role`, `selection_state`,
`service_result_id`, `contract_id`, `contract_version`, `content_digest`,
`result_state`, `source_as_of`, `market_date`, `available_at`, `usable_from`,
`freshness_state`, `finality_state`, `predictive_pit_status`, ordered
`evidence_ids`, and ordered `reason_codes`.

- `result_state` is `VALID`, `PARTIAL`, `UNAVAILABLE`, or `INVALID`.
- `freshness_state` is `CURRENT`, `EXPECTED_LAG`, `STALE`, or `UNKNOWN`.
- `predictive_pit_status` is `PIT_SAFE`, `PIT_LIMITED`, `PIT_BLOCKED`, or
  `UNKNOWN`.
- A revision-1 unavailable role has null contract/result/digest/timing fields,
  `result_state=UNAVAILABLE`, and `NO_ACCEPTED_LOCAL_RESULT` only.
- A bound role is eligible for descriptive facts only when its exact registry
  identity/digest/timing validates, its owner permits descriptive use, and its
  freshness is `CURRENT` or contract-valid `EXPECTED_LAG`.
- Predictive or causal language is impossible unless the selected input contract
  explicitly permits that exact claim. Revision 1 selects none.

A payload that supplies a result for an unselected role, omits any of the nine
registry bindings, duplicates a role, or labels invalid/nonfinite/unit/timing
evidence as unavailable is `INVALID`. An unavailable optional role must still
be present as an explicit `UNAVAILABLE` binding; `INPUT_BINDING_MISSING` is
always a validation failure and never an optional-section fallback.

## Sections and total dependency map

`sections` always contains these roles in this order:

| Section | Activation roles | Required/optional behavior |
|---|---|---|
| `MARKET_AND_POSITION` | `MARKET_STATE`; optional `OVERBOUGHT_OVERSOLD` | Market state is required for any summary; missing optional position evidence makes this section partial |
| `VALUATION` | `VALUATION` | Optional section; unavailable role yields no claims |
| `MACRO_AND_SOVEREIGN_RATES` | `MACRO_SOVEREIGN_RATES` | Optional; quote indices, futures prices, official yields, curves, and bond-ETF returns never substitute |
| `DERIVATIVES_VOLATILITY_AND_FLOW` | `DERIVATIVES_VOLATILITY_FLOW` | Optional; components remain separate and missing values are not neutral |
| `SECTOR_BREADTH_AND_CONCENTRATION` | `SECTOR_BREADTH_CONCENTRATION` | Optional; current classifications never backfill history |
| `CRASH_RISK` | `CRASH_RISK` | Optional; contract-only state yields no risk number or calm/safe claim |
| `TODAY_WATCH` | `REFRESH_STATUS` plus at least one eligible factual claim | Optional watch section; unproven next instant renders `다음 확인 시각 미확정` |

Each section has `section_state`, ordered `claims`, and ordered
`reason_codes`. `section_state` is:

- `AVAILABLE`: its activation result is `VALID` and every rendered component is
  eligible;
- `PARTIALLY_AVAILABLE`: at least one eligible claim exists but a selected or
  optional component is `PARTIAL`/`UNAVAILABLE`;
- `UNAVAILABLE`: no eligible claim exists; `claims=[]`; or
- `INVALID`: any dependent binding is invalid; the whole result becomes
  `INVALID` before rendering.

## Claims and current/prior comparison

Each claim contains exactly `claim_id`, `claim_class`, `template_id`,
`rendered_ko`, `current_evidence_refs`, `previous_evidence_refs`,
`source_as_of_refs`, `freshness_refs`, `uncertainty_codes`, and
`invalidation_conditions`.

`claim_class` is one of:

- `FACT`: direct contract-permitted local fact;
- `RULE_INTERPRETATION`: output of a cited deterministic versioned rule;
- `UNCERTAIN_INFERENCE`: separately permitted bounded inference with explicit
  uncertainty and non-empty invalidation conditions; or
- `OPINION`: pre-registered non-executable review prompt stating that the user
  decides.

Every number, date, direction, comparison, state adjective, and named entity
must trace to one exact field or registered rule. Templates accept no arbitrary
prose, provider/model text, URLs, paths, exceptions, credentials, or account
identifiers. Version 1 has no forecast, causal assertion, target, guaranteed
outcome, buy/sell/weight instruction, or safe-market template.

The previous boundary is the latest earlier complete boundary with identical
role, contract/version, semantics, unit, frequency, calendar, boundary kind,
and finality/vintage policy. A field comparison is `HIGHER`, `LOWER`,
`UNCHANGED`, or `NOT_COMPARABLE` only under the owning contract's tolerance.
No fill, splice, averaging, rebasing, resampling, or subjective prior selection
is allowed. `NOT_COMPARABLE` omits the compact change line and records
`직전 비교 불가` only in detail.

## Default Telegram projection

`compact_projection.profile` is exactly `TELEGRAM_COMPACT_V1`. It is a
deterministic projection of the structured result, not a second narrative.

```text
📌 한국시장 · {market_date} {boundary_label}
요약 | {one highest-priority FACT or RULE_INTERPRETATION} [{source}·{as_of_kst}]
변화 | {at most one comparable change} [{source}·{as_of_kst}]
확인 | {at most one material watch/uncertainty} [{source}·{as_of_kst}]
계좌 | {at most one sanitized relevance statement} [{snapshot_as_of_kst}]
상태 | {freshness_ko} · 미확인 {count} · 상세 {detail_action_id}
```

Rules:

- Header, `요약`, and `상태` are mandatory; absent optional lines disappear
  rather than showing repeated `N/A` blocks. A normal message is 3–4 lines.
- At most six lines and 480 Unicode code points, including evidence markers.
  Each claim line contains one sentence and one compact source/as-of marker.
- Optional retention priority is `확인`, `변화`, then `계좌`. Overflow drops a
  whole lowest-priority optional line; it never cuts a number, warning, source,
  timestamp, or uncertainty qualifier.
- The compact headline may use only `FACT` or `RULE_INTERPRETATION`.
  `UNCERTAIN_INFERENCE` may appear only on the `확인` line with `불확실`.
  `OPINION` is detail-only and is never pushed by default.
- `상태` compresses missing/stale/conflicting counts. Exact roles/reasons and
  full evidence remain in the structured detail view; diagnostics and stack
  traces never enter Telegram.
- `detail_action_id` is one allowlisted opaque local action, never a URL, path,
  command, provider parameter, or credential.

## Delivery and alarm control

- Default push eligibility is once per Korean `market_date`, only for an
  accepted `REGULAR_CLOSE` or `LAST_COMPLETED_SESSION` result.
- Recomposition with the same stable `summary_id`, including a recomposition
  whose only change is `composition_time_utc`, is idempotent and sends nothing.
  Intraday rereads update local detail only; they do not create repeated daily
  Telegram messages.
- One correction message is allowed only when a newly accepted source changes a
  rendered compact claim. It uses the `정정` header, a new digest, and the same
  bounds. Status-only or diagnostic changes do not trigger a correction.
- `NO_OUTPUT` and `INVALID` send no market-summary message. Repeated operational
  failures belong to the sanitized Issue-State alert owner, not this summary.
- This policy defines eligibility only; it does not install or change a
  scheduler or Telegram runtime.

## Account and watch boundaries

Account relevance is composed only after market claims are fixed. It accepts
one sanitized read-only account/NAV result and may state bounded exposure,
concentration, currency, or already validated policy/scenario relevance. Raw
balances, holdings payloads, account IDs, credentials, order history, and
executable allocation/action fields are forbidden. Missing or invalid price,
FX, NAV, identity, or timing suppresses the entire compact account line.

A watch item cites an eligible condition and a proven next-observation basis
from `gui-refresh-status/v1` or its exact input contract. It states what to
observe, never what will occur or what to trade. An unproven time is explicit
and cannot be inferred from composition time, mtime, GUI timers, or past runs.

## Total state and reason mapping

Evaluation order is fixed:

1. Any schema, registry, missing/duplicate/unexpected binding,
   identity/digest, nonfinite, unit, timestamp, timing, or privacy violation ->
   `INVALID`.
2. Otherwise unavailable `MARKET_STATE` or `REFRESH_STATUS`, invalid/ambiguous
   current boundary, or zero eligible factual sections -> `NO_OUTPUT`.
3. Otherwise all seven sections `AVAILABLE` and account view `VALID` ->
   `AVAILABLE`.
4. Every other renderable result -> `PARTIALLY_AVAILABLE`.

For both `INVALID` and `NO_OUTPUT`, the invariant is exact:
`sections[*].claims=[]`, `account_relevance.claims=[]`, `watch_items=[]`, and
`compact_projection=null`. No numeric token, account statement, watch item, or
market narrative may survive validation failure.

Closed reason codes and effects are:

| Condition | Reason | Fixed Korean detail label | Effect when otherwise valid |
|---|---|---|---|
| Registry role has no accepted result | `NO_ACCEPTED_LOCAL_RESULT` | `검증된 입력 없음` | Required role -> `NO_OUTPUT`; optional role -> dependent section unavailable and summary partial |
| Missing registry binding | `INPUT_BINDING_MISSING` | `입력 누락` | `INVALID` and complete suppression |
| Stale input | `SOURCE_STALE` | `기준시각 오래됨` | Dependent section unavailable; required core -> `NO_OUTPUT` |
| Unknown timing/finality | `TIMING_OR_FINALITY_UNKNOWN` | `기준시각·확정성 미확인` | Same as stale; never infer timing |
| Semantic/PIT prohibition | `PIT_OR_SEMANTIC_BLOCKED` | `의미·시점 검증 미완료` | Dependent claim/section unavailable |
| Prior not comparable | `PREVIOUS_NOT_COMPARABLE` | `직전 비교 불가` | Current fact may remain; compact change line omitted |
| Registered resolvable conflict | `CONFLICTING_EVIDENCE` | `근거 상충` | Dependent section partial with explicit detail |
| Conflict can change headline meaning/direction | `HEADLINE_CONFLICT` | `핵심 근거 상충` | `NO_OUTPUT` |
| Rule/template absent | `RULE_OR_TEMPLATE_MISSING` | `표시 규칙 없음` | Dependent claim unavailable; required headline -> `NO_OUTPUT` |
| Unsupported narrative | `UNSUPPORTED_CLAIM` | `지원하지 않는 해석` | `INVALID` |
| Account view absent | `ACCOUNT_VIEW_UNAVAILABLE` | `계좌 근거 없음` | Account line absent; summary partial |
| No eligible facts | `NO_ELIGIBLE_FACT_SECTION` | `표시 가능한 사실 없음` | `NO_OUTPUT` |
| Schema/identity/digest/unit/nonfinite/timing violation | `VALIDATION_FAILED` | `검증 실패` | `INVALID` and complete numeric/text suppression |
| Privacy violation | `PRIVACY_VALIDATION_FAILED` | `개인정보 검증 실패` | `INVALID` and complete suppression |

Unknown/free-form reason codes are `INVALID`.

## Project Goal mapping

| Goal requirement | Contract invariant |
|---|---|
| Market and overbought/oversold | Required `MARKET_STATE`, optional `OVERBOUGHT_OVERSOLD`, and `MARKET_AND_POSITION` |
| Valuation, macro/rates, derivatives/volatility/flow, sector, crash risk | Six separately bound non-substitutable roles/sections |
| Current versus prior | Exact comparable boundaries and contract-owned tolerance |
| What to watch | One evidence-bound compact `확인` line plus typed detail |
| Account meaning | Post-market sanitized optional account relevance |
| Fact/rule/inference/opinion separation | Closed claim classes; compact projection excludes opinion |
| Source, as-of, freshness | Per-claim refs plus compact source/as-of marker and status footer |
| Missing/stale/conflicting evidence | Total state/reason map and numeric-free suppression |
| Short Korean output | Typical 3–4 lines; hard maximum 6 lines/480 code points |
| No alert flood | Once-per-market-date final-boundary delivery, digest dedupe, one evidence-changing correction |
| Validated local inputs only | Closed reviewed registry; no raw file, GUI calculation, provider, or external AI |

## Acceptance and implementation boundary

This documentation task is complete only when links, required tokens, registry
coverage, total state mapping, and `INVALID`/`NO_OUTPUT` suppression are
deterministically checked. A later implementation is not accepted until a
reviewed registry revision binds `MARKET_STATE`, fixtures cover every state and
reason, replay is deterministic, all visible tokens trace to evidence, compact
length/deduplication/correction tests pass, and provider/account/Data/scheduler
mutation counts remain zero.

Until then this contract produces no message, number, account conclusion,
watch item, or trade view and authorizes no runtime or external action.
