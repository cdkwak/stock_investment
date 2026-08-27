# Yahoo commodity-futures maximum-history pilot

Status: **DAILY_LANDING_COMPLETE / NORMALIZED_REVIEW_REQUIRED**  
Superseded monthly run: `20260815T053758Z_0bc7f1c4692742c7a9efab5fb707bc66`  
Daily successor run: `20260815T054758Z_ae5fc5aa12cb4109877d033cc4cd5ea2`  
Successor calls: exactly 5, retry zero; no Normalized/state publication

The bounded capture reused the existing Yahoo chart endpoint and immutable public
HTTP capture primitive for exactly `GC=F`, `SI=F`, `HG=F`, `CL=F`, and `BZ=F`.
Every request explicitly used `range=max`, `interval=1d`, `events=history`, and
`includeAdjustedClose=false`. All five HTTP responses were retained before parsing.

Yahoo nevertheless returned `meta.dataGranularity=1mo` for every symbol. The
responses therefore contain monthly, not daily, observations and cannot satisfy a
daily commodity-futures contract. No contract was registered and no Normalized
artifact was written. The five-call authorization was exhausted, so the alternative
existing `period1`/`period2` daily-history mechanism was not queried.

| Symbol | Asset | Returned coverage | Rows | Raw quality observation |
|---|---|---:|---:|---|
| `GC=F` | Gold | 2000-09-01..2026-08-14 | 267 | 4 monthly OHLC relationship anomalies |
| `SI=F` | Silver | 2000-09-01..2026-08-14 | 267 | 2 monthly OHLC relationship anomalies |
| `HG=F` | Copper | 2000-09-01..2026-08-14 | 267 | 3 monthly OHLC relationship anomalies |
| `CL=F` | WTI crude oil | 2000-09-01..2026-08-14 | 267 | April 2020 monthly low `-40.32` preserved exactly |
| `BZ=F` | Brent crude oil | 2007-08-01..2026-08-14 | 197 | no observed monthly OHLC relationship anomaly |

All returned rows had complete OHLCV and unique per-symbol dates. The apparent
OHLC anomalies occur in provider monthly aggregation (for example, a close slightly
above the reported high); they were not repaired or dropped. WTI's retained April
2020 monthly row is open `20.1`, high `29.129999...`, low `-40.32`, close `18.84`,
volume `16,824,885`. It does not retain the daily negative settlement as a separate
row and must not be presented as daily evidence.

Landing evidence:
`data/landing/yahoo/global_commodity_futures_daily/20260815T053758Z_0bc7f1c4692742c7a9efab5fb707bc66/`.
Each provider response has an exact `response.body` and `call.json`; the run has
initial/final manifests. Reopen only under a separately bounded call budget using
explicit `period1`/`period2` daily requests, with `meta.dataGranularity=1d` as a
fail-closed gate. Continuous-series roll, adjustment, and historical-vintage
semantics would remain `VENDOR_CONTINUOUS_FUTURES / CURRENTLY_RECONSTRUCTED`.

## Daily successor

The cause was the interaction of `range=max` with Yahoo's chart service: despite
the requested `interval=1d`, the server coerced maximum-history responses to
monthly bars. The existing provider was minimally changed to use explicit
`period1` and `period2` with `interval=1d`. Parsing now fails closed unless the
response reports `meta.dataGranularity=1d`.

The successor retained five complete daily responses Landing-first. Source nulls,
zero volumes, negative prices, and provider OHLC relationship anomalies are
preserved and classified rather than repaired.

| Symbol | Asset | Daily coverage | Rows |
|---|---|---:|---:|
| `GC=F` | Gold | 2000-08-30..2026-08-14 | 6,597 |
| `SI=F` | Silver | 2000-08-30..2026-08-14 | 6,597 |
| `HG=F` | Copper | 2000-08-30..2026-08-14 | 6,597 |
| `CL=F` | WTI crude oil | 2000-08-23..2026-08-14 | 6,602 |
| `BZ=F` | Brent crude oil | 2007-07-30..2026-08-14 | 4,798 |

Total retained daily rows are 31,191 with zero duplicate `(date, symbol)` keys.
WTI's 2020-04-20 close `-37.630001068115234` and low
`-40.31999969482422` remain exact source values. The new Landing is
`data/landing/yahoo/global_commodity_futures_daily/20260815T054758Z_ae5fc5aa12cb4109877d033cc4cd5ea2/`.

The draft contract and existing Yahoo provider support bounded overlap upserts,
but no Normalized artifact was published in this pilot. Continuous-contract roll,
adjustment, and historical-vintage semantics remain provider-defined and are not
PIT-safe.
