# KOSPI200 Constituent Breadth Incremental

Status: `LIVE_VALIDATED_20260825 / API_ZERO_REPLAY / 20:30_AUTOMATION_ACTIVE`

This operation extends the completed retained 2026-08-12 proof only under the
current user instruction to restore KOSPI200 constituent breadth freshness. It
does not change or reinterpret the retained historical result.

## Exact target gate

- The only eligible target is `latest_accepted_date` in
  `data/state/canonical_equity_accepted_dates.json`.
- The target must already have complete retained KOSPI prices on both the target
  and the immediately preceding retained session.
- Membership is requested for that exact target from KRX `MDCSTAT00601`, ticker
  `1028`. A current list is never carried backward or forward.
- Exactly one business response is allowed, with retry count zero. Authentication
  traffic is not a dataset business response.
- The response must contain at least the nominal 200 unique, valid member symbols.
  The exact source-reported count is retained because a corporate-action transition
  can temporarily add a successor constituent before the index removes its
  predecessor. Empty, sub-200, malformed, or duplicate scope fails closed.

## Landing and promotion boundary

The response body is created immutably under
`data/landing/krx_mdc/kr_index_constituent_daily/<run-id>/response.json` before
normalization. Failed normalization retains that Landing evidence and leaves all
published datasets unchanged.

The existing transaction promotes these as one rollback unit while retaining
older exact-date history:

1. `kr_index_constituent_daily`;
2. `kr_kospi200_constituent_price_daily`;
3. `kr_kospi200_breadth_daily`;
4. `kr_kospi200_constituent_breadth.json` completion checkpoint.

Incomplete target or previous-session price coverage stops the transaction.
Same-target successful replay validates all three outputs before returning API
zero, and it does so before credentials or provider initialization.

## Automation result

The first live operation used one retry-zero KRX business response and promoted
200 exact 2026-08-25 members, 200 exact member prices, and one breadth row. The
result is 127 advancing, 66 declining, and 7 unchanged, with comparison session
2026-08-24 and no missing prices. Immediate operation replay and scheduler-lane
replay both returned API zero.

`KOSPI200_BREADTH_DAILY` now runs immediately after canonical equity in the
existing 20:30 KST bundle under lane-contract version 3. No additional Windows
task was created. Its target is always the latest canonical accepted date rather
than an independently guessed exchange date. Runtime coverage
contract-validates all three outputs, and the Dashboard selects the latest
retained exact-date row while preserving earlier exact observations.

The cross-registry scheduler test requires every automation-enabled dataset
lane to appear in a retained scheduler route. This prevents a READY lane from
remaining implemented but unreachable by natural execution.

This exact-date observation is safe for descriptive display. It does not create
historical constituent intervals and must not be used to backfill predictive
features for dates without an exact retained membership observation.
