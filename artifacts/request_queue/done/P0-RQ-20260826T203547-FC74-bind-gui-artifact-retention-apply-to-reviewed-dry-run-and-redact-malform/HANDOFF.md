updated_at: 2026-08-26T20:57:39+09:00
phase: completed
summary: Apply now requires the exact reviewed dry-run plan digest and rejects inventory, content, reference, policy, manifest, or malformed-path drift before mutation.
completed: Added deterministic plan digest, explicit reviewed digest CLI/API gate, fail-closed current-plan/manifest comparison, canonical reference validation with reason-only redaction, actual Windows junction probe, and add/modify/reference/policy/tamper/privacy regressions.
next: none
files_touched: scripts/maintenance/prune_gui_validation_artifacts.py; tests/unit/orchestration/test_gui_validation_artifact_retention.py; docs/gui/GUI_STATUS.md; docs/project/REPOSITORY_MAP.md
tests: Owning suite 22 passed, 1 symlink-privilege skip; actual Windows junction regression passed; live dry-run only 25 inventory/0 eligible/0 malformed/no private path; CLI help PASS.
risks: No live apply is permitted or needed for FC74. Awaiting independent verification of exact plan binding and privacy redaction.
new_discoveries: The historical 15-file apply completed before FC74 existed and had no observed drift, but future applies now require an explicitly reviewed digest.
