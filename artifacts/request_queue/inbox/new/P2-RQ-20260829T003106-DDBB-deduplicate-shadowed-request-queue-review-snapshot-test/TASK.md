# Deduplicate shadowed request-queue review snapshot test

## Problem
tests/unit/orchestration/test_request_queue.py defines test_review_verdict_is_bound_to_exact_handoff_snapshot_and_can_recover twice, so Python collects only the later definition.

## Evidence
Fresh independent Reviewer reproduced exact definitions at lines 1845 and 1996 after the D1E1 candidate passed 114 tests.

## Impact
The earlier regression body is shadowed and can silently lose intended review-generation coverage.

## Scope
allow:
- tests/unit/orchestration/test_request_queue.py
deny:
- unrelated files and operations

## Done When
Triage defines the exact acceptance boundary.

## Verify
Run rg -n '^def test_review_verdict_is_bound_to_exact_handoff_snapshot_and_can_recover' tests/unit/orchestration/test_request_queue.py and verify two definitions.
