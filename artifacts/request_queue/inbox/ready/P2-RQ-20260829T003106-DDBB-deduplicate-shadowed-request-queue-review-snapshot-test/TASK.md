# Deduplicate shadowed request-queue review snapshot test

## Problem
Two same-named review-snapshot regressions shadow one another so only the later body is collected.

## Evidence
The D1E1 independent Reviewer reproduced the two definitions in tests/unit/orchestration/test_request_queue.py after the candidate passed its owning suite.

## Scope
allow:
- Only tests/unit/orchestration/test_request_queue.py.

deny:
- No production, Queue protocol, lifecycle, external, scheduler, broker, account, protected CSV, or unrelated file change.

## Done When
Both intended review-generation recovery behaviors have distinct stable test names, both are collected and pass, and no production or Queue semantics change.

## Verify
Confirm the duplicate name is eliminated while both behaviors remain; run the two focused tests, the owning request-queue suite, Queue Doctor, and exact one-path manifest reconciliation.
