# Data Layer status

Data v1 is frozen at the verified baseline. No Dataset Contract, schema,
canonical-universe rule, normalized/derived/published Parquet, or checkpoint may
change without a separately approved task. The supported CLI is
`scripts/run_data_v1.py`; KRX is skipped by default.

## Verified operating coverage

This table reflects the current Parquet and checkpoint state, not a planning
estimate. Provider-specific legacy samples and smoke fixtures are not counted as
official continuous coverage.

| Area | Provider | Status | Next gate |
|---|---|---|---|
| Korean equity history 1995-2009 | FinanceData/marcap secondary | complete, 1995-05-02..2009-12-30; 22 rows quarantined | immutable annual-file/checksum audit only |
| Korean equity history 2010-2019 | KRX Open API primary | complete, 2010-01-04..2019-12-30 | no resume required |
| Korean source verification | authenticated pykrx 1.2.8 | bounded manual smoke passed; 5/5 public probes, 12 raw HTTP requests | automation remains dataset-specific; only the reviewed A007 single-stream collector is currently enabled |
| Korean equity price/cap/universe | marcap + KRX Open API + FSC data.go.kr | complete, 1995-05-02..2026-08-07 | daily incremental |
| Korean Open API history | KRX Open API | complete, 2010-01-04..2019-12-30 | daily ledger/checkpoint retained |
| Korean short selling | authenticated pykrx 1.2.8 | RUNNING; trading history has 6,974/9,174 completed scopes (3,487/4,587 dates), 7,233,722 rows through 2022-02-09; 75% milestone validated and next bounded batch active | preserve the single authenticated stream; then collect balance and investor scopes sequentially |
| `global_index_price_daily` | Yahoo chart API | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 49,051 rows through 2026-08-07 | canonical read, PK, OHLC, null, infinity, and gap audit pass; retained collection has no lossless Landing or call ledger |
| `fred_treasury_yield_daily` | FRED | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 16,853 weekdays, 1962-01-02..2026-08-06 | source-series nulls are preserved; retained state is only a coarse completion marker |
| `fred_usd_fx_daily` | FRED | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 14,500 weekdays, 1971-01-04..2026-07-31 | source-series nulls are preserved; retained state is only a coarse completion marker |
| `kr_market_investor_trading_daily` | Toss Securities | DATA_COMPLETE; 2014-07-01..2026-08-11, KOSPI/KOSDAQ, 5,946 rows | historical secondary; preserve the source `updatedAt`-derived `availability_date` |
| `kr_treasury_yield_daily` | Toss Securities | ARTIFACT_COMPLETE / PREDICTIVE_USE_BLOCKED; 2019-01-02..2026-08-10, six tenors, 11,162 rows | percent yield semantics verified; source volume unit and observation availability are unknown |
| Toss per-symbol short/program/lending/credit | Toss Securities | not survivorship safe; delisted sample returned `stock-not-found` for all four operations | official program screen `MDCSTAT02601` needs a post-A007 request-contract pilot; official credit market aggregates do not replace per-symbol shares/ratios |
| KB realtime | KB Securities | earlier OAuth success reported; 2026-08-11 fresh check failed with HTTP 500, result `9999`, process `E021`; IVSA0070 not called | verify app-key authorization externally, then authorize a new one-shot validation |
| Market breadth | canonical universe + Korean equity prices | complete, 1995-05-03..2026-08-07 | daily incremental |
| `us_treasury_spread_daily` | FRED yields | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 16,853 rows, 1962-01-02..2026-08-06 | exact local parity with retained yields; no independent state file |
| `kr_market_liquidity_daily` | FSC/KOFIA public API | complete, 2021-10-26..2026-08-05 | daily incremental |
| `kr_credit_balance_daily` | FSC/KOFIA public API | complete, 2021-11-09..2026-08-05 | daily incremental |
| `kr_kospi200_futures_daily` | FSC derivatives public API | complete, 2020-01-02..2026-08-07; 1,620 dates | daily incremental |
| `kr_kospi200_options_daily` | FSC derivatives public API | complete, 2020-01-02..2026-08-07; 1,620 dates | daily incremental |
| `kr_kosdaq150_futures_daily` | retained FSC derivatives Landing | DATA_COMPLETE_WITH_LIMITS; 2022-09-19 only, 7 outright rows | six source calendar-spread rows remain Landing-only; no historical-continuity claim |
| `kr_kosdaq150_options_daily` | retained FSC derivatives Landing | DATA_COMPLETE_WITH_LIMITS; 2022-09-19 only, 316 rows | retained one-day snapshot only; no historical-continuity claim |
| Other legacy general futures/options sample | FSC derivatives public API | partial, 2022-09-19 only | keep separate from KOSPI200 and KOSDAQ150 operational datasets |
| `kr_stock_lending_daily` | FSC stock-lending public API | DATA_COMPLETE, 2021-04-01..2026-08-10; 3,236,815 rows, 1,317 source dates | preserve source gaps; execution audit remains REVIEW_REQUIRED |
| `kr_stock_lending_market_daily` | FSC stock-lending public API | DATA_COMPLETE, 2021-04-01..2026-08-10; 1,254 rows/source dates | 63 source-absent dates versus detail intentionally preserved |
| `kr_stock_lending_participant_daily` | FSC stock-lending public API | DATA_COMPLETE, 2021-04-01..2026-08-10; 11,472 rows, 1,290 source dates | 27 source-absent dates versus detail intentionally preserved |
| `kr_equity_dividend` | FSC dividend public API | one current snapshot complete, 71,652 source events; no historical point-in-time series | define versioned incremental snapshot policy |
| `kr_equity_dividend_source_observation` | retained FSC dividend Landing snapshot | ARTIFACT_COMPLETE, 71,652 immutable source observations at `basDt=2026-08-08` | non-PIT/non-predictive; append future independently captured snapshots by Landing hash |
| `kr_equity_rights_schedule` | FSC rights public API | source diagnostic complete; canonical event dataset BLOCKED, 0 rows | source-observation contract is implementable; economic adjustment terms and a canonical event key remain unverified |
| Corporate-action source boundary | FSC/data.go.kr local official guides | analysis complete; no new dataset | verify split/merger/reduction economic-term source before deriving adjustments |
| Adjusted price / total return | none selected | not started | define corporate-action accounting policy and source |
| 2020+ official equity | FSC stock price/listed APIs | complete, 2020-01-02..2026-08-07 | daily incremental |
| `kr_equity_canonical_universe_daily` | listed-info + price union; master metadata | complete, 1995-05-02..2026-08-07 | daily incremental for primary sources |
| `kr_equity_master` | FSC issuance + observed daily identity | active, 2,770 rows; 2,754 issuance-enriched | increment current snapshot without dropping unmatched identities |
| KRX Open API 2010-2019 | KRX Open API | complete; 2,466 trading dates and 9,864 backfill calls | no resume required |
| `krx_legacy_kospi200_futures_daily` | verified legacy migration | DATA_COMPLETE, 2010-01-04..2019-12-30; 38,583 rows, 2,466 observed dates | retain legacy source-row provenance and session identity |
| `krx_legacy_kospi200_options_daily` | verified legacy migration | DATA_COMPLETE, 2010-01-04..2019-12-30; 1,090,078 rows, 2,466 observed dates | retain source `ISU_CD` as string and source-row identity |
| `kr_kospi200_option_pcr_daily` | legacy + official KOSPI200 options | DATA_COMPLETE, 2010-01-04..2026-08-07; 4,227 rows = 4,086 observed + 141 legacy valid-empty weekdays | 2010-2019 legacy 2,607 + 2020-present official 1,620; volume/open-interest PCR only |
| `kr_kospi200_futures_provider_bridge_daily` | legacy + official KOSPI200 futures | DATA_COMPLETE_WITH_LIMITS, 2010-01-04..2026-08-07; 38,601 contract rows | provider/session boundary preserved; 11,322 legacy spread rows excluded; no continuous/front-month roll rule |
| `kr_kospi200_options_provider_bridge_daily` | legacy + official KOSPI200 options | DATA_COMPLETE_WITH_LIMITS, 2010-01-04..2026-08-07; 3,782,720 contract rows | official session is unspecified by source; no continuous/front-month roll rule |
| `kr_kospi200_futures_nearest_listed_daily` | retained futures bridge + normalized source rows | DATA_COMPLETE_WITH_LIMITS, 2010-01-04..2026-08-07; 6,538 rows | nearest source-listed maturity only; provider/session boundaries preserved; exact expiry, normalized units, back-adjustment, and calendar roll are not inferred |
| `kr_market_investor_net_purchase_daily` | KRX via PyKRX 1.2.8 legacy artifact | DATA_COMPLETE, 1999-01-04..2014-06-30; 3,834 KOSPI rows | checksum-fixed pre-A001 dataset; signed source integers retain `unit_unknown` and must not be concatenated with A001 without an explicit bridge |
| `kr_market_investor_net_purchase_bridge_daily` | legacy investor + Toss A001 | DATA_COMPLETE, 1999-01-04..2026-08-11; 9,780 rows | Published provider-boundary bridge; legacy rows retain `unit_unknown`, null availability, and predictive-use block; cross-segment numeric comparison is prohibited |

