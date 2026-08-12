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
Landing-file SHA-256 is appended atomically; the same verified snapshot is a
no-op. A repeated hash with different rows or manifest, a PK collision, an
unreconciled state, or an incomplete Landing response fails without replacing
the existing artifact. State version 2 records a manifest and normalized-row
fingerprint for every retained snapshot. A verified legacy single-snapshot
state is upgraded on its next explicit offline build.

Before promoting a new snapshot, audit its Landing hash, page/count continuity,
combined PK uniqueness, state manifest, and retained prior-snapshot counts.
Never use this command as a network fallback or infer revision/supersession or
predictive availability from snapshot contents.
