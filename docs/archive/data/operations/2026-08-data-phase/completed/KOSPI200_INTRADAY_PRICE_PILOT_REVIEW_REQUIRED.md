# KOSPI200 Constituent Intraday Price Pilot

Status: `COMPLETED_EXACT_RAW_PILOT_20260812_API0_REPLAY`

This is the active exact-date Raw pilot runbook selected by `DATA_STATUS.md`.
It authorizes only the 2026-08-12 two-symbol budget below; it does not authorize
full-universe collection, canonical promotion, automation, or scheduler change.
The user approved application-runtime loading of the existing repository `.env`
for this exact bounded pilot on 2026-08-20. Agents must not directly open,
inspect, print, summarize, copy, or modify `.env`; credentials, tokens,
authentication headers/payloads/responses must remain runtime-only and must not
be recorded. This approval does not change the exact call budget or any other
fail-closed boundary below.

## Reviewed candidate source

- Provider: LS Securities OpenAPI.
- REST operation: `POST https://openapi.ls-sec.co.kr:8080/stock/chart`.
- TR: `t8412`, officially named `주식차트(N분)`.
- Native interval request: `ncnt=15`; resampling is forbidden.
- Official rate: one request per second.
- Official request identity: six-character `shcode`. The retained KRX
  constituent identity is already a zero-padded six-digit symbol and must be
  passed unchanged.
- Official response includes `date`, `time`, OHLC, `jdiff_vol`, adjustment code
  and rate, plus response-level `s_time`, `e_time`, `dshmin`, and record count.
- Official LS access requires an LS account, xingAPI registration, OpenAPI
  registration, terms acceptance, and OAuth credentials. Existing access to a
  different LS TR does not prove the t8412 entitlement.

Primary evidence, checked 2026-08-20:

