# Global ETF daily onboarding gates

Status: `DAILY_AUTOMATION_ACTIVE / EIGHT_SYMBOL_REGISTRY / SIX_SYMBOLS_NOT_YET_COLLECTED / PIT_BLOCKED`.

`global_etf_price_daily` stores provider-native unadjusted OHLCV and adjusted
close separately. Its single identity authority is
`GLOBAL_ETF_REGISTRY` in `src/stock_data/contracts/global_etf.py`:

| Symbol | Official fund identity | Official exchange | Exposure multiple | Retained state |
|---|---|---|---:|---|
| SOXX | iShares Semiconductor ETF | NASDAQ | 1 | retained through 2026-08-18 |
| EWY | iShares MSCI South Korea ETF | NYSE Arca | 1 | first collected 2026-09-02 |
| SOXL | Direxion Daily Semiconductor Bull 3X Shares | NYSE Arca | 3 | not yet collected |
| TQQQ | ProShares UltraPro QQQ | NASDAQ | 3 | not yet collected |
| QLD | ProShares Ultra QQQ | NYSE Arca | 2 | not yet collected |
| TLT | iShares 20+ Year Treasury Bond ETF | NASDAQ | 1 | not yet collected |
| QQQ | Invesco QQQ Trust, Series 1 | NASDAQ | 1 | not yet collected |
| SPY | SPDR S&P 500 ETF Trust | NYSE Arca | 1 | not yet collected |

Every entry also binds its issuer product page, USD currency, accepted Yahoo
exchange identifiers, ETF instrument type, daily granularity, cadence, and
validation contract. Leverage is explicit contract metadata and is exposed to
the display-independent identity catalog; consumers must not infer it from the
fund name.

The installed 06:10 KST task name
`STOCK_DATA_GLOBAL_ETF_SOXX_DAILY` remains unchanged for compatibility. Its
registry-default invocation now covers all eight symbols. Each symbol prepares
and promotes through an independent immutable Landing capture and whole-dataset
CAS transaction, so one identity or data failure preserves every prior valid
row and does not block valid peers.

SOXX is an ETF, not the SOX index, and neither is a fallback for the other.
EWY is not a Korean equity index and is not interchangeable with KORU. The six
new symbols remain `REGISTERED_NOT_YET_COLLECTED` until their first bounded
capture is reviewed and digest-bound promotion succeeds. Current descriptive
use is allowed after validation; predictive use remains blocked pending a
vintage/finality policy.
