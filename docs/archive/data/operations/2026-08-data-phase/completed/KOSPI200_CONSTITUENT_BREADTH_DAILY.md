# KOSPI200 Constituent Breadth Daily

Status: `COMPLETED_RETAINED_EXACT_20260812`

`DATA_STATUS.md` selects this procedure only for the already-retained exact
2026-08-12 scope. It authorizes no provider call: the retained membership and
local price inputs must promote atomically and replay pre-network with API 0.

## Accepted source boundary

- Membership source: KRX `MDCSTAT00601`, ticker `1028`, one explicit completed
  KRX session date.
- Retained evidence:
  `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T012324Z_5e43157087b0407e8af498f37b90d914/`.
- The retained `2026-08-12` and `2020-01-02` observations each contain 200
  unique symbols. They prove exact-date response behavior only.
- `date == observation_date` is mandatory. No current membership list may be
  carried backward or forward, and no interval/effective-change history is
  inferred from two snapshots.
- Price source: retained `kr_equity_price_daily` rows for every exact member on
  the target and immediately previous completed sessions. Missing rows remain
  missing and stop the scope; no fill or substitute source is allowed.

## Current bounded candidate

At `2026-08-20T04:02+09:00`, versioned XKRX calendar `4.13.2` identifies
`2026-08-19` as the latest completed session and `2026-08-18` as its previous
session. The only permissible membership request candidate is therefore one
`MDCSTAT00601` call with `date=20260819`, `ticker=1028`, business-call limit 1,
and retry count 0. This is a request candidate, not an assertion that KRX has
published a nonempty response; availability remains
`UNVERIFIED_UNTIL_NONEMPTY_EXACT_DATE_RESPONSE`.

The newest retained, parser-validated membership observation is `2026-08-12`.
It is also the newest known intersection with the Status-retained canonical
equity coverage through `2026-08-13`. It may support an exact 2026-08-12
offline scope after the target and previous-session price rows are readable,
but it must not be reused for any later date.

## Exact local read gate

The required KOSPI price input is
`data/normalized/kr_equity_price_daily/market=KOSPI/year=2026/data.parquet`.
The user-approved exact-file ACL change preserved owner, protected inheritance,
and every prior explicit ACE, then added only `ChangdaeNote\\k4545`
Read/Synchronize. No parent or sibling ACL changed. Evidence is
`artifacts/agent_runs/ur013_exact_file_acl_20260820T041149+0900.json`.

The actual-user Parquet probe passed. The exact 2026-08-11 and 2026-08-12 slice
contains 942 KOSPI symbols per date, 1,884 rows, and no duplicate
date/market/symbol keys. Joining only the retained 2026-08-12 membership yields
200/200 exact target prices and breadth `81 advancing / 111 declining / 8
unchanged`, with `missing_price_count=0`. No later date uses this membership.

## Transaction boundary

`kr_index_constituent_daily`, `kr_kospi200_constituent_price_daily`,
`kr_kospi200_breadth_daily`, and their completion checkpoint are one rollback
unit. The exact target-date keys are immutable. Existing history must be
preserved. Breadth is published only when both target and previous-session
prices cover the entire exact membership scope.

The offline implementation is
`stock_data.orchestration.kospi200_constituent_breadth`. It has no provider
client and reports `api_calls=0`. Same-date successful replay validates the
retained outputs before returning `NOOP_ALREADY_SUCCEEDED`.

## Selected one-time operation

For the retained 2026-08-12 production promotion, all of the following are
mandatory:

1. target exactly `2026-08-12`; no provider call and no retry;
2. the already-retained immutable Landing capture and verified response hash;
3. readable exact 2026-08-12/11 `kr_equity_price_daily` rows;
4. atomic transaction, failure rollback, and same-date API-0 replay;
5. Health/GUI registration that exposes the exact scope and hides incomplete
   or stale numbers.

As of 2026-08-20, `DATA_STATUS.md` selects only this retained exact-date scope.
The newer 2026-08-19 membership candidate remains unverified and unexecuted.
No network call, later-date promotion, scheduler change, or current-list
backprojection is authorized.

## Completed result

The selected retained operation completed on 2026-08-20. Production read-back
validated 200 exact membership rows, 200 exact member-price rows, and one
KOSPI200 breadth row for 2026-08-12. The breadth is 81 advancing, 111 declining,
and 8 unchanged, with zero missing prices and previous session 2026-08-11.
The membership, published prices, derived breadth, and completion checkpoint
committed as one transaction. Immediate same-date replay returned
`NOOP_ALREADY_SUCCEEDED` with `api_calls=0`.

This completion does not authorize another date, a provider request, membership
interval inference, scheduler installation, or reuse of the 2026-08-12 list for
later prices.
