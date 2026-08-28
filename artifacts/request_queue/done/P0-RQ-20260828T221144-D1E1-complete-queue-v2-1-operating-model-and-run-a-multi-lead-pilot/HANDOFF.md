updated_at: 2026-08-29T00:30:55+09:00
phase: completed
summary: Queue v2.1 now includes the direct-user PIPELINE recovery guide as a navigable Doctor-recognized root artifact; late review activation also recomputes the required reviewer profile.
completed: Reviewed the inherited diff, registered and linked PIPELINE.md, appended the material recovery change, repaired late-review profile consistency, and passed focused plus full regressions.
next: none
files_touched: .agents/skills/goal-inbox-planner/SKILL.md; .agents/skills/request-queue/SKILL.md; AGENTS.md; artifacts/request_queue/README.md; artifacts/request_queue/PIPELINE.md; artifacts/request_queue/WORKFLOW.md; artifacts/request_queue/WORKFLOW_CHANGELOG.md; scripts/maintenance/sync_issue_state.py; scripts/maintenance/telegram_agent_bridge.py; scripts/request_queue.py; tests/unit/orchestration/test_request_queue.py
tests: 6 focused plus 2 late-review/Doctor tests passed; 114 request-queue/issue-state/Telegram regressions passed in 29.86s; Queue Doctor OK.
risks: Legacy tasks intentionally derive safe profile defaults; PIPELINE is guidance rather than an executable registry; historical receipts remain unchanged.
new_discoveries: none
