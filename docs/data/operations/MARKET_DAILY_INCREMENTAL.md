# Exact-date market daily incrementals

Status: `LIVE_VALIDATED_WITH_PER_DATASET_LIMITS / KR_BUNDLE_V7_SCHEDULED`.

Use `scripts/manual/collect/run_market_daily_incremental.py` with an explicit
market date, explicit latest-finalized date, and bounded request budget. Any
legacy reviewed-operation confirmation is satisfied by the standing Data
authorization and may be removed or treated as a compatibility assertion. It
gates the existing collectors through
`stock_data.orchestration.market_daily_incremental`; it does not infer dates,
silently select a fallback, or itself define a scheduler. Agents may implement
provider-aware bounded retry and scheduler wiring without fresh approval while
preserving the dataset-specific finality rules below.

- Short selling: the official KRX trading screen states that, from 2025-03-04,
  same-date trading details are provided after 20:00 KST. The project contract
  deliberately retains the stricter next-XKRX-session (`T+1`) eligibility rule
  and an explicitly retained KRX
  trading date are required. Balance is retained through 2026-08-13; its
  2026-08-14 KOSPI response is an accepted retained valid-empty stop: the
  authenticated HTTP 200 body, immutable hash, scope correlation, and ledger
  agree, offline parsing returns zero rows, and the runner stopped before
  KOSDAQ or promotion. This does not prove whole-day absence; the exact retained
  occurrence stays no-repeat, while a new date/evidence run is authorized.
  Investor is retained
  through 2026-08-14. Trading completed the 2026-08-10 KOSPI/KOSDAQ scopes with
  two business calls inside the evidence-based seven-raw-call authentication
  budget. Availability is resolved. The exact-date trading route now copies the
  retained Normalized root and checkpoint into one date-scoped staging unit,
  requires both KOSPI and KOSDAQ scopes to validate there, and only then promotes
  the two-market Normalized root followed by its checkpoint. A durable journal
  and retained prior copies roll back promotion failures and recover interrupted
  transactions before retry; a completed date remains pre-network idempotent.
  Production validation then advanced the exact KOSPI/KOSDAQ transaction from
  2026-08-11 through the completed 2026-08-19 session. Every date committed both
  markets together; the immediate latest-date replay made zero business and raw
  calls. Trading remains an enabled child at 09:10, 14:10 and 20:30.

  Balance and investor are separate, independently receipted 20:30 scheduler
  phases in KR bundle contract v5. The official KRX balance boundary is T+2
  after 18:10 KST and may be corrected later; the lane therefore preserves
  `AS_RETRIEVED`, blocks predictive use, and catches up at most three consecutive
  XKRX sessions per occurrence. It advanced through 2026-08-24 in bounded live
  validation. The retained balance stop is a pre-network planning gate: an exact
  2026-08-14 request is always `RETAINED_VALID_EMPTY_STOP_NO_RETRY`. A strictly
  later date may be planned when that date is independently accepted and
  finalized. A legacy `--confirm-valid-empty-successor` flag is a safety
  assertion, not a user-approval gate; the 2026-08-14
  KOSPI object remains an unresolved observation and is neither retried nor
  promoted as whole-day empty. The scheduler records that date as an explicit
  gap and continues only with strictly later eligible dates.

  The official investor boundary is same-day after 18:10 KST. Since 2025-03-04
  the official screen includes KRX and NXT activity; the source fields remain
  as retrieved rather than being relabelled as an older KRX-only measure.
  Investor advanced through 2026-08-26 and uses exactly four single-date
  business scopes (KOSPI/KOSDAQ x volume/trading value).
  Repeated retained exact-date ledgers show five authentication requests plus
  four business requests, so its fresh-session raw budget is exactly nine,
  retry zero. The CLI rejects any different non-noop raw budget before network.
  Its scheduler occurrence also catches up at most three consecutive XKRX
  sessions and keeps the data descriptive-only.
- Stock lending: official data.go.kr metadata defines availability as D+1
  business day after 13:00 KST. Detail, market, and participant passed
  Landing-first collection and zero-call replay through 2026-08-14.
  `STOCK_DATA_LENDING_DAILY` is installed at 14:00 KST; its actual Task
  Scheduler trigger and immediate latest replay both returned API 0.
