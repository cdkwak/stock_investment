# Local Data Backup and Restore

Status: `OFFLINE_FIXTURE_VERIFIED / PRODUCTION_SELECTION_AND_RESTORE_PROMOTION_GATED`

This procedure defines a bounded, provider-call-free backup format. It does not
authorize a production backup, a restore over `data/`, deletion, pruning,
compaction, or promotion. The implementation accepts explicit files only; it
never recursively copies the current data tree.

## Inventory policy

| Class | Exact path scope | Backup disposition |
|---|---|---|
| `critical` | explicitly selected `data/raw/**`, `data/normalized/**`, durable `data/state/**` JSON/checkpoints/manifests, Dataset Contract modules, Dataset Index, and the active operation needed to interpret them | include only when each file is named in the reviewed plan |
| `immutable` | explicitly selected lossless `data/landing/**` response/provenance files | include only when each file is named and its plan budget is accepted |
| `reproducible` | `data/derived/**`, `data/published/**`, generated inventories and Health projections that have complete retained inputs and builders | exclude; rebuild from verified inputs |
| `sensitive` | `.env*`, credentials, OAuth/token material, private keys, authentication payloads, Toss/account snapshots, private account identifiers | always exclude; the tool rejects sensitive-looking included paths before opening them |
| `excluded` | `data/staging/**`, `data/quarantine/**`, locks, logs, caches, `.venv/**`, `.git/**`, temporary/smoke roots, backup roots | exclude |

No production files have been copied by the initial implementation. Before a
real backup, the operator must create and review a JSON plan containing exact
relative file paths, classification, validator, and optional Parquet row keys or
required JSON keys. Directories and symlinks are rejected. Critical and
immutable items cannot be silently marked skipped; reproducible, sensitive, and
excluded entries cannot be copied.

Example plan shape (illustrative paths only):

```json
{
  "items": [
    {
      "relative_path": "data/normalized/example/year=2026/data.parquet",
      "classification": "critical",
      "validator": "parquet",
      "row_identity_keys": ["date", "symbol"]
    },
    {
      "relative_path": "data/derived/example/data.parquet",
      "classification": "reproducible",
      "include": false,
      "reason": "rebuilt from contracted Normalized inputs"
    }
  ]
}
```

## Create and verify

Use a backup root outside the source project. `--max-files` and `--max-bytes`
are hard ceilings. Each immutable version contains exact payload bytes, source
mtime in nanoseconds, SHA-256, validator metadata, and a canonical deterministic
manifest. Parquet entries additionally retain schema, row count, and (when
configured) unique row-identity digest. JSON state can require named keys.

```powershell
.\.venv\Scripts\python.exe .\scripts\maintenance\data_backup_restore.py create `
  --source-root . --backup-root D:\stock-data-backup --plan reviewed-plan.json `
  --max-files 500 --max-bytes 2147483648

.\.venv\Scripts\python.exe .\scripts\maintenance\data_backup_restore.py verify `
  --backup-root D:\stock-data-backup
```

Publication builds a same-volume temporary directory, verifies it, renames it to
`versions/<manifest-sha256>`, then atomically replaces `LATEST.json`. Failure
before the pointer update preserves the prior last-valid pointer and immutable
version. Existing versions are verified and reused, never overwritten.

## Restore drill and approval boundary

```powershell
.\.venv\Scripts\python.exe .\scripts\maintenance\data_backup_restore.py restore-staging `
  --backup-root D:\stock-data-backup `
  --staging-destination D:\stock-data-restore-review\candidate
```

The destination must not exist. Restore rereads the backup manifest, verifies
every hash and semantic assertion, writes `RESTORE_VERIFIED.json` with
`production_promotion_authorized=false`, and atomically publishes only that
isolated staging directory. Corruption, missing files, unexpected files, schema
or row-identity mismatch, invalid state, and interrupted copying fail closed.

There is deliberately no production-promotion command. Replacing any path under
the repository `data/` tree requires a separate user-approved request naming:

1. the exact manifest SHA-256 and production targets;
2. a verified pre-restore production manifest and recoverable backup;
3. dataset-contract validation and owner-specific state/checkpoint rules;
4. one atomic promotion and rollback plan; and
5. post-promotion read-back plus service/Health verification.

Without that approval, stop after isolated staging verification.

