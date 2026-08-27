# Nasdaq Composite official-info current pilot (UR-194)

## Authorization and scope

This active one-shot operation is limited to UR-194's exact public route:
`https://api.nasdaq.com/api/quote/COMP/info?assetclass=index`.

It permits one HTTP GET only, with timeout 10 seconds and retry, redirect,
fallback, Auth, cookie, and environment use all fixed at zero.  It is distinct
from UR-190's completed SOXX ETF route and may not use its ETF/unit contract.

## Pre-transport contract

The durable state is `data/state/nasdaq_comp_info_api_ur194_pilot.json`.  Its
`IN_PROGRESS` state and `get_used=0` are required before transport.  The public
fixed headers are only `Accept: application/json, text/plain, */*` and a normal
browser `User-Agent`; no secrets or session state are permitted.

The runner writes the completed GET count and sanitized transport facts before
any parse.  An HTTP 200 body is retained losslessly under
`data/landing/nasdaq/comp_info_ur194/`, SHA-256 checked on readback, then parsed
from that retained file.  Any non-200, redirect, transport failure, parse
failure, identity/unit/time/session/realtime failure, or source age exceeding
60 minutes is terminal for this route/date and preserves any prior observation.

## Acceptance contract

Only the retained body may establish all of the following:

- exact `COMP` / Nasdaq Composite index identity, not an ETF or another index;
- a finite index-points value with no currency-marker inference;
- an explicit, timezone-aware provider timestamp that is today in KST and at
  most 60 minutes old at retrieval;
- a supported current session and an explicit realtime state.

If all facts pass, a separate `US_INDEX_CURRENT/NASDAQ/COMP` snapshot is
atomically written as provisional, display-only, and PIT-blocked, then replayed
with provider API calls zero.  No GUI, canonical/history, scheduler, Backtest,
or alternate endpoint/symbol is authorized.
