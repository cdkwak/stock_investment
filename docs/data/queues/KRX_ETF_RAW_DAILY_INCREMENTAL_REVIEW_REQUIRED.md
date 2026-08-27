# KRX ETF Raw Daily Incremental Candidate

Status: `REVIEW_REQUIRED / NOT_EXECUTABLE`

Scope: `kr_etf_universe_daily` and `kr_etf_ohlcv_daily` Raw logical views only

Candidate date: `2026-08-19`

Provider operation: KRX `MDCSTAT04301` via pinned pykrx

Call budget: one full-market business call, retry zero

This document is a review candidate, not an active operation. It does not
authorize credentials, provider calls, collection, promotion, state mutation,
or scheduler changes. The current 4,590-date baseline must not be reacquired.

## Verified source boundary

- KRX Data Marketplace lists **ETF > 전종목 시세** as the full-market ETF
  price screen. The pinned pykrx source binds its `전종목시세_ETF` adapter to
  `dbms/MDC/STAT/standard/MDCSTAT04301`, accepts one `trdDd`, and exposes the
  provider rows containing ETF identity, OHLC, NAV, volume, trading value,
  market capitalization, total net assets, and listed securities.
- The two logical datasets are projections of the same response. The provider
  must be called once, the bytes retained once, and both references must carry
  exactly the same Landing path and SHA-256.
- KRX states that the ETF regular session is 09:00-15:30. KRX also states that
  the administration company calculates NAV after the market closes, discloses
  it through KRX/KOSCOM, and that this NAV becomes the next day's real-time
  reference. This establishes `OFFICIAL_AFTER_CLOSE`, but not an exact endpoint
  publication time or correction freeze.
- Official KRX delisting rules confirm that ETFs can leave the listed universe.
  They do not define how or when a delisted issue disappears from historical
  `MDCSTAT04301` responses. Each response is therefore valid membership evidence
  only for its requested date; interval inference and current-list
  backprojection are forbidden.

Primary evidence:

- KRX Data Marketplace ETF menu:
  <https://data.krx.co.kr/contents/MDC/MAIN/main.jspx>
- KRX ETF trading hours and one-security trading unit:
  <https://global.krx.co.kr/contents/GLB/06/0605/0605010101/GLB0605010101T1.jsp>
- KRX administration-company NAV timing:
  <https://global.krx.co.kr/contents/GLB/02/0201/0201030203/GLB0201030203T5.jsp>
- KRX NAV definition and after-close calculation:
  <https://open.krx.co.kr/contents/OPN/01/01030203/OPN01030203T1.jsp>
- KRX ETF delisting requirements:
  <https://global.krx.co.kr/contents/GLB/03/0303/0303090305/GLB0303090305.jsp>
- Pinned pykrx ETF wrapper field projection:
  <https://github.com/sharebook-kr/pykrx/blob/master/pykrx/website/krx/etx/wrap.py>

## Verified field semantics

The contract-only schemas are
`src/stock_data/contracts/kr_etf_raw.py`. Raw numeric text remains lossless;
parsing does not grant Normalized authority.

| Provider fields | Accepted meaning / unit | Limit |
|---|---|---|
| `TDD_OPNPRC`, `TDD_HGPRC`, `TDD_LWPRC`, `TDD_CLSPRC` | Unadjusted market price, KRW per ETF security | No corporate-action adjustment claim |
| `NAV` | NAV per ETF security, KRW | KRX defines per-security NAV; not intraday iNAV |
| `ACC_TRDVOL` | Accumulated ETF securities traded | Zero is valid |
| `ACC_TRDVAL` | Accumulated trading value, KRW | Provider-native total |
| `MKTCAP` | Market capitalization, KRW | Retained exact rows reconcile as close multiplied by listed securities |
| `INVSTASST_NETASST_TOTAMT` | Total net assets, KRW | Distinct from per-security NAV |
| `LIST_SHRS` | Listed ETF securities | Date-specific only |

## Retained completeness evidence

Only checkpoint metadata was inspected; the 1,700,421 Raw rows were not
enumerated. The closed baseline remains 4,590 dates through 2026-08-12. For the
last 20 retained sessions (`2026-07-15..2026-08-12`), row counts were between
1,146 and 1,163, the latest count was 1,163, and observed adjacent-session
changes were between -1 and +5. The observed distinct counts were
`{1146, 1147, 1150, 1155, 1160, 1163}`.

For the one candidate date only, `(1146, 1163)` is an empirical anomaly bound,
not proof that the response is complete. A result outside it must stop for
review; it must not be truncated, padded, retried, or replaced. A result inside
it still requires all schema, uniqueness, OHLC, nonnegative-range, exact-date,
and shared-byte checks. This bound must not be reused automatically for later
dates.

## Candidate transaction

1. Confirm the requested date is the explicitly selected completed XKRX session
   and is no earlier than the next XKRX day after its 15:30 close.
2. Before credentials or transport, verify that the incremental checkpoint does
   not already contain the date. A verified hit returns
   `NOOP_ALREADY_SUCCEEDED` with provider calls 0.
3. Make exactly one `MDCSTAT04301` full-market request for `trdDd=20260819`,
   retry zero. No second call is allowed for the OHLCV logical view.
4. Retain the response immutably before validation. A provider error, malformed
   response, or valid empty result cannot replace prior valid state.
5. Validate exact request date, ETF-only identity, date-symbol uniqueness,
   required fields, OHLC relationships, nonnegative ranges, the one-date
   empirical row bound, and the identical logical-reference hashes.
6. Atomically replace only the incremental checkpoint. A crash in either the
   staged or promoted journal window restores the exact prior checkpoint while
   preserving the immutable received response as evidence.
7. Verify read-back, then replay the same date before any provider setup and
   require business calls 0.

The offline transaction implementation and recovery coverage are in
`stock_data.orchestration.kr_etf_raw_daily_incremental`.

## Gates that remain open

- `MDCSTAT04301` exact publication time after market close.
- Official correction/revision window and a revision-freeze rule.
- Historical row-presence semantics on listing, liquidation, and delisting
  effective dates.
- Independent full-market completeness proof. The recent row-count interval is
  anomaly detection only.
- Explicit Data Status selection of this exact operation and conversion of this
  file to an active runbook.

Until all affected gates are reviewed, `publication_finality_reviewed`,
`revision_policy_reviewed`, and `delisting_policy_reviewed` must remain false,
the candidate cannot run, and no scheduler is eligible.

