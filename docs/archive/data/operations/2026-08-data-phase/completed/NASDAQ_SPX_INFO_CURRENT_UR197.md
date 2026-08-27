# Nasdaq-hosted S&P 500 info current pilot (UR-197)

Only `https://api.nasdaq.com/api/quote/SPX/info?assetclass=index` is authorized.
The durable state `data/state/nasdaq_spx_info_api_ur197_pilot.json` must be
`IN_PROGRESS` with GET `0/1` before the single timeout-10 request. Retry,
redirect, fallback, Auth, cookie, and environment use are zero. Fixed public
headers are `Accept: application/json, text/plain, */*` and `User-Agent: Mozilla/5.0`.

A successful body must be Landing-first/hash-read back before parse. Acceptance
requires direct SPX/S&P 500 index identity, finite index points without ETF or
currency-marker inference, a timezone-aware provider timestamp today KST and
<=60 minutes old, supported session, and explicit realtime state. Failure is
terminal no-repeat. Success alone would permit a distinct provisional,
display-only/PIT-blocked atomic observation and API-zero replay; GUI,
history/canonical, scheduler, and Backtest remain forbidden.
