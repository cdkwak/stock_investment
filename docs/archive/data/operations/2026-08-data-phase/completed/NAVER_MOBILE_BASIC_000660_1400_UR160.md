# UR-160 Naver mobile-basic 000660 exact 14:00 KST window

Status: **FAILED_PRE_RESPONSE_EXACT_WINDOW_2026-08-21T14:00:00+09:00 / NO_REPEAT**

Only the already accepted UR-153 collector may run, and only when its local KST
window id is exactly `2026-08-21T14:00:00+09:00`. The earlier `13:00` pilot and
terminal `13:30` window are immutable/no-repeat. The selected request remains
`GET https://m.stock.naver.com/api/stock/000660/basic`, one raw GET maximum,
timeout 10 seconds, retry/redirect/fallback/auth/cookie/environment zero.

The collector preserves its full UR-153/UR-145 contract: exact 000660,
KS/KOR/domestic/Asia-Seoul regular session, delay zero, `OPEN`, finite KRW per
share, explicit today-KST `localTradedAt` no older than 60 minutes. It durable-
claims the 14:00 window before transport, uses successful-body Landing-first,
atomically preserves/replaces only the typed current observation/circuit and
proves same-window API-zero replay. No GUI, scheduler, history/canonical,
Backtest, environment/credentials, account or order action is allowed.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_naver_mobile_basic_30m.py `
  --project-root . --confirm-live-000660-window
```

## Completed exact-window outcome

The single 14:00 KST request was durably claimed/reserved and invoked, then
stopped with sanitized `ConnectionError` before an HTTP response. It consumed
raw GET `1/1`; Landing/new validation/projection was therefore unavailable.
The previous UR-145 value remains, the route circuit remains open, and the
same window replays at API `0`. Do not retry it. See
[`UR-160 result`](../../../artifacts/agent_runs/ur160/naver_mobile_basic_000660_1400_result_20260821T140023+0900.md).
