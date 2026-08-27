# Naver mobile-home UR-167 remaining windows

## Terminal state

`COMPLETED_EXACT_WINDOWS_20260821 / NO_FURTHER_WINDOWS`.

This runbook records a completed, non-recurring bounded operation. It does not
authorize a new Naver request, a scheduler, GUI transport, canonical/history
promotion, Backtest use, a fallback, or reuse of the original UR-166 capture or
retained recovery.

## Exact execution contract and result

| KST window | GET budget/result | Landing SHA-256 | Accepted current observation | Rejected identity result |
|---|---|---|---|---|
| 14:30 | 1/1; timeout 10 seconds; retry/redirect/auth/cookie/fallback 0 | `f162d6635b48e717e2b829081c14bfb5ee91c5a96105c6cdce22d6581a4ea6f0` | KOSPI 6941.55 @14:30; KOSDAQ 803.04 @14:30; USD/KRW 1382.6 @14:29 | Gold, WTI: `VISIBLE_CONTRACT_UNIT_MISSING` |
| 15:00 | 1/1; timeout 10 seconds; retry/redirect/auth/cookie/fallback 0 | `da72e71e8230db300f4c1d13f87dc3aa4075c9e3488054081eebdf5b68d6b2c5` | KOSPI 6907.74 @15:01; KOSDAQ 799.99 @15:01; USD/KRW 1384.1 @14:59 | Gold, WTI: `VISIBLE_CONTRACT_UNIT_MISSING` |
| 15:30 | 1/1; timeout 10 seconds; retry/redirect/auth/cookie/fallback 0 | `1e96f42ecdd16fc5b52211ebd5e87aaddc386fb86380ecb8431d4ada68b6aa92` | USD/KRW 1385.8 @15:29 | KOSPI, KOSDAQ: `VISIBLE_PRICE_OR_TIMESTAMP_MISSING`; Gold, WTI: `VISIBLE_CONTRACT_UNIT_MISSING` |

Every result used the exact strict mobile-home parser: explicit page identity,
unit, realtime status, today-KST provider time and source age <=60 minutes.
The three Landing files are under `data/landing/naver_mobile_home/ur167/` and
the durable ledger is `data/state/naver_mobile_home_ur167_windows.json`.

## Projection and no-repeat conditions

- Current projection is local-personal display-only, redistribution prohibited,
  provisional and PIT-blocked: `data/state/current_observations/naver_mobile_home_current.json`.
- At 15:30 KOSPI/KOSDAQ were not projected from the deficient page. Their
  already-valid 15:01 observations were preserved without a fallback or
  inferred value; USD/KRW atomically replaced only its own row.
- Gold and WTI have no page-proven contract unit, so no numeric observation was
  ever created. Their failure is route- and identity-specific.
- Each projection decision was primary-only. Its subsequent local replay made
  provider API calls `0`; no circuit was opened.
- The three window IDs are consumed. Same-window state returns no-repeat; there
  are no further authorized windows for this route/date. Any future collection
  needs a distinct approved queue request, runbook and pre-transport budget.

## Evidence

See `artifacts/agent_runs/ur167/naver_mobile_home_windows_20260821.md` for the
ledger, exact Landing paths, test boundary, and the pre-boundary API-zero
no-repeat CLI result. The original UR-166 state and recovery remain separate
and immutable.
