# Data Layer status

Data v1 is frozen at the verified baseline. No Dataset Contract, schema,
canonical-universe rule, normalized/derived/published Parquet, or checkpoint may
change without a separately approved task. The supported CLI is
`scripts/run_data_v1.py`; KRX is skipped by default.

| Area | Provider | Status | Next gate |
|---|---|---|---|
| Korean index/equity/investor history | KRX Open API primary | contract/approval blocked | approved API specification and rate limit |
| Korean source verification | pykrx | manual-only | explicit short smoke test only |
| Korean equity price/cap | Financial Services Commission data.go.kr | partial, 2026-08-06 validated | resumable 2020+ backfill under a documented call budget |
| Korean Open API history | KRX Open API | interface mapped, blocked | AUTH_KEY, product approval, official specs |
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
| 2020+ official equity | FSC stock price/listed APIs | partial, 2026-08-06 sample stored | approve call budget |
| `kr_equity_canonical_universe_daily` | listed-info + price union; master metadata | active, 2026-08-06, 2,763 rows | extend across collected daily coverage |
| `kr_equity_master` | FSC issuance + observed daily identity | active, 2,770 rows; 2,754 issuance-enriched | increment current snapshot without dropping unmatched identities |
| KRX Open API 2010-2019 | KRX Open API | blocked, 401 Unauthorized | approve four API products |

Automated access to `data.krx.co.kr` is disabled. Existing pykrx Parquet data and
checkpoints remain preserved. KB work remains read-only; order APIs are out of scope.

Point-in-time rule: normalized source observations keep their source date unchanged.
Research features/signals observed on trading day T may only be executed from T+1;
daily universe membership must come from that date's canonical universe, never from a
later master snapshot alone.

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

- KRX Open API `stk_bydd_trd` production endpoint is verified from the official
  development specification, but the one-shot 2019-01-02 probe returned HTTP
  401 `Unauthorized API Call`. Product approval remains the gate; no retry or
  backfill is permitted while blocked.
- Automated `data.krx.co.kr` and pykrx collection are disabled. pykrx remains
  only for explicitly requested short manual smoke/comparison/fixture checks,
  with no historical, scheduled, polling, or repair automation.
- KB Securities remains read-only and blocked pending a confirmed credential
  contract. Order, correction, cancellation, transfer, and withdrawal APIs are
  out of scope.
- Deferred source/definition work: 1995-2009 Korean equity history, short
  selling, VKOSPI, program trading, PCR aggregation, and futures-basis roll
  rules. These are not active Data v1 implementation tasks.
