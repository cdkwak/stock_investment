# Stock Exploratory Scanner Contract

Contract version: `stock-exploratory-scanner/v1`

Status: `IMPLEMENTED_PROVIDER_FREE / DESCRIPTIVE_PARTIAL_AXES`

## Purpose

The Research Workspace provides a practical daily discovery list without
waiting for every future fundamental dependency. It is intentionally separate
from the strict PIT/backtest contract in
[`stock-candidate-research/v1`](../backtest/STOCK_CANDIDATE_DISCOVERY_CONTRACT.md).

An independently available axis may display even when another axis is not yet
connected. Missing earnings or valuation evidence is labelled `N/A`; it does
not erase current technical candidates and is never converted to zero.

## Current technical scan

The provider-free local scanner:

- reads the latest two retained years of `kr_equity_price_daily`;
- restricts identities to the exact latest date of
  `kr_equity_canonical_universe_daily` with both listed-info and price presence;
- requires an exact latest-session observation, unique dates, positive closes
  and at least 60 retained observations;
- computes descriptive Wilder RSI14 and close/SMA60 from provider-native
  original prices;
- includes an extreme observation candidate when `RSI14 <= 30` **or**
  `close/SMA60 <= 80%`;
- orders the presentation by lowest RSI14, then lowest disparity, market and
  symbol, and displays at most 80 rows; and
- labels a >=50% absolute one-session move in the latest 60 sessions as
  `원가격 급변/분할 영향 가능` rather than hiding the entire candidate.

This ordering is an exploration convenience, not an alpha rank, expected
return, suitability result, portfolio weight, recommendation or order intent.
Original-price corporate actions can distort the technical measure, so the
warning and exact as-of date remain visible.

An exact-date KRX `MDCSTAT03501` current observation may independently display
provider-native trailing/current PER and PBR beside a technical candidate. The
scanner accepts it only when immutable response bytes match the sanitized
provenance, the date exactly equals the price/universe date, the scope is ALL,
the response has unique source symbols, and the artifact says
`PIT_LIMITED_FIRST_OBSERVED_ONLY`, `predictive_use=false`, retry zero and no
Normalized write. Literal `-` remains missing. Missing or invalid valuation
evidence leaves that row `N/A` without suppressing its valid technical axis.

PER/PBR display does not change inclusion or ordering and is not a relative
value, value-trap, earnings-revision, forward-value or recommendation claim.
Forward EPS revision and strict relative-value judgment remain `N/A · 미연결`;
historical predictive performance and a strict all-axis match must use the
PIT-safe Backtest boundary.

## GUI and runtime

The scan starts asynchronously when Research Workspace is first opened, not at
application startup, and can be rerun with `현재 후보 새로고침`. It reads
local files only, performs no provider call or persistent write, and shares the
existing managed equity-worker lane so application close waits for quiescence.

The visible summary reports source date, scanned instruments, total matching
instruments, displayed rows, partial-axis status and the non-recommendation
boundary. Activating a row searches the existing exact local equity catalog by
symbol and market before opening its 120-day Research Workspace chart. The
scanner's own identity text is not trusted as chart authority.

Malformed, nonfinite, date-mismatched or wrong-contract results clear all
candidate rows. A valid unavailable result is numeric-free and leaves the
manual refresh control available.
