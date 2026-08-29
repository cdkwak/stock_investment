# Add a fail-closed workflow-policy evaluation and promotion lifecycle

## Problem
Workflow-policy changes have no executable replay, independent-review, canary, promotion, rollback, or authority-tier lifecycle.

## Evidence
PROJECT_GOAL requires a closed evaluation loop; D1E1 provides only current documentation and changelog; Steward found no machine-checkable lifecycle.

## Scope
allow:
- Only the listed policy, replay, tests, and current workflow records after reviewed acceptance.

deny:
- No live Queue scheduler activation, no automatic production promotion, no broker or account mutation, no access-control or paid-service action, no secrets, no destructive migration, and no protected option-wall CSV access.

## Done When
Versioned policy proposals bind to accepted workflow-event snapshots, replay offline, require immutable independent-review PASS, remain disabled through bounded canary criteria, and produce explicit promotion or rollback receipts; authority tiers fail closed for broker, order, transfer, financial, access, secret, paid-service, destructive-migration, and unreviewed standing-authority actions; current workflow records update only after acceptance.

## Verify
Run tests/unit/orchestration/test_workflow_policy.py and all workflow-control and request-queue owning tests, Queue Doctor, replay determinism, stale-generation rejection, canary refusal, promotion, rollback, and protected-boundary fixtures.
