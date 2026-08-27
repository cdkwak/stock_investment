# GUI Refresh Status and Cadence Projection Contract

Status: `ACTIVE / PROVIDER_FREE_RUNTIME_IMPLEMENTED`

Contract ID: `gui-refresh-status/v1`

## Purpose and boundary

This contract defines one strict, read-only vocabulary for describing how each
GUI surface was refreshed. It is a status projection, not a market-data or
account-data payload. It owns no collector, provider call, scheduler, retry
implementation, file watcher, application worker, layout, or persistent path.
Those capabilities require a versioned owner, allowlist, and tests; agents may
establish or extend those owners under standing Project/Data/GUI authority
without a separate phase or permission decision.

The runtime projector may read only already accepted local metadata. It must not
read credentials, start acquisition, call a provider, mutate Data or account
state, or infer a schedule from GUI timers. The projection contains no market
value, holding, account identifier, order, raw response, command, URL, local
path, exception text, or traceback.

## Closed schema

The top-level object has exactly these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer, exactly `1` | Schema discriminator. |
| `contract_id` | string, exactly `gui-refresh-status/v1` | Semantic contract identity. |
| `generated_at_utc` | timezone-aware ISO-8601 string | Time this local status projection was composed; never a data as-of time. |
| `overall_state` | `OverallState` | Derived application summary; never supplied independently. |
| `surfaces` | ordered array of `SurfaceStatus` | Independently evaluated GUI surfaces. |

Unknown top-level fields fail validation. `surfaces` may be empty only when the
allowlisted surface registry itself is unavailable; that result is
`overall_state=UNKNOWN` and numeric-free.

Each `SurfaceStatus` has exactly these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `surface_id` | allowlisted stable string | Non-secret GUI surface identity. Free-form identities are rejected. |
| `cadence_kind` | `CadenceKind` | How refresh eligibility is established. |
| `cadence_seconds` | positive integer or null | Present only for `FIXED_INTERVAL`; `1800` means a 30-minute cadence. |
| `observation_semantics` | `ObservationSemantics` | What the displayed observation actually represents. |
| `operation_state` | `OperationState` | Derived summary of component attempts. |
| `freshness_state` | `FreshnessState` | Derived freshness of the usable observation. |
| `source_as_of` | timezone-aware ISO-8601 string or null | Provider/market observation time under the declared basis. |
| `source_time_basis` | `SourceTimeBasis` | Provenance of `source_as_of`. |
| `market_date` | `YYYY-MM-DD` or null | Exchange/session date when that is the contracted identity. |
| `last_success_at` | timezone-aware ISO-8601 string or null | Completion time of the last accepted outcome-complete operation. |
| `last_success_receipt_id` | sanitized stable string or null | Optional accepted receipt/checkpoint reference; never a path or secret. |
| `next_eligible_at` | timezone-aware ISO-8601 string or null | Next proven eligibility instant, not a promised completion time. |
| `next_eligible_basis` | `NextEligibleBasis` | Evidence from which `next_eligible_at` was obtained. |
| `retained_value_state` | `RetainedValueState` | Whether a previously verified value may remain visible. |
| `retry_capability` | `RetryCapability` | Exact user-facing action class allowed by an independent implementation owner. |
| `retry_action_id` | allowlisted opaque string or null | UI action identity only; never executable text or provider arguments. |
| `reason_codes` | ordered array of `ReasonCode` | Stable, sanitized explanations. Empty when no qualification is needed. |
| `component_results` | non-empty ordered array of `ComponentStatus` | Independent component outcomes; one failure cannot erase unrelated results. |

Each `ComponentStatus` has exactly these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `component_id` | allowlisted stable string | Component identity within the surface. |
| `operation_state` | `OperationState` | This component's attempt state. |
| `freshness_state` | `FreshnessState` | This component's freshness state. |
| `source_as_of` | timezone-aware ISO-8601 string or null | This component's independently proven observation time. |
| `source_time_basis` | `SourceTimeBasis` | Provenance of the component observation time. |
| `market_date` | `YYYY-MM-DD` or null | Component session date when applicable. |
| `last_success_at` | timezone-aware ISO-8601 string or null | Last accepted completed outcome for this component. |
| `retained_value_state` | `RetainedValueState` | Component-specific retained-display decision. |
| `reason_codes` | ordered array of `ReasonCode` | Stable codes only; no free-form provider or exception text. |

