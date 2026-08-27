# Stooq Nasdaq Composite current candidate pilot (UR-196)

UR-196 first permits only one public Stooq text-search document request:
`https://stooq.com/q/?s=nasdaq%20composite`. It uses the exact identity phrase,
not a presumed provider code such as `^NDQ`.

The durable state `data/state/stooq_nasdaq_current_ur196_pilot.json` requires
`SEARCH_IN_PROGRESS`, search GET `0/1`, and quote GET `0/0` before transport.
Timeout is 10 seconds; retry, redirect, fallback, Auth, cookie, and environment
use are all zero. A successful document is Landing-first with hash/readback.

Only a direct provider binding of Nasdaq Composite identity to a Stooq symbol,
plus direct smallest-interval/timezone/session/delay and personal-display terms,
can enable a separately written one-GET quote contract. Without every fact, the
quote cap stays zero and numeric acceptance is forbidden. No presumed code,
Nasdaq/Yahoo/FDR/Naver request, GUI, canonical/history, scheduler, or Backtest
work is permitted.
