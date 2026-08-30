updated_at: 2026-08-29T00:31:06+09:00
phase: discovered
summary: tests/unit/orchestration/test_request_queue.py defines test_review_verdict_is_bound_to_exact_handoff_snapshot_and_can_recover twice, so Python collects only the later definition.
completed: evidence captured
next: Coordinator triage
files_touched: none
tests: Run rg -n '^def test_review_verdict_is_bound_to_exact_handoff_snapshot_and_can_recover' tests/unit/orchestration/test_request_queue.py and verify two definitions.
risks: untriaged
new_discoveries: none
