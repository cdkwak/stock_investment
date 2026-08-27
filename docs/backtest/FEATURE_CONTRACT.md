# Phase 1 Feature Contract

Status: `OFFLINE_FOUNDATION_IMPLEMENTED / VERSIONED_EXTENSION_ALLOWED`

The first feature set consumes only frozen `kr_kospi200_index_daily` contract
version 1. It performs no network, provider, Data-state, or production write.

| Rule | Contract |
|---|---|
| Observation | Final KRX trading-date close at 15:30 Asia/Seoul |
| Availability | T close after its source is final |
| Earliest use | Next retained KRX trading date at decision time; never same-day |
| Calendar | Derived only from the ordered frozen artifact; no weekday inference |
| Missing policy | Drop rows until the complete declared lookback exists |
| Source anomaly | Preserve validated source close; never repair OHLC by inference |
| PIT | `PIT_SAFE_EOD_T_PLUS_1` for this selected input only |

Version 1 features are 5/20/60-day close returns, 20-day annualized realized
volatility, 60-day moving-average distance, and 60-day rolling drawdown. Every
output row carries observation time, available time, usable-from time, source
dataset/contract version, feature-set version, and PIT status.

Labels are not features. Forward returns, future drawdowns, MAE, and MFE live in
`market_backtest.labels` and cannot be passed to the feature builder or a
decision at the observation time.
