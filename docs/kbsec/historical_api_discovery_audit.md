# KB Securities historical API discovery audit

Date: 2026-08-14 KST  
Scope: official retained KB Securities B2C Open API metadata and samples; read-only
market data only  
Network calls: **0 OAuth, 0 business**

## Decision

No KB historical pilot or backfill is authorized from the retained evidence.

The official catalog contains two operations with genuine historical-shaped request
fields, but neither currently satisfies all integration gates:

- `IVU10430` has explicit `strt_dt` and `end_dt`, but the retained success sample is
  a single equity (`005930`). The optional-symbol behavior is not demonstrated, so a
  market-level, survivorship-safe result must not be inferred.
- `IVS11560` has a historical start date/count chart query and explicitly enumerates
  KOSPI200 futures/options market classes, but `is_cd` is required. The retained
  catalog exposes no historical derivatives instrument-universe/listing operation,
  so it cannot independently enumerate expired contracts without survivorship or
  coverage risk.

The retained official files do not include provider rate limits or terms authorizing
automated historical bulk use. This independently blocks the user's source-terms and
reasonable-volume gates. No token was issued merely for discovery, and no KB lock was
acquired or contended with the scheduled `IVSA0070` post-close task.

## Evidence boundary

Authoritative retained inputs inspected:

- `docs/kbsec/official/all_kbstock_excel-20260805-172605.xlsx`, SHA-256
  `6D2D2831282BDAFA94A2D7F249454B9E65FFBABBBFCD22A7FB5CAFF4F0378C41`;
- `docs/kbsec/official/current_b2c_all_api.xlsx`, SHA-256
  `1A229821CCD6491096EA09476A779760DF1988D5F112AC30120439B926496A24`;
- the corresponding official JSON samples under `docs/kbsec/official/samples/`;
- the implemented `IVSA0070` client, contracts, retained raw response, state, and
  superseding date-semantics audit.

Both workbooks expose the same 77 sheet names. Account and order sheets were excluded.
Official samples are examples, not proof of undocumented parameter meanings, maximum
history, pagination, units, or historical point-in-time availability.

An exhaustive cell comparison found that the 2026-08-05 and current 2026-08-11
workbooks have identical market-operation schemas and descriptions; only their
per-sheet update-date cell changed. The retained sample ZIP was also enumerated in
full. It adds no unlisted historical market operation. `IVS11430` appears only as a
theme snapshot sample (input `thm_cd`, no date/range); the newer overseas ranking
operations likewise have current ranking/count inputs rather than historical market
ranges. No filename or operation-code reference establishes a mini-futures or
mini-options endpoint.

The legacy/reference project was inspected read-only. Its only implemented KB market
operations are `IVSA0070` and `IVS11560`; it contains no additional investor,
program, foreign-ownership, valuation, mini, breadth, or liquidity TR. Legacy runtime
code is evidence only and is not imported into Rev1.

## Candidate classification

