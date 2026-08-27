# Dividend source-observation offline append

Status: **CURRENT_OFFLINE_OPERATION**

This procedure appends one already retained, complete data.go.kr dividend Landing
JSON to `kr_equity_dividend_source_observation`. It performs no network request and
does not establish canonical event identity, announcement time, revision lineage,
effective-time knowledge, adjusted-price eligibility, or PIT safety.

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\build\build_dividend_observation.py `
  --landing-path <retained-full_history.json>
```

## Required gates

- Accept only a complete retained Landing JSON whose page and `totalCount`
  continuity has already been audited.
- Verify the existing Parquet/state pair before any write.
- Verify combined primary-key uniqueness, the state manifest, retained prior
  snapshot counts, and the new Landing SHA-256.
- Treat an identical verified Landing SHA-256 as an idempotent no-op.
- Fail closed on incomplete pagination, repeated hashes with different rows or
  manifests, PK collisions, state disagreement, unexpected path types, or any
  fingerprint mismatch.
- Keep one authoritative dividend-observation writer at a time. This operation is
  never a network fallback.

## Crash recovery

Dataset and state promotion uses a durable phase journal and same-filesystem
renames. Journal phases and rename parents are `fsync`ed where supported. Startup
rolls every pre-verification interruption back to the verified old pair and
finalizes every verified new pair before removing backups.

Recovery fingerprints every canonical, stage, backup, and retired dataset/state
artifact before its first rename or deletion. If the journal is ambiguous or
unrecoverable, preserve all hidden `dividend-append` stage, backup, retired, and
transaction paths for manual audit; do not delete or improvise recovery.

Historical 2026-08-08 and 2026-08-13 capture evidence is retained in the
[execution audit](../../archive/data/audits/2026-08-data-phase/DIVIDEND_SNAPSHOT_EXECUTION_20260808_20260813.md).
