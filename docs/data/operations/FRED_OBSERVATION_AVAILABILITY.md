# FRED observation availability contract

Status: `DESCRIPTIVE_CURRENT_OPERATION_APPROVED / PREDICTIVE_GATE_UNCHANGED`.

This contract separates two facts that must never be collapsed:

- `CURRENT_AS_RETRIEVED`: an immutable FRED response showed an observation at
  `retrieved_at`; this is suitable for descriptive operational freshness.
- predictive/PIT eligibility: the retained response also proves what value was
  known at the strategy decision time.

The existing `fredgraph.csv` route retains observation date, value, and capture
time, but does not expose FRED real-time intervals or series `last_updated`.
Those captures therefore remain
`PIT_BLOCKED_PENDING_VINTAGE_RESOLVER`. Retrieval time is not substituted for
release time, and observation date is not substituted for availability time.

The offline executable gate is
`stock_data.orchestration.source_acceptance.evaluate_fred_observation`. A future
vintage-aware Landing may become decision-time eligible only when it binds the
official FRED API `realtime_start`, `realtime_end`, and timezone-aware series
`last_updated` to the retained response and all precede the supplied decision
time. The active global refresh runbook authorizes bounded capture-first
as-retrieved collection, Landing, and reviewed Normalized promotion for VIX,
Treasury yields, USD FX, and the derived Treasury spread. This contract does
not authorize predictive use or invent unavailable vintage metadata.

## Descriptive provider-availability policies

Exchange completion is not publication. `expected_latest.py` stores
`observation_calendar`, `provider_availability_policy`, `expected_lag_policy`,
and `finality_policy` independently. DGS2/DGS10/DGS30 follow the official H.15
weekday 16:15 ET release. DEXKOUS/DEXJPUS follow the official H.10 weekly
Monday 16:15 ET release, with the next-business-day holiday rule. VIXCLS keeps
the separate bounded-observation policy
`FRED_VIX_NEXT_BUSINESS_DAY_0840_CT`; it is not forced into H.15 or H.10.

Before a release gate, an unpublished observation is `EXPECTED_LAG`, not a
failure. After the gate, absence is stale. The 2026-08-19 post-gate Task
Scheduler run promoted all three H.15 yields and the dependent spread through
2026-08-17. Its immediate second trigger returned `NOOP` with API 0.

Official basis:

- FRED observations API documents real-time periods, output types, and
  `vintage_dates`: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- FRED explains that a real-time period records when information was known and
  that observations may be revised:
  https://fred.stlouisfed.org/docs/api/fred/realtime_period.html
- Federal Reserve H.15 publication schedule:
  https://www.federalreserve.gov/releases/h15/default.htm
- Federal Reserve H.10 publication schedule and holiday rule:
  https://www.federalreserve.gov/releases/H10/
