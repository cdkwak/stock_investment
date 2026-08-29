# Add role-aware PM and Lead profiles with a safe three-Lead runnable-buffer planner

## Problem
PM and Lead launch profiles, sandbox routing, and dependency-ready three-Lead allocation are not deterministic or machine projected.

## Evidence
PROJECT_GOAL sets PM Sol medium and role-aware routing; Queue currently derives only Worker and Reviewer profiles; recovered attempts show expensive-profile reuse and sandbox-to-host IPC failures.

## Scope
allow:
- Only the listed deterministic routing, disabled adapter, and offline test files.

deny:
- No auto-triage, claim, dispatch, live SDK call, scheduler activation, account or provider action, access-control change, secret use, or protected option-wall CSV access.

## Done When
A read-only router deterministically selects PM, Lead, Worker, and Reviewer model and effort profiles, sandbox boundary, and at most three dependency-ready pairwise-disjoint Leads with reasons; PM defaults to gpt-5.6-sol medium, existing Queue-derived Worker and Reviewer tiers remain authoritative, and a Codex Python SDK adapter is disabled by default behind a verified local fake boundary.

## Verify
Run tests/unit/orchestration/test_workflow_routing.py plus owning state tests and Queue Doctor; cover P0 precedence, dependencies, Review reservations, exact scopes, locks, Waiting and Blocked gates, capacity, sandbox routing, and disabled SDK behavior.
