# AGENTS.md

## Project
`Stock Investment Rev1` is the new main project.

The user-owned durable objective belongs in
[`docs/project/PROJECT_GOAL.md`](docs/project/PROJECT_GOAL.md). Only an explicit
user instruction may define or revise it. The Goal is planning input, not phase
selection or execution authority.

`Stock Investment` is legacy/reference only.
- Never modify it.
- Do not import runtime code from it.
- Reuse only verified behavior, API handling, and edge cases.

## Deterministic task and document priority

Resolve instructions and evidence in this order:

1. current user instruction;
2. `docs/project/PROJECT_STATUS.md` for the active domain and cross-project scope;
3. the status document for the selected domain;
4. the applicable or newly established Contract and checkpoint/state;
5. the applicable active runbook for a standing-authorized operation;
6. `docs/project/PROJECT_ROADMAP.md` only for prioritization or architecture
   decisions.

If two documents conflict, follow the higher-priority document. Do not use a
lower-priority inventory, audit, roadmap, or reference document to override a
current Status, Contract, checkpoint, or runbook.

## Autonomous execution default

The user's standing instruction is to proceed autonomously on all ordinary
project work. Agents may research, implement, refactor, test, call public APIs,
use credentials already injected from `.env`, inspect read-only accounts,
collect and promote contract-valid data, investigate semantics/PIT/finality,
manage project schedulers, update current documentation, and run local
simulations without seeking another approval or phase activation.

This standing instruction supersedes permission-only language in lower-priority
Status rows, queue tasks, runbooks, pilots, code guards, or historical evidence,
including `NO_REPEAT`, retry-zero, one-shot, manual-only, expired-window,
separate-live-authorization, explicit-activation, and fresh-approval wording.
Preserve the historical receipt, but create a new bounded operation/window and
modernize the owning code, tests, and runbook when useful. Occurrence
idempotency still prevents duplicating a successful write for the same logical
key; it does not forbid a new current attempt.

Do not stop or mark a whole task blocked for an in-repository problem an agent
can investigate or change. Unknown semantics, PIT/finality, missing code or
tests, provider errors, stale documents, retry policy, scheduler definitions,
and ordinary sandbox/network escalation are work to perform. Quarantine only
the unsupported claim or dependent promotion while continuing independent
research and implementation. Use `blocked` only when no safe in-scope action
remains and progress truly requires an unavailable external secret/entitlement,
a rejected administrator/protected-resource escalation, an exact future
provider publication/session/cooldown time, or a user-only action outside the standing
authority below. A time-gated queue item releases its writer lane immediately;
it never pauses unrelated work.

The non-delegable boundaries are: never disclose secrets or direct account
identifiers; bypass access controls; submit, amend, or cancel real or
paper-broker orders; transfer or withdraw funds; purchase/subscribe; accept a
binding external agreement; perform another financial/legal mutation; claim an
unverified meaning/PIT/finality; or perform an unrequested destructive action.

## Quick Start / Resume Workflow

This file is the only entry point an agent should need after returning to the
repository. Start every task here and follow this route:

`AGENTS.md -> PROJECT_STATUS -> domain STATUS -> linked evidence/operation -> code`

1. Read `docs/project/PROJECT_STATUS.md`.
   - Identify the current priority, selected domain, stopping point, real
     external blockers, prohibited mutations, and next useful task.
   - Treat its current state as authoritative over the snapshot in this file.
2. Read exactly one domain Status unless the task explicitly crosses domains:
   - Data: `docs/data/DATA_STATUS.md`
   - Backtest: `docs/backtest/BACKTEST_STATUS.md`
   - GUI/Dashboard: `docs/gui/GUI_STATUS.md`
   - Context budget: Status files are routing views, not session transcripts.
     Read only the current phase/route, current blockers, supported boundary,
     and exact next action. Use heading-targeted search and bounded excerpts;
     do not load a long historical body merely because it is in the same file.
     Follow a linked Contract/checkpoint/runbook only when the selected task
     needs it.