Unauthenticated/standalone automated access to `data.krx.co.kr` remains disabled.
Authenticated pykrx 1.2.8 access passed a bounded manual smoke test. A007 historical
short-selling collection is now enabled only as D-owned, bounded, sequential batches
with immutable Landing, call ledger, and checkpoint reconciliation. No second KRX
stream is permitted while A007 runs. Existing pykrx Parquet data and checkpoints
remain preserved. KB work remains read-only; order APIs are out of scope.

Point-in-time rule: normalized source observations keep their source date unchanged.
Research features/signals observed on trading day T may only be executed from T+1;
daily universe membership must come from that date's canonical universe, never from a
later master snapshot alone.

Equity survivorship contract: point-in-time daily trade/basic-info or FSC
price/listed-info determines daily existence. Current master data is metadata-only and
must never filter historical membership. Delisted historical symbols, preferred shares,
and rows with source-specific nullable corporate metadata are retained. Price, market
cap, and universe rows carry `source`, `source_operation`, and `source_date`; the
provider boundaries are FinanceData/marcap through 2009-12-30, KRX Open API from
2010-01-04 through 2019-12-30, and FSC from 2020-01-02. The marcap annual-file
manifest preserves repository commit and SHA-256 provenance; quarantined source rows
remain in landing and never overwrite normalized data.

