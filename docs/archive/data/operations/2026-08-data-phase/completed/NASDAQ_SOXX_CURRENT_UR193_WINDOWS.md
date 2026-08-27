# UR-193 Nasdaq SOXX current-observation windows

Status: `WAITING_NEXT_WINDOW_20260821_2200 / DISPLAY_ONLY / PIT_BLOCKED`.

This is a distinct time-window operation, not a repeat of the retained UR-190
17:08 KST GET. It permits only `https://api.nasdaq.com/api/quote/SOXX/info?assetclass=etf`, with
the exact fixed public `Accept: application/json, text/plain, */*` and
`User-Agent: Mozilla/5.0` headers. There is one GET at each independently
manifested KST boundary: 17:30, 18:00, 18:30, 19:00, 19:30, 20:00, 20:30,
21:00, 21:30, 22:00, 22:30, 23:00, and 23:30 on 2026-08-21.

Every window has timeout 10 seconds and retry, redirect, fallback, Auth,
cookie, and `.env` budgets of zero. `data/state/nasdaq_soxx_ur193_windows.json`
is atomically preclaimed before transport. A terminal or orphaned claim is
never retried. A successful body is immutable Landing first under
`data/landing/nasdaq/soxx_info_ur193/`, SHA-256 read back, then parsed only by
the UR-190 SOXX/ETF/NASDAQ-GM `$`-USD-per-share parser. Promotion may replace
only the display-only/PIT-blocked `nasdaq_soxx_info_current.json` observation
when the provider timestamp is today KST and no more than 60 minutes old at the
window execution clock; every rejection preserves the prior envelope. The
same-window replay is API zero. No canonical, history, scheduler, or GUI work
is authorized.

Operational eligibility is local to UR-193: an aware KST clock selects the
latest manifested half-hour boundary only while `[boundary, next boundary)` is
current. The ledger key is the selected boundary while `attempted_at_utc` is the
actual attempt clock; strict source today-KST/<=60-minute validation uses the
actual clock. A terminal/orphan claim, malformed ledger, pre-first time, or
after-final time is API zero/fail-closed. Once a half-open interval ends, that
boundary is expired and is never backfilled.

## Executed evidence

The independently manifested `2026-08-21T17:30:00+09:00` window completed as
`COMPLETE_ACCEPTED`: GET `1/1`, Landing SHA-256
`29870de7120c9525309f8b2e0c7708170fb1ef58130f13ce5045affab9c3f901`, provider
timestamp `2026-08-21T08:28:00+00:00`, and API-zero replay. Its terminal ledger
entry is immutable; remaining windows remain independently unattempted.

The independently manifested `2026-08-21T18:30:00+09:00` window completed
`COMPLETE_ACCEPTED`: GET `1/1`, Landing SHA-256
`d202d3e484ad5b4babf417b8d17bbe8d398babf3d09ac3ce926b8ff9fcdc6516`, provider
time `2026-08-21T09:31:00+00:00`, atomic typed projection readback, and API-zero
replay. It is terminal/no-repeat.

The 18:00 window was not invoked until 18:01 KST, so the exact-manifest
collector returned `WINDOW_NOT_MANIFESTED / raw_gets=0 / API-zero replay=0`.
Its durable key is now terminal `EXPIRED_API_ZERO_NO_BACKFILL`, with zero
reserved/invoked/completed requests and an aware expiry decision timestamp.
That boundary is never backfilled; CLI and GUI both return API zero/no runner
against it.

The independently manifested `2026-08-21T19:00:00+09:00` window completed
`COMPLETE_ACCEPTED`: GET `1/1`, Landing SHA-256
`32db8f4a08a46a9c0193343b2f09bbf53a7b6fa4083c64347d8662b943a12826`, provider
time `2026-08-21T10:00:00+00:00`, strict `529.0132 USD per share` display-only/
PIT-blocked projection, atomic readback, and immediate API-zero replay. This
window is terminal/no-repeat; the next independent window is 19:30 only.

The independently manifested `2026-08-21T19:30:00+09:00` window completed
`COMPLETE_ACCEPTED`: GET `1/1`, Landing SHA-256
`fd678154f5f231e22742ac228b0f7150298930c9866dba3236813c2b6aec5607`, provider
time `2026-08-21T10:30:00+00:00`, strict `529.32 USD per share` display-only/
PIT-blocked projection, atomic readback, and immediate API-zero replay. This
window is terminal/no-repeat; the next independent window is 20:00 only.

The independently manifested `2026-08-21T20:00:00+09:00` window completed
`COMPLETE_ACCEPTED`: GET `1/1`, Landing SHA-256
`f82abc7dfa8ee2006fec595dd829fd4351d6f3870161355843233a4dc0eee98f`, provider
time `2026-08-21T11:01:00+00:00`, strict `527.46 USD per share` display-only/
PIT-blocked projection, atomic readback, and immediate API-zero replay. This
window is terminal/no-repeat; a Lead may consider only the independent 20:30
key after its time window opens and a local ledger read proves it unattempted.

The independently manifested `2026-08-21T20:30:00+09:00` window completed
`COMPLETE_ACCEPTED`: GET `1/1`, Landing SHA-256
`ab6e9ade4c1d705581c29ebdc448fb42c482cd5c86db056167eb11e74d1b97cd`, provider
time `2026-08-21T11:30:00+00:00`, strict `529.5883 USD per share` display-only/
PIT-blocked projection, atomic readback, and immediate API-zero replay. This
window is terminal/no-repeat; a Lead may consider only the independent 21:00
key after the new half-open window opens and a local ledger read proves it
unattempted.
