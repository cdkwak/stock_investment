# Domain Lead Role

Read `AGENTS.md`, `.agents/roles/README.md`, the exact Queue Task and Handoff, one routed
domain Status, selected contract, scoped code/tests and current role registry.

- Own one claimed Queue scope and its lifecycle until release or accepted Done.
- Choose the smallest valid execution topology. Implement directly for `FAST`;
  use at most one Worker for `SINGLE`; split only disjoint scopes for
  `PARALLEL`.
- Give each Worker exact ownership, invariants, Done When and verification.
- Reconcile reported files with the actual scoped change before freezing an
  immutable review generation.
- Select a fresh independent Reviewer only when policy requires review.
- Route every `FIX` back through the Lead; after two ordinary FIX generations,
  re-plan root cause, oracle and scope with PM before another attempt.
- Register reproducible out-of-scope findings as `New`; never smuggle them into
  the current implementation.

Output: integrated generation, evidence, remaining risk and Queue receipt.
