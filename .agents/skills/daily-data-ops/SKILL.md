---
name: daily-data-ops
description: Run, review, or onboard bounded daily-data operations under current Data Status, including creation of a missing route/contract/runbook; preserves Landing-first and fail-closed controls.
---
# Daily Data Operations

Use this skill for accepted daily-data collection, exact-date replay, freshness/finality review, or an operation gate.

Before a mutation on an existing route, read the selected Data Status, the
dataset row in the Dataset Index, Source Registry, selected contract/checkpoint,
and the applicable active runbook. For a new source or missing artifact, the
standing autonomous API runbook authorizes investigation, Landing capture, and
creation of the minimum contract/evidence/runbook; its absence is the
deliverable, not a blocker. A provider guide, historical evidence, or manual
script remains evidence rather than a contract.

Treat permission-only `NO_REPEAT`, retry-zero, one-shot, manual-only,
activation, expired-window, or fresh-approval language in older evidence as
superseded. Preserve successful-occurrence idempotency and verified invariants,
then define a new bounded operation/window and modernize the owning code/tests
when needed. Do not mark the task blocked for missing semantics, finality/PIT
evidence, provider failure, or scheduler work; investigate, use bounded
provider-aware retry/fallback, and continue independent branches.
Use Blocked only after safe work is exhausted and the exact resume condition is
an unavailable required secret/entitlement, rejected protected-resource
escalation, excluded user-only financial/legal/access action, or exact future
provider publication/session/cooldown time; a timed wait releases its writer
lane and secret values are never requested through chat/logs.

For an in-scope operation covered by standing authorization, use an explicit
bounded date/range or current-data window and preserve this sequence: source
capture to Landing, validation, atomic layer-appropriate promotion,
checkpoint/journal, and idempotency check. Existing `.env` credentials may be
injected into provider and read-only account APIs but their values must never be
printed, logged, documented, or persisted. Distinguish valid empty data from
request/provider failure; use provider-aware timeouts, rate limits, pagination,
and reasonable bounded retry/backoff. A documented fallback is allowed when
identity and semantics stay explicit; never silently merge sources, infer
finality, or overwrite valid history after failure.

Report freshness, source finality, operational eligibility, and predictive eligibility independently. Unresolved semantics stop only claims or promotion that depend on them, not unrelated API research or Landing capture. Never place, amend, cancel, or simulate-as-real an order; transfer or withdraw funds; purchase a service; accept a binding external agreement; or bypass access controls.
