updated_at: 2026-08-29T11:52:57+09:00
phase: completed
summary: Distinct-event lifecycle effects now serialize through the durable controller transaction; Lead-origin discoveries use current-generation self-validation into non-executable New.
completed: ACTIVE-to-REVIEW barrier proves launch then settle with final review state; Worker/Reviewer/Lead provenance tests pass.
next: none
files_touched: artifacts/request_queue/PIPELINE.md; src/stock_data/orchestration/workflow_control/controller.py; src/stock_data/orchestration/workflow_control/runner.py; src/stock_data/orchestration/workflow_control/discovery.py; tests/unit/orchestration/test_workflow_controller.py; tests/unit/orchestration/test_workflow_discovery.py
tests: focused 19/19; workflow-control 81/81; request-queue 82/82; py_compile; exact diff; Doctor OK
risks: Fresh independent review pending; global controller lock intentionally favors lifecycle order over concurrency; production cutover disabled
new_discoveries: none
