# Korean security/ticker identity source discovery

Status: `PARTIAL_OFFICIAL_MACHINE_SOURCES / EXPLICIT_EDGE_SOURCE_NOT_FOUND`

This UR-085 investigation is source research only. It did not call an API,
capture provider data, promote a dataset, or change the corporate-action runtime
contract. The conclusion is deliberately fail-closed: current issuer name,
`corp_code`, corporate registration number, or six-character ticker cannot create
a historical security relationship.

## Decision

No reviewed official machine-readable source was found that supplies the complete
effective-dated relationship required by this project: predecessor ISIN,
successor ISIN, relationship type, effective date, relisting identity, recycled
short-code handling, publication time, correction lineage, and finality.

Official APIs do provide useful **identity nodes and observations**. They can
detect some ticker/ISIN changes and short-code collisions, but they cannot by
themselves prove that two different securities are economic or legal successors.
Therefore they are eligible only for a future bounded source pilot, not canonical
identity edges or continuous price-history joins.

## Official source matrix

| Official source | Machine-usable facts | What it can establish | Missing for canonical edges | Current decision |
|---|---|---|---|---|
| KRX Open API KOSPI/KOSDAQ/KONEX issue basic information | Date-requested issue panels include standard issue code/ISIN, short code, names, listing date, market, security class, stock class, par value, and listed shares | Effective-dated node observations when every requested `basDd` response is retained; same-day `(market, ISIN)` identity; short-code-to-ISIN collisions across captures | No predecessor/successor field, relationship type, delisting edge, relisting parent, publication timestamp, correction chain, or revision freeze | `PRIMARY_NODE_CANDIDATE / EDGE_BLOCKED` |
| Financial Services Commission `GetKrxListedInfoService/getItemInfo` | REST JSON/XML with base date, short code, ISIN, market, item name, corporate registration number and corporation name; daily update | A second official node panel and possible range-filtered identity observation | Page documents T+1 business-day availability after 13:00 but no revision/freeze policy; no predecessor/successor or relisting relation; foreign companies may lack corporate registration number | `NODE_CROSSCHECK_CANDIDATE / EDGE_BLOCKED / PIT_LIMITED` |
| Korea Securities Depository Stock Information Service | XML lookup from short code to ISIN/basic issue facts and ISIN to listing/delisting dates; official page also lists market-wide short-code lookup | Current/queried ISIN cross-check and per-ISIN listing/delisting dates | Short-code lookup has no effective-date parameter; no history or recycled-code relation; no predecessor/successor; no correction lineage/finality; usage is attribution/non-commercial and only 100 development calls | `POINT_LOOKUP_CROSSCHECK / EDGE_BLOCKED` |
| KRX KIND listing/change/relisting notices | Official notices visibly carry event dates, listing/change reasons, ISINs and short codes; KRX defines relisting and listing change | Manual event evidence and a candidate case list for later validation | No reviewed documented bulk/API contract for these notices; HTML layout is not a stable schema; examples do not consistently provide old and new ISINs as an explicit typed edge; notice amendment/finality lineage is unresolved | `OFFICIAL_HUMAN_EVIDENCE / AUTOMATION_BLOCKED` |
| OpenDART current `corp_code`/`stock_code` and merger/division filings | Filing-company identity and event terms | Filing and issuer evidence | Security-level predecessor/successor ISINs are not complete; names cannot be used to bridge the gap | `EVENT_CONTEXT_ONLY / EDGE_BLOCKED` |

Official references reviewed 2026-08-20 KST:

- [KRX Open API KOSPI issue basic information](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=PiwgMdTwmsenXhmqqxuj)
- [KRX Open API KOSDAQ issue basic information](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=CifLHplnUFMgpHIMMPXs)
- [FSC KRX listed issue information](https://www.data.go.kr/data/15094775/openapi.do)
- [KSD Stock Information Service](https://www.data.go.kr/data/15157413/openapi.do)
- [KRX listing-type definitions](https://global.krx.co.kr/contents/GLB/03/0302/0302020000/GLB0302020000.jsp)
- [KRX example of a relisting notice with a new ISIN/short code](https://kind.krx.co.kr/external/2020/09/18/000556/20200918001282/68156.htm)

The KRX definition says relisting includes stock issued by a company established
through merger or split, and listing change replaces existing stock when listed
details change. That establishes the event categories, not a machine-readable
security-to-security edge.

## Fail-closed research contract

### Node observation

Every future retained observation must preserve, without filling:

- `observation_date`, `captured_at_utc`, `available_at_utc` when documented;
- `market`, `isin`, `short_code`, full/short names, security class and stock class;
- listing/delisting dates only when the source explicitly supplies them;
- `source`, endpoint/operation ID, request scope, page number/total count,
  immutable Landing hash, response result code, and capture version;
- `publication_status`, `revision_status`, and `pit_status` as typed values.

The node key is `(observation_date, market, isin)`. A short code, issuer name,
corporate registration number, or OpenDART `corp_code` is an attribute, never the
security key.

### Identity edge

An edge is valid only when one official retained record explicitly supplies or
unambiguously binds all of:

- `predecessor_isin` and `successor_isin`;
- `relationship_type` from a closed set such as `ticker_change`, `listing_change`,
  `relisting`, `merger_successor`, `split_successor`, or `recycled_short_code`;
- `effective_date`, market and security class;
- official stable event/notice identifier plus publication/capture time;
- correction/amendment relation and accepted finality state.

If the source does not provide both security IDs, the record remains an event or
node observation and `identity_edge_status=BLOCKED_MISSING_EXPLICIT_IDS`.

### Recycled-code handling

- The same short code mapped to two different ISINs is a collision, not
  continuity.
- Non-overlapping date intervals may classify the code as potentially recycled,
  but do not create a predecessor/successor edge.
- Overlapping intervals, duplicate `(date, market, short_code)` mappings, a gap
  followed by reappearance, or one ISIN appearing under incompatible security
  classes quarantine the affected scope.
- Name equality, fuzzy name matching, adjacent dates, issuer equality, and price
  continuity are never edge evidence.

### Revision and finality

The FSC page documents daily T+1 availability after 13:00 KST. It does not
document a correction freeze. KRX/KSD references reviewed here also do not expose
a source revision sequence or finality timestamp for the needed identity history.
Consequently all future pilot observations start as
`AS_RETRIEVED / REVISION_UNKNOWN / PIT_BLOCKED`. A later unchanged re-capture may
be retained as empirical stability evidence, but it cannot be relabelled official
finality without an official rule.

## Bounded next evidence plan (not authorized to execute)

Before any provider call, an active runbook must fix:

1. exact KRX/FSC/KSD endpoint and entitlement/key route without exposing secrets;
2. four preselected official event IDs covering same-ISIN attribute change,
   different-ISIN relisting/split, delisting, and a suspected recycled short code;
3. exact observation dates immediately before/on/after each effective date;
4. page and call budget, serial pacing, timeout, retry zero, and Landing-first
   immutable capture;
5. schema/result/pagination validation, atomic checkpoint and API-zero replay;
6. a second capture date for empirical revision comparison;
7. acceptance only for node intervals and collisions unless an official response
   explicitly provides the complete edge contract above.

Until that runbook and evidence exist, `predecessor_security_id`,
`successor_security_id`, ticker continuity, relisting continuity, and recycled-code
relationships remain null/blocked. No price history may be joined or adjusted from
this research.
