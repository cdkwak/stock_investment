# UR-191 Naver mobile-home next-session 30-minute windows

Status: **ACTIVE_FUTURE_DATE_20260824 / PRE_DATE_PROVIDER_CALLS_ZERO / DISPLAY_ONLY_PIT_BLOCKED**

This is a new, date-scoped operation. It does not reopen any UR-167 or UR-176
2026-08-21 window, Landing artifact, state, route, or parser result.

## Exact manifest and schedule

- Target KST date: `2026-08-24` only.
- Route: one serial `GET https://m.stock.naver.com/` per due independent window.
- Window IDs: 09:30, 10:00, 10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30,
  14:00, 14:30, 15:00, and 15:30 KST.
- The 15:30 request is independently bounded to one GET, but its per-identity
  observation may replace prior state only when that same response passes the
  existing strict parser's explicit price and provider-timestamp gate. It never
  infers a close from a sparse page.
- Timeout is 10 seconds. Retry, redirect, continuation, fallback, auth, cookie,
  environment, and scheduler use are all zero.

The secret-free activation manifest is
`data/state/naver_mobile_home_ur191_activation.json`. It accepts only the
listed full KST window IDs. Any earlier date/time returns
`WINDOW_NOT_MANIFESTED` with raw GET count zero before claim or transport.

## Durable operation contract

Use the accepted UR-167 collector/parser unchanged with UR-191-only
`operation_id`, state `data/state/naver_mobile_home_ur191_windows.json`, and
Landing root `data/landing/naver_mobile_home/ur191/`. Every due window writes
its durable `ATTEMPTING` claim before transport, reserves exactly one raw GET,
retains and hash-readbacks a successful body Landing-first, validates
KOSPI/KOSDAQ/USD-KRW independently, and atomically replaces only a fresh valid
display-only/PIT-blocked observation. Prior valid observations survive every
failure or per-record rejection. Gold/WTI remain rejected unless separately
contracted.

Same-window replay is provider API zero. GUI composition remains the accepted
UR-168 local reread/coalescing behavior and is not changed by this operation;
it may invoke only when this exact manifest is active. No canonical/history,
Backtest, GUI source-code, or Windows scheduler mutation is authorized.

Manual execution at a due window only:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_naver_mobile_home_ur191_windows.py `
  --project-root . --confirm-ur191-window
```

## First check

Do not execute before `2026-08-24T09:30:00+09:00`. At that instant, first
read the exact activation manifest and durable UR-191 state. If the 09:30
window is already terminal, replay is API zero; otherwise execute its one GET
only. No early trigger, replay of 2026-08-21, or substitute route is allowed.
