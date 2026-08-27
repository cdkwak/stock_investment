# UR-244 Toss Korean-equity future 30-minute windows

Status: `ACTIVE_FUTURE_MANIFEST_ONLY_20260824 / PROVIDER_CALLS_0`.

This is the future-only display lane for exact Toss
`GET /api/v1/prices?symbols=000660` and
`GET /api/v1/prices?symbols=005930`, on verified literal XKRX session date
2026-08-24 KST. It is distinct from and must not repeat UR-239, UR-240, or
UR-241. It never writes GUI, canonical, history, or Backtest layers.

The immutable activation manifest permits only 25 KST half-hour boundaries:
08:00 through 20:00 inclusive. A process may run only its current half-open
`[boundary, boundary+30m)` manifest key. Past keys are never selected or
backfilled, future keys are never selected early, and an inactive CLI is API
zero without constructing a runtime client.

For each eligible identity, in fixed serial order `000660`, then `005930`, the
durable per-window/per-identity claim is written and read back before transport.
The fixed budget is OAuth `<=1` plus business GET `<=1`, timeout10, and
retry/redirect/fallback zero. A terminal or orphaned claim is never called
again. Successful bodies are retained to Landing before hash/readback parsing;
projection is isolated, atomic, display-only/PIT-blocked, prior-preserving, and
immediately API-zero replayed. No headers, cookies, auth material, or `.env`
content is retained.

During every non-final window, require exact six-digit identity, currency KRW,
typed unit `KRW per share`, expected KST date, aware provider timestamp, and
source age no more than 60 minutes. The only 20:00-window exception accepts an
exact 19:55:00–20:00:00 KST row with absent provider venue/session as
`TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW`, `venue_inferred=true`,
`NOT_LIVE`; it is not provider-declared NXT and does not satisfy a live-current
gate.

At 2026-08-21 KST this runbook authorizes only manifest/code preparation. The
first possible provider pre-call checkpoint is 2026-08-24 08:00 KST after
re-reading this runbook, the matching Data Status row, immutable manifest, and
UR-244 ledger. It must state the exact current boundary and the remaining two
independent OAuth/business budgets before either serial call.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect\collect_toss_equity_ur244_windows.py --project-root . --confirm-ur244-window
```

The CLI is scheduler-safe but does not install, modify, or depend on an OS
scheduler. It constructs a Toss runtime client only after the active manifest
and durable preflight select an unclaimed current identity.