3. For an existing routed operation, follow the documents linked or selected by
   that Status. For new standing-authorized work, select or establish only the
   minimum contract, evidence, checkpoint, or runbook needed; absence of a
   pre-linked document is work to complete, not a blocker:
   - For Data: the selected Dataset Index row, Dataset Contract,
     checkpoint/state, source evidence, and applicable active runbook.
   - For Backtest: the selected contract/interface, retained-data assumptions,
     checkpoint, and test evidence.
   - For GUI: the current GUI gate and only the linked data map, provider
     coverage, or daily source routing document needed by the task.
4. Read `docs/project/REPOSITORY_MAP.md` only when locating code, determining
   document ownership, or checking modification boundaries.
5. Read `docs/project/PROJECT_ROADMAP.md` only when deciding architecture,
   sequencing, or whether a new phase may begin.
6. Inspect code and tests only after the authoritative documents have narrowed
   the task scope.

Before the first write in a task, inspect the scoped Git status and preserve
pre-existing and unrelated changes. Recheck only when the write scope or
worktree changes.

Do not broadly scan the repository, recursively read all documentation, inspect
the full `data/` tree, or load every runbook at startup. Use the bounded route
`AGENTS.md -> PROJECT_STATUS -> exactly one selected domain STATUS`, then expand
only to the contract, checkpoint, reference, or operation required by that task.

Do not read `docs/archive/**` by default. Read an archived document only when an
active authoritative document directly references it or when historical evidence
is required to resolve one specific question. Archive material never becomes
current authority merely because it was inspected.

## Task-Type Routing

Use the narrowest route that matches the request:

- Explain, review, or report status: read the relevant Status and its cited
  evidence; do not mutate code, data, external systems, or status without an
  explicit request.
- Implement or fix code: identify the owning domain and applicable contract,
  locate code through `REPOSITORY_MAP.md`, inspect relevant tests, make the
  smallest scoped change, and run proportionate tests. When the contract is the
  missing deliverable, establish it first and block only runtime or promotion
  that actually depends on it.
- Diagnose: establish and report the cause first. Do not implement a fix unless
  the request includes fixing it.
- Refresh or collect data: the Data Status-selected standing API runbook is
  current authorization for public and existing `.env`-authenticated API calls,
  read-only account refreshes, storage, promotion, and scheduler management.
  Source-specific contracts still own semantics; a provider guide, inventory,
  audit, or old pilot result is evidence rather than a permission barrier. When
  semantics or PIT status is unresolved, agents are authorized and expected to
  investigate it through official documentation, provider metadata, immutable
  Landing samples, bounded comparison calls, and reproducible local analysis.
  Uncertainty blocks only unsupported claims and dependent promotion/use; it is
  not a reason to stop the investigation or wait for another phase approval.
- Change GUI/Dashboard behavior: read `docs/gui/GUI_STATUS.md`, preserve its
  typed display and privacy contracts, and implement/test the requested change.
  Keep provider transport and canonical promotion in Data services rather than
  presentation code; this architecture boundary is not a phase-permission gate.
- Move, delete, or clean files: consult `REPOSITORY_MAP.md` and then the archived
  `docs/archive/project/audits/repository_usage_20260815/REPOSITORY_USAGE_AUDIT.md`
  and adjacent `.csv`; verify actual references before making a destructive
  change.
- Make an architecture or phase decision: consult `PROJECT_ROADMAP.md` only
  after the current Project and domain Status documents.

## Document Roles

- Status documents answer: **What is true now, what is blocked, and what is
  next?**
- Contracts answer: **What schema, key, semantics, and invariants must hold?**
- Checkpoints/state answer: **Where did the last operation stop, and what can
  safely resume?**