| Priority slice | Candidate operation | Verified request shape | Classification | Integration decision |
|---|---|---|---|---|
| KOSPI200 futures investor flow | `IVSA0070.out5.fts_nt_b` | no input; one response observation | `DAILY_FORWARD_COLLECTION` | Snapshot only. Preserve the slice's post-close date contract; no historical backfill. |
| CALL / PUT investor flow | `IVSA0070.out5.call_opt_nt_b`, `put_opt_nt_b` | no input; one response observation | `DAILY_FORWARD_COLLECTION` | Snapshot only. Do not assign the liquidity or global-symbol source date to these rows. |
| Derivatives summary | `IVSA0070.out3` | no input; current selected instruments | `DAILY_FORWARD_COLLECTION` | Snapshot only; instrument membership may vary by capture. |
| Program trading, market level | `IVSA0070.mprft_nt_b`, `nmp_nt_b` | no input; one response observation | `DAILY_FORWARD_COLLECTION` | Snapshot only; this is the only catalogued market-level program slice. |
| Program trading, sampled per symbol | `IVU10450` | optional symbol, hourly/daily selector, row count | `BOUNDED_RECENT_HISTORY_UNVERIFIED_MARKET_MODE` | Not a true date/range endpoint. The sample proves a symbol request; optional-symbol market aggregation is undocumented and must not be inferred. Current-universe enumeration would be survivorship-unsafe. |
| Equity investor flow, sampled per symbol | `IVU10430` | optional symbol; explicit start/end dates; amount/quantity, side, accumulation selectors | `TRUE_RANGE_PER_SYMBOL_UNVERIFIED_MARKET_MODE` | Provisional only. The sample proves `005930`, not omitted-symbol market semantics. It does not cover derivatives. |
| Futures/options OHLCV/OI | `IVS11560` | required instrument code; start date or row count; daily/weekly/monthly/yearly/minute/tick | `TRUE_HISTORY_PER_INSTRUMENT` | Provisional only. No historical contract universe is available from the catalog; no KB-only survivorship-safe backfill. Existing KRX/FSC artifacts are superior for their covered interval. |
| Domestic indices | `IVS11560` | required code; market classes include KOSPI/KOSDAQ/KOSPI200 industries | `TRUE_HISTORY_PER_INDEX_CODE` | Potential bounded supplement only after code identity, source-date behavior, limits, and terms are verified; not a current high-value gap replacement. |
| Breadth | `IVSA0070` | no input; KOSPI/KOSDAQ counts | `DAILY_FORWARD_COLLECTION` | Snapshot only. Existing derived breadth history is superior and PIT-safe. |
| Liquidity / surrounding funds | `IVA10370`; `IVSA0070` liquidity fields | no input; response carries separate `dt`/`dt2` source dates | `DAILY_FORWARD_COLLECTION_LAGGED_SOURCE_DATE` | Snapshot only. Existing FSC liquidity/credit history is superior. Never substitute capture date for `dt`/`dt2`. |
| Mini futures/options | none verified | no retained field or operation | `UNSUPPORTED` | Do not infer from STAR futures, stock futures, or generic chart classes. |
| Foreign ownership | `IVU10140`, `IVU10020` | current per-symbol fields or rolling top lists | `SNAPSHOT_OR_CURRENT_RANKING` | No historical market-level series; unsuitable for backfill and top-list history is selection-biased. |
| Valuation/fundamentals | `IVU10140`, `IVM10050` | current per-symbol values | `SNAPSHOT_PER_SYMBOL` | No historical PIT vintages; unsuitable for historical valuation/fundamental backfill. |

## Exhaustive catalog exclusions

Every retained non-account/order market sheet and sample was checked for explicit
date/range semantics. The remaining operations do not close a target gap:

- `IVU10070`, `IVU10080`, `IVU10140`, and `IVU10420` are current per-symbol
  quote/trade/broker views. A record count in trades is not a historical date range.
- `IVU10210`, `IVU10240`, `IVU10270`, `IVU10280`, `IVU10550`, `IVS10910`,
  `IVS10920`, and `IVS11190` are current or prior-day rankings/screens. Fields ending
  in `_inpt_strt`/`_inpt_end` are numeric filter bounds (price, volume, capital, or
  market cap), not dates. Rolling windows such as 5/20/250 days do not return a daily
  historical panel.
- `IVU10020` is a current ranked cross-section over fixed lookback choices (previous
  day through year-to-date). It includes current holding ratio and period change, but
  not dated historical ownership rows; storing repeated rankings would be a selected
  top-list observation stream, not a survivorship-safe ownership history.
- `IVM10050` and `IVU10140` expose current per-symbol valuation/fundamental values.
  Dates for 52/250-day extrema are attributes of the current snapshot, not historical
  valuation dates or filing vintages.
- `IVM30010`, `IVS11430`, `IVA60140`, `IVA60190`, `SZQM0771`, and `IVSA0070` are
  current market/industry/theme/world-index/FX/session snapshots. `IVA60140.prd_clsf`
  is not documented as a start/end date range.
- `SIQM4900` is a current instrument master record. It does not establish a
  source-date-valid historical derivatives universe. `SIAM4983` is overseas master
  material and does not fill the Korean target gaps.
- `GSC10060` explicitly supports per-symbol overseas chart history, but it is outside
  the Korean KB gap scope and cannot provide Korean derivatives/investor/program data.
  The other `GS*` operations are current overseas quotes/trades/rankings.

Thus the only documented Korean history-shaped operations remain `IVU10430` and
`IVS11560`. `IVU10450` is count-based recent history. Everything else relevant is a
snapshot, ranking, or current master unless future official documentation says
otherwise.

## Legacy `IVS11560` observation

