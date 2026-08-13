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

The checkpoint records the production pre-manifest, request plan, call/status
accounting, capture hashes, overlap revision counts, candidate manifest, and
publication state. Omitted retained keys inside the returned response range,
schema failure, unexpected coverage, or production drift fails closed. Existing
production roots remain byte-identical.

Use `--end` for the reviewed completed-source date and
`--confirm-live-landing-only`. Review the Landing bodies, frozen plan, revision
report, candidate coverage, and manifests before publication.

Publication is a separate zero-network command using `--promote-checkpoint` and
`--confirm-offline-promotion`. It performs a content-manifest CAS and installs a
copy of each whole candidate root with rollback. A yield candidate also rebuilds
the Treasury spreads; yield and spread roots promote in the same transaction.
Candidate evidence remains retained after promotion.

Run and audit Yahoo, FRED yields, and FRED FX separately. A failure in one phase
cannot partially publish another. FRED current observations do not establish
vintage/revision history; retained historical provenance limitations remain.