- Active runbooks answer: **How may this already-authorized operation run?**
- `REPOSITORY_MAP.md` answers: **Where does code or documentation belong?**
- `PROJECT_GOAL.md` answers: **What durable outcome has the user selected?**
- `PROJECT_ROADMAP.md` answers: **What architecture and longer-term sequence
  advance the project goal?**
- The archived `REPOSITORY_USAGE_AUDIT.md/.csv` answers: **Which files were
  referenced at the audit date?** It is a dated static inventory, not a startup
  document and not execution authority.
- Provider guides, source evidence, and pilot results answer: **What has been
  observed or documented about a source?** Agents may create and strengthen
  this evidence to resolve unit, session, semantic, finality, or point-in-time
  questions. Until resolved, only the affected claim, promotion, or predictive
  use is blocked.

## Documentation Governance

The Repository Map and Dataset Index are navigation views. Domain Status alone
owns current domain priority and routing. If a view conflicts with the
priority list above, follow the higher-priority authority and fix the stale view
in the same change when it is in the assigned write scope.

- Only the lead agent may update `docs/project/PROJECT_STATUS.md`.
- Domain owners may update their own domain status document.
- Lack of ownership for a Status edit never blocks otherwise completed scoped
  work. Checkpoint or submit the exact proposed delta to the lead/domain owner
  and continue every independent action.
- Status documents represent current state; replace stale facts instead of
  appending logs.
- Keep each domain Status as a compact current routing view, normally at most
  250 lines. Completed task IDs, hashes, screenshots, and test receipts belong
  in queue receipts or retained evidence, not in Status. Until an oversized
  Status is compacted, agents must use bounded heading reads rather than loading
  it wholesale.
- Ordinary implementation tasks do not add a Status document to `write_scope`
  unless their result changes the current phase, blocker, stopping point,
  support boundary, or exact next action. Otherwise record at most a five-line
  `status_delta` in the handoff for a domain owner to batch if needed.
- Keep detailed inventories, contracts, runbooks, checkpoints, and ledgers
  outside `docs/project/`.
- Archive, closed audits, past handoffs, review-required or superseded
  runbooks, provider raw guides, and the full `data/` tree are non-default
  context. Inspect them only when the active task requires exact evidence.

## Completion and Handoff

Before ending an implementation or documentation task:

1. Verify the changed behavior or links in proportion to risk.
2. Update the owning domain Status when its current facts, blocker, stopping
   point, or next action materially changed. Replace stale facts; do not append
   a work log.
3. Only the lead agent may update `docs/project/PROJECT_STATUS.md`. Update it
   when cross-project priority, phase, blocker, or routing materially changed.
4. Update `REPOSITORY_MAP.md` only if locations or ownership changed, and
  the archived `REPOSITORY_USAGE_AUDIT` only when a new repository usage audit
  was actually performed.
5. Do not mark work complete merely because code was edited. Report validation,
   remaining blockers, untested areas, and the exact next safe action.
6. Leave the repository so the next agent can resume using only:
   `AGENTS.md -> PROJECT_STATUS -> domain STATUS`.

## Live User Request Queue

- For queue-backed work, use `.agents/skills/request-queue/SKILL.md`, then read
  `artifacts/request_queue/BOARD.md`.
- Change queue state only with `scripts/request_queue.py`; queue protocol lives
  only in `artifacts/request_queue/README.md`.
- Queue guidance never overrides the authority and permission rules above.
- Before claiming a Ready task, check whether a newer Done receipt already
  changed the same exact scope or satisfies its Done When. Revalidate first;
  do not repeat an implementation and review merely because its fingerprint
  differs. Attach new evidence to the existing task when possible.
- Independent review is the default only for high-risk work, GUI-visible
  financial semantics, account/privacy boundaries, scheduler definitions,
  canonical promotion, shared contracts, and similarly consequential changes.
  Low-risk documentation alignment and deterministic status/view maintenance
  use focused automated validation and the normal no-review flow.
