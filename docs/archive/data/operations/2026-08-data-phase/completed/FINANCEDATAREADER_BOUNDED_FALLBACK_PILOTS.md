# FinanceDataReader bounded fallback pilots

Status: `COMPLETED_SINGLE_USE_UR108_PILOTS / EXACT_VIXCLS_ROUTE_ACCEPTED`

This operation is selected only for UR-108 phase 2. It validates exact
FinanceDataReader 0.9.202 routes after the common synthetic fallback controller
has passed. It does not authorize a dependency install, general-purpose
FinanceDataReader use, a production fallback, or a scheduler change.

## Fixed safety boundary

- Project `.env`, credentials, cookies, tokens, authentication headers,
  account data, and authentication responses are never read, printed, copied,
  or summarized.
- Do not reinstall or upgrade FinanceDataReader. Do not repeat the user's
  `005930 / 2026-08-01..10` query.
- Every live request has timeout `10s`, retry `0`, serial execution, a fixed
  route-local budget, and no redirect following.
- Evidence is written only under `artifacts/agent_runs/ur108/pilots/`. Response
  bodies and response headers are never persisted. The call ledger retains only
  provider/upstream identity, public route name, sequence, status, byte count,
  SHA-256, timeout, and retry count.
- Unaccepted output never enters `data/landing`, `data/raw`, `data/normalized`,
  `data/derived`, `data/published`, or `data/state`.
- HTTP 401/403/429, timeout, redirect, malformed/empty payload, schema drift,
  request-budget overflow, unknown upstream, or a nonzero retry opens only that
  route's pilot circuit and stops it. It never selects another provider.
- A live pass proves transport and bounded schema only. Rights, PIT, finality,
  identity, currency/unit, and raw-versus-adjusted semantics remain independent
  gates.

## Exact route plan and budgets

All symbols and periods differ from the retained user query.

| Route id | Exact FDR scope | Actual upstream in 0.9.202 | Live requests | Pilot mode / reason |
|---|---|---|---:|---|
| `kr_listing_kospi` | `StockListing("KOSPI")` | KRX working date + community GitHub cache | 0 | Offline dispatch/schema only. Cache lineage and KRX redistribution chain are not accepted. |
| `international_listing_tse` | `StockListing("TSE")` | Naver overseas listing pagination | 0 | Offline only. Naver terms prohibit unapproved automated use. |
| `krx_delisted_july` | `StockListing("KRX-DELISTING", 2026-07-01..31)` | KRX working date + community GitHub cache | 0 | Offline only. Cache lineage/revision/right gates unresolved. |
| `krx_administrative_current` | `StockListing("KRX-ADMINISTRATIVE")` | KIND current HTML | 0 | Offline only. Undocumented automated route and no dated finality contract. |
| `kr_etf_listing` | `StockListing("ETF/KR")` | Naver Finance ETF list | 0 | Offline only. Naver automated-use restriction and current-universe PIT mismatch. |
| `us_exchange_nasdaq` | `StockListing("NASDAQ")` | Naver overseas listing pagination | 0 | Offline only. This dispatch does not use Nasdaq's official directory. |
| `sp500_current` | `StockListing("S&P500")` | Wikipedia current constituent table | 1 GET | Live bounded current-table pilot; reusable page terms do not make it official S&P PIT membership. |
| `fx_usd_jpy` | `DataReader("USD/JPY", 2026-08-11..12)` | Yahoo query2 chart | 0 | Offline only. Non-API automated access is not accepted. |
| `kr_index_ks11` | `DataReader("KS11", 2026-08-11..12)` | community GitHub KRX-index cache | 0 | Offline only. Cache lineage/finality/rights unresolved. |
| `global_index_dji` | `DataReader("DJI", 2026-08-11..12)` | Yahoo query2 chart | 0 | Offline only. Same provider family and unaccepted automated route. |
| `fred_vixcls` | `DataReader("FRED:VIXCLS", 2026-08-11..12)` | official FRED `fredgraph.csv` | 2 GET maximum | Live bounded pilot. The wrapper performs a probe GET and a second CSV read; both are counted and timeout-wrapped. Compare only with retained official VIXCLS. |
| `kr_daily_000660` | `DataReader("000660", 2026-08-11..12)` | Naver `fchart` | 0 | Offline only. Do not execute the retained 005930 route or a substitute Naver automation. |
| `global_daily_msft` | `DataReader("MSFT", 2026-08-11..12)` | Yahoo query2 chart | 0 | Offline only. Raw/adjusted, session, delist, PIT and rights gates fail. |

