# Corporate-action canonical identity and economic-terms audit

Status: **CANONICAL_IDENTITY_BLOCKED / ECONOMIC_TERMS_INCOMPLETE**  
Audit date: 2026-08-15  
Network and data mutation: none

Bounded official-documentation recheck: **2026-08-20 / no provider API call**.
The contract-only schemas and pure fail-closed selector are implemented in
`src/stock_data/contracts/corporate_actions.py` and
`src/stock_data/derived/corporate_action_adjustment.py`. They remain
unregistered and confer no collection or promotion authority.

This audit evaluates only retained source observations. It does not create a
canonical event dataset and does not authorize adjusted-price or total-return
calculation.

## Retained observations

| Source dataset / evidence | Retained scope | What it proves | Limitation |
|---|---:|---|---|
| `kr_equity_dividend_source_observation` | 71,652 rows; one 2026-08-08 source snapshot | Source-reported record, cash-payment and stock-delivery dates plus cash/stock dividend terms | One capture is not revision history; no announcement/publication time or canonical event ID |
| `kr_equity_rights_schedule` | 13 append-only observations; two retained captures for one 2019-12-31 query scope | Source schedule rows such as record, ex-right and registry-close dates | The 1-row and 12-row captures are incomplete/complete response evidence, not event versions; canonical identity and history are explicitly false in state |
| `kr_equity_stock_issuance_source_observation` | 152,676 rows; 2020-07-14..2026-08-12 source-reference coverage | Issuer/security identity, issue reason, effective/listing dates and issued-share value as reported | One capture; no publication time, stable event ID, revision edge, or complete adjustment mechanics |
| OpenDART ECOPRO BM known-positive evidence | one filing-list row and one combined paid/free-decision row | Positive economic-term schema for one free issue | Filing receipts differ and no explicit parent/original/supersession edge is retained |

## Bounded official-source capability matrix

The following matrix uses only the retained source observations above and the
linked official OpenDART/KRX documentation. A documented field is a candidate
source field, not proof that the field is populated, final, or sufficient to
derive a price factor.

| Action | Exact official candidate and documented terms | Date / revision evidence | Current contract decision |
|---|---|---|---|
| Share split / reverse split | The KRX disclosure handbook defines and requires disclosure of share split/consolidation decisions, but no bounded machine-readable event schema with ratio, exact security ID, ex/effective date, and revision lineage has been accepted | Decision obligation only; no accepted source response or explicit revision chain | `NO_CALL_PLAN / FACTOR_BLOCKED`; do not infer from price or before/after shares |
| Bonus/free issue | OpenDART [`fricDecsn`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020024) and [`pifricDecsn`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020025): ordinary/other new shares, pre-issue shares, par value, shares allocated per existing share, allocation record, delivery/listing, and board-decision dates | One positive combined response exists. `rcept_no` is a filing-version ID; list `rcept_dt` is only a date. No explicit parent receipt, verified ex-date, finality, historical security-class relation, fractional treatment, or paid/free sequence | `SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE / FACTOR_BLOCKED` |
| Rights/paid issue | Retained data.go.kr rights rows provide exercise and registry-close schedules plus par value. OpenDART paid/combined field catalogs do not supply the complete class-specific entitlement, final subscription economics, rights-instrument treatment, ex-right rule, fractions and explicit revision parent required together | Snapshot/capture and schedule dates are not announcement availability. A preliminary ex-right reference price is not the final subscription price | `SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE / FACTOR_BLOCKED`; never turn schedule, par value or issued shares into a price/volume factor |
| Capital reduction | OpenDART [`crDecsn`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020026) documents before/after capital and ordinary/other share counts, reduction ratios, record date, free-text method/reason, trading-stop/new-listing schedule, and board-decision date | Search window is initial receipt date from 2015 onward. No retained positive API row, exact security-class ID, explicit parent receipt, structured consideration terms, exact ex/effective rule, or completion/finality evidence exists | `SOURCE_OBSERVATION_ONLY_NO_POSITIVE_RETAINED / FACTOR_BLOCKED` |
| Merger | OpenDART [`cmpMgDecsn`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020050) documents merger method/form/ratio and basis, new-share counts, counterparty/new-company names, and expected merger/registration/delivery/listing schedule | No retained positive row, explicit revision parent, exact predecessor/successor security IDs, complete cash/stock consideration contract, or effective/listing finality exists. Names, current ticker and filing-company `corp_code` are never identity edges | `IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE / CONTINUOUS_PRICE_CHAIN_FORBIDDEN` |
| Corporate spin-off / company split | OpenDART [`cmpDvDecsn`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020051) documents division method/ratio, transferred business/property, surviving/new-company names, listing-maintained/relisting-application indicators, reduction-related terms, division/registration and decision dates | This is a company division endpoint, **not a share-split endpoint**. No retained positive row, explicit revision parent, exact surviving/new security IDs/classes, final listing relation, or effective finality exists. Names/current ticker/`corp_code` cannot create the mapping | `IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE / CONTINUOUS_PRICE_CHAIN_FORBIDDEN` |
| Ticker / security identity change | OpenDART [`list`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001) exposes the filing company's current `corp_code` and `stock_code`; current KRX listing facts do not establish a historical predecessor/successor edge | No accepted effective-dated ticker relation or recycled-code rule | `NO_CALL_PLAN / IDENTITY_LINK_BLOCKED`; never join by name/ticker alone |
| Cash dividend | Retained data.go.kr snapshot has record/payment/delivery dates and cash/stock amounts/ratios. OpenDART [`alotMatter`](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019005) is a periodic-report summary (`se`, share class, current/prior values, settlement date), not an event/ex-date feed | No complete event ex-date, declaration availability timestamp, gross/net/tax basis, currency lineage, or explicit revision chain | `MARKER_OBSERVATION_ONLY / NO_PRICE_FACTOR / TOTAL_RETURN_BLOCKED` |

