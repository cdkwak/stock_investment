# UR-218 Nasdaq VIX info one-shot

This active operation permits one public GET only:
`https://api.nasdaq.com/api/quote/VIX/info?assetclass=index`. It is distinct
from terminal UR-200 HTML discovery and all Cboe, Yahoo, FRED and futures routes.
Timeout is 10 seconds; retry, redirect, fallback, authentication, cookie and
environment use are zero.

The isolated ledger is `data/state/nasdaq_vix_info_ur218_pilot.json`. Its runner
atomically writes `ATTEMPTING` before the sole GET. Non-200, transport failure,
orphan, malformed ledger and terminal states are no-repeat. Only HTTP-200 bytes
are retained under `data/landing/nasdaq/vix_info_ur218/` and SHA-256 read back
before parsing.

Only retained readback bytes can prove direct VIX spot identity, explicit
index-points unit/scale, timezone-aware provider time today KST within 60
minutes, and explicit session/delay/realtime state. No glyph, retrieval time,
or external Cboe material fills a missing field. A full pass alone may create a
separate atomic display-only/PIT-blocked observation with API-zero replay;
otherwise exact prior bytes are preserved. GUI, canonical/history, Backtest and
scheduler changes are forbidden.

## Terminal execution

At `2026-08-21T09:54:56.505626+00:00`, the isolated runner durably reserved and
invoked its sole GET, then received sanitized `ConnectionError` before any HTTP
response (`1/1/0` reserved/invoked/completed).  No Landing body exists and the
terminal ledger replays `NO_REPEAT / raw_gets=0`.  This exact route/attempt must
not be retried or substituted.
