# D001 Deterministic Dataset Inventory Contract

## Purpose and classification boundary

This audit inventories retained Data-layer files and verifies bounded physical
properties. It never assigns `DATA_COMPLETE`; every result is an observation of
the local filesystem at invocation time. Missing registered datasets and
unregistered artifacts are reported, not silently classified as defects.

The command performs no provider or network calls and never reads Landing raw
bodies. Landing is bound to the inventory through a deterministic relative-path
and byte-count metadata manifest, then summarized by extension, count, and
bytes. Its pre/post manifest is part of the same scan-consistency gate.

## Report identity

- report schema: `stock_data.dataset_inventory`
- schema version: `2`
- deterministic key for an artifact root: `(layer, relative_root)`
- registered dataset association: exact contract name from Parquet schema
  metadata when present, otherwise exact artifact-root directory name
- file ordering: normalized repository-relative POSIX path
- no generated timestamp, hostname, secret, environment value, or raw payload

## Artifact-root discovery

Parquet files are discovered below `data/landing`, `data/normalized`,
`data/derived`, and `data/published`. The root is the directory immediately
before the first Hive-style `key=value` partition component; an unpartitioned
file uses its parent. Hidden, temporary, and quarantine path components are
listed as ignored and excluded from issue counts.

Nested Published bundles therefore retain their full relative root while still
associating to the exact leaf dataset name or Parquet `dataset` metadata.

## Verification levels

- files, repository-relative file manifest, partitions, bytes, rows, and
  SHA-256: exact current artifact evidence
- logical contract schema: column-name and dtype comparison against the contract;
  physical Arrow nullability is reported separately and never by itself changes
  this result
- physical nullability: exact per-file footer nullability. A nullable physical
  field can still satisfy a logical non-null contract when its exact observed
  null count is zero; this is reported as `MISMATCH` plus a separate required-
  value result rather than as a schema failure
- date coverage: exact row-group statistics, with date-column batch fallback
- required-value nullability: exact when required footer statistics exist or
  the dataset is within the configured scan-row bound
- primary-key duplicates: exact only when row count is within `max_key_rows`
- infinity count: exact only when row count is within `max_scan_rows`
- skipped checks carry an explicit reason and never imply PASS

State JSON is summarized using a fixed allowlist (`dataset`, `status`, task id,
and counts of operational collections). Error text, credentials, request
parameters, tokens, and arbitrary values are never emitted.

Explicit state-path aliases cover retained multi-dataset and versioned
checkpoints whose filename or internal short name differs from the registered
Dataset Contract. Nested immutable audit states are included and labeled
`IMMUTABLE_AUDIT`; only inventory snapshots identified by their report schema
are excluded. This avoids false missing-state results without treating an audit
as an operational checkpoint.

## Output and mutation

Default operation prints deterministic JSON and concise Markdown to stdout and
does not create a file. JSON/Markdown files are written atomically only when an
explicit output path is supplied. The audit never changes datasets, states,
checkpoints, registries, or Landing.

`--immutable-snapshot` independently rebuilds the report twice and creates or
reuses
`data/state/audits/dataset_inventory_v2/<inventory SHA-256>.json` by durable
temporary file plus no-overwrite hard link. The normal command remains dry-run;
the snapshot option must be explicit.

The latest retained v2 point-in-time snapshot is
[`48ce7887c965830c942e8f125346a7ca2a00e58ec2785c22e15cb509c10bc71f.json`](../data/state/audits/dataset_inventory_v2/48ce7887c965830c942e8f125346a7ca2a00e58ec2785c22e15cb509c10bc71f.json).
It records 42 artifact roots, 95,486,624 rows, 51 registered contracts,
38 observed registered artifacts, 13 missing registered artifacts,
zero unregistered artifacts, and 53 state files. Its classification remains
`READ_ONLY_INVENTORY_NOT_DATA_COMPLETE_ASSERTION`; later repository changes do
not alter this immutable snapshot.

An inventory-specific exclusive lock serializes the final independent rebuild,
CAS rebuild, and publication. It is not a lock for every Data writer and does
not claim to freeze the repository indefinitely: the immutable report is exact
point-in-time evidence for its successfully completed, unchanged pre/post scan.
The stable `.write.lock` uses a nonblocking kernel advisory lock, so ownership
is released automatically if the writer process is terminated. Its redacted
PID/process-start/acquisition metadata is diagnostic only; file existence never
proves a live owner and no PID- or age-based stale-lock deletion is performed.
If artifact, state, or Landing metadata changes before publication, the CAS
fails and no snapshot is linked. Existing targets and all parent components are
rechecked for symlinks, junctions, and reparse points before reads and links.
