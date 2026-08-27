# PIT-Safe Sector Taxonomy, Membership, and Input Feasibility

Status: `RESEARCH_CONTRACT / NUMERIC_CONSUMER_NOT_READY`

Contract version: `sector-input-feasibility/v1`

## Purpose and authority

This Data-owned contract is the feasibility boundary for the Backtest
[`sector-research/v1`](../../../backtest/SECTOR_RESEARCH_CONTRACT.md) consumer.
It inventories candidate authorities and current evidence; it does not select a
provider, authorize a collector, promote data, calculate a candidate, rank an
instrument, or inspect the sealed final holdout.

The states in this document have narrow meanings:

- `SUPPORTED`: an already accepted retained input supplies the stated,
  deliberately narrow consumer role with explicit identity, timing, units,
  rights, and PIT limits. It does not imply that sector research as a whole is
  ready.
- `RESEARCH_ONLY`: a candidate authority or retained observation can support
  source/contract research, but one or more role-complete PIT, finality,
  identity, schema, coverage, or rights gates remains open. Numeric Backtest
  consumption is forbidden.
- `UNAVAILABLE`: no role-complete binding exists. Numeric calculation and
  substitution are forbidden.

`candidate authority` below means only the source family whose own identity
could be researched. It is not a source selection, equivalence, entitlement,
or retention claim. The current local authority is
[`SOURCE_REGISTRY.md`](../../SOURCE_REGISTRY.md); official methodology pages
are supporting references only.

## Temporal and identity contract

A future accepted taxonomy record must contain
`market`, `taxonomy_owner`, `taxonomy_id`, `taxonomy_version`, `sector_id`,
`sector_label`, `published_at`, `retrieved_at`, `revision_id`, and the half-open
interval `[effective_from, effective_through)`. `effective_through` may be null
only for the version known to be open at the decision cutoff. A missing source
field remains null with a typed unavailable reason; retrieval time never stands
in for publication or effective time.

A future security/universe record must bind a stable security/share-class
identity separately from ticker, issuer, exchange, and listing interval. A
future membership record must bind that security identity to exactly one
taxonomy version and sector identity over its own half-open
`[effective_from, effective_through)` interval, with `source_observed_at`,
`published_at`, `retrieved_at`, `usable_from`, and `revision_id`. Corrections
create a new revision; they do not silently rewrite the version visible at an
older decision time.

For a decision time `t`, only a row with `usable_from <= t` and
`effective_from <= t < effective_through` (or a valid open end) may be used.
No current list, current label, nearest observation, later survivor, or current
taxonomy may be applied backward. Korea and U.S. taxonomies are independent;
matching labels or codes never establish cross-taxonomy equivalence.

## Taxonomy, security/universe, and membership inventory

| Market | Object | State | Candidate authority and observed coverage | Required time/revision evidence | Rights/retention | Exact unavailable reason and next evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Korea | Taxonomy | `UNAVAILABLE` | KRX-listed-company/industry classification is the official-exchange candidate; no versioned classification artifact is retained. | Historical `taxonomy_version`, company-change `effective_from/effective_through`, `published_at`, `retrieved_at`, and `revision_id` are all absent. | No taxonomy retention/reuse decision has been accepted. | `TAXONOMY_UNAVAILABLE`: identify an official version/change feed and retain a bounded notice plus terms evidence with explicit effective and publication times. |
| Korea | Security and universe identity | `RESEARCH_ONLY` | KRX/data.go.kr canonical equity chain observes KOSPI/KOSDAQ securities daily from 1995-05-02 through 2026-08-25; only eight recent dates have the current atomic accepted-state proof. | Observation date and D+1-after-13:00 KST availability policy exist; complete listing/delisting/share-class lineage and historical revision policy are not accepted. | Existing retained public inputs are usable only under their owning contracts; no new use is granted here. | `UNIVERSE_UNAVAILABLE` for sector candidate use: accept stable security lifecycle identity and prove delisted/inactive coverage over the intended research span. |
| Korea | Historical sector membership | `UNAVAILABLE` | No retained official company-to-sector change history. Exact KOSPI200 constituent snapshots are index membership on exact dates, not sector membership. | Half-open membership intervals and their publication, retrieval, usable-from, and revision lineage are absent. | No sector-membership retention decision exists. | `MEMBERSHIP_UNAVAILABLE`: obtain official dated additions/removals/reclassifications or immutable full snapshots whose differences are tied to published effective notices. |
| U.S. | Taxonomy | `UNAVAILABLE` | S&P DJI/MSCI GICS is the candidate taxonomy; public methodology exposes structure and historical structure versions, not a retained company-level historical classification feed. | Company-level effective intervals, availability times, retrieval lineage, and corrections are absent. Public consultation/pro-forma lists are not final membership history. | GICS is proprietary; project retention and consumer rights are unresolved. | `TAXONOMY_UNAVAILABLE`: document an entitled company-level historical product, terms, version/effective calendar, publication clock, and correction delivery. |
| U.S. | Security and universe identity | `RESEARCH_ONLY` | Sharadar SEP/fund products are the primary candidate for delisted-aware price/security history; SEC CIK is issuer identity only. No artifact is retained. | Provider EOD date exists conceptually; security/share-class mapping, listing intervals, availability/vintage, and revision rules are not accepted. | Sharadar requires paid entitlement; Tiingo terms/coverage remain partial; Stooq/Yahoo are forbidden fallbacks. | `UNIVERSE_UNAVAILABLE` for sector candidate use: close entitlement, permanent security identity, delisted coverage, exchange calendar, and PIT delivery policy before a bounded Landing pilot. |
| U.S. | Historical sector membership | `UNAVAILABLE` | No retained GICS company-history or index constituent/change feed is accepted. | Half-open membership intervals and publication/retrieval/usable/revision timestamps are absent. | Company-level GICS and index membership rights are unresolved. | `MEMBERSHIP_UNAVAILABLE`: accept an entitled historical classification/membership feed with effective notices and revision snapshots; never backfill a present S&P 500 or GICS list. |

