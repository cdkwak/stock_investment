# Archived Data Layer status (pre-dashboard)

> Historical evidence only. This file preserves the detailed status narrative as it
> stood before the 2026-08-14 dashboard refactor. It is not an active instruction or
> current coverage source; use [`docs/project/DATA_STATUS.md`](../../project/DATA_STATUS.md).

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
| Korean equity price/cap/universe | marcap + KRX Open API + FSC data.go.kr | complete, 1995-05-02..2026-08-11; price/cap 15,163,276 rows each, provider universe 14,938,636 rows | 2026-08-11 incremental used exactly two retry-free FSC calls (one shared price/cap response plus one listed-universe response); 2026-08-12 onward remains pending |
| Korean Open API history | KRX Open API | complete, 2010-01-04..2019-12-30 | daily ledger/checkpoint retained |
| Korean short selling | authenticated pykrx 1.2.8 | Trading DATA_COMPLETE; Balance DATA_COMPLETE, 4,958/4,958 scopes and 6,035,958 rows; final deterministic audit PASS | Investor STOPPED after exactly one boundary business call returned only the range end date (1/501 expected dates); preserve Landing/ledger and redesign the range gate without retrying or synthesizing zeros |
| `global_index_price_daily` | Yahoo chart API | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 49,051 rows through 2026-08-07 | canonical read, PK, OHLC, null, infinity, and gap audit pass; immutable content-addressed local-artifact audit retained, but collection has no lossless Landing or call ledger |
| `fred_treasury_yield_daily` | FRED | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 16,853 weekdays, 1962-01-02..2026-08-06 | old history lacks response provenance; bounded DGS10 ALFRED pilots validate future Landing/realtime semantics but do not retrofit the artifact |
| `fred_usd_fx_daily` | FRED | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 14,500 weekdays, 1971-01-04..2026-07-31 | source-series nulls are preserved; immutable content-addressed local-artifact audit retained, while source-response provenance remains unavailable |
| `kr_market_investor_trading_daily` | Toss Securities | DATA_COMPLETE; 2014-07-01..2026-08-11, KOSPI/KOSDAQ, 5,946 rows | historical secondary; preserve the source `updatedAt`-derived `availability_date` |
| `kr_treasury_yield_daily` | Toss Securities | ARTIFACT_COMPLETE / PREDICTIVE_USE_BLOCKED; 2019-01-02..2026-08-10, six tenors, 11,162 rows | percent yield semantics verified; source volume unit and observation availability are unknown |
| `bok_ecos_kr_treasury_yield_source_observation` | BOK ECOS distributor / KOFIA source | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 29,674 rows, six tenors, 1998-11-13..2026-08-13 | five HTTP-200 backfill calls plus one exactly adopted 3Y page response; publication/revision timing remains unknown, so predictive use is blocked |
| Toss per-symbol short/program/lending/credit | Toss Securities | not survivorship safe; delisted sample returned `stock-not-found` for all four operations | official program screen `MDCSTAT02601` needs a post-A007 request-contract pilot; official credit market aggregates do not replace per-symbol shares/ratios |
| KB realtime | KB Securities | ACCESS_BLOCKED; 2026-08-11 and audited one-call 2026-08-13 token checks returned HTTP 500/result `9999`/process `E021`; IVSA0070 not called | provider/app-key authorization requires external resolution; do not repeat probes without new evidence |
| Market breadth | canonical universe + Korean equity prices | DATA_COMPLETE, 15,417 rows, 1995-05-03..2026-08-11 | 2026-08-11 added two validated KOSPI/KOSDAQ rows after the retry-free official equity increment; prior frozen corrective evidence remains retained |
| `us_treasury_spread_daily` | FRED yields | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 16,853 rows, 1962-01-02..2026-08-06 | deterministic offline state records exact input-state/files, output hashes, formulas, validation, and `api_calls=0` |
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
| `kr_equity_rights_schedule` | FSC rights public API | PARTIAL_DIAGNOSTIC_SOURCE_OBSERVATION; 13 immutable observations across two retained responses for 2019-12-31 issuer 1115 | the completion response returned its declared 12/12 rows; canonical event identity, broader historical completeness, and economic adjustment terms remain BLOCKED |
| Corporate-action source boundary | FSC/data.go.kr local official guides | analysis complete; no new dataset | verify split/merger/reduction economic-term source before deriving adjustments |
| Adjusted price / total return | none selected | not started | define corporate-action accounting policy and source |
| 2020+ official equity | FSC stock price/listed APIs | complete, 2020-01-02..2026-08-11 | latest completed increment used Landing-first single-page calls and exact checkpoint reconciliation |
| `kr_equity_canonical_universe_daily` | listed-info + price union; master metadata | complete, 15,163,277 rows, 1995-05-02..2026-08-11 | 2,761 validated rows added for 2026-08-11; daily incremental for primary sources |
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
Authenticated pykrx 1.2.8 access passed a bounded manual smoke test. A007 Trading and
Balance are complete; Investor remains stopped after its first production request
failed the exact date-coverage gate. No A007 production stream is active. Any further
KRX diagnostic or pilot remains D-owned, bounded, sequential, and separately
authorized; there is never more than one KRX stream. Existing pykrx Landing, ledgers,
Parquet data, and checkpoints remain preserved. KB work remains read-only; order APIs
are out of scope.

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
| `kr_equity_rights_schedule` | two retained response observations (`basDt` 2019-12-31, issuer 1115) | exercise and registry-close dates | not provided | the second response retained all declared 12 rows; response hash + item ordinal preserves both captures, while canonical event identity and historical knowledge remain blocked |
| `kr_equity_master` | `source_date` | listing, delisting, deposit registration/cancellation dates | not provided | only when `source_date <= as_of`; missing `source_date` is ineligible for predictive features |
| `kr_treasury_yield_daily` | unavailable from the retained source response | candle date | not provided | blocked for predictive features until a defensible observation-availability policy exists |
| `bok_ecos_kr_treasury_yield_source_observation` | immutable capture ID/time and Landing hash | ECOS source date | not provided | historical values are retained as source observations; predictive use remains blocked because original publication and revision timing are unknown |

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
  implemented and offline-tested. A007 no longer owns an active production stream,
  but these pilots remain unexecuted and require separate D authorization, cooldown,
  and the single-KRX-stream gate. No candidate contract is registered before actual
  full-market response semantics and historical coverage are audited.
