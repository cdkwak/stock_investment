# Dividend source-observation append

`kr_equity_dividend_source_observation` preserves each independently captured
data.go.kr dividend snapshot. It is not a canonical event history, an adjusted
price input, or evidence that an event was known on its effective date.

Run the offline builder only with an already retained, complete Landing JSON:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\build_dividend_observation.py `
  --landing-path <retained-full_history.json>
```

The builder verifies the existing Parquet/state pair before any write. A new
Landing-file SHA-256 is staged as a complete combined artifact; the same
verified snapshot is a no-op. Dataset and state promotion uses a durable phase
journal and same-filesystem renames. Phase records are appended and `fsync`ed
before the next mutation. It is crash-recoverable rather than a
single filesystem-wide atomic operation: startup rolls every pre-verification
interruption back to the verified old pair and finalizes every verified new
pair before removing backups. Journal writes and rename parents are `fsync`ed
where the platform supports it. Ambiguous markers, orphan paths, unexpected
path types, or fingerprint mismatches fail closed for manual inspection.
Recovery fingerprints every existing canonical, stage, backup, and retired
dataset/state artifact and validates the complete layout against the durable
journal phase before its first rename or deletion. A corrupted backup or an
otherwise known-but-ambiguous old/new placement therefore leaves every byte in
place for inspection.

A repeated hash with different rows or manifest, a PK collision, an
unreconciled state, or an incomplete Landing response fails without replacing
the existing artifact. State version 2 records a manifest and normalized-row
fingerprint for every retained snapshot. A verified legacy single-snapshot
state is upgraded on its next explicit offline build.

Before promoting a new snapshot, audit its Landing hash, page/count continuity,
combined PK uniqueness, state manifest, and retained prior-snapshot counts.
Never use this command as a network fallback or infer revision/supersession or
predictive availability from snapshot contents.

If an interrupted run reports an ambiguous or unrecoverable journal, do not
delete hidden `dividend-append` stage/backup/retired paths. Preserve them and
audit the journal plus canonical fingerprints before any manual action.
The journal is recovery evidence, not a multi-writer coordination service;
retain one authoritative dividend-observation writer at a time.