The legacy project retained one `A0169000` (`F 202609`) CSV created from one
`IVS11560` count-mode request (`inq_clsf=2`, `inq_cnt=30`). It contains 30 dated bars,
2026-06-25 through 2026-08-06, and its capture timestamp is
2026-08-05T20:32:32+09:00. The August 6 bar during the August 5 evening capture is
direct evidence that the derivative chart can expose a next-calendar-day/night-session
source date. It must be classified from response date/session fields, never capture
date.

This legacy observation does **not** verify date-mode boundary behavior, pagination,
maximum history, expired-contract lookup, or all-contract coverage. The contract was
selected from the latest `IVSA0070` snapshot and only one current contract was ever
recorded, which is inherently insufficient for a survivorship-safe backfill. The
legacy CSV is normalized output rather than retained byte-exact response Landing, so
it cannot satisfy Rev1 Landing-to-Normalized reconciliation or provenance gates.

## Provisional dataset contracts

These contracts are discovery artifacts only. They must not be registered or
normalized until the stated gates pass.

### KB per-symbol equity investor history (`IVU10430`)

- Grain / provisional key: `(source_date, exchange_scope, instrument_code,
  amount_quantity, trade_side, accumulation_mode, investor_category)`.
- Source date: exact response `out[].dt`; request dates are scope metadata only.
- Values: preserve exact raw strings first; parse signed numeric values without
  converting missing values to zero. Preserve `mtrl_clsf` as confirmed/estimated.
- Required validation: requested interval containment, unique dates, category
  reconciliation where identities permit it, no NaN/infinity, byte-exact Landing,
  ledger/call reconciliation, and explicit empty-versus-failure handling.
- PIT restriction: historical publication/revision availability is unknown. Research
  use remains blocked until source availability semantics are evidenced.
- Blocking pilot question: whether omitted `is_cd` is a documented market aggregate
  or merely a default/invalid request. A one-symbol success cannot answer this.

### KB instrument chart history (`IVS11560`)

- Grain / provisional key: `(source_date, session_or_time, market_class,
  instrument_code, chart_interval, source_price_mode)`.
- Source date/time: exact `out2[].dt` and `out2[].tm`; preserve returned night/day
  session boundary fields. Never impose `snapshot_date` as the observation date.
- Values: raw OHLC, volume, trading value, open interest, prior close, adjustment
  classification/ratio, and provider market/session metadata.
- Required validation: instrument identity, monotonic/unique source timestamps,
  requested-boundary behavior, maximum rows, pagination or truncation, expiry
  coverage, day/night semantics, price scale, Landing reconciliation, and
  deterministic resume.
- Survivorship gate: build the request universe from a source-date-valid historical
  instrument master. A present-day symbol list is prohibited.
- Duplicate gate: do not replace the existing KRX/FSC bridge where it has equal or
  better official coverage and provenance.

## Bounded pilots if the gates are later cleared

Use a dedicated lock such as `data/locks/kbsec_historical.lock`, never the daily
snapshot lock. Before execution, confirm there is no active or imminent scheduled
`IVSA0070` run. Each pilot is Landing-first, retry zero, and capped at exactly one
OAuth call plus one business call.

1. `IVU10430`: one short, known trading-date interval using an explicitly documented
   market aggregate mode. PASS requires multiple exact source dates, demonstrated
   market grain, stable units/categories, and no symbol-survivorship dependency.
2. `IVS11560`: one expired KOSPI200 futures contract from a separately verified
   historical instrument master. PASS requires rows at the requested historical
   boundary, explicit instrument/session identity, and no silent truncation.

OAuth bodies must be redacted while preserving byte/hash identity; business responses
must be retained byte-exact. The append-only ledger records both calls, and the atomic
checkpoint records request cap, response hashes, source-date range, row count, and
PASS/FAIL. A pilot PASS does not authorize a backfill until provider bulk-use terms,
rate limits, total call estimate, and one-authoritative-writer ownership are recorded.

## Date-semantics constraint

Nothing in this audit changes the `IVSA0070` date review. `snapshot_date` remains
capture-partition metadata only. Each snapshot slice must retain its own verified
source/market-date rule after the post-close comparison; a common `market_date` across
breadth, program, investor, liquidity, derivatives, domestic indices, and global
symbols is prohibited unless independently evidenced for every slice.
