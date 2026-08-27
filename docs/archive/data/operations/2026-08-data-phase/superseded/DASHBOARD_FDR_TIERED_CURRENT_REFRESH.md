# Dashboard FDR tiered current refresh

## State

`ACTIVE_UR116_EXACT_ALLOWLIST`

Official API examples and route families are documented in the
[FinanceDataReader repository](https://github.com/FinanceData/FinanceDataReader);
the installed 0.9.202 source remains runtime authority for exact dispatch and
request counting.

The display order is validated native 15-minute observation, then validated
60-minute observation, then FinanceDataReader 0.9.202 daily polling. For a
selected Korean equity, bounded `NAVER:<code>` polling precedes an independently
validated `YAHOO:<code>.KS/.KQ` alternate-client route. `KRX:<code>` remains the
official long-history/detailed EOD candidate and is not repeatedly polled by the
GUI because its reader internally fans out in two-year chunks. Daily fallback
is always labelled `FDR 조회 시점 일봉`; it is never called a realtime bar or
promoted into finalized EOD/Backtest history.

FDR also implements Naver-backed overseas listing and U.S. ETF listing routes.
That is not yet evidence for a Naver overseas *price* dispatch contract: the
installed price reader is daily and its documented examples use Korean numeric
codes. Therefore no overseas Naver price symbol is guessed or called here.
This candidate remains disabled until an exact code path, symbol mapping,
upstream, unit, and usage boundary are established independently.

## Retained first batch result

The single bounded batch consumed ten GETs with retry zero. `SP500`, `NASDAQ`,
`SOXX`, `NQ_FUTURES`, `GOLD`, `WTI`, and a separate Yahoo/FDR `VIX`
observation validated through 2026-08-20. `KOSPI`, `KOSDAQ`, and `USD_KRW`
failed their exact tested routes and were not retried. Six non-VIX observations
are projected into Dashboard display values as `PIT_BLOCKED`; Yahoo/FDR VIX
does not replace the official FRED VIX metric. This proves pollable daily
display fallback only, not 15-minute, 60-minute, or streaming coverage.

## Exact allowlist and live budget

| Dashboard identity | FDR route | Unit |
|---|---|---|
| KOSPI | `YAHOO:^KS11` | index points |
| KOSDAQ | `YAHOO:^KQ11` | index points |
| S&P 500 | `YAHOO:^GSPC` | index points |
| Nasdaq Composite | `YAHOO:^IXIC` | index points |
| SOXX | `YAHOO:SOXX` | USD |
| NQ continuous | `YAHOO:NQ=F` | index points |
| Gold continuous | `YAHOO:GC=F` | USD |
| WTI continuous | `YAHOO:CL=F` | USD |
| VIX | `YAHOO:^VIX` | index points |
| USD/KRW | `YAHOO:KRW=X` | KRW per USD |

Selected Korean-equity current fallback uses exactly one `NAVER:<code>` request
for the active exact identity. The accepted UR-115 `NAVER:000660` observation is
not repeated. `KRX:<code>` is excluded from periodic polling; a future exact EOD
or historical operation must count every internal two-year request separately.

One serial GET per identity, maximum ten GETs for the first bounded batch,
timeout 10 seconds, retry zero. Each route stops independently on HTTP,
rate-limit, empty, schema, date, numeric, unit, or identity failure. Successful
chart bodies are retained; failure bodies and headers are not. No alternate
symbol or route is attempted within the batch.

## Expansion batch A — price-route substitution

This batch is independent of the completed ten-route batch and never repeats
its symbols. It is frozen to `2026-08-14..2026-08-20`, serial execution,
timeout 10 seconds, retry zero, and a global maximum of eight raw HTTP
operations:

| Candidate | Exact route | Expected raw operations | Purpose |
|---|---|---:|---|
| Korean equity via Naver | `NAVER:035420` | 1 GET | selected-equity daily display |
| Same equity via direct KRX | `KRX:035420` | 1 finder POST + 1 data POST | official detailed EOD comparison |
| KOSPI direct index | `KRX-INDEX:1001` | 1 POST | replace failed Yahoo KOSPI route |
| KOSDAQ direct index | `KRX-INDEX:2001` | 1 POST | replace failed Yahoo KOSDAQ route |
| U.S. equity | `YAHOO:MSFT` | 1 GET | overseas daily display |
| U.S. ETF | `YAHOO:IVV` | 1 GET | overseas ETF daily display |
| FX | `YAHOO:EURUSD=X` | 1 GET | non-KRW FX route behavior |

Only response-derived validated frames are retained. No result is promoted by
this pilot. KRX and Naver values are not averaged or silently substituted;
Yahoo-through-FDR remains the same upstream with an alternate client. Snapshot,
listing, financial-statement, and overseas-Naver-price candidates require a
separate batch after this boundary.

## Expansion batch B — bounded snapshots

The second independent batch is frozen to five raw HTTP operations, timeout
10 seconds, retry zero: `KRX/INDEX/STOCK/1001` (one working-day GET plus one
component POST), `NAVER/FINSTATE-2Q/035420` (two GETs), and `ETF/KR` (one GET).
The outputs are candidate evidence only and cannot update Dashboard price bars,
canonical membership, financial-statement history, factors, or Backtest.

`ETF/US` is confirmed in installed code as a Naver-backed overseas ETF listing
route, but the public implementation makes an initial page request and then an
open-ended page loop of up to 100 further requests without a timeout. It is not
executed in this bounded operation and is not suitable for periodic Dashboard
refresh until a page/call contract is implemented and tested.
