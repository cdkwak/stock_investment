result: Queue v2 now makes routed Leads read and own Queue work while Orca runs workers and reviewers without duplicating Queue authority.
changed: scripts/request_queue.py;tests/unit/orchestration/test_request_queue.py;artifacts/request_queue/README.md;artifacts/request_queue/BOARD.md
verified: 79 tests passed; focused Lead generation tests 2 passed; Queue Doctor OK; fresh Orca reviewer PASS; independent review by fresh_orca_reviewer: Fresh Orca reviewer PASS: every Lead mutation requires the current generation; original token cannot bypass fencing; current generation succeeds once and becomes stale after mutation; focused tests, full 79-test suite, and Queue Doctor passed.
completed_at: 2026-08-28T20:02:39+09:00
