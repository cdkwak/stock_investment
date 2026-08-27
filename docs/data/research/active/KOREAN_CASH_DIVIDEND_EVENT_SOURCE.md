# Korean event-level cash-dividend source acceptance

Status: `PARTIAL_OFFICIAL_MACHINE_SOURCES / COMPLETE_EVENT_CONTRACT_NOT_MET`

This UR-087 investigation assesses source quality at one economic cash-dividend
event and one exact security class per row. It made no provider/API call, did not
inspect credentials, and did not capture or promote data. Cash dividends remain
markers only: this research authorizes neither a price factor nor total-return,
tax, or reinvestment calculations.

## Decision

No reviewed official source supplies the full accepted contract: declaration
availability, exact security-class ID, explicit record/ex/payment dates,
amount/currency, gross/net/tax basis, finality, and explicit revision lineage.

The strongest machine candidates are the Korea Securities Depository (KSD)
`getDivInfo` cash-dividend operation and its `getCorpActionDtList` rights schedule,
with the Financial Services Commission (FSC) rights-schedule API as a dated
secondary panel. They can provide payment/rights observations and issuer-linked
schedules, but the reviewed official specifications do not establish a complete
event key, declaration timestamp, explicit correction parent, revision sequence,
or finality rule. The catalog-level `real-time` label for KSD is not a per-event
publication timestamp or a correction freeze.

KRX KIND `현금ㆍ현물배당 결정` notices are official event evidence and visibly
carry board-decision, record and proposed payment dates plus ordinary/preferred
amounts. Corrections may show old/new values and the original filing date.
However, the examples do not expose an exact ISIN for each class, an explicit
ex-date, a normalized currency/gross-net/tax field set, or an explicit parent
receipt identifier and finality state. Free-text tax notes can materially change
the meaning of a payment, so they must not be silently mapped to a generic gross
cash amount.

OpenDART periodic `alotMatter` and the FSC `getDiviDiscInfo_V2` representation are
fiscal-period summaries, not declaration events. OpenDART disclosure search and
original-document download may retain a receipt, but no reviewed structured
cash-dividend decision endpoint completes the contract. Accordingly all candidates
remain fail-closed markers.

## Source and quality matrix

| Source and grain | Useful official evidence | Contract failures | Severity / confidence | Decision |
|---|---|---|---|---|
| KSD CorpSvc `getDivInfo`, dividend observation | Official REST/XML operation dedicated to cash-dividend history; KSD source; catalog says real-time; non-commercial attribution license | Reviewed specification does not prove exact per-class ISIN, declaration/publication timestamp, explicit ex-date, normalized currency and gross/net/tax basis, revision parent, sequence, or finality | High / high | `PRIMARY_OBSERVATION_CANDIDATE / CANONICAL_BLOCKED` |
| KSD CorpSvc `getCorpActionDtList`, rights schedule | Official rights-schedule operation that can cross-check issuer-linked action dates | Schedule is not the dividend declaration; catalog does not define a stable join to one dividend event/version or complete amount/basis fields | High / high | `SCHEDULE_CROSSCHECK / JOIN_BLOCKED` |
| FSC Stock Rights Schedule `getRighExerReasSche_V2` | Official JSON/XML schedule, daily update; service description covers rights reasons, record/payment-related dates and registry-closure dates | Issuer/KSD-customer grain is not an exact security-class event; no amount/currency/tax, declaration timestamp, explicit revision relation, or finality | High / high | `T_PLUS_ONE_SCHEDULE_CROSSCHECK` |
| KRX KIND form 61500/71500 event notices | Board decision date, cash/physical type, ordinary/preferred per-share amount, total, record date, proposed payment date, AGM status; correction examples expose old/new fields and original filing date | No reviewed official machine API/bulk schema; no class ISIN, explicit ex-date, normalized tax basis, explicit parent receipt edge, or finality rule; payment may remain proposed | High / high | `OFFICIAL_HUMAN_EVENT_EVIDENCE / AUTOMATION_BLOCKED` |
| OpenDART `list` plus `document.xml` | Receipt discovery, original document retention and correction/withdrawal signals | Current issuer identity is not security class; receipt date is not a publication timestamp; no explicit correction parent/finality; original document is not a normalized event schema | High / high | `LANDING_CANDIDATE / EVENT_NORMALIZATION_BLOCKED` |
| OpenDART `alotMatter` and FSC `getDiviDiscInfo_V2` | Periodic common/preferred dividend amounts and fiscal-period summaries | Wrong grain: current/prior fiscal summary, not one declaration event; no event lifecycle or revision lineage | Critical / high | `OUT_OF_CONTRACT` |
| Retained one-snapshot dividend observations | Large historical payment/record-date coverage useful for cross-checks | One capture cannot establish announcement availability, changes, finality, or point-in-time revision history | High / high | `MARKER_OBSERVATION_ONLY` |