OpenDART `list` with `last_reprt_at=N` preserves all submitted filings and
exposes correction/withdrawal indicators in `report_nm`/`rm`. It does not expose
an explicit original-to-correction parent receipt. Consequently a later filing
must not replace an earlier factor unless `revision_parent_status` is
`VERIFIED_EXPLICIT`; `정`/`철`, issuer/date/name proximity, or matching terms are
insufficient. `rcept_dt` is not a publication timestamp. The only permitted
daily availability bound without stronger evidence is the later of a
conservative filing-date rule and the actual retained capture time.

Landing hashes, capture IDs and source ordinals provide reliable **source
observation identity**. Corporate number and ISIN can support entity/security
matching. Neither is sufficient to assert that two observations are versions of
the same economic event.

## Bonus/free-issue family closure (UR-080)

The retained ECOPRO BM run was not repeated. API use in this closure was zero;
only its immutable response bytes and the official OpenDART/KRX documentation
were reviewed.

OpenDART `list(last_reprt_at=N)` can retain all filings and expose `정` (a later
correction exists) or `철` (withdrawal). It does not expose the parent receipt of
a correction. The retained list receipt `20220406002324` has `rm=정`, while the
structured combined paid/free row has receipt `20220614000068`; proximity,
issuer identity and matching terms cannot manufacture their revision edge or
prove that the latter is final.

The official free/combined schemas expose ordinary/other share buckets, but
these are not an effective-dated security identifier plus exact security class.
They expose the allocation record date, not the exact market ex-right date or an
action-specific machine rule proving which date is the factor effective date.
The KRX material establishes that an ex-right market action exists and gives a
generic book-closure explanation; it does not bind this retained filing's record
date to a verified market-action row. The project therefore never substitutes
the record date, decision date, delivery date, or listing date for the ex-date.

The retained combined row reports 73,351,008 new ordinary shares, 24,530,810
pre-free ordinary shares, and three new shares per existing ordinary share.
Those values do not identify the eligible-share denominator needed to reconcile
the allocation (`24,530,810 × 3` differs from 73,351,008). No cause is inferred.
The official combined endpoint also does not supply the complete subscription
economics and explicit paid/free sequence needed to isolate the free component,
and neither endpoint documents the fractional-share treatment.

