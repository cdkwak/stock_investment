# UR-215 official Nasdaq Composite and S&P 500 chart pilot

Status: `TERMINAL_ROUTE_SCOPED_NUMERIC_FREE / COMP_1_SPX_1_CONSUMED`.

Only these literal, separate routes are authorized, serially: COMP
`https://api.nasdaq.com/api/quote/COMP/chart?assetclass=index` and SPX
`https://api.nasdaq.com/api/quote/SPX/chart?assetclass=index`. Each route has a
durable preclaim and one GET maximum, timeout 10, retry/redirect/fallback/Auth/
cookie/`.env` zero. HTTP-200 body is immutable Landing-first and SHA-256 read
back. A failed route is terminal/no-repeat without affecting the other route.

No number may project until its retained body explicitly proves exact index
identity, index-points unit/scale, timezone-aware provider timestamp,
session/delay/current status, finite value, ordered unique <=15m timestamps and
today-KST/<=60m freshness. No substitute or `info`/`summary` route is allowed.

## Retained API-zero conclusions

COMP's one captured body has `symbol=COMP` and `company=NASDAQ Composite Index`,
but its `timeAsOf=Aug 20, 2026` is date-only/stale at the 2026-08-21 operation,
chart labels are time-only ET, and it lacks an explicit index-points,
session/delay/current contract. SPX's one body is `data=null` with Nasdaq
`rCode=400 / Symbol not exists`. Both are terminal numeric-free/no-repeat;
neither result generalizes to Nasdaq or a different route.