Official references reviewed 2026-08-20 KST:

- [KSD Corporate Information Service (`getDivInfo`, `getCorpActionDtList`)](https://www.data.go.kr/data/15157348/openapi.do)
- [FSC Stock Rights Schedule API](https://www.data.go.kr/tcs/dss/selectApiDataDetailView.do?publicDataPk=15059609)
- [FSC Disclosure Information (`getDiviDiscInfo_V2`)](https://www.data.go.kr/data/15059649/openapi.do)
- [OpenDART periodic dividend summary](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019005)
- [OpenDART disclosure search](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)
- [OpenDART original disclosure document](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003)
- [KRX KIND corrected cash-dividend decision example](https://kind.krx.co.kr/external/2026/03/31/001332/20260331002313/61500.htm)
- [KRX KIND cash-dividend decision with class/date fields](https://kind.krx.co.kr/external/2026/02/26/000556/20260226001262/61500.htm)
- [KRX disclosure/listing handbook search evidence](https://kind.krx.co.kr/external/dst/reference/11499/%28%EA%B3%B5%EC%A7%80%2925%EB%85%84%EC%BD%94%EC%8A%A4%EB%8B%A5%EC%8B%9C%EC%9E%A5%EA%B3%B5%EC%8B%9C%EC%83%81%EC%9E%A5%EA%B4%80%EB%A6%AC%ED%95%B4%EC%84%A4%EC%84%9C.pdf)

## Required event contract

A candidate is accepted only when retained official records explicitly provide
or are joined through an officially documented event/version key to all fields:

- `event_id`, `source_receipt_id`, immutable Landing hash and source URL;
- `security_isin`, market and security class; ticker, issuer name, corporate
  registration number and KSD issuer customer number are attributes only;
- `declaration_date`, `published_at`, `available_at`, capture time and timezone;
- distinct `record_date`, `ex_date`, and `payment_date`, with proposed versus
  actual payment status preserved;
- `amount_per_share`, `currency`, `amount_basis` (`gross`, `net`, or explicit
  source-defined basis), `tax_basis_code`, and source text when tax treatment is
  conditional or exceptional;
- `revision_parent_event_id`, revision sequence, amendment reason,
  `finality_status`, and the official rule/timestamp establishing finality.

No field may be filled from a trading calendar or price movement. In particular,
ex-date must not be derived from record date, payment date must not be replaced by
an issuer's statutory deadline, and `원` in a notice must not be interpreted as a
complete gross/net/tax contract. Common and preferred amounts are separate
security-class observations and may not be combined under one issuer row.

## Revision, availability and finality

- KIND correction sections are useful version evidence, but an original filing
  date plus similar report name is not an explicit receipt-to-receipt parent edge.
- OpenDART `정`/`철` and report-name markers are signals only; proximity or equal
  terms must never create a revision relationship.
- KSD's catalog `real-time` update label has no reviewed per-record publication
  timestamp or revision freeze. FSC's daily service is available after 13:00 KST
  on the next business day, which is a conservative panel bound, not declaration
  availability.
- Proposed payment dates remain proposed. A later payment observation cannot
  silently overwrite the declaration version.
- Without explicit lineage and an official finality rule, use
  `REVISION_RELATION_UNRESOLVED / FINALITY_UNKNOWN / PIT_BLOCKED`.

## Bounded next evidence plan (not authorized to call)

No active provider runbook exists and current call count is zero. Before any call,
a runbook must preselect exactly two official KIND receipt cases: one ordinary-only
cash dividend and one corrected payment-date event, plus their expected ISINs from
an official security master. It must then fix this maximum budget:

1. one KSD `getDivInfo` request per preselected issuer/event window;
2. one KSD `getCorpActionDtList` request per same issuer/window;
3. optionally one FSC rights-schedule request per case only if the exact filter and
   response key are documented before activation;
4. serial requests, finite timeout, retry zero, no fallback endpoint, immutable
   Landing-first bytes and secret-safe request metadata;
5. schema/result/pagination validation, event/class join coverage, date/basis
   completeness and duplicate-key checks before an atomic checkpoint;
6. API-zero replay of retained responses and append-only second capture to test
   revisions; no overwrite or promotion.

The pilot must reject a row if the class ISIN, explicit ex-date, amount/currency
basis, declaration availability, revision relation, or finality rule is absent.
Even a passing marker pilot would not authorize a price factor or total-return
method; those require separate explicit contracts.
