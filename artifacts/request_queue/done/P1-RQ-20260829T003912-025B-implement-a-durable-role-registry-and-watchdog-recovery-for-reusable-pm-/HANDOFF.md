updated_at: 2026-08-29T09:52:40+09:00
phase: completed
summary: Second Reviewer FIX corrected with full identifier-only Dispatch history and idempotent fake readback reconstruction.
completed: All historical Dispatch reuse is atomically excluded and post-commit replay rebuilds fake terminal/Dispatch observations.
next: none
files_touched: src/stock_data/orchestration/workflow_control/registry.py; src/stock_data/orchestration/workflow_control/simulator.py; src/stock_data/orchestration/workflow_control/watchdog.py; tests/unit/orchestration/test_workflow_recovery.py
tests: PASS: 27 focused and owning workflow-control tests; Queue Doctor OK.
risks: Offline-only; history is identifier-only and foreign-key bound; simulator reconstructs fake state only and performs no real process or Queue action.
new_discoveries: none
