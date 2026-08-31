# Portfolio Risk Validation Evidence Contract

Status: `DOCUMENTATION_ONLY / READ_ONLY / NO_NUMERIC_RISK_CLAIM`

Contract version: `portfolio-risk-validation-evidence/v1`

## Purpose and boundary

This Backtest-owned contract defines a typed, read-only evidence envelope for displaying concentration descriptors, per-currency visibility, and the limits of a separately validated Backtest bundle. It is not a portfolio-risk model, account valuation, recommendation, suitability assessment, or executable backtest.

It authorizes no provider, broker, account, scheduler, or production-data call; no change to an accepted bundle; no holdout access; and no risk budget, target sizing, VaR, expected shortfall, loss forecast, FX conversion, or FX aggregation.

## Supported input and identity

The sole input is one immutable, locally available, independently validated bundle. Its envelope has exactly these fields:

| Field | Type | Requirement |
| --- | --- | --- |
| `contract_version` | string | Exactly `portfolio-risk-validation-evidence/v1`. |
| `bundle_identity` | `BundleIdentity` | Required, unique, and content-bound. |
| `bundle_validation` | `BundleValidation` | Required validated-bundle boundary. |
| `currency_sections` | ordered `CurrencySection[]` | Zero or more separately scoped currencies. |
| `overall_state` | enum | `AVAILABLE`, `PARTIALLY_AVAILABLE`, `UNAVAILABLE`, `BLOCKED`, or `INVALID`. |
| `unavailable_reasons` | ordered enum array | Required when state is not `AVAILABLE`. |

`BundleIdentity` contains stable `bundle_id`, `bundle_contract_version`, `content_sha256`, `validated_at`, `decision_time`, `usable_information_cutoff`, and `source_provenance[]`. Every provenance entry contains an opaque local evidence ID, input contract version, input content digest, observation/as-of time, available time, usable-from time, finality, revision/vintage state, and PIT status. It contains no provider response, credential, account identifier, absolute path, or holdout value.

`BundleValidation` contains `validation_contract_version`, frozen input and code/config digests, declared split/purge/embargo identity, and the Boolean `holdout_results_reviewed=false`. It must also declare whether the bundle is `DEVELOPMENT_ONLY`; this contract accepts only `true`. An absent, malformed, nonmatching, or nonvalidated binding is `INVALID`, not a usable proxy.

## Currency and concentration projection

Each `CurrencySection` has a single ISO-like `currency_code`, an opaque local scope identity, `coverage_state`, `concentration_descriptors[]`, and `unavailable_reasons[]`. All descriptors in a section use only that currency. Sections are never summed, ranked against one another, converted, netted, or given a cross-currency portfolio total. Missing FX evidence does not permit a fallback conversion or aggregation.

A `ConcentrationDescriptor` is descriptive only and contains:

- `descriptor_kind`, `descriptor_version`, and exact declared population/scope;
- the unit and weight basis, denominator coverage, observation/as-of and usable-from times, PIT/finality state, and evidence IDs;
- a typed value only when finite and fully bound; otherwise `null`; and
- `descriptor_state` of `AVAILABLE`, `UNAVAILABLE`, `BLOCKED`, or `INVALID`.

Allowed `descriptor_kind` values are `POSITION_WEIGHT_SHARE`, `TOP_N_WEIGHT_SHARE`, `ISSUER_COUNT`, `SECTOR_WEIGHT_SHARE`, and `INDUSTRY_WEIGHT_SHARE`. They describe supplied evidence only. They do not state diversification adequacy, risk limit compliance, expected loss, correlation, liquidity, leverage, suitability, or an action to take.

## States and fail-closed behavior

`UNAVAILABLE` means required evidence is absent. `BLOCKED` means present evidence cannot support the requested claim because its identity, timing, PIT, finality, currency scope, or validation boundary is unsafe. `INVALID` means schema, digest, ordering, duplicate identity, nonfinite value, or contract version validation failed. `PARTIALLY_AVAILABLE` exposes only independently valid currency sections and keeps every unavailable or blocked descriptor visible as typed state; it never fills, estimates, or treats missing evidence as zero or neutral.

The closed `UnavailableReason` vocabulary is: `BUNDLE_NOT_AVAILABLE`, `BUNDLE_VALIDATION_MISSING`, `BUNDLE_IDENTITY_MISMATCH`, `DEVELOPMENT_ONLY_LIMIT`, `HOLDOUT_BOUNDARY`, `CURRENCY_SCOPE_MISSING`, `CROSS_CURRENCY_AGGREGATION_PROHIBITED`, `CONCENTRATION_INPUT_MISSING`, `PIT_OR_FINALITY_UNSAFE`, `TIMING_OR_PROVENANCE_MISSING`, `UNSUPPORTED_DESCRIPTOR`, and `SCHEMA_OR_FINITE_VALUE_INVALID`.

Unknown values or free-form exceptions fail validation. No state may be promoted from a successful transport/readback alone.

## Validated-bundle claim limits

An accepted projection may say only that its displayed descriptors were read from the identified validated, development-only bundle under this contract. It must state that validation is limited to the bundle's declared frozen inputs, code/config identity, split, clocks, and metrics. It must not imply live performance, future performance, executable fills, capacity, taxes, financing, cost completeness, account reconciliation, or suitability.

The sealed final holdout remains uninspected: no holdout observations, labels, predictions, metrics, rankings, or outcomes are accepted or displayed here. This document cannot modify any previously accepted bundle or its claims.

## Consumer rules

Consumers may render only this typed envelope. They must preserve identity, provenance, state, reason, and currency separation; may not calculate, aggregate, infer, normalize, rank, or remediate its fields; and must show an unavailable/blocked state instead of a numeric substitute. GUI-specific presentation obligations are defined in `../gui/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md`.