## Vocabulary

`CadenceKind` describes scheduling mechanics and is deliberately separate from
observation semantics:

- `CONTINUOUS_STREAM`: a separately accepted persistent stream owns updates.
- `FIXED_INTERVAL`: eligibility repeats after `cadence_seconds`.
- `SCHEDULED_LOCAL`: an installed local schedule owns eligibility.
- `PROVIDER_PUBLICATION`: eligibility follows a provider publication event.
- `SESSION_EVENT`: eligibility follows an exchange/session boundary.
- `MANUAL`: only an explicit user action makes the operation eligible.
- `LOCAL_EVENT`: an accepted local artifact event makes reread eligible.
- `STATIC_NO_REFRESH`: the content is intentionally static.
- `UNSUPPORTED`: no supported refresh route exists.
- `UNKNOWN`: cadence evidence is absent or invalid.

`ObservationSemantics` is one of:

- `REALTIME_STREAM`
- `DELAYED_QUOTE`
- `PERIODIC_CURRENT`
- `DAILY_FINAL`
- `LAST_COMPLETED_SESSION`
- `RETAINED_ONLY`
- `STATIC`
- `UNSUPPORTED`
- `UNKNOWN`

A 30-minute or other periodic observation is `PERIODIC_CURRENT`, never
`REALTIME_STREAM`. A delayed provider quote remains `DELAYED_QUOTE` even if it
is reread frequently. Daily final and last-completed-session observations are
not labelled current intraday values.

`SourceTimeBasis` is one of:

- `PROVIDER_TIMESTAMP`
- `RETRIEVAL_TIMESTAMP`, only when the accepted source contract explicitly
  defines retrieval time as the observation boundary
- `MARKET_DATE`
- `RECEIPT_COMPLETED_AT`, for operation status only, never a substitute for a
  missing market observation timestamp
- `NONE`

`OperationState` is one of `IDLE`, `IN_PROGRESS`, `SUCCEEDED`,
`PARTIAL_FAILURE`, `FAILED`, `BLOCKED`, `UNSUPPORTED`, or `UNKNOWN`.

`OverallState` uses the same values except `IDLE`, `BLOCKED`, and
`UNSUPPORTED`; it is one of `IN_PROGRESS`, `SUCCEEDED`, `PARTIAL_FAILURE`,
`FAILED`, or `UNKNOWN`.

`FreshnessState` is one of `CURRENT`, `EXPECTED_LAG`, `STALE`, `UNKNOWN`, or
`NOT_APPLICABLE`.

`RetainedValueState` is one of `DISPLAYABLE`, `DISPLAYABLE_WITH_WARNING`,
`SUPPRESSED`, or `NOT_APPLICABLE`.

`NextEligibleBasis` is one of `INSTALLED_SCHEDULE`, `CONTRACT_POLICY`,
`PROVIDER_PUBLICATION`, `MANUAL_ONLY`, `WAIT_FOR_DEPENDENCY`, `UNSUPPORTED`, or
`UNKNOWN`.

`RetryCapability` is one of:

- `LOCAL_REREAD`: reread already accepted local state only.
- `READONLY_REFRESH_REQUEST`: request an independently accepted, bounded,
  read-only application-service operation.
- `WAIT_ONLY`: no user action; wait for the declared owner.
- `NONE`: no retry is applicable.
- `UNKNOWN`: capability evidence is missing.

