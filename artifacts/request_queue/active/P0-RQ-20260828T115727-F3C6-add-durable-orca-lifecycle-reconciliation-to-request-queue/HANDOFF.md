updated_at: 2026-08-28T19:39:32+09:00
phase: review_ready
summary: Queue work ledger and Lead-owned Orca execution are separated with generation-fenced Lead resume
completed: Lead worklist, exact Lead/domain routing, tokenless CAS resume, optional Orca link, review decoupling, CRLF-portable receipts
next: Parallel read-only Orca architecture and concurrency review of the committed candidate
files_touched: artifacts/request_queue/README.md; scripts/request_queue.py; tests/unit/orchestration/test_request_queue.py
tests: 79 request queue tests completed; CAS and link-only focused tests passed; Queue Doctor OK
risks: Legacy orca-reconcile remains compatibility telemetry only
new_discoveries: A stale duplicate Lead is rejected by Queue generation mismatch