- The configured OpenDART free-issue pilot executed its bounded three-request,
  zero-retry scope. `list`, `fricDecsn`, and `pifricDecsn` all returned HTTP 200 /
  source status `013` valid-empty for issuer `01160363` and
  2022-06-20..2022-07-20. Authentication and valid-empty handling are verified;
  positive-row schema, units, coverage, and revision behavior remain blocked
  until retained official evidence identifies a known-positive filing window.
  No Normalized artifact or corporate-action inference was created.
- BOK ECOS table `817Y002` (`1.3.2.1. 시장금리(일별)`) and all six official
  government-bond tenor identities/ranges are verified. The historical
  source-observation artifact is complete with 29,674 rows: the audited 3Y
  page-semantics response was adopted without a duplicate request, and the other five
  tenors completed serially with HTTP 200/retry 0. Exact Landing, ledger, checkpoint,
  parser, Parquet and state reconciliation passed. KOFIA remains the upstream
  final-quotation-yield source and BOK ECOS the official distributor; publication and
  revision timing remain unknown, so these values are not assumed identical to Toss
  OHLC candles and remain blocked from predictive use.
- Legacy pykrx failed checkpoint entries are preserved for audit but do not
  indicate a gap in the completed official 1995-2026 equity datasets.
- KB Securities remains read-only. An earlier OAuth success was reported, but the
  2026-08-11 fresh token check returned HTTP 500/result `9999`/process `E021`, so
  IVSA0070 was not called and no live snapshot was stored. A new fail-closed
  Landing-first token sentinel on 2026-08-13 reproduced HTTP 500/result `9999`/
  process `E021` in exactly one request with zero retries; its redacted response,
  ledger, and checkpoint are retained under
  `data/landing/diagnostics/kbsec_token_pilot/20260813T122256Z_686cca26e4454e74a501cd9ac0470fdc/`.
  KB access therefore remains blocked and no IVSA0070 request was attempted. Order, correction,
  cancellation, transfer, and withdrawal APIs are out of scope.
