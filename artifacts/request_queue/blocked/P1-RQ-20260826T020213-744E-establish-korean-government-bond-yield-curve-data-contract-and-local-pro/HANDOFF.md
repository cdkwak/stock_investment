updated_at: 2026-08-26T17:26:02+09:00
phase: blocked
summary: Batch 1 evidence remains intact and UNKNOWN; state-first/checkpoint-second crash reconciliation now validates exact retained evidence and completes checkpoint with API zero.
completed: Added exact state/Landing/ledger/row/hash/UI reconciliation before same-window no-op; 23 focused tests and actual retained API0 replay pass.
next: Execute batch 2 with the active runbook in the next 17:00-18:00 KST provider-publication window, then validate field/canonical-row comparison and API0 replay.
files_touched: BOK finality runner, owning regression test, DATA_STATUS, runbook, source evidence.
tests: 23 passed; actual retained replay calls 0+0 and reconciles complete checkpoint/state binding.
risks: Only 1 of 3 provider-day batches exists; finality and expected latest remain UNKNOWN.
new_discoveries: none
