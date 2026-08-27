# KRX Index Fundamental Daily

Status: `ACTIVE_DESCRIPTIVE_NON_PREDICTIVE / LIVE_VALIDATED_THROUGH_20260825 / API_ZERO_REPLAY`

This runbook governs only `kr_index_fundamental_daily` for official KRX
`MDCSTAT00702` index tickers `1001` (KOSPI) and `2001` (KOSDAQ). It does not
authorize KOSPI200, KOSDAQ150, KRX300, equity fundamentals, or any fallback.

## Contract boundary

- Normalized key: `(date, index_code)`; partition: `(market, year)`.
- Values are source-native close index points, weighted PER, weighted PBR, and
  dividend yield. Verified provider missing tokens remain null.
- Every row carries the SHA-256 of its exact immutable response.
- Publication/revision finality is unresolved. The dataset is descriptive and
  `NON_PREDICTIVE`; scheduler activation does not upgrade that boundary.

## First bounded advancement

The first reviewed range operation completed for `2026-08-13..2026-08-25`.
It used exactly two business calls, accepted the eight XKRX sessions
`13, 14, 18, 19, 20, 21, 24, 25`, inserted 16 rows, and advanced both markets
from 6,559 to 6,567 rows. Production readback has 13,134 unique rows, 54
Parquet files, and exact response hashes; same-target replay used API 0.

The preserved execution procedure is:

1. Confirm retained KOSPI and KOSDAQ latest dates are identical and precede the
   selected prior-completed XKRX target. For the first reviewed advancement,
   that retained date was `2026-08-12`.
2. Confirm no `data/state/kr_index_fundamental_daily.lock` owner exists.
3. Require KRX credentials before any authentication or business request.
4. Make one logical range call for ticker `1001`, then one for `2001`.
   A finite provider-aware transient retry/backoff may be implemented without
   fresh approval; do not silently change ticker identity, paginate beyond the
   verified response contract, or add unrelated logical scopes.
5. Persist each successful raw response create-only under
   `data/landing/kr_index_fundamental_daily/<run_id>/` before validation.
   Valid-empty is recorded as a distinct stop and never promoted.
6. Require exact schema, identity, requested bounds, expected sessions, hash
   binding, unique keys, finite numeric values, and positive displayed PER/PBR.
7. Merge without replacing any conflicting retained key. Promote KOSPI,
   KOSDAQ, and `data/state/kr_index_fundamental_daily.json` as one rollback
   unit; read back the contract after commit.
8. Replay the same target. It must return `NOOP_IDEMPOTENT` with provider calls
   zero before loading credentials.

Any transport, authentication, source-error, schema, date-set, identity, hash,
overlap, write, or readback failure stops the operation. Preserve successful
Landing bytes and all prior valid Normalized/state bytes.

## Recurring scheduler route

The existing `STOCK_DATA_KR_MARKET_DAILY_0910` bundle includes lane
`KR_INDEX_FUNDAMENTAL_DAILY`. The installed Task Scheduler definition is not
created or modified by this route. At 09:10 KST it targets only the prior
completed XKRX session and catches up all missing sessions in one two-ticker
range. A current accepted target is an API-zero no-op. Lane failure is contained
and reported in its own result and the bundle receipt.

## Verification

- Provider/network-free contract, provider, promotion, operation, scheduler,
  bundle, rollback, exact-session, valid-empty, and API-zero replay tests pass.
- First live evidence reports two business calls, retry zero, exact sessions,
  both markets at `2026-08-25`, production/state readback, and replay calls zero.
- Read-only inspect `STOCK_DATA_KR_MARKET_DAILY_0910`; do not alter its task
  definition.
