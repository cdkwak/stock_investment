# Nasdaq Composite official-summary current pilot (UR-195)

UR-195 authorizes only `https://api.nasdaq.com/api/quote/COMP/summary?assetclass=index`.
It is independent of, and must not repeat, UR-194's `/info` route.

The durable state `data/state/nasdaq_comp_summary_api_ur195_pilot.json` must
show `IN_PROGRESS` and GET `0/1` before transport. The exact budget is one GET,
10-second timeout, and zero retry, redirect, fallback, Auth, cookie, and
environment use. Fixed public headers are only `Accept: application/json, text/plain, */*`
and `User-Agent: Mozilla/5.0`.

A successful body is losslessly retained under `data/landing/nasdaq/comp_summary_ur195/`,
hash-read back, then parsed only from Landing. Acceptance requires exact COMP /
Nasdaq Composite index identity, a finite index-points value with no currency
inference, a dated timezone-aware provider timestamp, a supported realtime
session, and today-KST/source-age <=60 minutes. Failure is terminal for this
route/date and preserves prior state. A successful distinct current observation
would be provisional, display-only/PIT-blocked, atomically read back and replayed
with API zero. GUI, history/canonical, scheduler, Backtest and alternates are forbidden.
