# UR-221 Naver home USD/KRW 19:30 KST window

The only eligible key is `2026-08-21T19:30:00+09:00` through before 20:00.
UR-221's separate manifest, ledger, and Landing root are
`data/state/naver_mobile_home_ur221_activation.json`,
`data/state/naver_mobile_home_ur221_window.json`, and
`data/landing/naver_mobile_home/ur221`.

Only `scripts/manual/collect/collect_naver_home_ur221_usdkrw.py` may run after
a Lead reclaim. It permits at most one timeout-10 GET with retry, redirect,
fallback, Auth, cookie, and environment zero. An absent ledger is the valid
initial state; malformed/unreadable, `ATTEMPTING`, and terminal state are
callback/API-zero. It durable-claims before the callback, hash-verifies Landing
readback, accepts only fresh realtime `FX_USDKRW` in `KRW per USD`, and
preserves prior observations on failure. KOSPI/KOSDAQ/Gold/WTI writes are zero.

UR-211 and UR-214 are independent terminal routes and must never be reopened.

The one 2026-08-21 19:31 KST invocation consumed its GET budget and stopped
`COMPLETE_FAILURE` with sanitized `ConnectionError` before an HTTP body. No
Landing or projection exists; the prior shared projection is preserved. This
UR-221 window is terminal/no-repeat and did not refresh USD/KRW.