The version-1 `ReasonCode` allowlist is exactly
`SURFACE_REGISTRY_UNAVAILABLE`, `SOURCE_METADATA_MISSING`,
`SOURCE_TIMESTAMP_INVALID`, `MARKET_DATE_INVALID`, `VALUE_INVALID`,
`IDENTITY_MISMATCH`, `OUTCOME_INCOMPLETE`, `COMPONENT_FAILED`,
`PARTIAL_COMPONENTS`, `EXPECTED_LAG`, `RETAINED_VALUE_STALE`,
`STALE_BY_POLICY`, `DEPENDENCY_PENDING`, `OPERATION_BLOCKED`,
`NEXT_ELIGIBILITY_UNPROVEN`, `REFRESH_UNSUPPORTED`, `RETRY_NOT_ALLOWED`, and
`CADENCE_UNKNOWN`. Adding a code requires a schema-versioned contract change;
unknown codes and free-form text fail validation.

## Timestamp and eligibility provenance

1. All instants are timezone-aware and normalize to UTC at the projection
   boundary. Korean UI text may render KST but must preserve the underlying
   instant and label the timezone.
2. `generated_at_utc` is only projection time. File modification time, app
   startup time, GUI timer time, and watcher notification time cannot become
   `source_as_of` or `last_success_at`.
3. `last_success_at` requires an accepted outcome-complete receipt or
   checkpoint. A zero process exit, launched task, partial page, or file
   existence alone is insufficient.
4. `source_as_of` comes only from the accepted source contract and declared
   `source_time_basis`. If an exact instant is not proven, it is null. A
   daily/session result may still derive freshness from a proven `market_date`
   and the accepted expected-latest exchange calendar; with neither a proven
   instant nor a proven market date, freshness is `UNKNOWN`.
5. `next_eligible_at` is populated only from accepted installed-schedule
   readback, an exact contract policy calculation, or a provider/session event
   whose next instant is provable. It is null for `MANUAL_ONLY`, `UNSUPPORTED`,
   and `UNKNOWN`, and whenever DST, holiday, dependency, or publication
   evidence is unresolved. The GUI must not invent a next-run time.

## Fail-closed composition and retained values

The dimensions above are independent: a successful operation can still expose
a stale observation, and a failed current attempt does not delete a previously
verified value.

- A surface summary is derived from its components. Mixed usable and failed
  components produce `PARTIAL_FAILURE`; unaffected components remain visible.
- `IN_PROGRESS` preserves the prior accepted `last_success_at`, observation
  provenance, and retained value state. It does not imply success or freshness.
- A previous value may be `DISPLAYABLE_WITH_WARNING` only when its surface's
  existing accepted display contract permits retention and its exact
  provenance remains valid. The UI must show its as-of time and stale/lag
  warning together.
- Missing, malformed, nonfinite, identity-mismatched, or provenance-invalid
  metadata becomes `UNKNOWN`; no timestamp or schedule is inferred. A value is
  `SUPPRESSED` unless an independently validated retained value remains valid.
- `UNSUPPORTED` carries no `next_eligible_at`, no `retry_action_id`, and no
  numeric market/account display.
- One failed surface never blanks an unrelated surface. `overall_state` is
  `PARTIAL_FAILURE` for mixed successful/retained and failed states,
  `IN_PROGRESS` when work is active without a stronger failure qualification,
  `FAILED` only when every actionable surface failed and no valid retained
  result remains, `SUCCEEDED` only when every actionable surface succeeded,
  and otherwise `UNKNOWN`.

## Retry boundary

The contract itself owns zero acquisition actions; standing Project/Data
authority may supply a typed read-only owner outside this projector. The current
runtime implementation exposes at most one manual retry control per surface, and only when both
`retry_capability` and an independently maintained `retry_action_id` allowlist
agree.

`LOCAL_REREAD` may only enter an existing GUI local-read lane.
`READONLY_REFRESH_REQUEST` additionally requires a bounded, non-trading,
allowlisted application-service or Data-owned operation with owning tests. That
owner may be implemented under standing authority; no fresh user/Lead approval
is required. An action ID is an opaque key;
it must never contain a shell command, path, URL, provider parameters, account
identity, or credential. Unsupported, blocked, in-progress, and unknown actions
fail closed to disabled unless their independently accepted owner says
otherwise. Orders, amendments, cancellations, transfers, withdrawals, and
other trading actions are outside this contract.

## Project-goal coverage