## Snapshot/event availability

| Dataset | Source snapshot / as-of | Event-effective fields | Announcement field | Historical predictive use |
|---|---|---|---|---|
| `kr_equity_dividend` | `date` (`basDt`) | record, cash-payment, stock-delivery dates | not provided | from the captured source snapshot date only; event dates are not knowledge dates |
| `kr_equity_rights_schedule` | one retained successful diagnostic snapshot (`basDt` 2019-12-31) | exercise and registry-close dates | not provided | source observations may be retained immutably by response hash + item ordinal; canonical event identity and historical knowledge remain blocked |
| `kr_equity_master` | `source_date` | listing, delisting, deposit registration/cancellation dates | not provided | only when `source_date <= as_of`; missing `source_date` is ineligible for predictive features |
| `kr_treasury_yield_daily` | unavailable from the retained source response | candle date | not provided | blocked for predictive features until a defensible observation-availability policy exists |

Future effective events present in a snapshot remain valid source records. Total-return
accounting may apply a validated event retrospectively at its economic effective date;
predictive features must instead obey the captured snapshot/availability date. The data
layer does not shift either date.

## Operational blockers and deferred work

- KRX Open API stock trade/basic-info products are approved and smoke-verified.
  Historical backfill remains gated only by an explicit call budget and checkpointed
  operating plan.
- The pre-login KRX failure is no longer a sufficient source blocker. Installed
  pykrx 1.2.8 has authenticated session support, and the 2026-08-11 manual test
  authenticated successfully. Five sequential probes covered recent/historical,
  listed/delisted, short selling, investor flow, valuation, ETF, and foreign
  ownership observations; all succeeded in 12/14 permitted raw requests with
  zero retries. Historical, scheduled, polling, and repair automation remain
  disabled until the authenticated collection runbook gates are satisfied.
- Bounded Landing-only pilots for fundamentals, foreign ownership, and ETF are
  implemented and offline-tested. They remain unexecuted while A007 owns the
  single KRX stream; no candidate contract is registered before actual full-market
  response semantics and historical coverage are audited.
- The OpenDART free-issue observation pilot is IMPLEMENTATION_READY but unexecuted.
  It is capped at three sequential zero-retry requests and preserves filings as
  immutable observations; it does not infer supersession, canonical events,
  adjustment factors, prices, or predictive availability.
- BOK ECOS table `817Y002` (`시장금리(일별)`) and the six official government-bond
  tenor identities are reviewed. The bounded A010 metadata phase is ready, but this
  process has no `BOK_ECOS_API_KEY`; the value phase remains blocked until the one-call
  immutable metadata response is captured and its exact SHA-256 is independently
  approved. KOFIA remains the upstream final-quotation-yield source and BOK ECOS the
  official distributor; these values are not assumed identical to Toss OHLC candles.
- Legacy pykrx failed checkpoint entries are preserved for audit but do not
  indicate a gap in the completed official 1995-2026 equity datasets.