The remaining required fields for those identity rows are explicit below and
join one-to-one by `(market, object)`:

| Market | Object | Unit / currency | Predictive PIT state | Permitted consumer role | Exact consumer unavailable reason |
| --- | --- | --- | --- | --- | --- |
| Korea | Taxonomy | Not applicable; identifiers and labels only | `PIT_BLOCKED` | Authority/terms/effective-date research only | `TAXONOMY_UNAVAILABLE` |
| Korea | Security and universe identity | Security/share-class identifiers; currency not applicable | `PIT_LIMITED` | Exact observed-universe research under owning contracts only | `UNIVERSE_UNAVAILABLE` |
| Korea | Historical sector membership | Security and sector identifiers; currency not applicable | `PIT_BLOCKED` | None until intervals are accepted | `MEMBERSHIP_UNAVAILABLE` |
| U.S. | Taxonomy | GICS identifiers and labels; currency not applicable | `PIT_BLOCKED` | Methodology/entitlement research only | `TAXONOMY_UNAVAILABLE` |
| U.S. | Security and universe identity | Security/share-class/issuer identifiers; currency not applicable | `UNKNOWN` | Entitlement and identity research only | `UNIVERSE_UNAVAILABLE` |
| U.S. | Historical sector membership | Security and sector identifiers; currency not applicable | `PIT_BLOCKED` | None until intervals and rights are accepted | `MEMBERSHIP_UNAVAILABLE` |

