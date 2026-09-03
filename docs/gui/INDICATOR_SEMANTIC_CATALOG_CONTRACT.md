# Indicator Semantic Catalog and Explanation Contract

Status: `ACTIVE_CONTRACT / RUNTIME_IMPLEMENTATION_ALLOWED_UNDER_STANDING_GUI_AUTHORITY`

Contract ID: `indicator-semantic-catalog/v1`

Related accepted boundaries:

- [Project Goal](../project/PROJECT_GOAL.md)
- [GUI Status](GUI_STATUS.md)
- [GUI refresh-status contract](GUI_REFRESH_STATUS_CONTRACT.md)

## Purpose and authority boundary

This contract defines how a GUI consumer can explain an already
accepted indicator without changing its data, formula, source, or eligibility.
It owns semantic identity, version compatibility, concise Korean explanation,
and visual-composition safety only. It does not own provider or AI calls,
collectors, source selection, formula calculation, threshold creation,
normalization, predictive promotion, recommendation, or orders. Standing GUI
authority permits agents to implement the allowlisted registry, parser,
explanation view, widgets, local preferences, and tests that obey this contract;
provider transport and persistent market-data promotion stay Data-owned.

Source contracts and Data Status remain authoritative for data identity,
licensing, aggregation, finality, and PIT safety. Runtime refresh fields compose
by reference from `gui-refresh-status/v1`; this catalog never copies or invents
its operation state, last success, source as-of, or next eligibility.

## Two closed objects

Version 1 has two distinct objects:

1. `IndicatorSemanticEntry` is immutable meaning and presentation metadata.
2. `IndicatorExplanationView` binds exactly one entry version to one already
   validated observation and one refresh-status component.

Unknown fields, enum values, indicator identities, formula identities,
threshold identities, or reason codes fail validation. Free-form extensions
require a new contract version.

### `IndicatorSemanticEntry`

An entry has exactly these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `indicator_id` | allowlisted stable string | Meaningful identity; never a translated label or dataset column alone. |
| `indicator_version` | positive integer | Immutable semantic version for this identity. |
| `korean_display_name` | non-empty string | Short Korean display label. |
| `support_state` | `SupportState` | Whether this exact semantic is supported by accepted evidence. |
| `definition_ko` | non-empty string or null | Ordinary-language definition; null only when unsupported. |
| `formula` | `FormulaSpec` or null | Exact formula or provider-native declaration and its provenance. |
| `unit` | `UnitSpec` or null | Unit, scale, direction, and compatible-axis identity. |
| `horizon` | `HorizonSpec` or null | Forward/trailing/current/windowed meaning without substitution. |
| `aggregation` | `AggregationSpec` or null | Universe, version/as-of rule, method, and weighting. |
| `source_contract` | `SourceContractRef` or null | Exact source/dataset/series, timing, cadence, finality, PIT, and licence evidence. |
| `historical_comparator` | `ComparatorSpec` | Immutable same-definition comparison method or explicit unsupported state; never current coverage or a current result. |
| `ordinary_interpretation_ko` | non-empty string or null | Descriptive reading, never an action or prediction. |
| `limitations_ko` | non-empty ordered array | Known interpretation, timing, coverage, and source limits. |
| `thresholds` | ordered array of `ThresholdSpec` | Only evidenced descriptive references; may be empty. |
| `presentation` | `PresentationSpec` or null | Concise-to-detail text and safe axis/panel rules. |
| `unsupported_reason_code` | `SemanticReasonCode` or null | Required exactly when `support_state=UNSUPPORTED`. |

`SupportState` is exactly `SUPPORTED` or `UNSUPPORTED`. Unsupported entries
retain identity, Korean name, and a bounded reason, but every semantic numeric
field, formula, unit, horizon, aggregation, source, comparator value, threshold,
and presentation value slot is null or empty. Unsupported does not mean zero.

### Formula, unit, horizon, aggregation, and source

`FormulaSpec` has exactly `formula_id`, `expression`, `input_identities`,
`calculation_owner`, and `provenance_ref`.

- `calculation_owner` is `LOCAL_VERIFIED`, `PROVIDER_NATIVE`, or
  `DERIVED_BY_ACCEPTED_CONTRACT`.
- `PROVIDER_NATIVE` uses a declaration such as `provider-published aggregate;
  no local recomputation`. It must not reverse-engineer an absent formula.
- Every input identity and parameter is versioned. A parameter change requires
  a new `indicator_version` even if the display name stays unchanged.

`UnitSpec` has exactly `unit_id`, `display_unit`, `scale`, `direction`, and
`axis_compatibility_id`. `scale` is a rational transform from retained source
units, or `IDENTITY`. `direction` is `HIGHER_IS_HIGHER_READING`,
`LOWER_IS_HIGHER_READING`, `SIGNED_AROUND_BASELINE`, or `NO_MONOTONIC_MEANING`.
Direction is descriptive and cannot become a buy/sell rule.

