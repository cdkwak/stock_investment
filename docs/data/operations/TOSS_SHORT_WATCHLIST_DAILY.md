# Toss Short-Selling Fixed Watchlist Daily Boundary

Status: `LIVE_VALIDATED_20260819_API0_REPLAY / MANUAL_ONLY_FINALITY_UNRESOLVED`

The current user thread directly authorizes runtime-memory-only Toss credential
use for the exact 2026-08-19 `005930`/`000660` short-selling calls and validated
Toss-specific Landing/Normalized/journal/checkpoint writes. This supersedes the
earlier approval-recognition gate; it does not authorize another date, symbol,
retry, pagination, scheduler registration, official KRX dataset changes, or
Dashboard exposure. Credentials, tokens, authorization headers, and account
identifiers must not be persisted or logged.

The authorized run completed on 2026-08-20 KST with OAuth 1 and exactly two
market calls. Both fixed symbols returned the exact 2026-08-19 source date and
were promoted together after Landing, schema, membership, duplicate-key,
readback, and official-overlap validation. The immediate same-date replay was
`NOOP_ALREADY_SUCCEEDED` with OAuth 0 and market calls 0. Provider `updatedAt`
was 2026-08-19 18:13:47/18:14:07 KST; the successful next-morning observation
does not establish a recurring publication or revision-freeze rule. Scheduler
and GUI exposure therefore remain disabled.

## Identity and fixed scope

- Dataset: `toss_equity_short_watchlist_daily`, contract version 1.
- Provider operation: Toss `getStockShortSelling`.
- Watchlist version: `2026-08-20-v1`.
- Members: `005930` 삼성전자/KOSPI and `000660` SK하이닉스/KOSPI only.
- Observation scope: `KRX_ONLY_PROVIDER_EOD`, per symbol, daily.
- Values: short-selling volume in integer shares; amount in exact integer KRW;
  provider-published volume and amount rates remain ratios.
- It is never the official total-market short-selling trading, short balance, or
  short investor dataset. It cannot replace, extend, aggregate into, or act as a
  fallback for the official KRX `KRX_NXT_COMBINED` series.
- The fixed list is a provider-validation list, not a user watchlist or a
  historical security universe. Dynamic fan-out is forbidden.

## Retained evidence and unresolved finality

The complete fixed watchlist now safely retains two duplicate-free rows for
source date `2026-08-19`: Samsung Electronics and SK Hynix, both KOSPI and
`KRX_ONLY_PROVIDER_EOD`. Their provider `updatedAt` values are
`2026-08-19T18:13:47+09:00` and `2026-08-19T18:14:07+09:00`. Both were observed
in the authorized next-morning operation. This one complete two-symbol date is
valid descriptive evidence, but it does not prove a recurring publication time,
revision freeze, or unattended expected-latest policy.

## Selected call and transaction boundary

For the explicit completed XKRX target date 2026-08-19:

1. Before constructing a client, inspect the Toss-specific checkpoint and live
   artifact. A valid successful same-date checkpoint plus an exact two-member
   retained frame returns `NOOP_ALREADY_SUCCEEDED` with OAuth 0 and market calls
   0. A same-date incomplete journal returns
   `RECOVERY_REQUIRED_PRE_NETWORK`.
2. The exact maximum is one OAuth call plus two market calls: one call for each
   fixed symbol, `count=1`, `until=<target-date>`, retry zero, pagination zero.
3. Write both lossless responses to a transaction-owned Landing staging scope.
   Never write credentials or authorization headers.
4. Require HTTP success, one exact target-date row per symbol, matching symbol,
   source date, non-null `updatedAt`, non-negative integer shares/exact KRW, the
   contract schema, and no duplicate `(date, market, symbol)` key.
5. Reconcile both symbols with exact-date official KRX trading rows. Through
   `2025-03-03`, record same-scope KRX-only differences. From `2025-03-04`, label
   the comparison `NON_EQUIVALENT_KRX_ONLY_VS_KRX_NXT_COMBINED`; record but do
   not interpret or merge the numerical remainder.
6. Only after both symbols and overlap evidence pass may one transaction promote
   the Toss-specific root and atomically commit its journal/checkpoint. A fetch,
   validation, staging, promotion, or checkpoint failure rolls back the whole
   date and preserves the previous valid root.
7. Immediately replay the same date through the pre-network gate and require API
   0. Automation remains disabled until the retained result establishes a
   reviewed publication/finality rule.

## Display gate

No GUI implementation is authorized by this boundary. A future typed adapter
may show an exact-date, current Toss row only as `Toss 종목별 공매도 EOD
(KRX-only)`. Missing, stale, incomplete-transaction, finality-unresolved, or
schema-invalid rows are numeric-free. It must never be labelled official market
total, KRX+NXT combined, short balance, or a substitute for those datasets.