Global live cap: **3 GET requests**, zero POST, zero retries. A route-local
failure stops that route. The two independent rights-eligible routes may still
be assessed separately unless the failure indicates a process-wide safety
defect such as an uncounted request, evidence leakage, or timeout bypass; that
defect is a global stop.

## Required validation

### S&P 500 current table

- Exact output columns `Symbol`, `Name`, `Sector`, `Industry`.
- Non-empty, unique non-empty symbols; missing counts and ordering recorded.
- Upstream is `Wikipedia`, not S&P Global and not an exchange.
- Observation time is retrieval time only. No effective membership date,
  historical universe, finality, or PIT claim.
- Result cannot be `automatic_fallback`; maximum recommendation is
  `cross_check_only`.

### FRED VIXCLS

- Exact period 2026-08-11 through 2026-08-12, one `VIXCLS` column, unique
  ascending observation dates, finite numeric non-missing results.
- Raw CSV missing counts are measured before FinanceDataReader's `ffill`. Any
  raw missing value rejects the route; forward-filled output is never accepted.
- Compare date/value equality only with retained
  `data/normalized/fred_vix_daily/year=2026/data.parquet`; do not call the
  official collector again.
- Unit remains VIX index points; currency is not applicable. Dates are FRED
  observation dates with no intraday timestamp. Predictive vintage remains
  blocked.
- Even a match can activate only the exact VIXCLS route and only for a reviewed
  eligible primary parser/schema failure. Same-upstream timeout/HTTP failures
  are not a useful fallback trigger.

## Commands and checkpoint

Offline evidence first (network 0):

```powershell
.\.venv\Scripts\python.exe scripts\manual\pilot\financedatareader_fallback_routes.py `
  --project-root . --mode offline
```

Only after offline evidence passes, run the exact live cap:

```powershell
.\.venv\Scripts\python.exe scripts\manual\pilot\financedatareader_fallback_routes.py `
  --project-root . --mode live --confirm-live-three-get-cap
```

The script refuses to overwrite a completed mode artifact. Resume is
route-specific and requires an explicit new reviewed budget; do not repeat a
completed live route merely to recreate evidence.

If the sandbox blocks network before any provider response and the retained
result proves `provider_gets=0` with only `TRANSPORT_ERROR`, the same three-GET
provider budget may be resumed once outside the sandbox with
`--resume-zero-call-network-block`. The resume writes a distinct immutable
artifact and is forbidden after any provider response.

## Completed outcome

- Offline dispatch/schema evidence covered 11 zero-call routes. Those routes
  remain individually disabled, held, or cross-check-only.
- The completed live resume consumed exactly three successful provider GETs:
  one Wikipedia S&P 500 table request and two FRED VIXCLS requests. Retry count
  was zero and every timeout was 10 seconds. No official provider call was
  repeated for comparison.
- Wikipedia passed its bounded current-table schema but remains
  `cross_check_only`; it is neither official S&P membership nor PIT history.
- `FRED:VIXCLS` matched two already-retained official observation dates with
  maximum absolute difference zero. The exact same-upstream parser route is
  accepted only for a direct-FRED primary `SCHEMA_ERROR`. Raw missing values,
  malformed output, empty output, auth/rate/HTTP failures, and call-accounting
  drift fail closed and open only this route's circuit.
- The pilot commands are now no-repeat. Their artifacts remain evidence, not
  production datasets.
