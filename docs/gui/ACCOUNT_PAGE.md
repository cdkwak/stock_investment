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

## Manual input identity and save diagnostics

- 수동 증권 계좌 and 매매일지 use the same provider-free local symbol resolver.
  The index includes Korean equities, the latest validated
  `kr_etf_universe_daily` rows (including its non-Hive `partitioning=None`
  storage contract), falls back to `kr_etf_master` when that snapshot is not
  valid, and includes the contract-registry plus accepted GUI U.S. ETF catalog.
- `GET /api/stocks/resolve?code=...` performs an exact, case-insensitive ticker
  lookup and is readable through relayed clients. Its successful response keeps
  market, canonical symbol/name, currency, security type, and local source;
  unknown input returns the typed `미등록 코드` result without a provider call.
- A selected name fills canonical code, display name, and KRW/USD. When a POST
  has a blank code, one exact or unique local name match is accepted. Ambiguous
  input fails with at most three `code name` candidates; no match fails closed.
- Leaving or changing a code field, or pressing Enter in it, resolves after a
  300 ms debounce in both forms. A match fills currency and fills the name only
  when it is blank or was previously auto-filled; a user-entered different name
  is preserved. The inline text cue identifies success or gives the name-search
  fallback, so state is not communicated by green/amber colour alone.
- Code-only POSTs use the same resolver and persist the canonical name and
  currency for both manual holdings and manual journal entries.
- Every account-related local write attempt appends one JSON line to
  `artifacts/local_user/web_write_audit.jsonl`. Its complete schema is `ts`,
  `path`, `client_kind`, `status`, `error_code`, and integer-only `row_counts`.
  It never stores request/response payloads, amounts, quantities, holding names,
  free-form labels, memos, or account numbers.
- The page shows the five most recent validated receipts under `최근 저장 시도`.
  Save results also appear in the form status and in the same fixed toast for
  eight seconds; `403` identifies phone/relayed writes and `400` identifies
  field validation failures.

## Page order and input density

The display order is investment/net-worth headline, true investment return,
account sources, net-worth timeline and composition, then one collapsed input
panel. The four input subsections are individually collapsed. Form controls use
labels above inputs in a six-column desktop grid and a two-column narrow grid.
At 1600px and wider, the overview and expanded input subsections retain the
two-column desktop layout. The journal initially renders ten recent rows and
reveals further rows in batches through `더 보기`.
