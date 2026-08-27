# UR-147 Naver domestic-index current polling pilot

Status: **FAILED_BOUNDED_PRE_RESPONSE_TRANSPORT_20260821 / NO_REPEAT**

## Exact route and contract

The only authorized route is one unauthenticated combined request:

```text
GET https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ,KPI200
```

It is the script-backed domestic-index polling response used for this bounded
Naver Finance web investigation. It is distinct from UR-145's consumed
`m.stock.naver.com/api/stock/000660/basic` quote and from all FDR/Naver daily
`fchart` paths. Naver has not provided an official public API/reuse contract
for this web endpoint: successful data is only a local personal-display
candidate, PIT-blocked, and redistribution-unverified.

| Identity | Exact code | Value and unit | Required source/session fields |
|---|---|---|---|
| `KR_INDEX_CURRENT / XKRX / KOSPI` | `KOSPI` | `nv`, native `index points` | `dt=YYYYMMDDHHMMSS` KST, `ms=OPEN`, XKRX regular session |
| `KR_INDEX_CURRENT / XKRX / KOSDAQ` | `KOSDAQ` | `nv`, native `index points` | same |
| `KR_INDEX_CURRENT / XKRX / KPI200` | `KPI200` | `nv`, native `index points` | same |

No scaling, base-100 conversion, currency conversion, or retrieval-time
substitution is allowed. Each `dt` must be today KST, no later than retrieval,
and at most 60 minutes old. A missing or invalid row fails only that identity;
the response must nevertheless contain exactly this three-code set.

## Frozen budget and safety

- Date: `2026-08-21` KST; pre-transport XKRX regular-session/date gate.
- Raw provider operations: one combined GET maximum, serial, timeout 10
  seconds, retry/redirect/fallback zero.
- Auth, cookies, `.env`, credentials, headers, accounts, and orders: zero.
- Durable state: `data/state/naver_domestic_index_current_ur147_20260821.json`
  is exclusively created before transport and reserves the sole GET before
  invocation. Existing/attempting/terminal state is no-repeat fail-closed.
- Successful HTTP-200 bytes only are atomically retained before parsing beneath
  `data/landing/naver_domestic_index_current/ur147_20260821/<sha256>/`.
  Failure bodies and response headers are never retained.
- Valid rows may atomically update only
  `data/state/current_observations/naver_domestic_index_current.json` through
  UR-118 storage, then prove an API-zero replay. One raw GET fans out locally;
  its raw cap is recorded separately from the three route-local projections.

No GUI, scheduler, Normalized/Published/canonical/history, Backtest, account,
or order change is authorized.

## Invocation

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\naver_domestic_index_current_pilot_ur147.py `
  --project-root . --confirm-live-domestic-indices
```

## Completed outcome

The sole combined GET was durably reserved and invoked at 2026-08-21 KST, then
stopped with a sanitized `ConnectionError` before an HTTP response. It consumed
the one raw-operation budget (`1/1`) even though no response completed. There
is no successful Landing body, provider timestamp, projection or display value.
The terminal state returns API-zero `NO_REPEAT`; do not retry or expand this
exact combined Naver route. See
[`UR-147 result`](../../../artifacts/agent_runs/ur147/naver_domestic_index_current_result_20260821T133715+0900.md).
