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

## Additive input-readiness boundary for new consumers

`backtest-input-readiness/v1` is the provider-free, deterministic admission
boundary for new versioned Backtest feature, model, and portfolio consumers. It
does not retrofit or change the accepted Phase-1, indicator, or overnight-ML
replays. The declared input is exact:

| Field | Required value |
|---|---|
| Dataset / contract | `kr_kospi200_index_daily` / `1` |
| Coverage | `1990-01-03..2026-08-14` |
| Rows / files / bytes | `9,447 / 37 / 738,068` |
| Frozen digest | `a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713` |
| Source identity | ticker `1028`; `KRX_TRADING_DATE_DAILY_FINAL` |
| Feature identity | feature-set version `1`; `PIT_SAFE_EOD_T_PLUS_1` |
| Decision rule | `T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION` |
| Split identity | `PURGED_EXPANDING_WALK_FORWARD`; label horizon/purge `60/60` sessions; embargo `5` sessions |
| Holdout identity | `UNTOUCHED_FINAL_5_CALENDAR_YEARS`; start `2021-08-17`; development/holdout `8,225/1,222`; exact Boolean `results_reviewed=false` |

Every feature row has byte-exact `observation_time` and `available_at` at its
T date `15:30:00+09:00`, and `usable_from` at `09:00:00+09:00` on the exact
next date in the ordered retained calendar. Weekdays, holidays, or sessions are
never inferred. Pre-read and post-read verified manifests must both equal the
declared full manifest; a changed read, unresolved finality, unsafe PIT state,
wrong identity/version/clock, or row reaching the holdout fails closed.

The gate accepts no label or outcome argument. Feature columns beginning with
`forward_`, `future_`, `label_`, `outcome_`, `mae_`, or `mfe_`, plus every
declared `LABEL_NAMESPACE` column, are rejected. Its receipt contains canonical
sorted JSON and a SHA-256 over stable manifest, calendar, feature-metadata,
split, and holdout fields only: no wall clock, absolute path, feature value,
label, prediction, metric, or holdout outcome is serialized.
