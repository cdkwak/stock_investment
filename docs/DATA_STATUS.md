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
| Toss market data | Toss Securities | probe/fixture complete; no operational Dataset | define contracts and source policy before integration |
| KB realtime | KB Securities | earlier OAuth success reported; 2026-08-11 fresh check failed with HTTP 500, result `9999`, process `E021`; IVSA0070 not called | verify app-key authorization externally, then authorize a new one-shot validation |
| Market breadth | canonical universe + Korean equity prices | complete, 1995-05-03..2026-08-07 | daily incremental |
| Treasury spread | FRED yields | implemented | recalculate after yield updates |
| `kr_market_liquidity_daily` | FSC/KOFIA public API | complete, 2021-10-26..2026-08-05 | daily incremental |
| `kr_credit_balance_daily` | FSC/KOFIA public API | complete, 2021-11-09..2026-08-05 | daily incremental |
| `kr_kospi200_futures_daily` | FSC derivatives public API | complete, 2020-01-02..2026-08-07; 1,620 dates | daily incremental |
| `kr_kospi200_options_daily` | FSC derivatives public API | complete, 2020-01-02..2026-08-07; 1,620 dates | daily incremental |
| Legacy general futures/options sample | FSC derivatives public API | partial, 2022-09-19 only | keep separate from KOSPI200 operational datasets |
| Stock lending datasets | FSC stock-lending public API | partial, 2023-10-05 and 2026-08-05 snapshots | approve resumable backfill plan |
| `kr_equity_dividend` | FSC dividend public API | current snapshot complete, 71,652 source events | define incremental snapshot policy |
| `kr_equity_rights_schedule` | FSC rights public API | blocked/partial; contract verified, no normalized backfill | resolve failed probe and snapshot/event dedup policy |
| Adjusted price / total return | none selected | not started | define corporate-action accounting policy and source |
| 2020+ official equity | FSC stock price/listed APIs | complete, 2020-01-02..2026-08-07 | daily incremental |
| `kr_equity_canonical_universe_daily` | listed-info + price union; master metadata | complete, 1995-05-02..2026-08-07 | daily incremental for primary sources |
| `kr_equity_master` | FSC issuance + observed daily identity | active, 2,770 rows; 2,754 issuance-enriched | increment current snapshot without dropping unmatched identities |
| KRX Open API 2010-2019 | KRX Open API | complete; 2,466 trading dates and 9,864 backfill calls | no resume required |

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
| `kr_equity_rights_schedule` | `date` (`basDt`) | exercise and registry-close dates | not provided | from the captured source snapshot date only |
| `kr_equity_master` | `source_date` | listing, delisting, deposit registration/cancellation dates | not provided | only when `source_date <= as_of`; missing `source_date` is ineligible for predictive features |

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
- Deferred source/definition work: short selling, VKOSPI, program trading,
  PCR aggregation, and futures-basis roll
  rules. These are not active Data v1 implementation tasks.
