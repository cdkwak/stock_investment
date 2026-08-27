# UR-148 Naver 005930 current polling pilot

Status: **FAILED_BOUNDED_PRE_RESPONSE_TRANSPORT_20260821 / NO_REPEAT**

The only route is one unauthenticated request:

```text
GET https://polling.finance.naver.com/api/realtime/domestic/stock/A005930
```

It is a distinct script-backed polling route, not UR-129's FDR/Naver daily
`fchart` request, UR-145's mobile `000660/basic` request, or UR-147's combined
index polling endpoint. The endpoint has no verified official public API or
redistribution contract, so any valid value remains personal-display-only and
PIT-blocked.

The response must contain exactly one row with `cd=A005930`, `mks=KOSPI`,
`ms=OPEN`, positive finite `nv`, and `dt=YYYYMMDDHHMMSS` on 2026-08-21 KST,
nonfuture and <=60 minutes old. `mks=KOSPI` is required to reject NXT or an
unlabelled/blended venue. The accepted route maps its exact domestic KOSPI
identity to `KR_EQUITY_CURRENT/XKRX/005930`, `KRW per share`; no unit, session,
or timestamp is inferred from retrieval.

- One raw GET maximum; timeout 10 seconds; retry/redirect/fallback zero.
- Auth, cookie, `.env`, credential and header access: zero.
- Exclusively create `data/state/naver_005930_current_polling_ur148_20260821.json`
  before transport and reserve the raw GET before invocation. Existing state is
  terminal no-repeat.
- Retain successful 200 body bytes first at
  `data/landing/naver_005930_current_polling/ur148_20260821/<sha256>/`; never
  retain failure body or response headers.
- On full acceptance, atomically write only
  `data/state/current_observations/naver_005930_current_polling.json` and prove
  API-zero replay. No GUI, scheduler, history, canonical, Backtest, account or
  order change is authorized.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\naver_005930_current_polling_pilot_ur148.py `
  --project-root . --confirm-live-005930
```

## Completed outcome

The sole GET was durably reserved and invoked at 2026-08-21 KST, then stopped
with sanitized `ConnectionError` before an HTTP response. It consumed the raw
operation budget (`1/1`); no successful Landing, provider timestamp, schema
validation, projection or display value exists. The terminal checkpoint returns
API-zero `NO_REPEAT`. Do not retry this exact route/date/identity. See
[`UR-148 result`](../../../artifacts/agent_runs/ur148/naver_005930_current_polling_result_20260821T134131+0900.md).