| Project Goal refresh-status requirement | Contract field or invariant |
| --- | --- |
| Automatic startup/runtime refresh | `cadence_kind`, `operation_state`, and component results describe it; the automation remains with its existing owner. |
| One manual retry | `retry_capability` plus allowlisted `retry_action_id`; this contract grants no action. |
| Real-time, delayed, 30-minute, daily-final, and latest-close distinctions | Separate `cadence_kind`/`cadence_seconds` and `observation_semantics`; periodic data cannot claim real-time semantics. |
| Last successful update | Outcome-complete `last_success_at` and optional sanitized receipt identity. |
| Data as-of | `source_as_of`, `source_time_basis`, and optional `market_date`. |
| Next refresh | Evidence-bound `next_eligible_at` and `next_eligible_basis`; unresolved times remain null. |
| Progress, success, partial failure, and stale state | Independent `operation_state` and `freshness_state` at surface and component levels. |
| Retain the last verified value without blanking the screen | `retained_value_state`, explicit warning invariant, and component isolation. |
| GUI responsiveness | This contract is passive metadata; the accepted bounded local-read worker owner remains authoritative. |

## Non-duplication with accepted owners

- `RQ-20260824T085700-54DC` owns bounded GUI local-read execution, coalescing,
  latest-generation application, and deterministic close.
- `RQ-20260824T233837-0887` and `RQ-20260826T015615-149E` own exact local
  directory watching and debounce behavior.
- `RQ-20260825T004229-7C1A` owns saved read-only Research Workspace presets and
  local series identity boundaries.
- `RQ-20260825T233219-50EB` and `RQ-20260824T233837-CEEA` own Dashboard card
  content/layout and 1600x900 horizontal-fit behavior.
- `RQ-20260825T233359-0290` owns native release smoke quiescence validation.
- `RQ-20260825T090328-9DB8` owns the operational continuous-freshness release
  gate over installed triggers, outcome-complete receipts, Health, and cold GUI
  evidence.

This contract owns only the semantic status projection shared by those future
consumers. It neither changes their acceptance criteria nor establishes a new
runtime or persistence authority.

## Runtime implementation boundary

`src/stock_data/gui/refresh_status.py` implements the closed four-surface local
projection for Dashboard current observations, Data Health, read-only account
snapshots, and unsupported U.S. investor classification. It reads only the
already composed typed GUI views plus the two exact fixed scheduler receipt
paths for Yahoo current observations and Daily Health. Receipt content is
reduced to an aware completion instant and an allowlisted sanitized run ID; a
path, URL, command, provider argument, account identifier, credential, numeric
holding, or raw response cannot enter the model.

Yahoo last-success additionally requires the exact ordered 17-route terminal
outcome set, lane-specific accepted outcome vocabulary, and reconciled
accepted, failed, preserved, API-call, and maximum-call counts. Daily Health
last-success requires positive dataset/runtime-validation counts, zero runtime
failures, and API calls zero. A three-field `PASS` fragment, duplicate JSON
key, partial row, or malformed completion time is not a success. A current
metric is usable only with an aware valid source timestamp;
`displays_value=true` cannot turn a missing, naive, or malformed timestamp into
`CURRENT` or `DISPLAYABLE`.

The only wired action is `dashboard-local-reread`, which enters the existing
900 ms coalesced GUI local-read lane. It cannot start a provider runner or
mutate Task Scheduler or Data. Account refresh remains a separately owned
manual Account-page action in the current GUI implementation. Agents may add a
bounded Data-owned periodic/off-thread read-only refresh and expose its typed
status after owning tests pass; unsupported or unknown actions stay disabled.
Missing or malformed receipts leave last-success and next-eligibility null.
No next-run instant is inferred from the GUI timer or a prior completion.

The Data Status page renders the four independent lifecycle rows, while the
Dashboard heading renders only the compact overall state and exact 30-minute
local reread cadence. Full owning GUI validation passes 251 tests with one
intentional skip, including provider-call-zero local reread, partial/malformed
receipt handling, retained-value warning, Market Flow preservation, responsive
layout, watcher coalescing, and deterministic worker shutdown.
