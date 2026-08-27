# Global ETF / SOXX onboarding gates

Status: `DAILY_AUTOMATION_ACTIVE / SYMBOL_BOUND / PIT_BLOCKED`.

SOXX is the `iShares Semiconductor ETF`, an ETF listed on NASDAQ under ticker
`SOXX` (CUSIP `464287523`). It is not the SOX index, and neither series is a
fallback for the other. The official identity evidence is the iShares product
page: https://www.ishares.com/us/products/239705/SOXX

The generic `global_etf_price_daily` contract preserves provider-native
unadjusted OHLCV and adjusted close separately. The explicit 2026-08-19 user
authorization closed the first manual Landing gate, and these checks passed:

1. official identity and provider metadata agree on `SOXX`, ETF, USD, NASDAQ,
   and daily granularity;
2. Data Status explicitly reviews the operation;
3. the completed U.S. session/finality rule is reviewed;
4. provider retention/revision handling is reviewed.

The five-session 2026-08-11..17 sample passed identity, OHLC, adjusted-close,
volume, duplicate, null, and session validation. The promoted one-year slice is
2025-08-18..2026-08-17 with 251 rows matching all 251 XNYS sessions and no
extra dates. Identical-range replay is a pre-network API-0 no-op. Both live
calls used immutable Landing, retry zero, candidate validation, and digest-bound
CAS promotion. Predictive use remains blocked pending a vintage policy,
Round 3 advanced the retained slice to 2026-08-18 (252 XNYS rows) through one
overlap-preserving call. `STOCK_DATA_GLOBAL_ETF_SOXX_DAILY` is installed at
06:10 KST. Its actual trigger and second trigger both completed with result 0;
the second was a pre-network API-0 no-op. Automation is bound to the single
registered `SOXX` symbol and grants no other ETF symbol or fallback.