- Liquidity and credit: `TWO_PASS_CONFIRMATION_AUTHORIZED / FINALITY_GATE`.
  The official data.go.kr KOFIA product page identifies
  `getSecuritiesMarketTotalCapitalInfo` and `getGrantingOfCreditBalanceInfo`
  and labels the product update cycle `실시간`, but supplies no publication
  clock or revision-freeze rule. Do not inherit lending's D+1 13:00 policy.
  The first validation was fixed to 2026-08-06, the next missing retained date
  and more than five provider business days old at review time. Run each
  dataset independently with exactly one page/API call, retry zero, explicit
  `latest-finalized=2026-08-06`, and reviewed-operation confirmation. Immutable
  Landing is retained before validation; valid empty commits no Normalized row;
  malformed/failure preserves prior production; successful data uses the
  dataset/date journal and atomic Normalized/checkpoint promotion. Immediately
  replay the same dataset/date and require API 0. Both dataset operations passed:
  each retained one page and one exact-date row, promoted atomically, and its
  immediate replay used API 0. This old-date operation proves transport/schema/
  transaction behavior only. It does not establish a daily
  expected-latest clock, revision finality, or scheduler eligibility. Treat each
  retained 2026-08-06 Landing plus its exact promoted row as the first
  provisional observation. A later confirmation pass is authorized on
  2026-08-20 for that same source date, independently per dataset, with exactly
  one page/API call and retry zero. The confirmation must use a timestamped
  immutable Landing path and compare every contracted field. An identical
  response may mark that exact source date stable and retain the existing
  atomic Normalized/checkpoint result; a changed response becomes `REVISED`,
  resets the comparison anchor, and remains unpromoted until another identical
  later capture. Valid-empty likewise requires two matching captures. Failure
  preserves prior Normalized/checkpoint and its Landing evidence. A stable
  result must then pass a pre-network `NOOP_STABLE` replay with API 0. This
  two-pass historical result still does not define a daily publication clock.
  The user's 2026-08-23 authorization adds a bounded recent-date observation
  schedule inside `STOCK_DATA_KR_MARKET_DAILY`: one call per dataset at 20:30
  KST and a second observation of the same latest completed XKRX date at the
  following 09:10 invocation. The first capture is `PROVISIONAL`; identical
  contracted fields become `STABLE/OK`; a mismatch becomes
  `REVISED/DIFFERENT`, resets the anchor, and remains unpromoted. This is a
  finality-observation lane, not evidence that the provider has a proven daily
  publication clock or that predictive use is safe.

### 2026-09-05 liquidity/credit late-publication handling

- The lane always queried the latest completed XKRX session for both KOFIA endpoints.
- Credit returned `VALID_EMPTY` on that same-day date because publication lags the session.
- Two matching empty captures became terminal `STABLE`, so that date was never queried later.
- The next occurrence moved to a new same-day target; no lagged credit row could promote.
- Runtime coverage then used provisional/stable observation dates instead of the Normalized max.
- Credit makes one extra call only after an empty target, selecting one of the prior 1–3 sessions.
- A stable-empty date is reopened; a newly complete row is `REVISED` until an identical second pass.
- The 2026-09-05 retained/live diagnosis showed that market liquidity is different:
  Normalized stopped at 2026-08-06 while the endpoint returned one row for
  `basDt=20260820` and remained empty for `basDt=20260903`, an observed lag of
  roughly two weeks.
- Each market-liquidity occurrence now calculates every missing XKRX session in
  `(last retained date, latest completed session]`, plus any earlier retained
  provider-empty holes. It requests the oldest dates first and makes at most 20
  exact-`basDt` calls per occurrence. Two-pass comparison and atomic promotion
  remain mandatory.
- `VALID_EMPTY` is `publisher_not_yet_available` and remains eligible on later
  occurrences. Only after the source date is more than 45 calendar days old is
  its already-stable empty observation closed as `publisher_gap_confirmed`; no
  row is invented or forward-filled.
- Liquidity receipts retain the target-date fields and add
  `gap_dates_requested`, `gap_rows_promoted`, `oldest_pending_date`, and typed
  per-date `gap_results`. `NOOP_STABLE` means there was no missing liquidity
  session, not that a queried date returned empty.
- Credit health now reports the contract-valid Normalized max against a two-XKRX-business-day
  availability target. A retained T-2 row is `EXPECTED_LAG`; anything older remains
  `STALE`/`지연/경고`. Because KOFIA documents no liquidity publication SLA,
  liquidity health uses the latest contract-valid published date actually
  observed by this lane as its expected-available watermark. This avoids a
  false fixed-day delay claim while the backlog worker continues independently.

Human-run commands (the live command performs public-provider calls):

```powershell
# LIQUIDITY_CREDIT_DAILY is bundle-only (not a --lane choice); it runs inside the KR_MARKET_DAILY 09:10 and 20:30 slots.
# Manual single-lane check from the repo root (dry run, then live; subsequent natural runs continue the bounded backlog):
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -c "import sys; sys.path[:0]=['src','scripts/maintenance']; from datetime import datetime, timezone; from pathlib import Path; import json, run_provider_scheduler as r; print(json.dumps(r._run_liquidity_credit_observation(Path('.').resolve(), clock=datetime.now(timezone.utc), dry_run=True), ensure_ascii=False))"
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -c "import sys; sys.path[:0]=['src','scripts/maintenance']; from datetime import datetime, timezone; from pathlib import Path; import json, run_provider_scheduler as r; print(json.dumps(r._run_liquidity_credit_observation(Path('.').resolve(), clock=datetime.now(timezone.utc), dry_run=False), ensure_ascii=False))"
```

All provider errors, restrictions, schema changes, valid-empty observations,
checkpoint conflicts, and validation failures stop only the selected dataset
and dependent promotion. Preserve evidence, classify the error, use a finite
provider-aware retry or verified fallback when semantics permit, and continue
unrelated datasets. Never promote a failed/empty date or silently broaden the
date range.