- KB Securities remains read-only. An earlier OAuth success was reported, but the
  2026-08-11 fresh token check returned HTTP 500/result `9999`/process `E021`, so
  IVSA0070 was not called and no live snapshot was stored. Order, correction,
  cancellation, transfer, and withdrawal APIs are out of scope.
- Toss market investor and Korean Treasury history use cursor pagination with
  source rate-limit headers, landing-first capture, atomic Parquet merge, and
  explicit provenance fields. Treasury responses do not provide `updatedAt`;
  the earlier inferred `availability_date = source_date` was removed by an
  offline replay of all 60 retained landing responses, with no API calls. The
  official-source audit in `A006_TREASURY_AVAILABILITY_AUDIT.md` confirms that
  Toss OHLC cannot inherit KOFIA's separate 16:30 final-quotation timestamp;
  all Toss availability dates remain null and predictive use stays blocked.
  Per-symbol historical APIs are blocked from full-universe
  backfill because a canonical delisted symbol returned `stock-not-found` in each
  operation; this prevents survivorship-safe coverage.
- The three stock-lending artifacts passed schema, PK, null, infinity,
  duplicate, landing-to-normalized, survivorship, and state reconciliation
  checks. Their wrapper timed out without terminating its child process, so an
  overlapping resume occurred and exact task-level calls are unreconstructable.
  This execution finding remains REVIEW_REQUIRED but does not invalidate the
  independently verified artifacts; do not rerun the backfill merely to rebuild
  accounting. The minimum successful unique responses are 333, with no observed
  429, 5xx, or parser errors. The retained runner now reconstructs completed
  source-row counts only from consistent, contiguous landing pages, rejects
  contradictory valid-empty envelopes, preserves `VALID_EMPTY` on resume, and
  is protected by an atomic single-process lock.
- The legacy KOSPI200 migration records 141 source-empty weekdays in state and
  emits no fabricated Normalized rows for those dates. PCR reproduces the 2,466
  legacy observed aggregates exactly within floating-point tolerance and
  represents those same 141 dates as valid-empty rows.
- Corporate-action adjustment remains blocked. The current dividend snapshot
  has no verified announcement/ex-date history, and filtering it through the
  current master would drop 23,305 rows across 3,533 ISINs. Rights, issuance,
  split, merger, and capital-reduction terms require separate verified sources
  and versioned observation contracts before any adjustment-factor derivation.
- The retained dividend Landing file was rebuilt offline into 71,652 immutable
  source-observation rows keyed by Landing SHA-256 and source item ordinal.
  Its projection exactly reproduces `kr_equity_dividend`, but it is one current
  snapshot with no capture timestamp, prior snapshot, or correction history.
  It is therefore artifact-complete only and remains ineligible for historical
  PIT, predictive, adjusted-price, or total-return use.
- Official OpenDART discovery found 2015+ filing APIs for paid/free capital
  increases, capital reductions, mergers, divisions, and division-mergers. The
  responses carry filing receipt identity and decision/economic schedule fields,
  and some products expose share counts or ratios. This is a defensible
  corporate-action observation candidate, not yet an adjustment-factor dataset.
  The repository has no OpenDART API key, so collection is paused at the external
  credential gate; no values or coverage before 2015 are inferred.
- The one-call B002-P1 Rights diagnostic returned HTTP 200/result `00` with
  12 source records reported and one retained item (page size one). It proves
  source usability only, not historical coverage. B002-P2 found that an
  immutable source-observation contract can use response-body SHA-256 plus item
  ordinal as its key and append later corrections, but the response does not
  provide ratios, share quantities, issue price, adjustment factors, or a safe
  field-only canonical event identity. No economic Rights event dataset or
  adjusted-price input is therefore marked complete.
