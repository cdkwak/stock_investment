updated_at: 2026-08-28T19:34:59+09:00
phase: simplified_lead_control
summary: Queue is the work ledger; routed Leads own execution and Orca is link-only
completed: Lead-filtered worklist, restart-safe Lead ownership, exact route fencing, optional Orca linkage, CRLF-portable receipts
next: Run one parallel Orca review batch on the simplified Queue v2 candidate
files_touched: artifacts/request_queue/README.md; scripts/request_queue.py; tests/unit/orchestration/test_request_queue.py
tests: 79 request queue tests completed; 4 focused tests passed
risks: Legacy orca-reconcile remains compatibility telemetry but is no longer a completion or review gate
new_discoveries: Repository-local request-queue skill remains compatible because README is its canonical protocol