`evaluate_bonus_free_issue_factor_evidence` now records this family-specific
boundary. A source version may enter canonical-event review only after all of
the following are explicit: confirmed original status or a verified parent
receipt, stable security identifier and exact class, record/ex/effective dates
with an official action-specific rule, verified finality, positive and
reconciled eligible/new/pre-issue share terms, allocation ratio, par value and
fraction policy, plus complete paid terms and sequencing for a combined action.
Even a passing decision grants no normalization/promotion authority and computes
no factor. The retained case remains `SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE
/ FACTOR_BLOCKED`.

## Capital-reduction family closure (UR-081)

No `crDecsn` provider call was made. The repository contains no immutable
positive `crDecsn` response, and there is no active exact runbook authorizing
the bounded disclosure-list plus one-endpoint pilot. The family therefore stays
API-zero and source-only.

The official `crDecsn` guide documents one receipt ID and ordinary/other
response buckets; affected counts; par value; before/after capital and issued
share counts; percentage reductions; one reduction record date; free-text
method/reason; expected shareholder-meeting, old-share, trading-suspension,
delivery and listing schedules; and the board-decision date. It does not expose
an exact effective-dated security identifier/class, an original-to-correction
parent receipt, verified finality, a dedicated market ex/effective date, or a
structured consideration amount/currency and holder-treatment contract.

