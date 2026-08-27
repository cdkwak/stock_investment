# Local issue-state and Inbox discovery contract

Status: `ACTIVE / LOCAL_PROVIDER_FREE_IMPLEMENTATION`

This contract defines the Project-owned boundary that summarizes
already-sanitized local operational evidence for GUI, operations checks, and a
Goal review agent. The supported implementation is `src/issue_state/` with
`scripts/maintenance/sync_issue_state.py`; its sole canonical store is the path
specified below. `STOCK_PROJECT_ISSUE_STATE_SYNC` is installed only when an
operator selects the `IssueState` registration target. It was enabled only
after discovery-disabled provider-free baselines, one explicit exact-target
policy, two closed managed-dataset policies, and exact scheduler semantic
readback. No implicit/default escalation policy exists. This boundary grants no
provider, Data, account, Backtest,
retry, recollection, or financial-mutation authority.

## Contract identities

- Aggregate record: `issue-state/v1`.
- Escalation policy: `escalation-policy/v1`.
- One aggregate identity represents one stable cause and target, not one log
  line, run, timestamp, exception, or artifact revision.
- Timestamps are timezone-aware UTC ISO-8601 values. Dates remain source dates
  and are never promoted to timestamps.

Fingerprint input uses one exact canonical representation. Text is Unicode NFC
before validation; `stable_code`, `domain`, and `target_kind` must match
`[A-Z][A-Z0-9_.-]{0,63}`. Ordinary `target_id` is NFC-normalized, ASCII-lowercased, and
must match `[a-z0-9][a-z0-9_.:/=-]{0,159}`. Leading/trailing whitespace,
control characters, non-ASCII target identities, locale-dependent case folding,
unknown keys, booleans in integer fields, and over-bound values are rejected.
The fingerprint payload contains exactly `fingerprint_version`, `stable_code`,
`domain`, `target_kind`, and `target_id`; it is encoded as UTF-8 without BOM or
terminal newline, with lexicographically sorted keys, NFC string values,
`ensure_ascii=false`, separators `,` and `:`, and no insignificant whitespace.
`fingerprint` is lowercase SHA-256 of those exact bytes.

## `issue-state/v1`

Every record has exactly these semantic groups:

