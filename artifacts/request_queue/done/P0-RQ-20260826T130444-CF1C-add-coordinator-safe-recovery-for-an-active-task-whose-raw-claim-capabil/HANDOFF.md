updated_at: 2026-08-26T14:10:30+09:00
phase: completed
summary: Expired Active receipt parser now rejects every 0-3-space CommonMark ATX H1/H2 structural bypass.
completed: Patched parser and added byte-identity plus exhaustive 1-3-space regressions.
next: none
files_touched: scripts/request_queue.py; tests/unit/orchestration/test_request_queue.py
tests: 72 request queue tests passed; py_compile passed; Queue Doctor OK.
risks: No queue mutation occurs on malformed receipt rejection; original bytes remain identical.
new_discoveries: none
