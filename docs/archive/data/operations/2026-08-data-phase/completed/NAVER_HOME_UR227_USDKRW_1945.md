# UR-227 Naver mobile-home USD/KRW urllib 19:45 KST window

The independent one-use key is `2026-08-21T19:45:00+09:00` only in the
half-open window `[19:45, 20:00)`. The active manifest, durable ledger, and
Landing root are `data/state/naver_mobile_home_ur227_activation.json`,
`data/state/naver_mobile_home_ur227_window.json`, and
`data/landing/naver_mobile_home/ur227`.

Only `scripts/manual/collect/collect_naver_home_ur227_usdkrw.py` may execute
the route after the boundary. It uses one direct Python standard-library
`urllib.request` GET to `https://m.stock.naver.com/`, timeout 10 seconds, fixed
public User-Agent and Accept, disabled redirects, an explicit empty proxy map,
and no cookie/session/authentication/environment inspection. Request cap is
one; retry, redirect, and fallback caps are zero.

The CLI allows an absent ledger only as a new eligible initial state. A missing
or malformed manifest, malformed/unreadable ledger, `ATTEMPTING`, and every
terminal key are callback/API-zero. It durably preclaims before the GET,
captures a successful body Landing-first, SHA-256 verifies readback, and uses
the strict accepted mobile-home parser. Only a realtime, today-KST,
source-age-at-most-60-minute `FX_USDKRW` observation in `KRW per USD` may be
projected; KOSPI, KOSDAQ, Gold and WTI writes are zero. Any failure preserves
the existing shared projection. The terminal ledger supports API-zero replay
only. UR-211, UR-214 and UR-221 remain immutable unrelated terminal routes.

## Offline readiness

Focused synthetic coverage verifies exact half-open/date eligibility, durable
preclaim, one callback, Landing readback, FX-only acceptance, malformed/
attempting/terminal API-zero behavior, replay API-zero, and prior preservation.
No provider request is made before the exact boundary.

## Completed result

At `2026-08-21T19:45:09.707723+00:00`, the sole `urllib.request` transport
attempt durably consumed GET `1/1` and stopped `COMPLETE_FAILURE` with the
sanitized failure class `URLError` before an HTTP response. Landing files are
zero; no schema validation, FX projection, or non-FX write occurred. The prior
shared projection remains byte-identical (SHA-256
`4a15a42658aefe7b2ad45f51028f782f1ad37a5e3cbc1bccc404154ed21710ff`). The
immediate terminal CLI replay at `2026-08-21T19:45:24.015574+00:00` was
`PREFLIGHT_API_ZERO`, raw GETs zero. This independent route/window is
terminal/no-repeat; its result is not provider-wide.