- Toss market investor and Korean Treasury history use cursor pagination with
  source rate-limit headers, landing-first capture, atomic Parquet merge, and
  explicit provenance fields. Treasury responses do not provide `updatedAt`;
  the earlier inferred `availability_date = source_date` was removed by an
  offline replay of all 60 retained landing responses, with no API calls. The
  official-source audit in `../data/audits/A006_TREASURY_AVAILABILITY_AUDIT.md` confirms that
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
- A separately authorized second dividend snapshot attempt for `basDt=2026-08-13`
  made exactly one request with zero retries. The source-success result `00`
  response declared and returned zero rows. The Landing page is retained with
  SHA-256 `c8ee7766deedd80acd8fb1a3bcdb1b29e67caa037c73b2ec8c8e11042ce148a5`;
  an offline-reconstructed checkpoint and ledger classify it
  `VALID_EMPTY_STOP`. The former local non-empty assertion stopped after Landing
  persistence but before transport status was recorded, so exact HTTP status is
  intentionally marked unreconstructable. No second call, completed
  `full_history.json`, Normalized append, or semantic inference was made. The
  existing 2026-08-08 snapshot therefore remains the sole artifact snapshot.
- Official OpenDART discovery found 2015+ filing APIs for paid/free capital
  increases, capital reductions, mergers, divisions, and division-mergers. The
  responses carry filing receipt identity and decision/economic schedule fields,
  and some products expose share counts or ratios. This is a defensible
  corporate-action observation candidate, not yet an adjustment-factor dataset.
  The configured credential unlocked the bounded three-call valid-empty pilot
  described above. A successful-row contract remains blocked on a separately
  evidence-selected known-positive issuer/window; no values or coverage before
  2015 are inferred.
- The one-call B002-P1 Rights diagnostic returned HTTP 200/result `00` with
  12 source records reported and one retained item (page size one). It proves
  source usability only, not historical coverage. That retained item is now
  promoted as one immutable Normalized source observation with the exact
  envelope/body/ledger/handoff hash chain; this does not change the canonical
  or economic-event blocker. B002-P2 found that an
  immutable source-observation contract can use response-body SHA-256 plus item
  ordinal as its key and append later corrections, but the response does not
  provide ratios, share quantities, issue price, adjustment factors, or a safe
  field-only canonical event identity. No economic Rights event dataset or
  adjusted-price input is therefore marked complete.
