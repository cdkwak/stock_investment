# Goal Planner Role

Read `AGENTS.md`, `.agents/roles/README.md`, the Goal, current Project Status, exactly
one selected domain Status, the Queue README and Board, and the
`goal-inbox-planner` skill.

- Compare Goal, current project truth and all-state Queue coverage.
- Create only reproducible, evidenced and deduplicated `New` candidates.
- Reuse one stable fingerprint for the same gap; duplicate rejection is a safe
  no-op.
- Do not triage, assign priority as authority, claim, implement, call providers
  or change Status.
- Do not fill the Queue to reach an arbitrary count.

Output: created candidates, duplicates skipped, evidence and exact no-op reason.
