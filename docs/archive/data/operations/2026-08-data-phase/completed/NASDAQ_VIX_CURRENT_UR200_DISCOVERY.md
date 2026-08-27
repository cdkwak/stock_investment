# UR-200 official Nasdaq VIX route discovery

Status: `TERMINAL_OFFICIAL_HTML_HTTP302_NO_REPEAT / NUMERIC_FREE`.

The only first operation is one exact HTML GET to
`https://www.nasdaq.com/market-activity/index/vix`, with fixed non-sensitive
`Accept` and `User-Agent` headers, timeout 10, and retry/redirect/Auth/cookie/
`.env` zero. The durable ledger is written before transport; a repeated or
orphaned page claim fails closed. A HTTP-200 body is immutable Landing under
`data/landing/nasdaq/vix_ur200_discovery/` and SHA-256 read back before local
inspection. This is discovery only: quote/chart API calls are zero.

At most one directly referenced official asset GET may follow, only if the
captured page itself identifies its exact URL. A VIX current route needs exact
identity, direct endpoint/parameters, response schema, index-points unit,
timezone-aware provider timestamps, session/delay semantics, and native
interval no coarser than 15 minutes. If any fact is absent, stop numeric-free;
do not infer from SOXX or call Cboe, Yahoo, FDR, or previously consumed routes.

## Result

At `2026-08-21T17:38:28.428446+09:00`, the sole official-page GET completed
HTTP 302 with redirect budget zero. The durable preclaim became terminal
`COMPLETE_FAILURE`; no body/Landing was retained for the non-200 result, and
the direct asset and quote/chart budgets remain zero. Therefore no exact VIX
identity-to-intraday endpoint/schema/time/unit/session binding exists and the
route is numeric-free/no-repeat.
