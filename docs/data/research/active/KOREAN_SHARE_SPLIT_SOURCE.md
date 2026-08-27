# Korean share split and reverse-split source discovery

Status: `OFFICIAL_EVENT_DOCUMENTS_EXIST / STABLE_MACHINE_EVENT_SCHEMA_NOT_FOUND`

This UR-084 investigation is source research only. It made no provider/API call,
did not inspect credentials, and did not capture or promote market data. It also
did not infer a corporate action from prices, listed-share changes, or names.

## Decision

No reviewed official source satisfies the complete machine contract required for
canonical Korean share split/reverse-split events: exact security ID, explicit
conversion ratio, distinct decision/ex/effective dates, publication availability,
correction lineage, and accepted finality.

KRX KIND publishes official individual change-listing notices whose rendered
content can contain an ISIN, short code, before/after par value, before/after share
counts, issue date, change-listing date, and a reason such as `액면분할` or
`액면병합`. These notices are strong human-verifiable event evidence. However, no
reviewed official bulk/API specification defines their fields, versioning,
pagination, correction relationship, finality, or historical completeness.
Treating the notice HTML layout as an undocumented scrape is not accepted.

OpenDART supplies a documented disclosure search API and an original-document ZIP
download keyed by receipt number. It does not document a structured stock
split/reverse-split decision endpoint. Its documented `회사분할 결정` and
`회사분할합병 결정` APIs concern division of a company/business, not division or
consolidation of each outstanding share. Original filing documents may provide
decision terms, but they are not a stable field-level response schema and do not
guarantee a security-level ISIN or an explicit revision-parent edge.

The result is fail-closed: these sources may seed a later evidence pilot, but no
canonical split factor, price adjustment, identity join, or Backtest history can
be produced from this research.

## Official source matrix

| Official source | Verified useful facts | Missing contract elements | Decision |
|---|---|---|---|
| KRX KIND individual `변경상장(액면분할/액면병합)` notices | Examples expose standard security code/ISIN, short code, before/after shares and par value, issue date, change-listing date, reason, and notice URL | No reviewed documented API/bulk response schema; ex-date and original decision date are not present in the examples; no correction-parent field, revision sequence, finality policy, availability SLA, or completeness guarantee | `OFFICIAL_HUMAN_EVIDENCE / AUTOMATION_BLOCKED` |
| OpenDART disclosure search (`list.json`) | Receipt number, issuer/corporation identity, report name, receipt date, submitter, market authority marker, correction marker in report title | Search result is not a split-event schema; ticker/issuer identity is not an exact security identity; report-title correction marker is not an explicit parent/child relationship or finality state | `DISCOVERY_CANDIDATE / EVENT_BLOCKED` |
| OpenDART original document (`document.xml`, ZIP) | Immutable receipt-scoped original filing material can be retained and hashed | Document templates are not a documented normalized event response; exact ISIN, all required dates, and explicit correction lineage are not guaranteed | `LANDING_EVIDENCE_CANDIDATE / NORMALIZATION_BLOCKED` |
| OpenDART `회사분할 결정` / `회사분할합병 결정` APIs | Structured company/business division decisions | Different legal event from share split or reverse split; must never be mapped to a per-share conversion factor | `OUT_OF_SCOPE` |
| KRX Open API/FSC listed-issue/KSD stock-information panels | ISIN/short-code/current issue facts and, in some services, listing or delisting dates | Node observations only; no explicit split decision, conversion ratio, ex/effective date set, or correction/finality lineage | `IDENTITY_CROSSCHECK_ONLY` |

Official references reviewed 2026-08-20 KST:

- [OpenDART disclosure search API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)
- [OpenDART original disclosure document API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)
- [OpenDART company-division decision API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020051)
- [OpenDART company-division/merger decision API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020052)
- [KRX KIND LS ELECTRIC split change-listing notice](https://kind.krx.co.kr/external/2026/04/10/000814/20260410001941/68155.htm)
- [KRX KIND SK Securities reverse-split change-listing notice](https://kind.krx.co.kr/external/2026/04/22/000186/20260422000598/68155.htm)
- [KRX listing-change definition](https://global.krx.co.kr/contents/GLB/03/0302/0302020000/GLB0302020000.jsp)
- [FSC KRX listed-issue information](https://www.data.go.kr/data/15094775/openapi.do)
- [KSD Stock Information Service](https://www.data.go.kr/data/15157413/openapi.do)

## Required event contract

A future source is acceptable only if one retained official event record, or a
documented official key relationship between retained records, supplies all of:

- `event_id`, `source_receipt_id`, `source`, immutable Landing hash and URL;
- `security_isin` and market/security class; ticker and issuer name are attributes;
- `action_type` from the closed set `share_split` or `reverse_split`;
- explicit `old_share_units`, `new_share_units`, and normalized
  `new_shares_per_old_share`;
- distinct `decision_date`, `record_date`, `ex_date`, `effective_date`, and
  `change_listing_date`, preserving null when the source omits a date;
- `published_at`, `available_at`, capture time and the official availability rule;
- `revision_parent_event_id`, revision sequence, `finality_status`, and the rule
  that makes the accepted revision final.

The ratio may be normalized only from an explicit official ratio or explicit
old/new par-value terms in the same event record. Before/after total shares alone
are not a ratio source because cancellations, fractional-share treatment, other
capital actions, and rounding can change the totals. A price jump is never event
evidence. `change_listing_date` must not be substituted for ex-date or effective
date, and ticker/name/corporation code must never replace the ISIN.

## Revision and finality rules

- A report title such as `[기재정정]` is a revision signal, not a proven link to
  the superseded receipt.
- Every receipt/document capture must remain immutable; later captures append and
  never overwrite an earlier event version.
- Without an official parent link and finality rule, classify the candidate as
  `REVISION_RELATION_UNRESOLVED / FINALITY_UNKNOWN`.
- A KIND change-listing notice may be later lifecycle evidence, but it does not by
  itself prove the terms and dates of the original decision or a revision freeze.
- An unchanged re-capture is empirical stability evidence only, not official
  finality.

## Bounded retry-zero pilot boundary

Current authorized execution is API zero: source pages were reviewed, but no API,
document-download, KIND collection, or promotion was performed. The retained
research document and checkpoint are the API-zero replay evidence.

A future active pilot may be approved only after an official, documented access
route is selected. Its fixed scope must be:

1. preselect exactly two official receipt/notice IDs: one split and one reverse
   split, including the expected ISIN before any call;
2. make at most one OpenDART disclosure-list call for each preselected issuer and
   exact date window, then at most one original-document call per accepted receipt;
3. use serial calls, finite timeout, retry zero, no fallback endpoint, and no KIND
   HTML automation unless KRX documents that route as a machine interface;
4. retain response bytes and request metadata in Landing before parsing; never log
   an authentication key, header, or full request URL containing a key;
5. validate HTTP/status, ZIP/JSON structure, receipt ID, report name, date window,
   pagination, and non-empty content before any atomic checkpoint;
6. accept only records satisfying the complete event contract above; quarantine
   every missing/ambiguous field without calculating a factor;
7. replay the retained receipts at API zero and compare normalized output before
   any proposal for promotion.

Until a source passes that pilot and revision/finality review, share split factors
remain null and the Dashboard research layer, canonical history, and Backtest must
not consume these candidates.
