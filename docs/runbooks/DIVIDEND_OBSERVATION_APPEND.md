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

## Prepared second-snapshot collection lane

`collect_dividend_snapshot.py` prepares a genuinely new immutable source
snapshot for the offline builder. The endpoint, operation, `basDt` filter,
`pageNo`/`numOfRows` pagination, stable `totalCount`, and eight-page shape are
evidenced by the retained 71,652-row snapshot. The live runner therefore uses
the existing HTTPS `getDiviInfo_V2` operation with page size 9,999, permits at
most ten pages, and requires an explicit source snapshot date.

Each invocation is limited to one or two requests, uses the shared data.go.kr
network lock, and configures zero retries. Every successful page is written to
Landing and hashed before its checkpoint and append-only call-ledger entry are
advanced. Resume verifies every retained page, hash, source snapshot date,
parser, contiguous page number, stable total, and exact expected page row count.
Source fields remain intact in Landing. Access, schema, total-count, page-count,
credential-echo, or page-bound failures stop terminally and retain their evidence;
they require offline audit and cannot silently resume.

After all pages reconcile, the runner atomically creates `full_history.json` and
validates it through `load_dividend_observation`. It does **not** update Normalized
data. Promotion remains the separate reviewed offline command above, preserving
the network/append trust boundary.

Prepared command (not yet executed):

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\collect_dividend_snapshot.py `
  --snapshot-date <YYYYMMDD> --max-calls 2 --confirm-live
```

The requested `basDt` is a source snapshot identity, not an announcement date,
knowledge date, effective date, revision timestamp, or proof of historical PIT
coverage. A second capture may reveal source changes but does not define event
supersession or justify adjusted prices.

### 2026-08-13 execution outcome

The first bounded request for `basDt=20260813` returned source result `00` with
`totalCount=0` and zero items. The page was retained before the runner's former
non-empty local assertion stopped the process. No further request was made.
Because the process stopped before its ledger step, an offline recovery records
exactly one call, zero retries, the retained page hash, and
`http_status_reconstructable=false`; it does not invent the missing transport
status. The checkpoint is terminal `VALID_EMPTY_STOP`, preventing a silent
repeat.

This valid-empty response cannot be passed to the non-empty observation contract
and did not produce `full_history.json` or a Normalized append. The runner now
recognizes exact source-success `totalCount=0` pages and records this terminal
state directly, while mismatched empty pages still fail closed. The retained
2026-08-08 snapshot remains unchanged and authoritative as the only artifact
snapshot. A future attempt requires a separately selected later `basDt`; do not
retry 2026-08-13 merely to seek non-empty data.