`HorizonSpec` has exactly `horizon_kind`, `window_length`, `window_unit`,
`target_period`, and `completion_rule`. `horizon_kind` is one of `FORWARD`,
`TRAILING`, `CURRENT_POINT`, `WINDOWED_BACKWARD`, `SPOT`, or `UNRESOLVED`.
Forward, trailing, current, and unresolved records are incompatible identities.
No one may substitute for another. An unresolved provider meaning cannot be
labelled forward or trailing.

`AggregationSpec` has exactly `universe_id`, `universe_version_basis`,
`constituent_as_of_basis`, `aggregation_method`, and `weighting_method`.
Unknown provider methodology is written `PROVIDER_DEFINED_UNRESOLVED`, never
filled from a market convention. A scalar technical indicator uses
`aggregation_method=NOT_APPLICABLE` and identifies its input series instead.

`SourceContractRef` has exactly `dataset_id`, `series_id`, `source_authority_id`,
`source_contract_version`, `source_time_basis`, `market_date_basis`,
`source_publication_cadence_kind`, `finality_kind`, `pit_status`, and
`licence_status`.
Unproven licence or source semantics make the entry unsupported; they are not
inferred from file presence. Actual timestamps do not live in this static
object. `source_publication_cadence_kind` describes the accepted source's
publication/session cadence only. It is not the runtime read/retry cadence,
operation state, last success, or next eligibility owned by
`gui-refresh-status/v1`.

### Historical comparator and thresholds

`ComparatorSpec` has exactly `support_state`, `comparator_id`,
`definition_digest`, `method`, `frequency`, `minimum_observations`, and
`limitations_ko`.

- `support_state` is `SUPPORTED` or `UNSUPPORTED` and changes only with an
  accepted semantic/source-method contract change.
- `SUPPORTED` requires the same indicator identity/version, formula, unit,
  horizon, aggregation, source boundary, frequency, and comparison method.
- `UNSUPPORTED` means no accepted comparable-history contract exists.
- Static entries never contain `coverage_start`, `coverage_end`,
  `observation_count`, a current availability state, or a computed comparator
  value. Those change as retained data advances and belong only to the runtime
  `ComparatorObservation` below; changing them never creates a semantic version.
- A median, percentile, z-score, or range definition must name its exact method,
  frequency, and minimum observation count. No silent joining, padding,
  resampling, or gap filling is allowed.

`ThresholdSpec` has exactly `threshold_id`, `operator`, `value`, `unit_id`,
`role`, `evidence_ref`, `evidence_status`, and `interpretation_ko`.
`role` is `REFERENCE_LINE`, `DESCRIPTIVE_BAND`, or `VALIDATED_DECISION_RULE`.
`evidence_status` must be `EVIDENCED`; otherwise the threshold is absent rather
than displayed. A source or accepted validation artifact must prove the exact
value, operator, unit, horizon, and role. A familiar market convention is not
enough. Reference lines are not promoted to decision rules, and an empty
threshold list is the normal fail-closed state.

### Presentation and axis safety

`PresentationSpec` has exactly `summary_template_ko`, `detail_field_order`,
`preferred_surface`, `axis_compatibility_id`, `normalization_id`, and
`accessibility_template_ko`.

- The persistent summary contains Korean display name, value or availability,
  unit, and short interpretation. It does not repeat source internals.
- Detail order is definition, formula/provenance, unit/scale, horizon,
  aggregation/universe, historical comparison, thresholds, limitations, then
  the composed source/as-of/refresh block.
- Two series may share one unlabeled Y axis only when their exact
  `axis_compatibility_id`, unit, scale, and baseline semantics match.
- Otherwise each uses an explicitly labelled independent axis or panel.
- Normalization requires a versioned `normalization_id`, formula, base date or
  sample, unit, and limitations. `normalization_id=null` means no normalization.
  Visual pixel alignment is never semantic normalization.
- Price, percentage, percentage-point, ratio, yield, currency, volume,
  percentile, and 0–100 oscillator units are incompatible by default.

## `IndicatorExplanationView`

A future runtime view has exactly these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `contract_id` | exactly `indicator-semantic-catalog/v1` | View contract. |
| `indicator_id` / `indicator_version` | accepted entry key | Exact immutable semantic. |
| `availability_state` | `AVAILABLE`, `UNAVAILABLE`, or `UNSUPPORTED` | Observation availability, independent of support. |
| `value` | finite number or null | Null unless available and unit-valid. |
| `unit_id` | entry unit or null | Must equal the entry exactly. |
| `comparator_observation` | `ComparatorObservation` or null | Current definition-bound value, availability, and actual coverage; null when no comparator applies. |
| `threshold_ids` | ordered allowlisted IDs | Subset of the entry's evidenced thresholds. |
| `refresh_surface_id` / `refresh_component_id` | allowlisted references or null | The sole runtime as-of/date/freshness/cadence binding: exact foreign key into one `gui-refresh-status/v1` component. |
| `reason_codes` | ordered `SemanticReasonCode` values | Stable numeric-free explanation. |

