# UR-214 Naver home USD/KRW 19:00 KST window

The only eligible boundary is `2026-08-21T19:00:00+09:00` through before
19:30. The separate `UR-214` manifest, ledger, and Landing root are
`data/state/naver_mobile_home_ur214_activation.json`,
`data/state/naver_mobile_home_ur214_window.json`, and
`data/landing/naver_mobile_home/ur214`.

Only `scripts/manual/collect/collect_naver_home_ur214_usdkrw.py` may run this
window after a Lead reclaim. It makes at most one `https://m.stock.naver.com/`
GET with timeout 10 and redirect/retry/fallback/Auth/cookie/environment zero.
An absent ledger is the one valid initial state; malformed/unreadable,
`ATTEMPTING`, or terminal current-window state is callback/API-zero. The
collector durable-claims before the callback, hash-verifies Landing readback
before parsing, accepts only fresh `FX_USDKRW` with `KRW per USD`, and preserves
the prior projection on failure. KOSPI, KOSDAQ, Gold, and WTI writes are zero.

UR-211's 18:30 terminal ledger is independent and must never be reopened.

The one 2026-08-21 19:01 KST invocation claimed and consumed the GET budget,
then stopped `COMPLETE_FAILURE` with sanitized `ConnectionError` before an HTTP
body. Landing is absent and the prior shared projection is preserved; this
window is terminal/no-repeat and did not refresh USD/KRW.