| Group | Required fields | Invariant |
|---|---|---|
| Identity | `schema`, `fingerprint_version`, `fingerprint`, `stable_code`, `domain`, `target_kind`, `target_id` | Tokens are bounded and sanitized. `fingerprint` is lowercase SHA-256 of canonical JSON containing only the version, stable code, domain, target kind, and target ID. |
| Current state | `state`, `severity`, `retryability`, `epoch`, `opened_at`, `latest_at` | `state` is `ACTIVE`, `OBSERVING`, or `RECOVERED`. Severity is `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; it never grants execution. |
| Occurrence | `first_at`, `latest_at`, `occurrence_count`, `source_event_count` | Counts are positive, monotonic, and idempotent by source schema plus source event identity. Time, message text, and evidence hashes never change the fingerprint. |
| Success/recovery | `last_success_at`, `recovered_at`, `recovery_count`, `previous_epochs` | `recovered_at` is present only for `RECOVERED`. A later recurrence increments `epoch`; it never overwrites the prior recovery. |
| Operational context | `freshness`, `source_as_of`, `expected_by`, `severity`, `retryability`, `importance` | Unknown values remain `UNKNOWN`/null. Expected times and importance are copied only from typed evidence and are never inferred from a task name, cadence label, or severity. |
| Evidence | `evidence` | Each item is a project-relative path plus SHA-256 and source schema. Absolute paths, URLs, messages, payloads, values, and identifiers are forbidden. |

`retryability` is one of `NOT_RETRYABLE`, `SAFE_LOCAL_RETRY`,
`AUTHORIZED_OPERATION_REQUIRED`, or `UNKNOWN`. It describes the evidence; it
does not start an operation. `SAFE_LOCAL_RETRY` is not permission to call a
provider, mutate Data, or bypass the selected Status/runbook.

Aggregation is bounded. The record retains the first evidence identity and at
most the latest 15 distinct evidence identities. It retains at most eight full
`previous_epochs`; older epochs collapse only into monotonic
`historical_epoch_count`, `historical_occurrence_count`, and first/latest
timestamps. Total occurrence and recovery counts are never reset by rotation.
Malformed, conflicting, out-of-order, unknown-schema, or privacy-invalid input
fails closed and cannot update a prior valid aggregate.

## Local store, locking, and recovery

The canonical store has one exact path:
`artifacts/issue_state/v1/issues.json`. It contains at most 10,000 issue records
and 8 MiB of canonical UTF-8 JSON. Reaching either limit fails closed; an active
or recovered issue is never silently pruned. The only adjacent control paths
are `.write.lock`, `.issues.transaction.json`, `.issues.next`, and
`.issues.backup` under the same directory. Symlinks, reparse redirection,
alternate roots, per-consumer copies, and writes outside this directory are
forbidden.

One writer creates `.write.lock` with exclusive-create and a random token. Lock
contention fails closed after the implementation's explicit bounded wait; time
alone never authorizes deleting a lock. Stale-lock recovery requires proving
the recorded local process identity is absent and rereading the unchanged lock
token immediately before removal. Readers never take the writer lock: they read
one canonical file, validate schema and its whole-file SHA-256, and either
return the complete generation or the prior accepted generation.

Under the lock, a write follows this exact transaction:

1. Validate the current generation and retain its exact bytes/digest as
   `.issues.backup`; write the next canonical bytes to `.issues.next` using
   exclusive-create, file flush and `fsync`.
2. Atomically create `.issues.transaction.json` with the prior/next digests,
   byte counts and phase `PREPARED`, then `fsync` the directory.
3. Use same-parent `os.replace` to publish `.issues.next` as `issues.json`,
   `fsync` the directory, and atomically advance the journal to `REPLACED`.
4. Reread and validate the canonical file, exact bytes and next digest. Only
   then mark `VERIFIED`, remove backup/journal residue, and release the exact
   owned lock token.

Startup recovery first acquires the same lock. If canonical equals the next
digest, it completes readback and cleanup. If a `PREPARED` journal's canonical
equals the prior digest, it discards only the verified unpublished next file.
A `REPLACED` or `VERIFIED` journal paired with the prior canonical generation is
an impossible conflicting state: readers and writers preserve all residue and
fail closed. If canonical matches
neither but the backup matches the prior digest, it atomically restores that
backup and verifies it. Any missing, malformed, conflicting, or hash-mismatched
journal/backup preserves every file and fails closed for operator review. A
reader never repairs, merges generations, or chooses a file by modification
time.

## Allowlisted source adapters

Adapters validate their native schema before projecting it. They do not copy a
whole source record.

| Native evidence | Allowlisted projection | Explicit exclusions |
|---|---|---|
| `runtime-diagnostic/v1` | `occurred_at`, `domain`, `kind`, `code`, `stage`, sanitized run/session correlation, and validated relative artifact identities | Exception messages, exception chains, frames, traceback text, arguments, locals, URLs, and payloads |
| `data-update-event/v1` | `event_at_utc`, terminal `state`, `reason_code`, `transition`, `job_route`, `logical_dataset`, source dates, validation/commit/freshness/finality states, provider-call and retry counts | `requested_scope`, `message`, provider request/response, URL, credential, account, holding, balance, or valuation content |
| Health V2 | Artifact generation/as-of identity and the exact dataset, latest, expected, freshness, operational, automation, blocker, and runtime-coverage fields | Dataset payload values, inferred freshness, and unregistered rows |
| Immutable scheduler occurrence receipt | Exact scheduled-for/slot identity, terminal process and occurrence states, due-lane IDs, typed lane outcomes, API counts, post-write/readback state, and Health reconciliation result | Compatibility last-result alone, provider payloads, command lines, environment, credentials, and account identifiers |

The source contracts remain authoritative. The issue aggregate is neither a
replacement for BB29 application diagnostics, 626E Data update events, Health,
immutable scheduler receipts, Landing, a checkpoint, nor queue state. A source
adapter may report `UNKNOWN`; it may not reinterpret units, finality, session,
PIT status, or success.

## Stable cause, target, and recovery

The adapter maps a native typed failure to an allowlisted `stable_code` and one
sanitized target. Targets are logical identities such as a dataset ID,
scheduler lane ID, application component, or contract name. Symbols, account
numbers, holdings, request parameters, paths outside the repository, and raw
exception text are not targets.

An occurrence joins an active epoch only when its fingerprint is exact. A typed
success for the same fingerprint target closes the epoch as `RECOVERED`, records
`last_success_at` and `recovered_at`, and increments `recovery_count`. A later
failure opens the next epoch while preserving prior recovery. Silence is not
success, and expiry alone never marks recovery.

## Suppression lifecycle

Suppression affects only Inbox discovery eligibility; it never hides an issue,
changes severity/freshness, drops occurrences, marks recovery, or authorizes a
retry. Each issue has one `suppression` object with state `NONE`, `ACTIVE`,
`EXPIRED`, or `RELEASED`. An active suppression binds exactly one issue
fingerprint and records a stable `suppression_id`, allowlisted `reason_code`,
`started_at`, mandatory `expires_at`, local actor token, and relative evidence
identity. Wildcard target/domain suppression is forbidden.

An expiry must be after start and no more than 30 days later. At the first
evaluation at or after expiry, state becomes `EXPIRED` while retaining all
fields. Explicit early release records `released_at`, `release_reason_code`, and
actor token and becomes `RELEASED`; fields are never deleted or reused. A new
suppression requires a new ID and history entry. Expiry/release does not create
a discovery retroactively: the next immutable issue snapshot must independently
satisfy the current escalation policy and queue deduplication.

## `escalation-policy/v1`

There is no default escalation threshold. With no explicit, versioned policy
row, an issue may be aggregated and displayed but cannot create a discovery.

The first active policy was intentionally narrow. Revision 1
`KR_MARKET_0910_REPEATED_FAILURE` binds only
`SCHEDULER_OCCURRENCE_FAILURE / SCHEDULER_LANE / kr_market_daily:0910`, requires
severity `ERROR` and two failures in one active epoch, and permits at most one
sanitized discovery per 24 hours with a 24-hour cooldown. A provider-free
baseline was committed before its `effective_from`, so retained historical
events create no retroactive Inbox backlog. This row is an explicit policy,
not a general default for another dataset, lane, failure code, or target.

Revision 2 adds two closed managed-dataset selectors for `HEALTH_STALE` and
`HEALTH_UNKNOWN`. The literal selector
`group=automation-enabled-datasets` resolves only against the current typed
Dataset Universe rows whose `automation_enabled` flag is true; it is not a
wildcard and cannot match manual, research-only, unsupported, or unknown
dataset IDs. Each row still requires two failures in one active epoch and uses
the same one-per-24-hour rate and cooldown. Its activation follows a
discovery-disabled Health baseline, so a single transient or pre-activation
state cannot create a discovery.

Each enabled policy row binds an exact fingerprint, an exact stable-code and
target identity, or the one closed automation-enabled Dataset Universe selector
described above, and declares:

- `policy_id`, `revision`, `enabled`, `effective_from`, and optional
  `effective_until`;
- `minimum_severity` plus an explicit `all_of` predicate list;
- at least one persistence predicate: active-epoch `occurrence_count >= 2`, positive
  `active_duration`, or positive `overdue_by` relative to a typed
  `expected_by`;
- optional occurrence, duration, overdue, freshness, and importance predicates;
- a bounded discovery rate and cooldown; and
- the exact queue fingerprint and sanitized discovery template.

The supported JSON row contains exactly `policy_id`, `revision`, `enabled`,
`effective_from`, `effective_until`, `fingerprint`, `stable_code`,
`target_kind`, `target_id`, `minimum_severity`, `all_of`, `discovery_rate`,
`cooldown_seconds`, `queue_fingerprint`, and `discovery_template`.
`all_of` is a non-empty list of exact `{kind, operator, value}` objects. Numeric
`occurrence_count`, `active_duration_seconds`, and `overdue_by_seconds`
predicates use `gte`; freshness and typed importance use `in`. Every row has at
least one of the three numeric persistence predicates. `discovery_rate` has
exact positive `max_count` and `window_seconds`; both its window and the
cooldown are bounded to 60 seconds through 30 days. Unknown keys and implicit
defaults fail closed.

A one-off occurrence cannot escalate. Severity alone cannot escalate. Missing
expected time cannot satisfy an overdue predicate. All predicates are evaluated
against one immutable issue-state snapshot, and the evaluation result records
the policy revision and issue-state digest. Recovered issues and malformed or
privacy-invalid records are ineligible.

## Queue bridge and all-state deduplication

The only permitted bridge is an explicit invocation of
`scripts/request_queue.py discover`, producing one `inbox/new` discovery. Before
that invocation, the proposed fingerprint must be checked against every live
queue state (`inbox/new`, `inbox/ready`, `active`, `review`, `blocked`, and
`done`) and the digest-protected `COMPLETED_INDEX.json`. Matching evidence is
attached to the existing issue/task lifecycle; it is not a new discovery.

The bridge cannot call `triage`, `claim`, `checkpoint`, `submit`, `review-*`,
`block`, `unblock`, `reopen`, or `compact-done`. It cannot retry, recollect,
start a scheduler, call a provider, write Data, or change a workflow outcome.
Queue state remains governed solely by the
[request queue protocol](../../artifacts/request_queue/README.md).

Recurrence never causes a direct queue-file edit. For a matching mutable `done`
task, the automatic gate records `RECURRENCE_REVIEW_REQUIRED` and creates
nothing; an authorized queue operator may inspect the evidence and use
`scripts/request_queue.py reopen` explicitly. For a fingerprint reserved only
by `COMPLETED_INDEX.json`, the compacted task has no mutable directory and must
not be reconstructed. If a later recovered epoch genuinely recurs and again
satisfies policy, the sole automatic bridge may call `discover` with a distinct
queue recurrence fingerprint: lowercase SHA-256 of canonical
`queue-recurrence/v1` identity containing the stable issue fingerprint,
completed task ID, and positive recovery epoch. The discovery must link those
three sanitized identities, and both the recurrence fingerprint and stable
issue fingerprint are checked across all states first. This creates only
`inbox/new`; it does not reopen, triage, claim, or imply that the prior Done
result was false.

## Consumers and privacy

GUI, read-only operational checks, and a Goal review agent may consume the same
validated aggregate. Consumers may filter or explain it but may not create a
second severity/fingerprint truth. A GUI retry control, if separately
authorized, must use its own allowlisted operation contract; this issue state
does not authorize it.

No record or discovery may contain a password, token, authorization material,
account identifier, holding, balance, valuation, order, raw response, request
body, URL, exception message, traceback, stack frame, local variable, or
absolute path. Evidence links must remain project-relative and hash-bound.

The aggregator, policy evaluator, and bridge are local-file-only. They may not
open a socket or send HTTP, email, SMS, chat, webhook, push, desktop/OS toast,
external telemetry, or any other outbound message or notification. They may
not invoke an external AI/service. Passive rendering by a separately authorized
local GUI is a read-only consumer, not notification authority.

## Goal requirement mapping

| Project Goal requirement | Contract closure |
|---|---|
| Stable code/fingerprint and target | Canonical identity plus exact target or closed typed-registry selector |
| First/latest occurrence and count | Monotonic occurrence group with idempotent source identities |
| Last success and recovery preservation | Explicit success fields, recovery state, and bounded recovery epochs |
| Severity and retryability | Closed enums that describe state but grant no action |
| Sanitized evidence | Relative SHA-256 evidence identities and strict exclusions |
| Shared GUI/operations/Goal consumption | One Project-owned validated aggregate |
| Ignore transient single failures | Persistence predicate is mandatory and occurrence-only starts at two |
| Repetition/duration/expected-time/importance thresholds | Explicit versioned policy predicates; no defaults or invented expected time |
| Queue-wide duplicate check | All live states plus `COMPLETED_INDEX.json` |
| Discovery without execution | `request_queue.py discover` to `inbox/new` is the sole bridge |
| Crash-safe shared state | Exact local path, exclusive writer token, atomic replace, hash readback, and journal recovery |
| Temporary suppression | Exact-fingerprint, bounded-expiry lifecycle that preserves visibility, counts, and recovery |
| Recurrence after completion | Explicit manager `reopen` for mutable Done; epoch-bound `discover` only for compacted recurrence |
| No unsolicited external action | Local-file-only operation and explicit outbound notification ban |

References: [Project Goal](PROJECT_GOAL.md),
[Project Status](PROJECT_STATUS.md), and the
[request queue protocol](../../artifacts/request_queue/README.md).