`SemanticReasonCode` is exactly `SEMANTIC_UNSUPPORTED`, `SOURCE_UNAUTHORIZED`,
`LICENCE_UNPROVEN`, `FORMULA_UNPROVEN`, `HORIZON_UNRESOLVED`,
`AGGREGATION_UNRESOLVED`, `VALUE_UNAVAILABLE`, `SOURCE_AS_OF_UNAVAILABLE`,
`HISTORY_UNAVAILABLE`, `HISTORY_DEFINITION_MISMATCH`, `THRESHOLD_UNEVIDENCED`,
`UNIT_MISMATCH`, `AXIS_INCOMPATIBLE`, or `REFRESH_STATUS_UNAVAILABLE`.

`ComparatorObservation` has exactly `comparator_id`, `definition_digest`,
`availability_state`, `value`, `coverage_start`, `coverage_end`, and
`observation_count`. `availability_state` is `AVAILABLE`, `UNAVAILABLE`, or
`UNSUPPORTED`. `AVAILABLE` requires an exact ID/digest match to the immutable
`ComparatorSpec`, a finite value, valid ordered coverage boundaries, and
`observation_count >= minimum_observations`. `UNAVAILABLE` retains the matching
ID/digest and actual validated coverage/count when known but has `value=null`.
`UNSUPPORTED` and a missing comparator have no numeric value or invented
coverage. Runtime coverage and count never mutate the catalog entry.

The view never carries `source_as_of`, `market_date`, operation state,
freshness, last success, runtime cadence/seconds, or next eligibility itself.
Those render exclusively from the one referenced `gui-refresh-status/v1`
component. `availability_state=AVAILABLE` requires a valid non-null component
foreign key whose allowlisted component registration is bound to the exact
`SourceContractRef` dataset and series; the explanation does not duplicate that
registration. An absent, ambiguous, or identity-mismatched reference suppresses
the numeric value with `REFRESH_STATUS_UNAVAILABLE`. The catalog must not copy a
timestamp/date into the view, choose a surface summary when a component is
required, or synthesize timing from file mtime, GUI generation, or now.

## Fail-closed invariants

1. An unknown `indicator_id` or version is `UNSUPPORTED`, numeric-free, and
   cannot fall back to a similarly named entry.
2. `UNSUPPORTED` and `UNAVAILABLE` are distinct. The first lacks an accepted
   semantic/source route; the second has one but no currently valid value.
3. A value requires finite numeric input, exact unit, exact identity, and source
   timing/finality/PIT gates owned by its source contract.
4. Forward, trailing, current, unresolved, provider-native, and locally derived
   values never substitute for one another.
5. Historical comparisons require a definition digest match across the whole
   covered sample. A mismatch suppresses the runtime comparator observation,
   not the current indicator value or immutable comparator definition.
6. Missing aggregation, licence, formula, or threshold evidence remains
   explicitly unresolved. The GUI cannot infer it from a label or common usage.
7. An evidenced reference threshold is descriptive until a separate accepted
   validation explicitly identifies it as a decision rule.
8. A semantic or comparator failure cannot blank another independently valid
   indicator, chart, or Dashboard surface.
9. Concise Korean text cannot contain a recommendation, certainty claim,
   prediction, or unsupported causal story.

## Evidence-bound examples

These examples describe current accepted boundaries; they are not themselves
runtime registry entries. Agents may register and display them under standing
GUI authority only when the exact semantic/source evidence and numeric gates in
this contract pass.

### KRX weighted market PER/PBR

`KOSPI_WEIGHTED_PER` and `KOSPI_WEIGHTED_PBR` may reference
`kr_index_fundamental_daily/v1`, index code `1001`, provider-native ratio units,
XKRX market-date basis, accepted daily retained observations,
`finality_kind=UNRESOLVED`, and `NON_PREDICTIVE` use. Publication/revision
finality is not upgraded by successful collection or a completed market date.
Their formula/aggregation owner is KRX provider-native; any methodology detail
not present in the accepted source stays `PROVIDER_DEFINED_UNRESOLVED`. A
supported immutable comparator definition may specify an as-of-only median and
empirical percentile; its runtime observation separately discloses actual
coverage/count. It does not turn a low percentile into undervaluation or a trade
instruction.

