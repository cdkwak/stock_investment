# Account Page Contract

`/account` is a local, read-only dashboard projection. It may read retained
account observations and local user entries, but it never calls a provider or
exposes account identifiers.

## Total-asset history

- USD values use one date-keyed FX series for both the current account value
  and the total-asset chart.
- `bok_ecos_usd_krw_daily.rate_krw_per_usd` is preferred on every date where a
  valid BOK observation exists. `fred_usd_fx_daily.dexkous` fills only dates
  that BOK does not provide; sources are never averaged.
- Forward-filled calendar points are `partial` only when an included account
  observation is absent or more than three weekday trading sessions old, or
  when the applicable FX observation is absent or more than three sessions
  old. Weekends do not consume the three-session allowance.
- The chart explains the quality marker as: `주황 점: 계좌 관측 또는 환율이
  3거래일 넘게 오래된 날`.

## Period and chart presentation

- The normal initial period is `3M`. If retained history starts after the 3M
  boundary and `ALL` is calculable, the API selects `ALL` initially.
- The complete-history tab includes its observed start, for example
  `전체 (08-26~)`. Other period tabs remain clickable and keep their typed
  unavailable reason when history is too short.
- The chart tooltip labels the primary series `총자산` and the rebased
  comparison `KOSPI (시작값 맞춤)`.

## Cash completeness and narrow layout

- Toss buying power is part of the documented Toss account-value component,
  not a verified cash balance. Its cash cell remains unavailable and exposes
  `현금 미확인` as its tooltip/accessibility text.
- A total-cash value is unavailable whenever any included account has
  unverified cash; known accounts are not silently presented as a complete
  cash total.
- On narrow screens, the account name owns the wrapped note, while the title
  remains unbroken. Reference date and inclusion state move under the account
  name so the account table does not require horizontal scrolling at 375px.
