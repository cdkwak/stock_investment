# UR-242 inferred NXT-close local readiness projection

Status: `TERMINAL_CONSUMED_UR242_API_ZERO`

This one-use operation is API-zero. It reads only the canonical 64-row
readiness CSV and the two exact typed local Toss close projections:
`toss_000660_nxt_session_close_ur240.json` and
`toss_005930_nxt_close_ur241.json`. It may update only their mapped Korean
equity rows through the existing allowlisted local projector; no provider,
worker, scheduler, history, canonical, or Backtest action is permitted.

Admission is route-local and fail-closed: same KST date, provider timestamp in
the inclusive `[19:55, 20:00]` KST close window, exact identity/unit/route and
typed display-only/PIT-blocked contract. The accepted rendering status is
`NXT_SESSION_CLOSE_INFERRED` with visible label
`NXT 마감(시간창 추론) HH:MM:SS`. It is not live/realtime and does not claim a
provider-declared venue/session. Before 20:00, these post-close-only routes are
numeric-free because no active-session start contract is available; on the next
KST date they are numeric-free again.

The projector required explicit confirmation, preserved a durable preimage, and
atomically replaced/read back the UTF-8 RFC4180 CSV at
`2026-08-21T22:51:37.1028973+09:00`. Its preimage was 제거됨
(backup/repo-cleanup-phase2-20260903 브랜치에 보존).
The manifest is now terminal; any replay is API-zero and no-write.