The accepted contract does not prove that this provider-native PER is forward
PER. Its horizon therefore remains `UNRESOLVED`, and a separate
`KOSPI_FORWARD_PER` entry is `UNSUPPORTED / HORIZON_UNRESOLVED` with no numeric
value. PBR cannot substitute for PER. KOSDAQ code `2001` is a separate series
identity and never fills KOSPI.

### RSI14 and 60-session disparity

`RSI14_WILDER` is the verified Wilder 14-completed-observation calculation on
the exact accepted close series, with a 0–100 oscillator unit. Existing 30 and
70 guide lines are evidenced descriptive `REFERENCE_LINE` thresholds; their
interpretation says oversold/overbought reference and explicitly not an
investment-decision rule. RSI cannot share an unlabeled price or ratio axis.
Its first average is the simple mean of the first 14 gains/losses; each next
average is `(previous * 13 + current) / 14`, `RS = avgGain / avgLoss`, and
`RSI = 100 - 100 / (1 + RS)`, with `avgLoss == 0` defined as RSI 100.

`CLOSE_DISTANCE_MA60_PP` is the same retained `close / MA60 * 100` disparity
displayed as signed `(close / MA60 - 1) * 100` percentage-point distance around
zero. Its zero line follows from the formula and is a baseline, not a buy/sell
threshold. It has a distinct axis identity from RSI14, price, PER, and PBR.

### VIX/VKOSPI percentile context

VIX and VKOSPI are separate source/market identities. A percentile must name
its exact window, actual valid observation count, frequency, definition digest,
and empirical ranking method. Current accepted UI uses volatility as context
and does not invent percentile thresholds. The VIX temperature row may rank
one independently accepted Yahoo `^VIX` completed 15-minute current
observation against the retained FRED VIXCLS completed-daily distribution only
when both roles and the delayed/current timestamp are visible. This query-time
comparison does not append, resample, average, or promote the Yahoo value into
FRED history and is not a Backtest input. Missing VKOSPI may select that
independently accepted VIX context only when the owning view explicitly labels
the fallback; VIX and VKOSPI histories and values are never merged or averaged.

## Project Goal requirement map

| Project Goal requirement | Version-1 field or invariant |
| --- | --- |
| Definition and Korean explanation | `korean_display_name`, `definition_ko`, concise/detail templates. |
| Formula and provenance | Versioned `FormulaSpec`; unknown provider methodology stays unresolved. |
| Unit, scale, direction | `UnitSpec`, rational/identity scale, descriptive direction. |
| Forward/trailing/current horizon | Closed `HorizonSpec`; non-substitution invariant. |
| Aggregation, universe, weighting | `AggregationSpec` with universe/version/as-of and unresolved state. |
| Source, licence, basis, source-publication cadence, finality, PIT | `SourceContractRef`; source cadence is distinct from runtime refresh cadence. |
| Exact as-of, market date, runtime cadence, and last update | Exclusively the exact component foreign key into B2ED `gui-refresh-status/v1`; the explanation owns no duplicate timing field and permits no mtime/now inference. |
| Historical median/percentile/range | Immutable definition-identical `ComparatorSpec` plus runtime `ComparatorObservation` with actual coverage/count. |
| Ordinary interpretation and limitations | `ordinary_interpretation_ko`, ordered `limitations_ko`; no signal language. |
| Threshold meaning and evidence | Closed `ThresholdSpec`; absent unless exact provenance exists. |
| Unsupported and temporarily unavailable | Separate support and availability states; both numeric-free when not available. |
| Short summary with natural detail | `PresentationSpec` summary and fixed detail order. |
| Safe chart/panel composition | Exact unit/scale/axis compatibility or labelled independent axis/panel/versioned normalization. |
| No forward/trailing substitution | Identity/version/horizon invariant and unsupported forward-PER example. |

## Compatibility and implementation route

An entry is backward-compatible only when every field except Korean wording and
non-semantic accessibility punctuation is unchanged. Formula, input, parameter,
unit, scale, direction, horizon, universe, weighting, source, finality, PIT,
comparator definition, threshold, or normalization changes require a new
`indicator_version`. Removing an accepted entry requires a catalog-version
change and an explicit migration; silent remapping is forbidden.

A runtime implementation must define an allowlisted registry, strict
serializer/parser, definition digests, observation bindings, and Korean text
length/escaping rules. Tests must cover every enum, unknown fields, unit and
axis mismatches, forward/trailing separation, unsupported/unavailable numeric
suppression, comparator definition drift, unevidenced thresholds, refresh
foreign-key absence/ambiguity/source-identity mismatch, rejection of duplicate
runtime timing fields, rejection of runtime coverage/count in immutable entries,
and independent partial availability. Agents may implement and validate that
task now under standing GUI authority without separate user/Lead acceptance.
Until an implementation actually passes those gates, this document alone
changes no current GUI behavior or source eligibility.
