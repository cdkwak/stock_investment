# UR-201 official Nasdaq TNX route discovery

Status: `TERMINAL_OFFICIAL_HTML_HTTP302_NO_REPEAT / NUMERIC_FREE`.

The first and only active operation is one direct HTML GET to
`https://www.nasdaq.com/market-activity/index/tnx`, intended to confirm the
page's explicit Cboe 10-Year Treasury Note Yield Index/TNX identity. It has
timeout 10 and retry/redirect/Auth/cookie/`.env` zero, and is durably claimed
before transport. A HTTP-200 response is Landing-first and SHA-256 read back.
At most one directly page-referenced official asset can follow.

Quote/chart API remains zero unless the captured page/asset explicitly binds
the endpoint and parameters, schema, yield-percent unit/scale, timezone-aware
provider timestamp, session/delay state, and native ordered unique <=15m
interval. Any missing fact stops numeric-free: never substitute ETF, futures,
FRED, daily data, Yahoo, Cboe, SOXX, Composite, SPX, or VIX routes.

## Result

At `2026-08-21T17:42:01.346358+09:00`, the exact official TNX HTML GET
returned HTTP 302. The redirect-zero policy ended the route before body/Landing
capture, direct asset, or quote/chart access. No requested identity, endpoint,
schema, yield scale, timestamp/session, or <=15m series binding was proven;
the result is terminal numeric-free/no-repeat.
