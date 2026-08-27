# UR-199 Naver Korean-equity next-session windows

Only `2026-08-24` KST 09:30 through 15:30 inclusive, at 30-minute boundaries,
is eligible. The identities run independently and serially in exact order
`000660`, then `005930`, through `m.stock.naver.com/api/stock/<code>/basic`.

Before `2026-08-24T09:30:00+09:00`, `is_active(root, now=...)` is false and
`NaverEquityUr199Runner.run(...)` returns API `0` without constructing a
transport callback or durable claim. The first resume boundary is exactly
`2026-08-24T09:30:00+09:00`; first reread the immutable activation manifest and
the UR-199-only state. Each identity/window is then durably marked `ATTEMPTING`
before its one injected callback. Existing terminal claims return `NO_REPEAT`;
orphaned `ATTEMPTING` claims return `ORPHANED_NO_REPEAT`.

Each future transport must retain a Landing body/hash/readback, apply the
existing strict KS/KOR/domestic, Asia/Seoul, zero-delay, OPEN-session, KRW per
share, source-time and <=60-minute contract, and preserve prior valid state on
failure. Retry, redirect, fallback, Auth, cookie, and environment use are zero.
No 2026-08-21 UR-145/152/161 route/window, GUI, canonical/history, or Backtest
action is authorized.

Supported manual entrypoint: `scripts/manual/collect/collect_naver_equity_ur199_windows.py --project-root . --confirm-ur199-window`. It reads the manifest and ledger before callback construction, creates only exact mobile-basic GET callbacks for unclaimed active identities, and prints sanitized window/status/call counts only.

