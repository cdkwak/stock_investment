# KB Securities source notes

## Official reference and authentication

- [KB Securities OpenAPI portal](https://openapi.kbsec.com/)

The project uses environment variable names `KBSEC_BASE_URL`, `KBSEC_APP_KEY`,
and `KBSEC_APP_SECRET`. Values, token calls, Authorization headers, account
identifiers, and full business responses must never be logged or documented.

## Accepted role

KB Securities currently provides a read-only `IVSA0070` market snapshot. The
seven retained slices include Korean breadth/index cross-checks and global or
market-summary fields. Their accepted role is a provisional current snapshot or
cross-check, not canonical daily history.

## Required labels and limits

- Always show `provider=KB`, capture time, slice market date, and semantic
  status together.
- Capture date/time is not accepted as market date when the slice does not prove
  one.
- `DATE_UNRESOLVED` and unit-review states suppress canonical promotion.
- KB fields never fill KRX, Toss, Yahoo, or FRED history and are never averaged
  with those providers.
- OAuth and API credentials stay in environment variables and tokens stay in
  memory. Authentication payloads, headers, and responses must not be logged.
- The integration is read-only. Do not add order, correction, cancellation,
  transfer, or withdrawal endpoints.

## Runtime route

- Contract: `src/stock_data/contracts/kbsec_snapshot.py`
- Provider: `src/stock_data/providers/kbsec/market_summary.py`
- Snapshot model: `src/stock_data/contracts/current_snapshot.py`
- Operation: [KB Securities Daily Market Snapshot](../../operations/KBSEC_DAILY_MARKET_SNAPSHOT.md)
- Semantic authority: [KB Snapshot Contract](../../research/active/KBSEC_SNAPSHOT_CONTRACT.md)
- Dashboard role: [Dashboard Daily Source Routing](../../../gui/DASHBOARD_DAILY_SOURCE_ROUTING.md)

The scheduled path is bounded by the provider lock and the documented OAuth +
business-call cap. Reuse the existing client and normalizer; do not guess
additional fields or endpoints from similarly named brokerage APIs.

## Safe read example

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\diagnostic\smoke_kbsec_ivsa0070.py --help
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_kbsec_daily_snapshot.py --help
```

Only `IVSA0070` is documented here. Project/Data Status permits agents to add
and call other market-data and read-only account families with existing
credentials once their exact endpoint, schema, identifier redaction, and
valid-empty behavior are verified. This guide provides no authority for order,
transfer, withdrawal, purchase, contract-acceptance, or other broker mutation
endpoints.


## Verified read-only transaction history (2026-09-05)

`POST /api/v1/swqa2301` (거래내역 조회) answered with the documented shape on the
live account: body `strt_dt`/`end_dt` as `YYYYMMDD`, `inq_clsf` `"1"`, the other
fields empty/`"0"` as in the official sample; six rows per page, `dataBody.nxt_key`
carries the continuation token (blank when finished), rows in `dataBody.Record1`
with `dl_dt`, `dl_typ_cd`, `smry_typ_cd`, `smry_nm` (e.g. 타사대체 입고, 주식장내매수,
오픈뱅킹 입금, 전자금융입금, 쿠폰 입금, 예탁금이용료 입금), `is_nm`, amounts and the tax
fields `incm_tx`/`rsdnt_tx`. 2025-01-01..2026-09-04 needed 34 calls (201 rows).
Dividends would appear as their own 요약 rows; none existed on the account at the
time. `SWQM2412` (row detail by `dl_sq`) and `SRQM3051` (해외 권리내역, `rgt_clsf`
`"1"`) follow the samples under `docs/archive/.../kb/official/samples/`.
Amounts, sequence numbers and account identifiers must never be logged; the
cash-flow lane may write amounts only into identifier-free Landing and the local ledger.

## KB transaction cash-flow daily lane

- Dataset/contract: `kbsec_transactions_daily`,
  `src/stock_data/contracts/kbsec_transactions.py` v1.
- Provider: `src/stock_data/providers/kbsec/transactions.py`; it reuses the
  existing in-memory KB OAuth client and only calls read-only `SWQA2301`.
- Lane: `KB_TRANSACTIONS_DAILY`, due daily at 07:20 KST after the 07:00 Toss and
  07:10 KB balance snapshots. It is `MANUAL_READY` with automation disabled
  until the coordinator reviews one live run.
- Window: prior calendar day back through seven overlapping days, extended to
  cover any gap after the last retained row; the first run starts 2025-01-01.
  Pagination follows `dataBody.nxt_key`, accepts at most six rows per page, and
  stops after at most 40 page calls.
- Privacy/storage: every page is projected to an identifier-free Landing file
  before classification. Raw-row SHA-256 is the idempotency key. Amounts exist
  only in those Landing pages and `artifacts/local_user/cash_flows.json`; state,
  receipts, documentation, and logs contain no amounts or direct identifiers.
- Ledger entries retain the existing JSON v1 shape and use `account="kb_auto"`.
  Existing manual entries are not rewritten; `OTHER` rows remain in Landing and
  identifier-free state only, so buys, sells, and interest do not alter returns.

Provider-free plan:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m stock_data.orchestration.kbsec_transactions_daily --dry-run
```

First coordinator-run live command:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m stock_data.orchestration.kbsec_transactions_daily --confirm-live
```
