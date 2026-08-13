# Market Breadth Retained-Input Rebuild

`kr_market_breadth_daily` is Derived data. Its frozen contract is
`KR_MARKET_BREADTH_DAILY`: key `(date, market)`, partition `(market, year)`, and
nonnegative integer `advancing`, `declining`, `unchanged`, and `total` counts.
The three component counts must sum to `total`.

The rebuild uses only retained `kr_equity_price_daily` and
`kr_equity_canonical_universe_daily` Parquet. It performs no provider calls.
For each security, direction compares the current close with its immediately
preceding retained close observation. Only point-in-time canonical members on
the current date enter the aggregate.

Run the non-mutating gate first:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\rebuild_market_breadth.py `
  --project-root . --mode dry-run
```

The gate requires exact physical Arrow schemas and matching market/year row
identity for both inputs. The retained 63-file breadth layout may differ only
by its known all-nullable physical fields; its logical names, order, types,
rows, keys, and values remain strict. It detects input changes during the run
and validates the complete staged output with the exact contract schema.

Normally every existing derived key and value must be preserved. One corrective
transition is frozen from an independent audit: exactly four additions, nine
old-to-new replacements, and zero deletions across thirteen named keys. The
gate accepts that transition only when every old and new field matches the
embedded delta manifest, the rebuilt output has exactly 15,413 rows and its
frozen semantic fingerprint, and both current price and canonical-universe
inputs match their frozen physical and lossless semantic manifests. One changed
field, extra/missing delta, or input-manifest drift fails closed. This is not a
general exception mechanism.

The state records the exact delta and bindings, the correction rationale, the
limitation that retained historical state cannot prove the old values came from
the current input revisions, and the schema-only migration semantic guarantee.
Apply requires the exact dataset confirmation:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\rebuild_market_breadth.py `
  --project-root . --mode apply `
  --confirm-rebuild kr_market_breadth_daily
```

Apply stages the complete dataset and deterministic state before promotion.
The state records zero API calls, input contract versions and byte manifests,
the output manifest, coverage, rows, semantic fingerprint, and the fingerprint
of preserved existing rows. A transaction marker provides rollback or finalizes
a verified promotion after interruption. Do not run apply concurrently with an
equity input writer. The single-writer lock and compare-and-swap checks cover
the existing output and state. A `VERIFIED` transaction is finalized only after
the promoted output manifest and state hashes match the marker; otherwise all
backups remain for inspection. Backup retirement is journaled before each
deletion, so recovery can resume after either the output backup or state backup
has already been removed. Once `OUTPUT_BACKUP_RETIRING` is durable, recursive
deletion is resumable even if only part of the backup tree was removed; the
canonical promoted output/state pair is reverified, while the intentionally
partial retired backup is not mistaken for corruption. Before fresh-run access, resolved paths for both
inputs, output, state, marker, lock, stage, and backups must remain beneath the
resolved project root; junction or symlink escapes fail closed.

The retained price and canonical-universe roots must first complete their
contract-schema migrations. Do not run the real dry-run or apply during the
A007 disk window.
