# UR-153 Naver mobile-basic 000660 30-minute current collector

Status: **FIRST_LATER_WINDOW_FAILED_PRE_RESPONSE_20260821T133000_KST / NO_REPEAT**

This operation activates only UR-145's accepted, undocumented mobile-basic
route `GET https://m.stock.naver.com/api/stock/000660/basic` for
`KR_EQUITY_CURRENT/XKRX/000660`. It preserves UR-145's exact KS/KOR/domestic/
Asia-Seoul, `0900..1530`, zero-delay, `OPEN`, finite `KRW per share` and
explicit fresh `localTradedAt` contract. It remains personal display-only,
PIT-blocked and redistribution-unverified.

The consumed UR-145 pilot window is `2026-08-21T13:00:00+09:00` and is never
called again. The first eligible later KST window is `13:30`; only that window
may make one GET in this task. Every window is durably claimed before transport
factory construction, reserves one GET, fails closed for orphan `ATTEMPTING`
records and uses a same-volume process lock against overlap.

- One GET per distinct later window, timeout <=10 seconds; retry, redirect,
  fallback, auth, cookie and environment access are zero.
- Successful 200 body only is immutable Landing-first under
  `data/landing/naver_mobile_basic/ur153_000660_30m/`; failure bodies/headers
  are not retained.
- The UR-145 store `data/state/current_observations/naver_web_000660_current.json`
  receives an atomic route-scoped projection only after full validation. Typed
  failure opens its circuit and preserves the prior valid value; a later normal
  window can close it only on full success. API-zero replay is required.
- No GUI, OS scheduler, canonical/history/Published, Backtest, account or
  order action is authorized.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_naver_mobile_basic_30m.py `
  --project-root . --confirm-live-000660-window
```

## Completed first later-window outcome

The authorized `13:30` KST window reserved and invoked its sole GET, then
stopped with sanitized `ConnectionError` before an HTTP response. It consumed
raw GET `1/1`, retained no body/Landing, atomically preserved UR-145's valid
observation, and opened the route circuit with `NAVER_TRANSPORT_ERROR`. The
same window now returns API-zero `NO_REPEAT`. It must not be retried. A future
window needs separate authorization; it may close the circuit only on complete
validation. See
[`UR-153 result`](../../../artifacts/agent_runs/ur153/naver_mobile_basic_000660_30m_result_20260821T135123+0900.md).
