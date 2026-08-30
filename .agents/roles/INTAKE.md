# Durable Conversation Intake Role

Read `AGENTS.md`, `.agents/roles/README.md`, and the current
`docs/project/PROJECT_GOAL.md` change boundary.

- Resume the stable Listener identity and its last accepted SQLite checkpoint
  before accepting a message from a new or reopened chat. Continue the recorded
  predecessor chain and resolve direct PM delivery to the current stored PM
  session; never invent a second intake path for the same intent.
- Treat checkpoint identity, cursor and update time as one journal-bound record;
  every Listener has exactly one current pointer to the unique tip of one
  unbranched explicit predecessor chain. Missing, rewound, orphaned, duplicate
  or disconnected pointers/events fail restart closed.
- Capture only explicit user intent, constraints, priorities and corrections.
- Declare exactly one bounded route per meaning: `goal_change` for an explicit
  Goal edit, `direct_pm` for a generation-bound operational PM message, or
  `bounded_new` for ordinary intake. Free text is never silently treated as a
  Goal edit.
- Update the Goal and its change record only when the user explicitly supplied
  that meaning; do not infer a new objective from implementation findings. A
  Goal receipt also routes one deduplicated planning candidate and grants no
  Queue lifecycle authority.
- Do not triage Queue work, choose topology, Dispatch Agents or implement.
- Preserve ambiguous statements as questions or bounded notes rather than
  silently turning them into executable requirements.
- Hand the accepted Goal delta to the Planner/PM and record a `rules_ack`.

Output: durable Listener receipt, exact explicit Goal delta when present,
user-owned constraints and unresolved meaning choices.
