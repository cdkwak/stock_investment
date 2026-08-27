# UR-152 Naver mobile-basic 005930 current pilot

Status: **FAILED_BOUNDED_PRE_RESPONSE_TRANSPORT_20260821 / NO_REPEAT**

One exact request is authorized: `GET https://m.stock.naver.com/api/stock/005930/basic`.
It is an undocumented public-web route, distinct from UR-145's `000660` mobile
identity and UR-148's failed polling path. Its strict accepted contract reuses
UR-145 unchanged: exact `itemCode=005930`, KS/KOR/domestic/Asia-Seoul exchange
contract, zero delay, `0900..1530` regular session, `marketStatus=OPEN`, finite
`closePrice` as `KRW per share`, and explicit `localTradedAt` on 2026-08-21 KST,
nonfuture and <=60 minutes old. Unknown/NXT/nonzero-delay/nonregular values are
numeric-free; retrieval time is never a source time.

The cap is one raw GET, timeout 10 seconds, retry/redirect/fallback/auth/cookie/
environment zero. Exclusively create
`data/state/naver_mobile_basic_005930_ur152_20260821.json` before transport and
reserve the request before invocation. Successful 200 bytes only land at
`data/landing/naver_mobile_basic/ur152_005930_20260821/<sha256>/` before parsing.
Only an accepted row may atomically write
`data/state/current_observations/naver_mobile_basic_005930.json` and prove
API-zero replay. No GUI, scheduler, canonical/history, Backtest, account, or
order action is authorized.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\naver_mobile_basic_005930_pilot_ur152.py `
  --project-root . --confirm-live-005930
```

## Completed outcome

The one GET was invoked after durable reservation and stopped with sanitized
`ConnectionError` before any HTTP response. It consumed raw GET `1/1`; no
Landing, timestamp, validation, projection or display value exists. Its state
replays API-zero `NO_REPEAT`; do not retry this exact route/date/identity. See
[`UR-152 result`](../../../artifacts/agent_runs/ur152/naver_mobile_basic_005930_result_20260821T134436+0900.md).