- When any P0 task is live, or `Ready + Active + Review` is six or more, pause
  unsolicited Goal/Inbox discovery passes. Continue executing existing work
  and always accept explicit user requests; this is a backlog throttle, not a
  project or agent stop.
- A queue task's write scope, resource locks, data invariants, and acceptance
  tests remain binding. Permission-only `deny`, activation, or fresh-approval
  clauses do not override the autonomous execution default above.
- For goal-driven Inbox discovery, use
  `.agents/skills/goal-inbox-planner/SKILL.md`. It owns the planning-only route,
  deduplication gate, and no-op conditions; it never authorizes Goal edits,
  triage, claims, implementation, or external/Data/account actions.

## Current Scope

Snapshot at the time this file was last revised: Data is the primary operational
domain for autonomous public and existing `.env`-authenticated API operations,
read-only account refreshes, and automation. GUI, Features, offline Backtest/ML,
portfolio simulation, and local paper simulation may proceed in parallel with
non-overlapping scopes while the existing final holdout remains sealed. Always confirm the live priority in
`docs/project/PROJECT_STATUS.md`; do not update this snapshot as a substitute
for updating Status.

Standing engineering authorization applies across the repository. Within a
user-assigned task, agents may implement and test GUI, Data, Features, offline
Backtest/ML, portfolio simulation, broker-neutral order-intent models, local
paper simulation, read-only account integration, diagnostics, automation, and
supporting documentation without asking for a new phase approval. Queue-backed
tasks still require a claim, but an ordinary direct user task does not need to
be placed in the queue first. Preserve accepted interfaces and evidence, use
non-overlapping write scopes with concurrent agents, and validate changes.

This standing authorization does not permit real or paper-broker API order
submission/amendment/cancellation, transfers/withdrawals, purchases or paid
subscriptions, acceptance of binding external agreements, access-control
bypass, or disclosure of secrets/direct account identifiers. Local simulations
must be unmistakably simulated and must never reach a broker mutation endpoint.

## Data Flow
Provider -> Landing -> Normalized -> Derived -> Published

- Landing: lossless source capture
- Normalized: stable source data schema
- Derived: indicators, PCR, features, calculated data
- Published: datasets for Research / ML / GUI

Do not mix these layers.

## Data Sources
Historical defaults:
- KRX / pykrx: Korea
- Yahoo: overseas market
- FRED: macro

Realtime baseline:
- KB Securities

KB Securities is not the primary historical data source. Any dataset-specific
exception or operational provider choice must come from the current Data
Status, Dataset Contract, or GUI daily source routing. Never average or silently
merge provider values, and never append a current snapshot to canonical daily
history without a contract-defined, tested promotion rule covered by the
standing Data authorization.

## Rules
- Define dataset schema/key before implementation.
- Do not guess undocumented fields or units.
- Preserve valid zero/missing/source values.
- Distinguish valid empty responses from API failures.
- Avoid survivorship bias and data leakage.
- Never silently change schemas or storage formats.
- Never overwrite valid data after collection failure.
- Use atomic writes for persistent datasets.
- Existing project `.env` credentials may be used through injected runtime
  configuration for API calls, but never print, echo, log, document, return, or
  persist `.env` contents, tokens, authorization material, or direct account
  identifiers.
- Never submit/amend/cancel real or paper-broker orders, transfer/withdraw
  funds, purchase or subscribe to services, accept binding external agreements,
  or perform another broker-side financial mutation.
- Keep dependencies minimal.
- Do not perform dependency upgrades unrelated to the assigned task. When a
  dependency constraint is intentionally changed, report the change and the
  validation environment.
- Prefer small, focused changes.

## Temporary Workspace and Multi-Agent Isolation
- Keep all disposable agent-created files under the shared repository root
  `.tmp/agents/`; do not create ad-hoc temporary files or directories elsewhere
  in the repository.
