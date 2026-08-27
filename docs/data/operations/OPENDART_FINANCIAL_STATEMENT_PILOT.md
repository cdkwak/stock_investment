# OpenDART financial-statement bounded Raw evidence pilot

Status: `STANDING_LIVE_RESEARCH_AUTHORIZED / RAW_ONLY / PROMOTION_GATED`

UR-106 defined one historical frozen financial-statement scope. Preserve that
scope and any completed receipt idempotently, but do not treat its former
one-shot permission boundary as a ban on new bounded evidence work. Under the
standing autonomous Data runbook, an agent may validate a new exact scope, load
the existing credential through the runtime boundary, call OpenDART, and retain
immutable Landing/Raw evidence without separate approval. Financial-metric
promotion remains gated below. This route is independent of UR-110's
disclosure-list and corporate-action endpoint family.

## Frozen scope

The company identity is reused only as the already retained `corp_code` anchor,
not as a corporate-action observation or an identity bridge:

| Field | Frozen value |
|---|---|
| Issuer | ECOPRO BM |
| `corp_code` | `01160363` |
| Financial period | business year `2021` |
| Report | annual business report, `reprt_code=11011` |
| Consolidation | consolidated, `fs_div=CFS` |
| Official endpoint | `fnlttSinglAcnt.json` |
| Wrapper | isolated `opendartreader==0.3.3`; never add it to the project environment |
| Intended output | one immutable Raw/landing source observation only |

This exact issuer code is already retained in the UR-110 baseline evidence.
The historical UR-106 occurrence must not call `list.json`, `fricDecsn.json`,
`pifricDecsn.json`, any
corporate-action endpoint, or any KRX/KIND route; nor may it reuse, inspect, or
reinterpret UR-080 through UR-083 observations.

## Request budget and stop rules

For the historical frozen route, exactly one read-only GET was budgeted. A new
run may choose a provider-appropriate bounded retry policy under standing Data
authorization. Its public parameters are only `corp_code`, `bsns_year`,
`reprt_code`, and `fs_div`; the authentication key remains in memory only.
Keep timeouts, rate limits, call accounting, Landing capture, and idempotency
explicit.

Any transport/HTTP/authentication/entitlement/rate/provider-status/empty,
malformed/schema/scope, Landing write/readback, credential-scan, or accounting
failure ends or safely retries according to the declared provider-aware bounded
policy. It does not select an alternative endpoint, report, issuer, period, or
source. A new scope uses a new idempotency key and checkpoint.

## Credential, package and Landing boundary

- Do not open, test for, print, record, summarize, or modify `.env`, keys,
  headers, tokens, cookies, account values, or authentication responses.
- A bounded runner may let the isolated process use the application
  runtime credential loader only. It must never expose credential material to
  command output, URLs, manifests, logs, or error reports.
- The isolated package environment is disposable and outside the project venv;
  `pyproject.toml`, locks, production dependencies, and application code remain
  unchanged.
- The historical executable route is the credential-blind injected-transport
  runner `run_landing_first_financial_statement_pilot`. Under standing
  authorization it can count one
  zero-argument fetch, atomically create (never replace) one same-volume
  immutable Landing response, read it back, verify SHA-256, and give **only the
  verified readback bytes** to the parser. The future production Landing target
  is deterministically scoped below
  `data/landing/diagnostics/opendart_financial_statement_pilot/`; a collision or
  orphaned Landing body is fail-closed and never overwritten.
- Its completed/failure checkpoints contain exactly public scope, call count,
  response byte count, response-body SHA-256, canonical first-capture UTC ISO
  timestamp, and typed outcome. The initial timestamp must be timezone-aware and
  is normalized before any fresh transport or Landing action. URLs, request
  objects, headers, key material, authentication values/responses, and body text
  are excluded. Landing/write/readback/hash/parse/checkpoint failures are typed
  fail-closed and cannot replace a pre-existing valid output.
- A completed exact scope first verifies the immutable checkpoint schema and
  exact bytes, Landing byte count and readback hash, then reparses readback with
  only the retained first-capture timestamp. It deliberately ignores a new
  caller-supplied timestamp, so this `NOOP_API_ZERO_REPLAY` reproduces the same
  observation rows without reloading credentials or contacting the provider.

## Contract, revision, PIT and rights gate

`contracts/opendart_financial_statement_pilot.py` defines source-observation
identity as `(source_operation, landing_response_body_sha256,
source_item_ordinal)`. Amount fields are retained exactly as raw strings; no
numeric values are accepted for display or calculation. A receipt number is a
filing-version identifier only. The endpoint does not establish an explicit
revision-parent relation, provider intraday publication time, historic
availability, revision finality, redistribution terms, or a usable-from date.

Accordingly every pilot row remains
`PIT_BLOCKED_PUBLICATION_AND_REVISION_UNVERIFIED` and
`RIGHTS_AND_REDISTRIBUTION_UNVERIFIED`, with `available_at_utc` and
`usable_from` null. No Normalized/Derived/Published/canonical dataset, GUI,
automation, scheduler, adjusted-price process, Backtest input, or corporate
action inference is permitted.

## Offline checkpoint

The synthetic-only scope/parser/runner tests prove scope locking, raw-string
handling, response-schema/scope rejection, null PIT/revision/rights fields,
pre-credential call count zero, strict initial timestamp validation before
fetch, one-fetch Landing-first commit/readback parse, sanitized checkpoints,
parsed-failure immutability, typed storage failure, and completed-scope API-zero
replay with a deliberately different caller time but identical parsed rows.
Their temporary directories are not project
Landing/state roots. No package install, provider call, project Landing write,
test payload retention, or credential access has occurred.

An agent may now validate the exact issuer/period/endpoint, current provider
limits, secret injection, Landing path, and idempotency boundary and then run a
bounded Raw evidence occurrence under standing authorization. A completed exact
scope remains API-zero replay-only; a new date, period, or revised
implementation requires a new idempotency key and checkpoint. Official semantic,
revision, publication, PIT, and rights research may continue from Landing/Raw
evidence, but no higher-layer use is allowed until the gates above are proved.
