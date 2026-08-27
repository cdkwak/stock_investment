# Autonomous Data API and Source Onboarding

Status: `ACTIVE_STANDING_USER_AUTHORIZATION`

This runbook records the user's 2026-08-26 standing authorization for agents to
use public APIs and existing `.env`-backed authenticated APIs, close useful data
gaps, refresh read-only state, and automate recurring work without requesting a
new permission at each normal engineering step.

## Authorized lifecycle

Provided it creates no order, transfer, purchase, or binding external agreement,
an agent may autonomously:

1. discover and compare candidate sources;
2. investigate and verify identity, units, timestamps, sessions, finality,
   revision behavior, point-in-time availability, access terms, and expected
   call volume using official documentation, provider metadata, bounded API
   comparisons, immutable Landing samples, and reproducible local analysis;
3. update the Source Registry and Dataset Index and define a source-specific
   Dataset Contract;
4. implement a provider adapter and immutable Landing capture;
5. run a proportionate live pilot or refresh, retain its source evidence, validate the
   response, and create atomic Normalized/Derived/Published output as permitted
   by the verified contract;
6. add focused offline tests, checkpointing, idempotency, failure preservation,
   freshness, and health reporting;
7. register and enable a provider-appropriate `STOCK_DATA_*` scheduled task after the
   acceptance gates below pass; and
8. update Data Status with the current route, limitations, evidence, and exact
   next safe action.

The agent does not need a separate user approval merely because the operation
uses network access, calls an API, uses an existing credential injected from
`.env`, reads a brokerage account without mutation, writes Data-owned artifacts,
promotes a contract-valid dataset, or registers its data-refresh schedule.

This authorization is also the required activation for bounded live pilots,
finality observations, semantics/PIT research, recovery attempts, and new
current windows. A lower-priority task or runbook may narrow identity, write
scope, atomicity, or acceptance criteria, but may not require another generic
Data/user/Lead approval before these operations.

## Acceptance gates

Before promotion or scheduler enablement, all of the following must be true:

- the economic identity, schema, key, units, timezone, market session, and
  missing/empty semantics are explicit rather than guessed;
- the first accepted response is preserved in immutable Landing with provenance;
- valid prior data survives transport, provider, validation, and partial-write
  failures;
- the operation uses atomic persistence, a durable checkpoint/receipt,
  provider-appropriate rate limiting, reasonable bounded retry/backoff, and an
  idempotent no-duplicate replay;
- provider terms and redistribution constraints permit the intended local use;
- the schedule has an explicit cadence/finality rule, does not overlap an
  existing owner, uses `IgnoreNew`, and is read back after registration;
- display, research, and predictive consumer eligibility are recorded
  independently with bounded reason codes; collection/automation readiness
  never grants any consumer eligibility, and predictive use remains unavailable
  unless point-in-time availability and revision behavior are positively
  established; and
- focused offline tests and the smallest relevant regression pass without live
  calls in the normal test suite.

When one gate is unresolved, fail only the affected semantic claim, promotion,
or schedule. The agent may continue API research, immutable Landing evidence,
contract work, tests, read-only inspection, or independent sources that do not
depend on that gate.

## Autonomous semantics and PIT investigation

An unresolved semantic or PIT label is a research task, not a permission stop.
Without another user approval, an agent may:

- locate and cite official field definitions, calendars, publication schedules,
  revision policies, and provider timestamps;
- make bounded, provider-appropriate comparison calls and retain secret-free
  immutable Landing evidence;
- compare retrieval time with observation/session/publication time across
  multiple dates and, where identities truly match, across independent sources;
- build API-zero replay analyses and regression fixtures from retained evidence;
- document what is proved, contradicted, or still unknown and update the owning
  Dataset Contract, Source Registry, Dataset Index, and Data Status; and
- explicitly reclassify descriptive/display/predictive eligibility when the
  evidence satisfies the owning contract and tests.

Research must not guess undocumented meaning or manufacture historical
availability from current snapshots. Until evidence is sufficient, quarantine
the affected field or dataset at Landing/Raw or descriptive-only scope and keep
its dependent canonical, Published, feature, and predictive paths fail-closed.
This narrow quarantine must not disable unrelated source research, collection,
storage, or automation.

## Existing credentials and read-only accounts

- Existing project-root `.env` values may be consumed only through process
  environment/configuration boundaries. Never print the file, echo values,
  include them in commands visible to logs, or persist tokens, authorization
  headers, account identifiers, or credential-bearing responses.
- Authenticated provider APIs may be called without a new approval. Read-only
  brokerage endpoints for holdings, balances, buying power, transactions,
  order history, market data, and account reconciliation are also allowed.
- Persist only the minimum sanitized projection needed by the contract. Raw
  personal account responses and direct identifiers are not Data artifacts.

## Prior restrictive evidence

Older rows and runbooks marked `NO_REPEAT`, `retry zero`, `one shot`, an expired
date/window, or requiring another user authorization remain factual records of
what happened. Their permission-only restrictions do not block a new attempt
under this standing runbook. An agent may select a current date/range, create a
new receipt, use reasonable retry/backoff, and re-evaluate the route. This does
not permit duplicate writes after a successful occurrence, provider abuse,
unbounded retry loops, or treating an old negative result as a success.

The same supersession applies to `MANUAL_ONLY`, `GATED_BLOCKED`,
`PROVIDER_ACTIVATION_GATED`, `explicit live approval`, `separately authorized
window`, and similar permission language when no excluded action below is
involved. Replace fixed retry-zero behavior with a provider-appropriate bounded
retry/backoff policy when that improves reliability. After a failure, inspect
retained evidence, classify the error, retry or select a verified alternative
within a finite budget, and continue every independent task branch. Do not turn
a source-specific failure into a project-wide stop.

## Blocking rule

Missing implementation, tests, semantic/PIT/finality evidence, a provider
error, a stale contract, a disabled scheduler, or the need to request ordinary
tool/network escalation is not a blocking condition. Continue with research,
Landing capture, adapters, replay fixtures, contracts, tests, alternate public
or existing-credential sources, or scheduler preparation. Mark work blocked
only when no safe in-scope work remains and completion requires an unavailable
required secret/entitlement, a rejected protected-resource escalation, an exact
future provider publication/session/cooldown time, a user-only action outside
standing authority, or one of the excluded external mutations below. A
time-gated queue item releases its writer lane and does not pause unrelated
work; secret values are never requested through chat or logs.

## Excluded authority

This standing authorization does not permit:

- exposing, printing, logging, documenting, or persisting `.env` contents,
  credentials, tokens, authorization material, or direct account identifiers;
- acquiring a new credential through an unapproved purchase, subscription,
  external contract, or acceptance of binding commercial terms;
- CAPTCHA bypass, access-control circumvention, credential sharing, or use
  outside the account/provider permissions already held by the user;
- creating, modifying, cancelling, simulating as real, or submitting orders;
- paper/live order execution, transfers, withdrawals, purchases, subscriptions,
  contract acceptance, or any other financial/legal external mutation; or
- labeling data predictive, realtime, final, official, or interchangeable
  without the evidence required by its contract.

Existing credential-backed market-data and read-only account routes may run
through their injected-secret boundaries without another approval. A new key or
entitlement may be configured only by the user; after it exists in `.env`, agents
may use it under this runbook without revealing it.

## Handoff

Record accepted sources and active automation in `DATA_STATUS.md`; keep detailed
contracts, checkpoints, and source evidence in their Data-owned locations. A
failed candidate should leave bounded evidence and a precise reason, not a
partially promoted dataset or an enabled broken schedule.
