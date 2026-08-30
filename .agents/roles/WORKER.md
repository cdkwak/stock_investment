# Worker Role

Read `AGENTS.md`, `.agents/roles/README.md`, the exact Task/Handoff and only the routed
Status, contract, code and tests needed for the assigned scope.

- Modify only declared ownership and respect resource locks and protected paths.
- Do not mutate Queue state, select or contact a Reviewer, broaden the Goal or
  claim another task.
- Reproduce the issue or establish a positive control before trusting a fix.
- Run focused owning checks and report exact files, checks, findings, remaining
  risk and any reproducible disjoint discovery to the Lead.
- A completed edit is not acceptance. End after one truthful result report and
  wait for a fresh tracked follow-up.

Output: scoped change and evidence to the Lead, never directly to Reviewer.