- Short-selling source feasibility is confirmed but its dataset contract/coverage
  pilot is now complete. The 25 sequential business probes used 40 raw HTTP
  requests: 25 business responses and 15 authentication requests across the
  original process plus two verified checkpoint resumes. Every response was
  HTTP 200, no business request was repeated, and credential scans were clean.
  Full-market trading and investor probes were non-empty on 2008-01-02;
  full-market balance probes were non-empty on 2016-06-30. Both current and
  historical/delisted symbol probes succeeded, including KOSPI `003410` and
  KOSDAQ `030270`. Weekend trading/balance returned empty arrays; the investor
  endpoint returned its source-specific blank-date, all-zero placeholder, which
  is retained and classified separately as valid-empty. These sentinels prove
  feasibility, not the earliest possible source date. The v2 contracts and
  bounded single-stream collector passed D and independent offline review.
  Recovery requires immutable HTTP-200 provenance plus an exact same-run
  ledger/scope correlation and Normalized row reconciliation; non-200, forged,
  missing, or path-escaping evidence fails closed. A007 has completed 6,974 trading
  scopes and 7,233,722 production rows through 2022-02-09, with the next bounded
  batch active. V-KOSPI 200 is PILOT_READY through
  an official authenticated KRX daily-index candidate, but exact source index
  identity, returned fields, historical start, and revision/cutoff policy still
  require the post-A007 bounded pilot documented in `VKOSPI200_SOURCE_AUDIT.md`.
  Toss per-symbol program/lending/credit history remains survivorship blocked.
  Program trading is no longer source-unknown: local KRX metadata identifies
  `MDCSTAT02601`, but parameters, grain, fields, and units require the bounded
  post-A007 discovery gate in `KRX_PROGRAM_TRADING_SOURCE_READINESS.md`.
  `kr_credit_balance_daily` remains DATA_COMPLETE at market-aggregate grain;
  `OFFICIAL_CREDIT_BALANCE_SOURCE_AUDIT.md` shows that its monetary FreeSIS
  series cannot substitute for Toss per-symbol share quantities and ratios.
  KOSPI200 PCR is linked through 2026-08-07. The combined atomic writer
  preserved all ten 2010-2019 Parquet files byte-for-byte and added seven
  official 2020-2026 partitions. The 4,227-row result has zero PK duplicates or
  infinities; ratio nulls are exactly the 141 audited legacy valid-empty rows.
- The KOSPI200 derivatives Published bridges retain contract rows across the
  2019-12-30 legacy / 2020-01-02 official boundary. Boundary contract-code
  intersections are exact for futures (7/7) and options (894/894). Regular and
  night futures remain separate; 11,322 legacy spread rows are excluded from
  the outright-futures bridge. Official option session, exact expiry dates,
  and legacy price/volume/open-interest units are not inferred. These datasets
  are not continuous contracts and must not be used as one without a separately
  specified and tested roll rule.
- The Derived nearest-source-listed futures series selects the minimum retained
  maturity month independently within each provider/session segment. It has
  6,538 rows and 106 observed contract transitions; regular-session source-native
  settlement basis exists on 4,086 rows, while 2,452 night rows remain null.
  Exact expiry, normalized units, calendar roll, and back-adjustment are not inferred.
- The retained Yahoo/FRED artifacts passed a deterministic 2026-08-11 audit.
  Global indices contain 49,051 rows: S&P 500 24,766
  (1928-01-03..2026-08-07), NASDAQ Composite 13,993
  (1971-02-05..2026-08-07), and NASDAQ-100 10,292
  (1985-10-01..2026-08-07). FRED yields contain 16,853 weekday rows and
  USD FX 14,500 weekday rows; source-start nulls are preserved rather than
  backfilled. Treasury-spread values reproduce the retained yield arithmetic
  exactly. These datasets are artifact-complete with provenance limits because
  their retained states lack Landing reconciliation, call ledgers, and strong
  source-response manifests.

## Artifact and execution classifications

`DATA_COMPLETE` means the retained Landing, Normalized/Derived artifact, key,
coverage, and integrity checks passed. It does not erase a separate execution
audit finding or make a dataset point-in-time safe. `ARTIFACT_COMPLETE /
PREDICTIVE_USE_BLOCKED` means the stored source values are complete for the
verified collection scope, but missing knowledge-time or unit semantics prevent
predictive use.

## Physical schema conformance

The integrated Toss contracts declare the established on-disk representation:
`date` is `date32`, provenance dates are nullable/non-null strings as documented,
and source timestamps are UTC `timestamp[ns]`. A001 conforms without data
migration. A006 was replayed offline so all 11,162 unverifiable
`availability_date` values are null while every other field remains identical.
Generic repository-wide enforcement of physical Arrow types for older datasets
remains separate technical debt; it must not be used to silently rewrite frozen
artifacts. The two legacy KOSPI200 Normalized datasets and their Derived PCR
dataset have separate active contracts registered with their implemented Arrow
schemas, source identity, keys, sort order, partitioning, and nullability.
The legacy investor-flow contract is likewise registered separately with key
`(date, market)`, year partitioning, a strict pre-A001 end date of 2014-06-30,
and checksum-fixed source provenance. Its monetary scale remains deliberately
`unit_unknown`.
