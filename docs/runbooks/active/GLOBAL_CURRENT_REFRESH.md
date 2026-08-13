# Capture-first global current refresh

`scripts/manual/refresh_global_current.py` prepares one bounded provider phase.
The live command never changes a production Normalized or Derived root.

- `yahoo`: exactly 3 sequential calls (SP500, NASDAQ Composite, NASDAQ-100)
- `fred_yields`: exactly 3 sequential calls (DGS2, DGS10, DGS30)
- `fred_fx`: exactly 2 sequential calls (DEXKOUS, DEXJPUS)

Each item has a frozen, explicit start and end in the checkpoint. Requests have
a hard call cap and retry count zero. Every response is atomically captured
under `data/landing/global_current_refresh/<run_id>/` before a complete candidate
is written under `data/staging/global_current_refresh/<run_id>/`. Each Landing
body is hash-bound to its call record. Yahoo overlap starts from each symbol's
own retained maximum.

The Landing audit binds exactly one unique call record to every frozen plan
item, including provider, operation, URL, parameters, HTTP 200 status, and body
hash. Every item must remain inside its planned window, reach the explicit end,
and overlap retained coverage. FRED revision checks are per series: omitted
dates and finite-to-null changes fail closed, while finite revisions and
null-to-finite observations are reported separately.

The checkpoint records the production pre-manifest, request plan, call/status
accounting, capture hashes, overlap revision counts, candidate manifest, and
publication state. Omitted retained keys inside the returned response range,
schema failure, unexpected coverage, or production drift fails closed. Existing
production roots remain byte-identical.

Use `--end` for the reviewed completed-source date and
`--confirm-live-landing-only`. Review the Landing bodies, frozen plan, revision
report, candidate coverage, and manifests before publication.

Publication is a separate zero-network command using `--promote-checkpoint` and
`--confirm-offline-promotion`. The operator must also supply the exact
`--approval-digest` printed in the reviewed checkpoint. It performs a
content-manifest CAS and installs a
copy of each whole candidate root with rollback. A yield candidate also rebuilds
the Treasury spreads; yield and spread roots promote in the same transaction.
Candidate evidence remains retained after promotion.

The approval digest binds the pre-production operational state, exact call and
HTTP-status accounting, retry count, frozen paths, call-record hashes, revision
details, and every candidate/pre-production manifest. Changing any reviewed
field invalidates approval.

Dataset and operational-state candidates are promoted together. A durable
transaction journal is written before staging begins and supports deterministic
rollback after an interrupted, uncommitted transaction or cleanup after a
committed transaction. Committed recovery reconstructs any missing canonical
target from a fingerprint-verified candidate or stage before deleting backups;
if no verified new copy exists, it preserves all remaining copies and stops.
Rollback only installs a backup whose fingerprint matches the recorded
pre-transaction target; a still-valid canonical target is preferred over an
unknown or partial backup. Journal entries must match the exact ordered
candidate-to-production mapping, so swapping dataset and state sources is
rejected without mutation. The global refresh lock is acquired before recovery
or promotion preflight and remains held through final publication.
All candidate, Landing, production, and state manifests
are checked again after the provider lock is acquired. Paths must match the
run/phase topology; symlinks, junctions/reparse points, extra files, and unknown
partition layouts are rejected.

Run and audit Yahoo, FRED yields, and FRED FX separately. A failure in one phase
cannot partially publish another. FRED current observations do not establish
vintage/revision history; retained historical provenance limitations remain.
