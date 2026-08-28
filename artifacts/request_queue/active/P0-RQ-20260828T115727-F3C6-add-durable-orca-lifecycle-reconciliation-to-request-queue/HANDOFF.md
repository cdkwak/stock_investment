updated_at: 2026-08-28T17:46:41+09:00
phase: candidate_ready
summary: Queue v2 multi-Lead control plane implemented with Waiting, safe release, domain routing, bounded Orca reconciliation, and review binding
completed: Implementation and focused regression suite
next: Commit candidate and dispatch independent Orca reviewer
files_touched: artifacts/request_queue/README.md; scripts/request_queue.py; tests/unit/orchestration/test_request_queue.py
tests: 76 request queue tests passed; py_compile passed; Queue Doctor OK; git diff --check passed
risks: Live Orca desktop graph was stuck; headless runtime is healthy and used for canary review
new_discoveries: Desktop Orca runtime can leave a stale crashpad helper and require elevated headless serve