Official supporting references: S&P DJI's
[methodology library](https://www.spglobal.com/spdji/en/governance/methodologies/)
publishes GICS structure material, including historical structure versions;
its [2023 revision notice](https://www.spglobal.com/spdji/en/documents/indexnews/announcements/20220630-1453818/1453818_2023gicspressreleaseselectlistjune2022-final.pdf)
distinguishes announced/pro-forma classifications from later effective
implementation. Neither reference supplies the missing retained company-level
history or project rights. SEC filing timestamps are relevant only to the U.S.
fundamental candidates below; CIK does not replace a security master.

## Required-role feasibility matrix

There are exactly twenty rows: each of the ten input roles in
`sector-research/v1` appears once for Korea and once for the U.S. A
`SUPPORTED` row is limited to the exact stated basis and does not override a
different role's `UNAVAILABLE` state.

| Market | Input role | State | Candidate authority / coverage | Effective, published, retrieved, revision timing | Unit / currency | Rights / retention | Permitted consumer role | Research detail (non-runtime) | Next evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Korea | `INSTRUMENT_RETURN_SERIES` | `RESEARCH_ONLY` | KRX/data.go.kr canonical equity daily, 1995-05-02..2026-08-25; exact recent accepted dates are source-registry bound. | Trading date and D+1-after-13:00 KST policy; `retrieved_at` is operation-bound; historical revision and corporate-action vintage are unresolved. | Provider-native KRW price; return/adjustment basis not yet selected. | Existing retained public inputs only; no new retention. | Source and adjustment-contract research only. | `PRICE_SERIES_UNAVAILABLE`: sector consumer lacks accepted total/price-return basis, corporate-action treatment, and revision vintage. | Bind one immutable price basis, adjustment events, stable security IDs, availability clock, and revision policy. |
| U.S. | `INSTRUMENT_RETURN_SERIES` | `RESEARCH_ONLY` | Sharadar SEP/fund candidate (advertised stock 1998+, fund 1997+); no retained artifact. | Provider EOD date only; effective publication/retrieval/vintage rules not accepted. | Provider-specific raw/adjusted USD OHLCV. | Paid entitlement and local-use rights unresolved; no retention. | Entitlement/schema/PIT research only. | `PRICE_SERIES_UNAVAILABLE`: no accepted source, artifact, stable identity, or vintage policy. | Resolve rights, then run a bounded Landing-first delisted/security/corporate-action audit. |
| Korea | `MARKET_COMPARATOR_SERIES` | `SUPPORTED` | Retained KRX KOSPI200 spot index, 1990-01-03..2026-08-25, for a KOSPI200 price-return comparator only. | XKRX trading date; usable EOD T+1; exact retrieval is operation evidence; retained revisions are not silently inferred. | Index points; no currency conversion; price-return basis only. | Existing retained official source under its owning contract. | Frozen offline market price comparator with the same calendar/basis; no sector result by itself. | None for this narrow comparator; other benchmarks require their own binding. | Freeze exact dataset digest, calendar, decision cutoff, and matching return formula in a future run. |
| U.S. | `MARKET_COMPARATOR_SERIES` | `RESEARCH_ONLY` | Yahoo global index daily has long retained history but permanent Raw provenance limits; no accepted U.S. sector-run binding. | Provider date and retrieval exist; historical publication/vintage/finality are incomplete. | Provider-native index points; USD return basis not selected. | Retained only under existing limits; no fallback/equivalence. | Descriptive comparator research only. | `MARKET_COMPARATOR_UNAVAILABLE`: PIT vintage and exact benchmark/return/currency binding are not accepted. | Select one benchmark and close calendar, close/total-return, availability, revision, and rights semantics. |
| Korea | `SECTOR_COMPARATOR_SERIES` | `UNAVAILABLE` | No accepted sector portfolio series or reproducible PIT member aggregation. | Taxonomy/membership effective, publication, retrieval, usable, and revision fields are missing. | Weighting, return basis, and KRW treatment unbound. | No retained role-complete input. | None; market-index substitution forbidden. | `SECTOR_COMPARATOR_UNAVAILABLE`. | First accept taxonomy/membership history, then pre-register weights, missing-member policy, corporate actions, and return basis. |
| U.S. | `SECTOR_COMPARATOR_SERIES` | `UNAVAILABLE` | No accepted GICS sector index history or PIT member aggregation. | Company classification/membership and index-change availability/revision lineage missing. | Weighting, total-return/price-return, and USD basis unbound. | GICS/index product rights unresolved. | None; ETF or current S&P sector list substitution forbidden. | `SECTOR_COMPARATOR_UNAVAILABLE`. | Accept entitled taxonomy/membership and either an official sector-index contract or reproducible PIT aggregation. |
| Korea | `SECTOR_BREADTH` | `UNAVAILABLE` | Market breadth and exact KOSPI200 breadth exist, but neither is sector breadth. | Sector membership intervals and decision-time denominator versions are absent. | Numerator/denominator counts and formula unbound. | No sector-breadth artifact or retention decision. | None; market/index breadth substitution forbidden. | `BREADTH_UNAVAILABLE`. | Establish PIT sector membership, exact eligible denominator, missing-price policy, formula, and availability clock. |
| U.S. | `SECTOR_BREADTH` | `UNAVAILABLE` | No retained U.S. PIT sector breadth or role-complete inputs. | Taxonomy/membership/price availability and revision lineage absent. | Numerator/denominator counts and formula unbound. | No accepted source or derived retention. | None. | `BREADTH_UNAVAILABLE`. | Close U.S. security, membership, price, calendar, and breadth-formula contracts before deriving anything. |
| Korea | `SECTOR_FLOW` | `UNAVAILABLE` | Retained investor flow is market aggregate; no accepted security-to-sector flow aggregation. | Participant-series publication/revision remains PIT-blocked and sector membership timing is absent. | Provider-native participant net purchase; sector unit/aggregation unbound. | Existing market aggregate cannot be repurposed. | None; market flow substitution forbidden. | `FLOW_UNAVAILABLE`. | Identify security-level official flow with participant/unit/finality, then join only to same-cutoff PIT membership. |
| U.S. | `SECTOR_FLOW` | `UNAVAILABLE` | No retained U.S. security- or sector-level flow authority. | Observation/publication/retrieval/revision and membership timing all absent. | Participant meaning, unit, currency, and window unbound. | No accepted rights or retention. | None. | `FLOW_UNAVAILABLE`. | Define the economic variable first, then identify a rights-compatible source with PIT membership and revision evidence. |
| Korea | `VALUATION` | `RESEARCH_ONLY` | KRX equity-fundamental Raw spans 2008-01-03..2026-08-12; KOSPI/KOSDAQ weighted PER/PBR is a separate broad-index descriptive series through 2026-08-25. | Source date exists; accounting-period, publication, retrieval-at-decision, and revision/finality policy are incomplete. | Provider-native PER/PBR/EPS/BPS/DPS text; KRW denominator semantics and sector weights unaccepted. | Existing Raw/index retention only; no sector aggregation grant. | Schema/finality/aggregation research only. | `VALUATION_UNAVAILABLE`: no PIT sector membership, comparable metric definition, loss treatment, weights, or revision lineage. | Accept metric/accounting-period semantics and vintage policy, then define same-time market/sector/own-history comparators. |
| U.S. | `VALUATION` | `RESEARCH_ONLY` | SEC EDGAR as-filed facts plus a future accepted price/security master are candidates; no canonical valuation dataset exists. | Filing acceptance may supply availability, but metric context, amendments, retrieval, and revision lineage are not selected. | Filing-native units/currencies; USD valuation ratio and aggregation unbound. | SEC fair-access applies; no accepted retained pipeline. | Filing/metric feasibility research only. | `VALUATION_UNAVAILABLE`: security mapping, price, metric contexts, denominator policy, membership, and weights are missing. | Pre-register canonical facts/contexts and amendments, then bind security, price, membership, and availability. |
| Korea | `EARNINGS_QUALITY` | `RESEARCH_ONLY` | KRX Raw exposes provider EPS text; OpenDART filings are an official candidate, but no accepted metric/vintage contract is retained. | Report period, filing publication/acceptance, retrieval, amendments, and revision lineage incomplete. | Provider/filing-native values; KRW scale and per-share basis unaccepted. | KRX Raw retained under existing limits; OpenDART retention not selected here. | Metric and filing-timing research only. | `EARNINGS_QUALITY_UNAVAILABLE`. | Choose source facts, report-period and restatement rules, stable issuer/security mapping, currency/unit, and usable-from policy. |
| U.S. | `EARNINGS_QUALITY` | `RESEARCH_ONLY` | SEC EDGAR filings/submissions/companyfacts, historical by filing/event; no selected canonical metric. | EDGAR acceptance time is a candidate availability field; amendments, contexts, retrieval, and metric revision lineage remain open. | Filing-native units/currencies; earnings level/change formula unbound. | Official no-fee access subject to fair-access; no accepted retention pipeline. | Filing/PIT metric research only. | `EARNINGS_QUALITY_UNAVAILABLE`. | Bind exact GAAP facts/contexts, amendments, security mapping, availability, currency/unit, and comparison rule. |
| Korea | `CASH_FLOW_QUALITY` | `RESEARCH_ONLY` | OpenDART filings are the official candidate; no retained operating/free-cash-flow contract. | Filing publication/receipt, report period, retrieval, restatement, and revision lineage absent. | Filing-native KRW; OCF/FCF definition and scale unbound. | Rights/rate/retention policy not accepted for this role. | Source/schema feasibility research only. | `CASH_FLOW_QUALITY_UNAVAILABLE`. | Select statement facts and FCF formula, consolidate/amendment policy, security mapping, usable-from clock, and retention rights. |
| U.S. | `CASH_FLOW_QUALITY` | `RESEARCH_ONLY` | SEC EDGAR as-filed cash-flow facts are the candidate; no canonical metric exists. | Acceptance time candidate exists; period/context/amendment/retrieval/revision policy incomplete. | Filing-native units/currencies; OCF/FCF formula unbound. | SEC fair-access applies; no accepted retained pipeline. | Filing/PIT metric research only. | `CASH_FLOW_QUALITY_UNAVAILABLE`. | Bind facts, periods, amendments, security mapping, currency conversion policy, and versioned FCF rule. |
| Korea | `BALANCE_SHEET_RISK` | `RESEARCH_ONLY` | OpenDART filings are the official candidate; no retained debt/leverage/liquidity/solvency metric contract. | Publication/receipt, report period, retrieval, restatement, and revision lineage absent. | Filing-native KRW; ratio definitions and scale unbound. | Rights/rate/retention policy not accepted for this role. | Source/schema feasibility research only. | `BALANCE_SHEET_RISK_UNAVAILABLE`. | Pre-register exact facts, consolidation and amendment policy, security mapping, units, availability, and ratio formulas. |
| U.S. | `BALANCE_SHEET_RISK` | `RESEARCH_ONLY` | SEC EDGAR as-filed balance-sheet facts are the candidate; no canonical metric exists. | Acceptance time candidate exists; contexts/amendments/retrieval and revision lineage incomplete. | Filing-native units/currencies; leverage/liquidity ratios unbound. | SEC fair-access applies; no accepted retained pipeline. | Filing/PIT metric research only. | `BALANCE_SHEET_RISK_UNAVAILABLE`. | Bind exact facts/contexts, amendments, security mapping, availability, units, and versioned risk formulas. |
| Korea | `STRUCTURAL_DECLINE_EVIDENCE` | `UNAVAILABLE` | No accepted versioned composite input/rule; required earnings, cash flow, balance sheet, valuation, and membership roles are incomplete. | Component publication/usable/revision clocks are not jointly bound. | Mixed component units/currencies; no formula. | No retained role-complete artifact. | None; low PER/PBR cannot substitute. | `STRUCTURAL_DECLINE_EVIDENCE_UNAVAILABLE`. | Close every component role, then pre-register a tri-state rule and missing/conflict behavior before any calculation. |
| U.S. | `STRUCTURAL_DECLINE_EVIDENCE` | `UNAVAILABLE` | No accepted versioned composite input/rule; required fundamentals, price, valuation, and membership roles are incomplete. | Component publication/usable/revision clocks are not jointly bound. | Mixed component units/currencies; no formula. | No retained role-complete artifact. | None; sector/issuer narrative cannot substitute. | `STRUCTURAL_DECLINE_EVIDENCE_UNAVAILABLE`. | Close every component role, then pre-register a tri-state rule and missing/conflict behavior before any calculation. |

### Predictive PIT state overlay

This table joins one-to-one to the feasibility rows by `(market, input_role)`;
it is separated only to keep every long evidence row readable. `UNKNOWN` and
`PIT_LIMITED` are ineligible for numeric sector research exactly like
`PIT_BLOCKED`; only the narrowly stated Korea comparator is `PIT_SAFE`.

| Market | Input role | `predictive_pit_status` | Basis |
| --- | --- | --- | --- |
| Korea | `INSTRUMENT_RETURN_SERIES` | `PIT_LIMITED` | Exact recent accepted dates exist, but historical adjustment/revision vintage is not accepted for this consumer. |
| U.S. | `INSTRUMENT_RETURN_SERIES` | `UNKNOWN` | No accepted source artifact or vintage policy. |
| Korea | `MARKET_COMPARATOR_SERIES` | `PIT_SAFE` | Only the retained KRX KOSPI200 price-return, EOD T+1 comparator stated above. |
| U.S. | `MARKET_COMPARATOR_SERIES` | `PIT_LIMITED` | Retained Yahoo history has permanent Raw provenance and vintage limits. |
| Korea | `SECTOR_COMPARATOR_SERIES` | `PIT_BLOCKED` | Historical taxonomy/membership is missing. |
| U.S. | `SECTOR_COMPARATOR_SERIES` | `PIT_BLOCKED` | Historical taxonomy/membership and rights are missing. |
| Korea | `SECTOR_BREADTH` | `PIT_BLOCKED` | Decision-time sector denominator is missing. |
| U.S. | `SECTOR_BREADTH` | `PIT_BLOCKED` | Decision-time sector denominator and accepted prices are missing. |
| Korea | `SECTOR_FLOW` | `PIT_BLOCKED` | Security-level flow finality and PIT membership are missing. |
| U.S. | `SECTOR_FLOW` | `PIT_BLOCKED` | No accepted economic-variable/source/timing binding exists. |
| Korea | `VALUATION` | `PIT_BLOCKED` | Accounting period, revision lineage, sector membership, and aggregation are incomplete. |
| U.S. | `VALUATION` | `PIT_BLOCKED` | Security mapping, canonical metrics, prices, membership, and amendments are incomplete. |
| Korea | `EARNINGS_QUALITY` | `PIT_BLOCKED` | Filing availability/restatement and metric identity are incomplete. |
| U.S. | `EARNINGS_QUALITY` | `PIT_BLOCKED` | Metric contexts, amendments, and security mapping are incomplete. |
| Korea | `CASH_FLOW_QUALITY` | `PIT_BLOCKED` | No accepted filing-time OCF/FCF contract exists. |
| U.S. | `CASH_FLOW_QUALITY` | `PIT_BLOCKED` | No accepted filing-time OCF/FCF contract exists. |
| Korea | `BALANCE_SHEET_RISK` | `PIT_BLOCKED` | No accepted filing-time metric and restatement contract exists. |
| U.S. | `BALANCE_SHEET_RISK` | `PIT_BLOCKED` | No accepted filing-time metric and amendment contract exists. |
| Korea | `STRUCTURAL_DECLINE_EVIDENCE` | `PIT_BLOCKED` | Required component roles and a versioned rule are incomplete. |
| U.S. | `STRUCTURAL_DECLINE_EVIDENCE` | `PIT_BLOCKED` | Required component roles and a versioned rule are incomplete. |

### Exact consumer unavailable reasons

The descriptive detail labels in the long matrix are not emitted runtime
reason codes. Each role has exactly one valid `sector-research/v1`
`UnavailableReason` below whenever its feasibility is not `SUPPORTED`.

| Input role | Korea reason | U.S. reason |
| --- | --- | --- |
| `INSTRUMENT_RETURN_SERIES` | `INPUT_ROLE_UNRESOLVED` | `INPUT_ROLE_UNRESOLVED` |
| `MARKET_COMPARATOR_SERIES` | Not applicable for the narrow supported binding | `INPUT_ROLE_UNRESOLVED` |
| `SECTOR_COMPARATOR_SERIES` | `INPUT_ROLE_UNRESOLVED` | `INPUT_ROLE_UNRESOLVED` |
| `SECTOR_BREADTH` | `BREADTH_UNAVAILABLE` | `BREADTH_UNAVAILABLE` |
| `SECTOR_FLOW` | `FLOW_UNAVAILABLE` | `FLOW_UNAVAILABLE` |
| `VALUATION` | `VALUATION_UNAVAILABLE` | `VALUATION_UNAVAILABLE` |
| `EARNINGS_QUALITY` | `QUALITY_EVIDENCE_UNAVAILABLE` | `QUALITY_EVIDENCE_UNAVAILABLE` |
| `CASH_FLOW_QUALITY` | `QUALITY_EVIDENCE_UNAVAILABLE` | `QUALITY_EVIDENCE_UNAVAILABLE` |
| `BALANCE_SHEET_RISK` | `QUALITY_EVIDENCE_UNAVAILABLE` | `QUALITY_EVIDENCE_UNAVAILABLE` |
| `STRUCTURAL_DECLINE_EVIDENCE` | `VALUE_TRAP_EVIDENCE_UNKNOWN` | `VALUE_TRAP_EVIDENCE_UNKNOWN` |

## Current decision and promotion gate

Current role counts are Korea `SUPPORTED=1`, `RESEARCH_ONLY=5`,
`UNAVAILABLE=4`; U.S. `SUPPORTED=0`, `RESEARCH_ONLY=6`, `UNAVAILABLE=4`.
These counts describe the ten required input roles only, not the separate
taxonomy/universe/membership inventory.

Sector research remains `UNAVAILABLE`: both markets lack accepted historical
taxonomy/membership intervals, and `SECTOR_COMPARATOR_SERIES`,
`SECTOR_BREADTH`, `SECTOR_FLOW`, and `STRUCTURAL_DECLINE_EVIDENCE` have no
role-complete numeric binding. No row may be promoted merely because a public
page, current list, similar label, market-wide aggregate, ETF, or present-day
classification is available.

Before any future numeric implementation, Data must accept a versioned local
contract for every required role, freeze exact content digests, demonstrate
decision-time joins using only then-available revisions, resolve retention and
consumer rights, and test interval edges, reclassifications, delistings,
restatements, and missing data. Backtest must then bind those exact inputs and
keep the existing final holdout sealed. This document itself produces no
numeric candidate, collector, schema, promotion, prediction, or holdout access.