- [LS OpenAPI stock chart guide](https://openapi.ls-sec.co.kr/apiservice?group_id=73142d9f-1983-48d2-8543-89b75535d34c&api_id=12320341-ad85-429a-90bd-5b3771c5e89f)
- [LS OpenAPI usage requirements](https://openapi.ls-sec.co.kr/howto-use)
- [KRX KOSPI trading hours](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp)

## Exact bounded scope

- Membership date and price date: exactly `2026-08-12`.
- Index ticker: `1028`.
- Sample: `000660` (SK hynix) and `005930` (Samsung Electronics), both verified
  in the retained 200-row exact-date membership.
- Budget after review: one OAuth request, two t8412 data requests, zero retry,
  and no continuation because one 15-minute regular session fits below the
  documented 500-row uncompressed limit.
- Session: KRX regular session `09:00-15:30`. Each response must echo
  `s_time=090000`, `e_time=153000`, and `dshmin=10`.
- Same-date or live-forming capture is rejected. The first pilot remains a
  historical exact-date observation performed after the target session.

The 2026-08-12 constituent list must not be used for another date. No full-200
fanout is part of this pilot.

## Fail-closed semantics

The official guide does not state whether the response `time` labels a bar's
start or end, and it does not publish a historical revision-freeze policy.
The reviewed pilot therefore explicitly accepts only the typed Raw policies
`PROVIDER_TIME_LABEL_PRESERVED_START_END_UNKNOWN` and
`AS_RETRIEVED_HISTORICAL_REVISION_FREEZE_UNKNOWN`. These are preservation
policies, not claims that either semantic has been resolved. The contract keeps
`provider_time` rather than inventing `bar_start` or `bar_end`, uses
provider-native price/volume units, retains the adjustment fields, and remains
Raw/PIT-blocked. A response with an unexpected
time shape, off-grid time, wrong symbol/date/session, duplicate key, missing
intended symbol, failed status, invalid OHLC, or negative volume rejects the
entire two-symbol sample.

## Entitlement evidence policy

Successful OAuth and other LS TR operations establish only base LS OpenAPI
access; they do not prove t8412 entitlement. No extra entitlement probe is
allowed. When Lead activates this exact route, the first of the two already
budgeted t8412 calls is also the entitlement check. Only HTTP 200 plus provider
`rsp_cd=00000`, the exact `shcode`, and the reviewed response blocks establish
t8412 access. An HTTP/provider permission or registration failure stops the
attempt with retry zero, performs no second call, and advances no Raw projection
or checkpoint. It is a failed bounded attempt, not evidence that a different
provider or endpoint may be substituted.

Implementation:

- `stock_data.contracts.kospi200_intraday_pilot`
- `stock_data.providers.ls_t8412` (retained-response parser and the completed
  single-use exact-scope transport)
- `stock_data.orchestration.kospi200_intraday_pilot` (pre-network plan,
  all-intended-responses validation, and injected-capture atomic transaction)

The transaction executor accepts only an injected capture builder and is covered
with temporary-root fixtures. The selected builder is single-use, official-host
only, and fixed to this exact plan. The transaction retains each response at an
immutable per-symbol Landing path, validates both symbols before replacing the
Raw projection, then advances the checkpoint. Projection and checkpoint have
pre-commit backups; failure or restart restores both together while keeping
Landing evidence. A verified successful exact-date checkpoint returns before
invoking the builder, with OAuth/data calls zero.

## Exact review gates applied before the completed call

1. Lead selects this exact LS t8412 route in `DATA_STATUS.md`.
2. This candidate is reviewed and activated for exactly 2026-08-12, the two
   symbols above, native 15 minutes, one OAuth plus two data calls, retry zero.
3. Lead authorizes entitlement verification only through the first already
   budgeted t8412 call; no additional probe call is permitted and credentials
   remain runtime-only.
4. The typed Raw-only bar-time and revision policies above are accepted. Their
   unresolved semantics keep the output Raw and PIT-blocked.
5. Lead supplies or selects an approved transport only after gates 1-4. The
   injected result must reconcile exactly to one OAuth call, two data calls,
   zero retries, and the two reviewed symbols; partial or over-budget results
   remain Landing-only and fail closed.

The offline transaction and API-zero replay controls were implemented and tested
before the route was activated. The completed call reconciled to these gates.

## Satisfied Lead-owned activation handoff

Lead made the route executable before the call using the following exact facts,
without changing Project phase or registering automation:

`KOSPI200 constituent intraday pilot | ACTIVE_EXACT_RAW_PILOT_20260812 | LS
t8412, native 15m, membership/index 1028 dated 2026-08-12, symbols 000660 and
005930, maximum 1 OAuth + 2 serial data calls, retry/continuation 0; first data
call is the only entitlement check; provider_time remains uninterpreted,
historical revision freeze unknown, Raw/PIT-blocked; both responses must pass
before atomic projection/checkpoint; immediate retained replay API 0; no
scheduler/full-200 expansion.`

The required Data Status selection, runbook activation, and later user approval
for runtime-only configuration use were all present before the transport ran.

## Completed exact pilot result

- Executed on 2026-08-20 for only market/membership date `2026-08-12`,
  symbols `000660` and `005930`, and native `ncnt=15`.
- Consumed exactly one OAuth call and two serial t8412 calls with retry and
  continuation zero. Both immutable Landing responses passed joint validation.
- Atomically committed 52 Raw rows, 26 per symbol, with zero duplicate primary
  keys and an exact-date checkpoint. Production readback passed.
- The immediate checkpoint replay returned `NOOP_ALREADY_SUCCEEDED` with OAuth,
  data calls, and retries all zero.
- Secret-free evidence is retained at
  `artifacts/agent_runs/ur014_kospi200_intraday_live_result_20260820.json`.

This closes only the reviewed two-symbol exact-date Raw pilot. Provider-time
start/end meaning and historical revision freeze remain unresolved, so the
result remains Raw/PIT-blocked and unregistered. Do not repeat the provider
calls or replay. Full coverage, canonical/Normalized/Published promotion,
backfill, automation, predictive use, and scheduler installation remain unsafe.