- The separately authorized B002-P3 completion sentinel made exactly one
  retry-free request for the same fixed 2019-12-31 issuer/page with page size
  12. It returned HTTP 200/result `00` and exactly 12/12 unique source records
  matching the reviewed source schema and request identity. Its immutable
  Landing response, ledger, checkpoint, and hash-chain handoff passed credential,
  parser, contract, PK, and state reconciliation checks. The existing append-only
  observation builder added the complete response as 12 observations; the two
  retained response identities now total 13 rows with zero PK duplicates. This
  completes only that one source response snapshot and does not establish wider
  historical coverage, canonical business-event identity, economic rights terms,
  adjustment factors, announcement timing, or supersession semantics.
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
  missing, or path-escaping evidence fails closed. A007 Trading is DATA_COMPLETE:
  9,174 unique scopes across 4,587 dates produced 10,161,884 rows for
  2008-01-02..2026-08-07. All 9,174 Landing hashes reconcile with the checkpoint;
  PK duplicates, PK nulls, and numeric infinities are zero. The retained ledgers
  contain exactly 9,174 successful business responses, 255 authentication
  responses, no non-200 response, no duplicate completed scope, and one local
  pre-response socket error from the interrupted Windows session. Balance is also
  DATA_COMPLETE: 4,958/4,958 scopes produced 6,035,958 exact Normalized rows for
  2016-06-30..2026-08-07. Its final deterministic audit reconciles Landing,
  provenance, ledgers, checkpoint, Parquet rows, PK, nulls, NaNs, and infinities
  with no remaining scope. The earlier `20240425_KOSDAQ` restriction response and
  recovery are retained as audit history and were not rebuilt or discarded.
  Investor then made exactly one production business request for
  `20080102_20091230_KOSPI_volume`; the HTTP-200 JSON returned only the range end
  date instead of all 501 expected canonical dates. The collector retained the
  evidence, made no retry, wrote no checkpoint or Normalized data, and remains
  `STOPPED`. A later one-request recent-window diagnostic passed all five expected
  dates. The subsequent S1 request returned healthy-looking data for all 485/485
  expected dates, but the original classifier stopped with
  `TOP_LEVEL_SCHEMA_MISMATCH` because KRX included a verified `CURRENT_DATETIME`
  metadata field beside `OutBlock_1`. A zero-network verifier then validated the
  frozen inputs, exact request/ledger/provenance chain, all 485 expected dates,
  exact row schema, nonnegative integers, component totals, and 485 positive-total
  dates as `S1_FULL_RANGE_CONFIRMED`. The original terminal event remains preserved;
  this offline PASS does not authorize Investor resume. Three later one-request
  historical availability diagnostics were also retained and audited. H1
  (2010-01-04..2012-01-04; 502 expected dates), H2
  (2012-01-05..2014-01-03; 494), and H3
  (2014-01-06..2016-01-06; 494) each returned exactly one range-end row with all
  investor components and total equal to zero. Each is classified
  `PRE_AVAILABILITY_COLLAPSE`; each used five authentication responses plus one
  business response, all HTTP 200, with retry zero. These results bound three
  unavailable historical windows but do not establish a complete availability
  boundary, authorize another probe, synthesize missing dates or zeros, or permit
  Investor resume. H4 then requested 490 KOSPI-volume dates for
  2016-01-07..2018-01-05 and stopped as `AMBIGUOUS_STOP:154/490`: its 154 positive
  rows are the exact canonical suffix 2017-05-22..2018-01-05, while the 336-date
  prefix through 2017-05-19 is absent. A separately audited two-date boundary
  pair returned sole positive 2017-05-22 and no 2017-05-19, classified
  `BOUNDARY_SHAPED_CONFIRMED`. This establishes the observed boundary shape only
  for `MDCSTAT30301` KOSPI volume. KOSDAQ, value mode, total-date parity, and
  historical production coverage remain unverified, so Investor stays stopped
  and no resume or synthesis is authorized. The later parity diagnostic stopped
  immediately on its first scope, KOSPI trading value: HTTP 403 restriction HTML
  was retained with provenance and ledger. Retry count was zero; KOSDAQ volume
  and KOSDAQ trading value calls 2/3 were not made. The KRX stream is
  `PAUSED_ACCESS_SAFETY`; no further recovery or parity probe is authorized.
  The historical sequence remains documented in
  `runbooks/A007_FOLLOWON_BALANCE_INVESTOR.md` and the diagnostic runbooks.
  V-KOSPI 200 is PILOT_READY through
  an official authenticated KRX daily-index candidate, but exact source index
  identity, returned fields, historical start, and revision/cutoff policy still
  require the post-A007 bounded pilot documented in `../data/audits/VKOSPI200_SOURCE_AUDIT.md`.
  Toss per-symbol program/lending/credit history remains survivorship blocked.
  Program trading is no longer source-unknown: local KRX metadata identifies
  `MDCSTAT02601`, but parameters, grain, fields, and units require the bounded
  post-A007 discovery gate in `KRX_PROGRAM_TRADING_SOURCE_READINESS.md`.
  `kr_credit_balance_daily` remains DATA_COMPLETE at market-aggregate grain;
  `../data/audits/OFFICIAL_CREDIT_BALANCE_SOURCE_AUDIT.md` shows that its monetary FreeSIS
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
- Configured FRED credentials enabled three bounded DGS10 ALFRED diagnostics.
  The initial two-request pilot stopped fail-closed because its requested vintage
  scope exceeded the source limit. Two later one-request, zero-retry scopes passed
  offline audit: a bounded realtime interval retained 27 rows, and a historical
  revision interval retained 29 dates with zero dates having multiple observed
  value versions. These prove credentialed Landing and realtime-period semantics
  for bounded future collection, but do not retrofit old normalized provenance or
  justify activating a revision dataset without useful revision evidence or an
  explicit provenance-only decision.

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
was completed for the six allowlisted schema-only migration roots. Their logical
values and row identity were verified unchanged while the physical Arrow schemas
were brought to their registered contracts; this was not a source rebuild. The
already-completed migrations do not have a retrospective tool-owned old-to-new
transaction ledger, so one must not be fabricated. Future schema migration
provenance remains separate maintenance work. The two legacy KOSPI200 Normalized
datasets and their Derived PCR
dataset have separate active contracts registered with their implemented Arrow
schemas, source identity, keys, sort order, partitioning, and nullability.
The legacy investor-flow contract is likewise registered separately with key
`(date, market)`, year partitioning, a strict pre-A001 end date of 2014-06-30,
and checksum-fixed source provenance. Its monetary scale remains deliberately
`unit_unknown`.
