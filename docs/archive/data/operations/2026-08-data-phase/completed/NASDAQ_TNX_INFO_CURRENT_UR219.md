# UR-219 Nasdaq-hosted TNX information current pilot

Status: `TERMINAL_ROUTE_SCOPED_NUMERIC_FREE / TNX_INFO_1_CONSUMED`.

The sole authorized route is
`GET https://api.nasdaq.com/api/quote/TNX/info?assetclass=index`. It is distinct
from UR-201's consumed Nasdaq TNX HTML page. UR-219 records an isolated durable
claim before one literal GET with timeout 10 seconds; retry, redirect, fallback,
Auth, cookie, and `.env` use are zero. It never invokes an alternate endpoint or
provider.

Only an HTTP-200 body is retained Landing-first, SHA-256 read back, and reviewed
at API zero. Numeric promotion is permitted only if that retained body directly
proves the Cboe 10-Year Treasury Note Yield Index/TNX identity, yield-percent
unit/scale, timezone-aware provider timestamp, session/delay/realtime state, a
finite value, and today-KST/source-age<=60 minutes. A full pass alone may create
one atomic display-only/PIT-blocked TNX observation and immediate API-zero replay.
Every missing condition is terminal numeric-free with prior preservation.

No FRED, Yahoo, Cboe, ETF, or Treasury par-yield substitute is in scope. UR-201
HTML state and Landing are immutable and never read, changed, or resumed here.

## Retained API-zero conclusion

The sole captured response is 142 bytes, SHA-256
`cbe508b6af79789851ca0053cc7e528bbb2e5a443696388c74b51577a052136f`, and has
`data=null` with Nasdaq `rCode=400 / Symbol not exists`. It establishes no TNX
identity, value, yield-percent scale, provider timestamp, or session/delay/
realtime state. Retained-body review terminalized the isolated route at API zero;
immediate replay returned `NO_REPEAT / raw_gets=0`. No display observation was
created. This exact result does not generalize to Nasdaq, TNX, Cboe, FRED, Yahoo,
or another endpoint.
