# Install an unattended event-driven Python PM Queue runner

## Problem
The accepted Python PM control plane is one-shot, so no installed unattended local runner observes durable Listener or Queue material events and resumes the sole stored PM/Lead hierarchy after the invoking process exits.

## Evidence
PROJECT_GOAL collaboration lines 218-274 require prompt Listener intake with asynchronous PM/Lead progress; PROJECT_STATUS says the host is event-driven one-shot with no unattended Queue scheduler; WORKFLOW_CHANGELOG excludes unattended scheduler activation from C118; fresh Queue fingerprint/title/scope search found no duplicate and Doctor returned OK.

## Scope
allow:
- Implement only the scoped Python PM event runner/service/supervisor, supported workflow entrypoint, one exact Windows background task registration/readback, local sanitized receipts, provider-free tests, and current workflow/scheduler/status documentation. Use canonical Queue APIs only and preserve one PM authority.

deny:
- No Orca runtime/fallback/health/resume; no provider, account, broker, order, transfer, secret, protected artifacts/analysis/kospi200_option_wall_recent_250.csv, unrelated Data mutation, destructive cleanup, Queue lifecycle shortcut/direct file mutation, competing PM/Lead/Worker/Reviewer, console-dependent execution, or files outside write_scope.

## Done When
A Python-only, no-console Windows background runner is installed and read back exactly; it acquires at most one live PM generation, observes only durable material Listener/Queue events, resumes the stored PM and routed Lead sessions through generation/session-bound public APIs, survives restart, and writes sanitized idempotent receipts. Provider, broker, secret, protected CSV, Orca, duplicate-session, and Queue-shortcut effects remain zero. Focused tests prove one-shot exit followed by an event advances only through the installed runner, duplicate/restart/stale identity are zero-effect, and uninstall/disable or fail-closed recovery is bounded.

## Verify
Run Queue Doctor before and after; run workflow-control unit/integration suites including Listener continuity, controller service/supervisor, persistent control plane and a provider-free Windows scheduler definition/readback test; run the supported one-shot and confirm exit; inject one local material event and prove exactly one stored PM/Lead wake without manual controller invocation; verify single-writer lease, sanitized receipt, no visible console, direct transport with orca_used=false, no provider/broker calls, and exact scheduler action/arguments/readback.
