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
| Korean source verification | pykrx | manual-only | explicit short smoke test only |
| Korean equity price/cap/universe | marcap + KRX Open API + FSC data.go.kr | complete, 1995-05-02..2026-08-07 | daily incremental |
| Korean Open API history | KRX Open API | complete, 2010-01-04..2019-12-30 | daily ledger/checkpoint retained |
| Korean short selling | unassigned/pykrx contract reference | draft blocked | live schema verification after restriction |
| Global index | Yahoo | available | routine validation |
| US macro | FRED | available | routine validation |
| `kr_market_investor_trading_daily` | Toss Securities | DATA_COMPLETE; 2014-07-01..2026-08-11, KOSPI/KOSDAQ, 5,946 rows | historical secondary; preserve the source `updatedAt`-derived `availability_date` |
| `kr_treasury_yield_daily` | Toss Securities | ARTIFACT_COMPLETE / PREDICTIVE_USE_BLOCKED; 2019-01-02..2026-08-10, six tenors, 11,162 rows | percent yield semantics verified; source volume unit and observation availability are unknown |
| Toss per-symbol short/program/lending/credit | Toss Securities | not survivorship safe; delisted sample returned `stock-not-found` for all four operations | do not run full-universe historical backfill |
| KB realtime | KB Securities | earlier OAuth success reported; 2026-08-11 fresh check failed with HTTP 500, result `9999`, process `E021`; IVSA0070 not called | verify app-key authorization externally, then authorize a new one-shot validation |
| Market breadth | canonical universe + Korean equity prices | complete, 1995-05-03..2026-08-07 | daily incremental |
| Treasury spread | FRED yields | implemented | recalculate after yield updates |
| `kr_market_liquidity_daily` | FSC/KOFIA public API | complete, 2021-10-26..2026-08-05 | daily incremental |
| `kr_credit_balance_daily` | FSC/KOFIA public API | complete, 2021-11-09..2026-08-05 | daily incremental |
| `kr_kospi200_futures_daily` | FSC derivatives public API | complete, 2020-01-02..2026-08-07; 1,620 dates | daily incremental |
| `kr_kospi200_options_daily` | FSC derivatives public API | complete, 2020-01-02..2026-08-07; 1,620 dates | daily incremental |
| Legacy general futures/options sample | FSC derivatives public API | partial, 2022-09-19 only | keep separate from KOSPI200 operational datasets |
| `kr_stock_lending_daily` | FSC stock-lending public API | DATA_COMPLETE, 2021-04-01..2026-08-10; 3,236,815 rows, 1,317 source dates | preserve source gaps; execution audit remains REVIEW_REQUIRED |
| `kr_stock_lending_market_daily` | FSC stock-lending public API | DATA_COMPLETE, 2021-04-01..2026-08-10; 1,254 rows/source dates | 63 source-absent dates versus detail intentionally preserved |
| `kr_stock_lending_participant_daily` | FSC stock-lending public API | DATA_COMPLETE, 2021-04-01..2026-08-10; 11,472 rows, 1,290 source dates | 27 source-absent dates versus detail intentionally preserved |
| `kr_equity_dividend` | FSC dividend public API | one current snapshot complete, 71,652 source events; no historical point-in-time series | define versioned incremental snapshot policy |
| `kr_equity_rights_schedule` | FSC rights public API | analysis complete; dataset BLOCKED, 0 rows | after D lock/quota approval, run at most one no-retry diagnostic; then define versioned observation grain and a safe key |
| Corporate-action source boundary | FSC/data.go.kr local official guides | analysis complete; no new dataset | verify split/merger/reduction economic-term source before deriving adjustments |
| Adjusted price / total return | none selected | not started | define corporate-action accounting policy and source |
| 2020+ official equity | FSC stock price/listed APIs | complete, 2020-01-02..2026-08-07 | daily incremental |
| `kr_equity_canonical_universe_daily` | listed-info + price union; master metadata | complete, 1995-05-02..2026-08-07 | daily incremental for primary sources |
| `kr_equity_master` | FSC issuance + observed daily identity | active, 2,770 rows; 2,754 issuance-enriched | increment current snapshot without dropping unmatched identities |
| KRX Open API 2010-2019 | KRX Open API | complete; 2,466 trading dates and 9,864 backfill calls | no resume required |
| `krx_legacy_kospi200_futures_daily` | verified legacy migration | DATA_COMPLETE, 2010-01-04..2019-12-30; 38,583 rows, 2,466 observed dates | retain legacy source-row provenance and session identity |
| `krx_legacy_kospi200_options_daily` | verified legacy migration | DATA_COMPLETE, 2010-01-04..2019-12-30; 1,090,078 rows, 2,466 observed dates | retain source `ISU_CD` as string and source-row identity |
| `kr_kospi200_option_pcr_daily` | derived from migrated options | DATA_COMPLETE, 2010-01-04..2019-12-31; 2,607 rows = 2,466 observed + 141 valid-empty weekdays | volume/open-interest PCR only; zero denominators remain null |
| Legacy KOSPI investor net purchase | KRX via PyKRX 1.2.8 legacy artifact | analysis complete, NOT_IMPLEMENTED; 3,834 pre-A001 candidate rows | separate versioned import only; exact monetary unit remains unverified |

Automated access to `data.krx.co.kr` is disabled. Existing pykrx Parquet data and
checkpoints remain preserved. KB work remains read-only; order APIs are out of scope.

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
| `kr_equity_rights_schedule` | no retained successful snapshot | exercise and registry-close dates in the guide | not provided | blocked; do not infer knowledge time or collapse later snapshots into earlier history |
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
- Automated `data.krx.co.kr` and pykrx collection are disabled. pykrx remains
  only for explicitly requested short manual smoke/comparison/fixture checks,
  with no historical, scheduled, polling, or repair automation.
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
  offline replay of all 60 retained landing responses, with no API calls.
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
- Deferred source/definition work: short selling, VKOSPI, and futures-basis roll
  rules. Toss per-symbol program/lending/credit history remains survivorship
  blocked. KOSPI200 PCR for 2010-2019 is implemented; later-period linkage is a
  separate task.

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
