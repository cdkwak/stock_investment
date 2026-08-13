# Data-phase handoff — 2026-08-13

This is the D-owned current-state handoff. It records verified repository facts;
it does not authorize a provider call, alter a Dataset Contract, or advance work
to Feature or Backtest.

| Area | Verified state | Remaining gate |
|---|---|---|
| A007 Trading | DATA_COMPLETE; 9,174/9,174 scopes, 10,161,884 rows; deterministic audit PASS | none |
| A007 Balance | DATA_COMPLETE; 4,958/4,958 scopes, 6,035,958 rows; deterministic audit PASS | none |
| A007 Investor | STOPPED; first production range returned 1/501 dates; no checkpoint or Normalized write | do not resume |
| Investor five-date diagnostic | PASS; exactly 5/5 dates in one business request | evidence only |
| Investor S1 diagnostic | offline PASS `S1_FULL_RANGE_CONFIRMED`; retained request returned 485/485 dates and the original classifier false-negative on verified `CURRENT_DATETIME` remains preserved | evidence only; no retry or production resume |
| Investor H1/H2/H3 diagnostics | audited `PRE_AVAILABILITY_COLLAPSE`; each returned one zero-valued range-end row instead of 502/494/494 expected dates for consecutive 2010-01-04..2016-01-06 windows | boundary remains unresolved; no retry, synthesis, next probe, or production resume is authorized |
| Six schema migrations | completed; exact contract schemas with logical values preserved | do not invent retrospective migration provenance |
| Market breadth | DATA_COMPLETE; 15,413 rows, comprising 15,400 unchanged, 9 replaced, 4 added, 0 deleted | frozen corrective evidence retained |
| Rights | one immutable partial diagnostic source observation | canonical economic-event identity, terms, and historical coverage remain blocked |
| Dividend | 71,652 immutable rows from one retained snapshot | no historical PIT/correction history; wait for a genuinely new capture |
| Treasury spread | artifact-complete and reproducible from retained yields | upstream availability/provenance limits predictive use |
| Yahoo/FRED | immutable artifact audits passed | historical Landing/call-ledger provenance cannot be reconstructed; capture future responses |
| Stock lending | three artifacts passed integrity and source reconciliation | execution accounting remains `REVIEW_REQUIRED`; do not rerun merely to recreate it |

## Operating constraints

- Do not rerun A007 Trading or Balance.
- Do not retry S1 or resume Investor from the current evidence.
- Any later KRX request requires explicit D authorization, cooldown confirmation,
  and exactly one active KRX stream.
- Do not create retrospective source or migration provenance.
- Do not proceed to Feature, Backtest, GUI, or trading/order execution.
- Do not push a remote branch from this handoff.

The immutable inventory v2 snapshot
`b02e39e5b7a1a711062f10ed156456458e4cc5bd281def7d45a942478793b2e6`
was created from a quiescent point-in-time scan before H3. It records 41 artifact
roots, 1,165 artifact files, 95,446,002 rows, 50 registered contracts, 37 observed
registered artifacts, 13 missing registered artifacts, zero unregistered
artifacts, and 50 state files. Its classification is
`READ_ONLY_INVENTORY_NOT_DATA_COMPLETE_ASSERTION`; it does not assign completion
status and must not be silently updated for later evidence.
