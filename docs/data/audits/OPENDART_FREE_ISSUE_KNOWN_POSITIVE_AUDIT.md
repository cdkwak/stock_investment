# OpenDART free-issue known-positive audit

Status: **KNOWN_POSITIVE_SCHEMA_CONFIRMED / CANONICAL_IDENTITY_BLOCKED**

The bounded Landing-only run
`20260813T191033Z_1891cf7c6047424aa484f66fea129bfc` queried OpenDART for
ECOPRO BM (`corp_code=01160363`) over `2022-04-01..2022-04-15`. It made exactly
three serial requests with retry count zero. All completed successfully and no
Normalized or canonical dataset was written.

| Operation | Result | Rows | Body SHA-256 |
|---|---:|---:|---|
| `list` | success | 1 | `8989558075ce1dcca810808bee3294815dc0c23e925f7bab66746b5da973249e` |
| `fricDecsn` | valid empty | 0 | `5b9f448765008cd323ab5b94081c873aa00d39974e7ed5aa1964b00a19e8242b` |
| `pifricDecsn` | success | 1 | `74f9309c44e74a8dfed976b7585ebb1ff15c43043726b254ffac555ca5347f58` |

The filing list identifies receipt `20220406002324`, filed 2022-04-06, as
`주요사항보고서(유무상증자결정)` and marks it as corrected. The structured
`pifricDecsn` result identifies corrected receipt `20220614000068`, whose receipt
date lies outside the requested April window. The retained
success row confirms the documented combined paid/free-issue schema and, for the
free issue, reports 73,351,008 new ordinary shares, 24,530,810 ordinary shares
before issue, three new ordinary shares per existing ordinary share, par value
KRW 500, allocation record date 2022-06-28, and listing date 2022-07-15.

These are source-reported economic terms, not an adjustment factor. Receipt number
is a filing-version identity, not a canonical event identity: the list and terms
responses expose different receipts for the original/corrected filing family, and
the retained APIs do not explicitly provide the parent-revision edge or fully define
the date-filter behavior that selected the later receipt. A future
source-observation contract may key rows by operation, Landing body hash, and item
ordinal, but a canonical corporate-action contract remains blocked until revision
lineage and cross-source event matching are independently specified. Splits,
mergers, reductions, rights, dividends, and cancellation/supersession behavior are
still separate evidence gaps. The standalone `fricDecsn` success schema is not
confirmed by this run; only its official valid-empty branch is confirmed. No broad
OpenDART backfill is authorized from this single event.

Retained run hashes: checkpoint
`b4658773b7104e932d37ed83bdde5d48e0703235c8b9c96f776e1a769a230de0`,
ledger `325c6aae3d8956c0dcb239b25b99cd546945253d63bd0a0300c6ac8e657c27be`,
manifest `61e5e11c2f0bd8a4dc1f7d7b4e3d732728dabe41c95995421d4cdda03f099b68`.

The zero-network lineage audit independently rechecks that the retained list
request used `last_reprt_at=N`, then compares the retained list and terms rows.
It finds list receipt `20220406002324`, terms receipt `20220614000068`, and no
explicit parent/original/supersession receipt field in either source row. The
terms receipt's date prefix is also outside the list request's
`2022-04-01..2022-04-15` window. The resulting decisions are therefore
`PARENT_EDGE_UNAVAILABLE_IN_RETAINED_EVIDENCE` and
`SEMANTICS_UNRESOLVED`; neither issuer/date/name proximity nor matching economic
terms may be used to manufacture the missing edge. The reusable audit is
`scripts/manual/audit_opendart_revision_lineage.py` and performs no network or
persistent-data writes.

Official field definitions remain in the archived
[OpenDART pilot record](../../archive/2026-08-data-phase/OPENDART_FREE_ISSUE_PILOT.md).
