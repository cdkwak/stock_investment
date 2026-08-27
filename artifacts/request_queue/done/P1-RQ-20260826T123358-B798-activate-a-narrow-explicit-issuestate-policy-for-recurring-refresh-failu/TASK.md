# Activate a narrow explicit IssueState policy for recurring refresh failures

## Problem
The reviewed provider-free IssueState engine cannot create Inbox discoveries because no explicit policy exists, no baseline store has been established, and its Windows task remains disabled.

## Evidence
F640 is Done; current read-only inputs project to sanitized events while artifacts/issue_state/v1/issues.json and escalation_policy.json are absent and STOCK_PROJECT_ISSUE_STATE_SYNC is disabled. The Project Goal requires recurring refresh/data failures to reach inbox/new without execution.

## Scope
allow:
- Exact versioned IssueState policy/store baseline, provider-free sync tests, explicit IssueState registration enable flag and exact project routing docs

deny:
- No retroactive backlog, provider call, retry, recollection, Data/account write, raw exception/payload/identifier, automatic triage/claim/review/execution, external notification, other scheduler change or financial mutation

## Done When
A baseline provider-free sync with discovery disabled atomically records existing sanitized history and creates zero backlog; one versioned exact-target policy covers SCHEDULER_OCCURRENCE_FAILURE for scheduler lane kr_market_daily:0910 with ERROR severity, active-epoch occurrence_count>=2, bounded rate/cooldown, and sanitized queue template; post-baseline one failure is below threshold, two create exactly one inbox/new, replay/RECOVERED/suppressed/all-state duplicate are no-op; only STOCK_PROJECT_ISSUE_STATE_SYNC is enabled after dry-run and exact semantic readback with StartWhenAvailable=false, IgnoreNew and PT5M.

## Verify
Run provider-free integration tests for baseline/threshold/recovery/suppression/dedup and privacy; prove production baseline creates zero discoveries and provider calls; validate canonical policy/store; dry-run/register/read back the exact Windows task without exposing values; run owning scheduler tests, secret scan and queue Doctor.
