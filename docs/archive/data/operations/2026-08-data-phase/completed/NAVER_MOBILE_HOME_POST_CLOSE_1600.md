# Naver mobile-home exact 16:00 post-close snapshot

Status: `COMPLETED_SINGLE_USE_NO_POST_CLOSE_NUMERIC_20260821 / NO_REPEAT`

This runbook authorizes exactly one new Naver mobile-home HTML GET for the
`2026-08-21T16:00:00+09:00` window. It does not reopen or repeat UR-166/167.

- URL: `https://m.stock.naver.com/`
- Durable window id: `2026-08-21T16:00:00+09:00`
- Raw GET cap 1; serial; timeout 10 seconds; retry/redirect/fallback/auth/cookie
  zero; durable preclaim before transport
- Successful body Landing-first with SHA-256/readback and API-zero replay
- Reuse the exact KOSPI/KOSDAQ/USD-KRW/Gold/WTI identity and unit gates.
  `OPEN/realtime` remains provisional current. A same-day explicit post-close
  state may be accepted only as `POST_CLOSE_SNAPSHOT`, never relabelled
  intraday; it still requires a direct timezone-aware provider timestamp no
  older than 60 minutes. Missing value/time/unit/session preserves prior valid
  observations independently.
- Gold/WTI remain numeric-free unless direct visible unit/contract evidence is
  present in this exact body; no inference from prior pages or exchange specs.
- Display-only/PIT-blocked/local-personal; no GUI integration in this task,
  canonical/history/Backtest/scheduler or redistribution approval.

## Terminal execution result

The one 16:00 window completed at `2026-08-21T07:00:29.913203+00:00` with
durable raw counts `1/1/1`, all retry/redirect/fallback/auth/cookie counts zero,
and Landing SHA-256
`a9da7464601961be4a412a097d0cc2d59128bf3f1a5b10dc9376c9f377f7702e`.

No row met the direct post-close gate: KOSPI/KOSDAQ had no visible price/time,
USD/KRW lacked explicit post-close status, and Gold/WTI lacked direct unit
evidence. Prior 15:01 KOSPI/KOSDAQ and 15:29 USD/KRW observations remain
unchanged; no `POST_CLOSE_SNAPSHOT` row was created. Replay is API zero and
the distinct UR-176 state is terminal no-repeat. No further window is
authorized by this runbook.
