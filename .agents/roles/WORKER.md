# Worker Role

Read `AGENTS.md`, `.agents/roles/README.md`, the exact Task/Handoff and only the routed
Status, contract, code and tests needed for the assigned scope.

- Modify only declared ownership and respect resource locks and protected paths.
- Do not mutate Queue state, select or replace a Reviewer, broaden the Goal or
  claim another task. Use only the Reviewer preassigned in the immutable
  `TaskContract`.
- Reproduce the issue or establish a positive control before trusting a fix.
- Run focused owning checks, freeze a scoped candidate digest, submit it through
  the typed mailbox directly to the preassigned Reviewer, and wake that stored
  Reviewer session idempotently. The Lead receives a visibility copy.
- Apply at most two ordinary typed `FIX` decisions received directly from that
  same Reviewer. A third `FIX` is `REPLAN_REQUIRED` for Lead+PM and authorizes
  no further patch.
- A completed edit is not acceptance. Reviewer `PASS` goes to the Lead for
  integration; the Worker never changes Queue state or claims acceptance.

Output: immutable scoped candidate to the preassigned Reviewer, with Lead
visibility and focused evidence.