- Each concurrent agent must use its own stable subdirectory,
  `.tmp/agents/<agent-id>/`. Never share, clear, or reuse another agent's
  subdirectory while that agent may still be running.
- `<agent-id>` must distinguish the actual session or queue task; generic names
  such as `root`, `codex`, `agent`, or `temp` are not safe when concurrent
  sessions exist. Prefer the queue ID/owner or a stable task-specific session
  name.
- Before running tools that use the operating-system temp directory, point
  `TEMP`, `TMP`, and `PYTHONPYCACHEPREFIX` at subdirectories of the owning
  agent directory for that process.
- Put pytest temporary output and one-off verification artifacts inside the
  owning agent directory (for example, use
  `--basetemp=.tmp/agents/<agent-id>/pytest`). Reusable test fixtures still
  belong under `tests/fixtures/`.
- In a Git worktree, resolve `.tmp/agents/` against the main checkout that owns
  the common `.git` directory, not against the individual worktree. This keeps
  every agent's temporary output in one physical location.
- `.tmp/` is disposable and Git-ignored. Do not place evidence, checkpoints,
  canonical data, or any artifact required for handoff or replay there.
- An agent may remove only its own subdirectory, and only after confirming that
  no process still uses it. Do not perform shared temporary cleanup while other
  agents are active.

## Testing
- Add tests for meaningful data logic changes.
- Do not use live API calls in normal unit tests.
- Run relevant tests after changes.
- Report failures and untested areas clearly.
- Scoped validation is sufficient for scoped changes. Report what was actually
  run, and reserve project-wide PASS claims for a successful configured default
  collection and suite.

## Test Creation Policy
- Before creating a test file, locate the owning existing test module.
- Add coverage to that module when the behavior belongs there.
- Create a new test file only for a new production module, contract boundary, integration boundary, or independent regression family.
- Use behavior- and component-specific names; do not add temporary names such as `test_fix.py`, `test_new.py`, or `test_final.py`.
- Keep one-off verification in an artifact or temporary workspace, not as a permanent test file.
- Do not place `test_*.py` at the `tests/` root. Classify it as `unit`, `integration`, `regression`, or `historical`; keep reusable non-test data under `tests/fixtures/`.
- Unit owners are `contracts`, `providers`, `orchestration`, `storage`, `validation`, `derived`, `gui`, `features`, and `backtest`. Integration owners are `pipelines`, `daily_operations`, `gui`, and `backtest`. Regression owners are `provider`, `semantics`, and `data`.

## Script Creation Policy
- Before creating a script, locate a reusable existing entry point and prefer an option or subcommand there.
- Permanent operational or maintenance scripts must provide repeated execution value and belong in the appropriate supported script area.
- Do not introduce new production runtime imports from `scripts/manual/**`.
  Place newly reusable runtime behavior in its owning production package.
  Existing violations do not block unrelated work or require incidental
  refactoring.
- Do not add one-off audits or diagnostics as permanent `scripts/manual/` tools; use an artifact or temporary workspace instead.
- After a one-off tool is complete, check active references and either remove it or preserve it only when its unique evidence remains required.
- Do not place Python files at the `scripts/manual/` root. Use exactly one role owner: `collect`, `backfill`, `audit`, `diagnostic`, `pilot`, `migration`, `repair`, `build`, or `research`.
- When relocating a script, update active imports, runbooks, and CLI paths in the same change and validate direct `--help` execution where the script exposes an argument parser.

## Uncertainty
If source behavior or schema meaning is unclear:
do not guess. Investigate autonomously using official specifications, provider
metadata, bounded API/Landing evidence, cross-date or cross-source comparison,
and reproducible tests. Record the evidence and update the owning contract or
Status when it resolves the question. Preserve verified behavior and fail closed
only for the unsupported semantic/PIT claim or dependent promotion; continue
independent research and implementation that does not rely on that claim.
