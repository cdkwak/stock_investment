# Data Layer status

Data v1 is frozen at the verified baseline. No Dataset Contract, schema,
canonical-universe rule, normalized/derived/published Parquet, or checkpoint may
change without a separately approved task. The supported CLI is
`scripts/run_data_v1.py`; KRX is skipped by default.

## Public API backfill call estimate

These are planning estimates only; no backfill was run. Trading-date counts use
the stored KOSPI calendar. Page counts use verified landing responses with
`numOfRows=9999`. The public portal user's daily quota is **UNKNOWN**.

| Source group | Planning/confirmed window | Dates | Calls/date | Total | Checkpoint calls complete | Estimated remaining |
|---|---|---:|---:|---:|---:|---:|
| Equity price + market cap (shared response) | 2020-01-02..2026-08-06 confirmed | 1,619 | 1 | 1,619 | 1 | 1,618 |
| Listed universe | 2020-01-02..2026-08-06 confirmed | 1,619 | 1 | 1,619 | 1 | 1,618 |
| Futures | 2020-01-02..2026-08-06 planning cutoff; latest availability unknown | 1,619 | 1 observed | 1,619 | 1 | 1,618 |
| Options | same planning cutoff; latest availability unknown | 1,619 | 2 observed | 3,238 | 2 | 3,236 |
| Stock lending, three independent operations | 2021-04-01 conservative month boundary..2026-08-05 confirmed | 1,310 | 3 | 3,930 | 6 | 3,924 |
| Canonical universe + market breadth | source-date intersection | 1,619 after source completion | 0 | 0 | 0 | 0 |

Recommended conservative caps while quota is unknown: equity 50 calls/run,
universe 50, futures 50, options 40 (about 20 dates), and stock lending 30
(10 dates across three operations). Run sequentially, keep independent source
checkpoints, skip completed/valid-empty dates, stop immediately on code 22, and
allow at most one retry only for transient transport/server errors.

| Area | Provider | Status | Next gate |
|---|---|---|---|
| Korean equity history 1995-2009 | FinanceData/marcap secondary | complete, 1995-05-02..2009-12-30; 22 rows quarantined | immutable annual-file/checksum audit only |
| Korean equity history 2010-2019 | KRX Open API primary | complete, 2010-01-04..2019-12-30 | no resume required |
| Korean source verification | pykrx | manual-only | explicit short smoke test only |
| Korean equity price/cap | Financial Services Commission data.go.kr | partial, 2026-08-06 validated | resumable 2020+ backfill under a documented call budget |
| Korean Open API history | KRX Open API | complete, 2010-01-04..2019-12-30 | daily ledger/checkpoint retained |
| Korean short selling | unassigned/pykrx contract reference | draft blocked | live schema verification after restriction |
| Global index | Yahoo | available | routine validation |
| US macro | FRED | available | routine validation |
| KB realtime | KB Securities | blocked | official credential variable contract and fixtures |
| Market breadth | canonical universe + Korean equity prices | 2026-08-06 recalculated, 2 market rows | extend only after point-in-time canonical coverage exists |
| Treasury spread | FRED yields | implemented | recalculate after yield updates |
| `kr_market_liquidity_daily` | FSC/KOFIA public API | complete, 2021-10-26..2026-08-05 | daily incremental |
| `kr_credit_balance_daily` | FSC/KOFIA public API | complete, 2021-11-09..2026-08-05 | daily incremental |
| Futures/options normalized | FSC derivatives public API | partial, 2022-09-19 sample stored | approve multi-thousand-call backfill plan |
| Stock lending datasets | FSC stock-lending public API | partial, 2023-10-05 and 2026-08-05 snapshots | approve resumable backfill plan |
| `kr_equity_dividend` | FSC dividend public API | current snapshot complete, 71,652 source events | define incremental snapshot policy |
| `kr_equity_rights_schedule` | FSC rights public API | contract verified; no normalized backfill | confirm snapshot/event dedup policy before collection |
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
- KB Securities remains read-only and blocked pending a confirmed credential
  contract. Order, correction, cancellation, transfer, and withdrawal APIs are
  out of scope.
- Deferred source/definition work: short selling, VKOSPI, program trading,
  PCR aggregation, and futures-basis roll
  rules. These are not active Data v1 implementation tasks.
