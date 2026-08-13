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
| Investor H4 + boundary pair | H4 `AMBIGUOUS_STOP:154/490`; exact positive KOSPI-volume suffix 2017-05-22..2018-01-05. Audited pair `BOUNDARY_SHAPED_CONFIRMED`: sole positive 2017-05-22, 2017-05-19 absent | KOSDAQ/value/date-parity gates remain; no retry, synthesis, or production resume |
| Investor parity access pause | first planned parity scope, KOSPI trading value, returned retained HTTP 403 restriction HTML; retry zero and calls 2/3 were not made | `PAUSED_ACCESS_SAFETY`; no further access probe; KOSDAQ volume/value parity remains unknown |
| Six schema migrations | completed; exact contract schemas with logical values preserved | do not invent retrospective migration provenance |
| Market breadth | DATA_COMPLETE; 15,417 rows through 2026-08-11; the frozen corrective cycle comprised 15,400 unchanged, 9 replaced, 4 added, 0 deleted, followed by four validated daily incremental rows across 2026-08-10..11 | frozen corrective evidence and both incremental Landing/checkpoint chains are retained |
| Rights | 13 immutable partial source observations across two response identities; the completion response returned its declared 12/12 rows | canonical economic-event identity, terms, announcement/revision semantics, and broader historical coverage remain blocked |
| Dividend | 71,652 immutable rows from one retained snapshot; the 2026-08-13 second-snapshot attempt returned exact source-success valid-empty (0/0) and was not retried | no historical PIT/correction history; wait for a genuinely new non-empty capture |
| BOK ECOS Treasury | ARTIFACT_COMPLETE / PROVENANCE_LIMITED; 29,674 source-observation rows, six tenors, 1998-11-13..2026-08-13 | publication/revision timing remains unknown; predictive use stays blocked |
| OpenDART free issue | bounded three-call pilot completed with three HTTP-200 source-status-013 valid-empty responses | retain evidence; select a known-positive official filing window before another pilot |
| KB realtime | one bounded token check reproduced HTTP 500/result 9999/process E021; no market/account/order call | provider/app authorization requires external resolution |
| Treasury spread | artifact-complete and reproducible from retained yields | upstream availability/provenance limits predictive use |
| Yahoo/FRED | immutable artifact audits passed; bounded current and 2008 ALFRED audits found no multi-version DGS10 differences | historical Landing/call-ledger provenance cannot be reconstructed; keep the ALFRED contract draft until revision evidence or a provenance-only policy exists |
| DATA.GO equity increment | price, market cap, provider universe, canonical universe, and breadth validated through 2026-08-11 | advance only one evidenced source date at a time; do not infer later publication availability |
| Stock lending | three artifacts passed integrity and source reconciliation | execution accounting remains `REVIEW_REQUIRED`; do not rerun merely to recreate it |

## Operating constraints

- Do not rerun A007 Trading or Balance.
- Do not retry S1 or resume Investor from the current evidence.
- Do not retry the parity 403 or issue another KRX recovery probe while access is paused.
- Any later KRX request requires explicit D authorization, cooldown confirmation,
  and exactly one active KRX stream.
- Do not create retrospective source or migration provenance.
- Do not proceed to Feature, Backtest, GUI, or trading/order execution.
- Do not push a remote branch from this handoff.

The latest immutable inventory v2 snapshot
`48ce7887c965830c942e8f125346a7ca2a00e58ec2785c22e15cb509c10bc71f`
was created from a quiescent point-in-time scan after the credential-validation
cycle and before the 2026-08-11 equity increment. It records 42 artifact roots,
95,486,624 rows, 51 registered contracts, 38 observed registered artifacts,
13 missing registered artifacts, zero unregistered artifacts, and 53 state
files. Its classification is
`READ_ONLY_INVENTORY_NOT_DATA_COMPLETE_ASSERTION`; it does not assign completion
status and must not be silently updated for later evidence.