An official [KRX/KIND capital-reduction disclosure](https://kind.krx.co.kr/external/2024/10/23/000161/20241023000508/00591.htm)
illustrates why those gaps matter: its full filing separately identifies a 4:1 no-consideration equal
consolidation of registered ordinary shares, the reduction record date, the
next-day legal effective date, a multi-day trading suspension, change-listing
date, and cash-in-lieu treatment for fractions. It also says counts and dates
may change through legal, authority and shareholder-meeting processes. These
full-filing facts are not a retained `crDecsn` response and do not establish its
revision parent or finality. The generic [KRX listing-change procedure](https://global.krx.co.kr/contents/GLB/03/0303/0303050400/GLB0303050400.jsp)
likewise describes capital-reduction registration and a suspension window but
does not bind an individual filing to an exact factor date.

`evaluate_capital_reduction_factor_evidence` now requires an accepted immutable
positive source observation; confirmed original status or an explicit parent
receipt; stable security identifier and exact class; reconciled before, after
and reduced share counts; complete method and equal class-wide holder treatment;
explicit no-consideration or complete cash amount/currency terms; fractional
treatment; record/ex/effective dates with an official action-specific rule; and
verified finality. Counts and capital amounts alone never satisfy the gate. A
passing result only enters later canonical-event review, computes no factor and
grants no normalization/promotion authority. Current disposition is
`SOURCE_OBSERVATION_ONLY_NO_POSITIVE_RETAINED / FACTOR_BLOCKED`.

## Merger identity-discontinuity closure (UR-082)

No `cmpMgDecsn` provider call was made. The repository contains no immutable
positive response, and the review-required two-call budget is not an active
runbook. The family remains API-zero and source-only.

The official endpoint documents the filing company's current `corp_code` and
name, a merger method/form, ratio and textual basis, external evaluation,
ordinary/class-share new-share counts, counterparty and new-company names, and
expected merger, registration, delivery and listing dates. It does not provide
a stable exact predecessor security ID, exact successor security ID, explicit
revision-parent receipt, a fully structured cash/stock/mixed consideration
contract, or proof that the expected effective and listing dates became final.
The generic ordinary/class-share buckets also do not establish an
effective-dated exact security class.

KRX separately classifies merger outcomes as possible [new-stock listings or
relistings](https://global.krx.co.kr/contents/GLB/03/0302/0302020000/GLB0302020000.jsp).
This confirms that the lifecycle and resulting listed security depend on the
transaction structure; it does not supply the event-specific predecessor/
successor mapping. Consequently company names, current tickers, current
`corp_code`, similar business descriptions, or a numerical ratio cannot bridge
the old and new price histories.

`evaluate_merger_identity_evidence` now requires an accepted immutable positive
observation; confirmed original status or an explicit revision parent; complete
merger method/form and consideration type/ratio/basis; distinct stable IDs and
exact classes for predecessor and successor; final merger, predecessor-trading,
successor-listing and successor-trading dates with an official action-specific
rule; verified event and listing finality; and a separately accepted exact-ID
successor-mapping contract. A passing result is still an identity discontinuity
eligible only for mapping review. It grants no promotion and its continuous
price-chain status remains `FORBIDDEN`. Current disposition is
`IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE /
CONTINUOUS_PRICE_CHAIN_FORBIDDEN`.

## Company-division identity-discontinuity closure (UR-083)

No `cmpDvDecsn` provider call was made. The repository contains no immutable
positive response, and the review-required disclosure-list plus endpoint budget
is not an active runbook. The family remains API-zero and source-only.

The official endpoint documents division method, effects, ratio, transferred
business/property, surviving and new-company names and financial summaries,
whether the surviving listing is maintained, whether the new company applies
for relisting, reduction/allocation terms, and expected division, registration
and listing dates. It supplies neither stable exact security IDs/classes for the
surviving and new companies nor an explicit revision parent or proof that the
event and listing outcomes became final. Its reduction/allocation fields are
part of a company-division transaction and must never be routed as a standalone
share-split event.

An official [KRX/KIND division filing](https://kind.krx.co.kr/external/2026/06/02/000342/20260602000833/10085.htm)
shows why exact version and class-specific listing evidence is required: its
division ratio changed after treasury-share cancellation; common shares are
planned for relisting, one preferred class for a new listing, and another class
to remain unlisted; the listed dates are still subject to change. The generic
[KRX listing classification](https://global.krx.co.kr/contents/GLB/03/0302/0302020000/GLB0302020000.jsp)
confirms that companies created by spin-off can use relisting. Neither source
provides a retained API observation plus the complete effective-dated security
mapping needed here.

`evaluate_company_division_identity_evidence` now requires an accepted positive
immutable observation; confirmed original status or explicit revision parent;
explicit company-division-not-share-split classification; complete method,
ratio/basis and transferred-property terms; distinct stable IDs and exact
classes for surviving/new companies; final listing-maintained/change-listed and
relisted/new-listed relations; final effective/registration/listing dates with
an official action-specific rule; event and listing finality; and a separately
accepted exact-ID mapping contract. A passing result remains an identity
discontinuity eligible only for mapping review, grants no promotion, and leaves
continuous price chaining `FORBIDDEN`. Current disposition is
`IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE /
CONTINUOUS_PRICE_CHAIN_FORBIDDEN`.

## Rights-issue economic-term closure (UR-088)

No rights-event provider call was made. The retained data.go.kr rows are thirteen
append-only schedule observations: they preserve exercise/registry-close dates,
issuance reason and par value, but no immutable official event version links
them to an exact security class, entitlement ratio, subscription price or
revision parent. They therefore remain supplementary schedule evidence only.

The official [OpenDART paid-issue decision field catalog](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020023)
does not provide the complete acceptance set in one versioned event. More
importantly, the official [KRX disclosure/listing handbook](https://kind.krx.co.kr/external/dst/reference/11635/%EC%9C%A0%EA%B0%80%EC%A6%9D%EA%B6%8C%EC%8B%9C%EC%9E%A5%20%EA%B3%B5%EC%8B%9C_%EC%83%81%EC%9E%A5%20%EC%97%85%EB%AC%B4%ED%95%B4%EC%84%A4%EC%84%9C.pdf)
requires the planned price and its finalization date, a later correction for the
final price, corrections when non-subscription changes the shareholder ratio,
and later subscription/issuance-result disclosures. An official
[KIND ex-right notice example](https://kind.krx.co.kr/external/2025/03/11/000795/20250310002458/70651.htm)
likewise labels its KRW 5,000 amount as a first issue price and states that the
final issue price is calculated later. Thus a schedule row or preliminary
ex-right price cannot establish final subscription economics or event finality.

`evaluate_rights_issue_factor_evidence` now requires an accepted immutable
positive rights event; confirmed-original state or an explicit revision parent;
exact security ID/class; a positive class-specific entitlement numerator and
denominator with a verified basis; positive currency-bound **final** subscription
price; verified tradable/non-tradable rights-instrument identity and complete
exercise/unsubscribed-share treatment; fractional-share policy; record,
ex-right, factor-effective, subscription and payment dates with an official
action-specific rule; supplementary-schedule linkage or event-native dates; and
final no-superseding/cancellation status. Ex-right and factor-effective dates
must align. A passing result is eligible only for canonical-event review and
still grants no factor computation or promotion authority.

There is no active retry-zero Landing-first runbook and no retained official
positive response satisfying those gates, so the current disposition is
`SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE / FACTOR_BLOCKED`. This is an
intentional API-zero fail-closed result; no factor, scheduler, GUI or canonical
artifact was changed.

## Identity decision

A canonical event must remain separate from its source observations and versions:

1. source observation: provider, operation, capture/response hash and ordinal;
2. source event version: provider-native event or filing-version identifier;
3. revision edge: explicit original/parent/superseded/cancelled relationship;
4. canonical event: stable identity spanning versions and, only with documented
   matching rules, providers;
5. dated security identity: issuer, ISIN/security class, predecessor/successor.

The retained evidence supports item 1 and partial entity/security matching. It
does not support items 2-4 consistently. Names, nearby dates, issue sequence/round,
or matching terms must not manufacture a revision or canonical-event edge. The
OpenDART receipt numbers `20220406002324` and `20220614000068` identify different
filing versions, but retained rows contain no explicit relationship between them.

Decision: **do not create a canonical corporate-action artifact from the current
observations.** The unregistered contract-only boundary must not be populated
until an observation passes its exact identity, PIT, finality, and economic-term
gates.

## Date and availability semantics

| Date class | Retained examples | Allowed interpretation |
|---|---|---|
| Capture / source-reference | `captured_at_utc`, `source_snapshot_date`, requested `basDt` | When this project observed or queried the source; not historical market knowledge time |
| Announcement / knowledge availability | OpenDART filing date/receipt where present | Provider filing observation only; broad action coverage, correction lineage and intraday availability remain incomplete |
| Record / entitlement | dividend record date, rights record date, free-issue allocation record date | Economic entitlement date; not announcement time |
| Ex-date | rights schedule ex-right row when explicitly present | Price-entitlement transition for that source row only; absent for much of the retained corpus |
| Effective / lifecycle | issue effective date, exercise window, registry close | Action-specific legal/operational date; not knowledge time |
| Settlement / delivery | cash payment, stock delivery, listing date | Delivery/listing outcome; not knowledge time |

No event-effective date may be substituted for announcement or availability. A
capture date may be used as a conservative observation-time bound for analyses
performed after capture, but not as a reconstructed historical announcement date.

## Missing evidence for adjustment accounting

All action families require a stable canonical event/version chain, explicit
announcement or knowledge-availability semantics, cancellation/withdrawal status,
security-class and identifier history, and a rule for selecting the version known
at each backtest timestamp.

Additional economic terms are missing or incomplete:

- cash dividends: verified ex-date coverage, currency/tax/gross-net basis and the
  applicable share class;
- stock dividends/free issues: old-to-new share ratio, ex-date, fractional-share
  treatment and version/cancellation lineage;
- rights issues: entitlement ratio, subscription price, tradability/exercise
  treatment, ex-right date and fractions;
- splits/consolidations: exact conversion ratio and effective/ex-date;
- capital reductions: reduction ratio/method and any cash consideration;
- mergers: exchange ratio, cash/stock consideration and predecessor/successor
  security mapping.

`issued_shares` alone, including retained negative values, is not an adjustment
factor and must not be reinterpreted.

## Readiness decision

- Canonical corporate-action dataset: **BLOCKED**.
- Adjusted-price series: **BLOCKED**.
- Total-return series: **BLOCKED**.
- Existing source-observation artifacts: retain and append only under their
  current contracts; they remain useful for source research and future linkage.

## Review-required bounded acceptance plan

Status: **REVIEW_REQUIRED / DO_NOT_EXECUTE**. This is a future source-observation
plan, not an active runbook and not permission to call, normalize, promote, or
schedule anything.

Each pilot is independent and may use exactly one preselected Korean listed
issuer and one official-portal-confirmed positive window of at most 32 calendar
days starting in 2015. Never fan out issuers or action families.

| Pilot family | Exact call budget | Allowed endpoints | Stop/acceptance boundary |
|---|---:|---|---|
| Bonus / combined paid-free revision pair | 3 calls, retry 0, serial | `list.json(last_reprt_at=N,page=1,count=100)` + `fricDecsn.json` + `pifricDecsn.json` | Existing completed known-positive run is not repeated. A future run needs a separately preselected original/correction or withdrawal pair and must prove an explicit parent edge; otherwise retain observations only |
| Capital reduction | 2 calls, retry 0, serial | same bounded `list.json` + `crDecsn.json` | Require one positive documented row, exact share class, before/after counts, method, dates, and a separately evidenced effective/ex-date rule; otherwise no factor |
| Merger | 2 calls, retry 0, serial | same bounded `list.json` + `cmpMgDecsn.json` | Retain filing terms; require stable predecessor/successor security identifiers before any identity link; never create a continuous price chain from names |
| Company split / spin-off | 2 calls, retry 0, serial | same bounded `list.json` + `cmpDvDecsn.json` | Retain division terms; require stable successor IDs and listing relation; never classify this endpoint as a share split |

Every allowed future response must be written byte-for-byte to a new immutable
Landing run before parsing, with sanitized request parameters, response SHA-256,
append-only call ledger, atomic checkpoint, and no credential in artifacts. Stop
on non-200, non-JSON, source status other than `000`/documented `013`, schema or
count mismatch, credential echo, or more than one disclosure-list page. A
completed checkpoint must replay pre-network with API calls `0`. Any promotion
requires a separate reviewed transaction with staging validation, atomic
replacement, injected-failure rollback, prior-root hash preservation, and API-0
replay. No scheduler or broad history is eligible from a pilot.

There is deliberately no call plan for share split/reverse split, ticker change,
event-level cash dividend, or rights issue: no exact accepted machine source
satisfies the required fields. Rights remains schedule/source-observation only
until an official positive event version provides complete final subscription
economics, explicit lineage, exact class/rights treatment and a verified
ex-right rule. These are technical source gaps, not authority to improvise a
factor.

## Offline immutable-acceptance boundary

`stock_data.orchestration.source_acceptance.evaluate_corporate_action_pilot`
now verifies the reviewed OpenDART bonus/paid-free three-operation shape using
the immutable response bytes, checkpoint hashes/classifications, exact response
topology, retry-zero ledger sequence, and terminal call accounting. Passing this
gate accepts only `IMMUTABLE_SOURCE_OBSERVATION_ACCEPTED`; canonical identity and
factor eligibility remain explicitly blocked, and the result grants no live-call
authority.

The source-only acceptance manifest uses the shared recovery-supervisor journal
and checkpoint primitive. Fixture-based failure injection proves restoration of
the exact prior manifest/checkpoint bytes, and an identical completed scope
replays as pre-network `API_ZERO_NOOP`. This closes the offline transaction
boundary, not a provider acceptance run: the retained known-positive call was
not repeated and no exact retained run root was newly selected or promoted.

Different economic and identity semantics are routed to independent queue
requests: bonus/free issue `UR-080`, capital reduction `UR-081`, merger
`UR-082`, company division/spin-off `UR-083`, share split/consolidation
`UR-084`, effective-dated ticker/security identity `UR-085`, event-level cash
dividend `UR-087`, and rights issue `UR-088`. No family may borrow another
family's accepted source shape or finality rule.

Reopen only when official evidence supplies explicit event/revision lineage,
historical announcement availability, and action-family economic terms sufficient
to reproduce adjustment factors without inference.

Related evidence: [OpenDART known-positive audit](../../../archive/data/evidence/2026-08-data-phase/research/OPENDART_FREE_ISSUE_KNOWN_POSITIVE_AUDIT.md),
[Dataset Index](../../DATASET_INDEX.md), and the source-observation contracts under
[`src/stock_data/contracts/`](../../../../src/stock_data/contracts/).
