updated_at: 2026-08-26T21:06:40+09:00
phase: completed
summary: Reopened parent now preserves every future successful apply as a unique inventory-excluded immutable receipt; historical first apply is honestly reconstructed from exact retained evidence.
completed: Added pre-mutation receipt collision gate, atomic fully-flushed hard-link receipt publication, receipt-root inventory exclusion, final-manifest-failure receipt coverage, second-apply non-overwrite coverage, and redacted historical reconstruction for 15 deleted files.
next: none
files_touched: scripts/maintenance/prune_gui_validation_artifacts.py; tests/unit/orchestration/test_gui_validation_artifact_retention.py; docs/gui/GUI_STATUS.md; docs/project/REPOSITORY_MAP.md; artifacts/gui_validation/.retention_receipts/historical-20260826-first-apply.reconstructed.json
tests: 25 passed, 1 file-symlink privilege skip; real junction PASS; live dry-run only inventory=25/eligible=0/malformed=0; reconstructed 15 entries sum exactly 1,807,529 bytes and contain no private path.
risks: The original first-apply transaction id, reviewed digest, and per-file hashes were overwritten before immutable receipts existed and cannot be recovered; reconstruction explicitly records this limitation and does not guess.
new_discoveries: Successful apply receipts must be append-only and excluded from screenshot retention, while the mutable latest manifest remains operational state.
