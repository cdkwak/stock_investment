# Data Layer Status

## 1. Current Baseline

| Field | Current value |
|---|---|
| Last updated | 2026-08-14 KST |
| Rev1 commit | `00298a7` (`Preserve stock issuance source semantics`); issuance artifact publication followed from the retained run |
| Coordination commit | `ada48c1` (`Stabilize project control and repository context`) |
| Data phase | Core collection substantially complete; maintenance, bounded refreshes, provenance, and high-value gap filling |
| Active network stream | None |
| Active locks/processes | None; provider lock absent and no Python process remains after the 2026-08-12 offline rebuild |

The matrix below reports verified artifacts, not planning estimates. The 2026-08-12
price, market-cap, provider-universe, canonical-universe, and breadth integration is
complete, with zero additional network calls during adoption and rebuilding.

## 2. Executive Summary

- Core Korean equity price, market-cap, universe, breadth, short-selling Trading and
  Balance, liquidity, credit, stock-lending, and provider-boundary derivative
  artifacts are retained and validated for their current contracts.
- The largest target-history gaps are KOSPI200 futures 1996-2009, options/PCR
  1997-2009, canonical corporate actions, adjusted-price/total-return accounting,
  and the stopped short-selling Investor series.
- Yahoo indices are current through 2026-08-12; FRED Treasury yields/spreads through
  2026-08-11; FRED FX through the source-observed 2026-08-07. Older Yahoo/FRED
  history remains provenance-limited.
- KRX Investor access recovered on one post-cooldown sentinel, but the source again
  returned only the positive range-end row; historical-range semantics remain stopped.
  KB Securities is externally blocked. OpenDART now has one retained known-positive
  combined paid/free-issue observation, but canonical revision/event identity is unresolved.
- Stock-issuance source history is complete as a non-predictive source-observation
  artifact: 152,676 rows with signed counts and invalid/out-of-range date tokens
  preserved explicitly. Canonical economic-event identity remains a separate gap.
- No further high-value collection is immediately runnable without new source evidence,
  provider authorization, publication lag, or a separately reviewed bounded scope.
  Data remains in maintenance/gap-filling mode; Backtest is the primary phase.

### Status legend

- **Artifact Status:** `DATA_COMPLETE` means current-contract artifact and validation
  evidence are complete; `ARTIFACT_COMPLETE` means the local artifact is valid but
  provenance or semantics limit the claim; `PARTIAL`, `STOPPED`, `BLOCKED`, and
  `NOT_IMPLEMENTED` are literal.
- **Goal Coverage Status:** `TARGET_COMPLETE`, `TARGET_GAP`, `SOURCE_MAX_COMPLETE`,
  or `UNKNOWN` describes coverage against the project goal, independently of artifact
  validity.
- **Research Usability:** `PIT_SAFE`, `USABLE_WITH_LIMITS`,
  `PREDICTIVE_USE_BLOCKED`, or `NOT_READY` describes permitted use, not data quality.

## 3. Core Dataset Status Matrix

| Dataset | Layer | Target Coverage | Actual Coverage | Artifact Status | Goal Coverage Status | Research Usability | Next Action |
|---|---|---|---|---|---|---|---|
| Korean equity price / market cap | Normalized | 1995-present | 1995-05-02..2026-08-12; 15,166,038 rows each | DATA_COMPLETE | SOURCE_MAX_COMPLETE | PIT_SAFE | Incremental maintenance only |
| Provider equity universe | Normalized | 1995-present | 1995-05-02..2026-08-12; 14,941,284 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | PIT_SAFE | Incremental maintenance only |
| Canonical equity universe | Published | 1995-present | 1995-05-02..2026-08-12; 15,166,039 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | PIT_SAFE | Incremental maintenance only |
| Market breadth | Derived | 1995-present | 1995-05-03..2026-08-12; 15,419 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | PIT_SAFE | Rebuild atomically after equity/universe increments |
| KOSPI200 futures provider bridge | Published | 1996-present | 2010-01-04..2026-08-07; 38,601 rows | DATA_COMPLETE | TARGET_GAP | USABLE_WITH_LIMITS | Confirm/purchase licensed KRX 1996-2009 history; do not infer a continuous contract |
| KOSPI200 options provider bridge | Published | 1997-present | 2010-01-04..2026-08-07; 3,782,720 rows | DATA_COMPLETE | TARGET_GAP | USABLE_WITH_LIMITS | Confirm/purchase licensed KRX 1997-2009 history |
| KOSPI200 option PCR | Derived | 1997-present | 2010-01-04..2026-08-07; 4,227 rows | DATA_COMPLETE | TARGET_GAP | USABLE_WITH_LIMITS | Backfill 1997-2009 options first, then rebuild |
| Short-selling Trading | Normalized | Retained source maximum | 2008-01-02..2026-08-07; 10,161,884 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | USABLE_WITH_LIMITS | Maintenance only; preserve T+1 availability rule |
| Short-selling Balance | Normalized | Retained source maximum | 2016-06-30..2026-08-07; 6,035,958 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Preserve source values; availability semantics remain limiting |
| Short-selling Investor | Normalized | 2008-present | No accepted artifact; production range returned 1/501 dates | STOPPED | TARGET_GAP | NOT_READY | Access recovered on one sentinel, but range semantics remain incomplete; no backfill |
| Market liquidity / credit balance | Normalized | Official source history-present | 2021-10-26..2026-08-05 / 2021-11-09..2026-08-05 | DATA_COMPLETE | SOURCE_MAX_COMPLETE | USABLE_WITH_LIMITS | Add safe incremental refresh semantics before another run |
| Stock lending detail / market / participant | Normalized | Official source history-present | 2021-04-01..2026-08-10; 3,236,815 / 1,254 / 11,472 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | USABLE_WITH_LIMITS | Maintenance only; execution-call total remains unreconstructable |
| Dividend source observations | Normalized | Versioned source snapshots | 71,652 rows at `basDt=2026-08-08`; 2026-08-13 was valid-empty | ARTIFACT_COMPLETE | TARGET_GAP | PREDICTIVE_USE_BLOCKED | Wait for a genuinely new non-empty snapshot |
| Rights source observations | Normalized | Historical corporate-action observations | 13 rows for one 2019-12-31 issuer snapshot pair | PARTIAL | TARGET_GAP | PREDICTIVE_USE_BLOCKED | Resolve canonical event identity, economic terms, and broader history |
| Stock issuance source observations | Normalized | Official unfiltered history through reference date 2026-08-12 | 2020-07-14..2026-08-12; 152,676 rows | ARTIFACT_COMPLETE | TARGET_GAP | PREDICTIVE_USE_BLOCKED | Resolve publication timing and canonical economic-event identity; do not rerun snapshot |
| Market investor-flow bridge | Published | 1999-present | 1999-01-04..2026-08-11; 9,780 rows | DATA_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Keep provider segments separate; resolve legacy unit/availability semantics |
| Yahoo global indices | Normalized | Available history-present | 1928-01-03..2026-08-12; 49,060 rows | ARTIFACT_COMPLETE | SOURCE_MAX_COMPLETE | USABLE_WITH_LIMITS | Bounded refreshes with Landing/ledger; old history stays provenance-limited |
| FRED Treasury yields | Normalized | Available history-present | 1962-01-02..2026-08-11; 16,856 rows | ARTIFACT_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Use bounded refresh wrapper; old history lacks retained response provenance/vintages |
| US Treasury spreads | Derived | Match FRED yield coverage | 1962-01-02..2026-08-11; 16,856 rows | ARTIFACT_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Rebuild atomically whenever yield inputs change |
| FRED USD FX | Normalized | Available history-present | 1971-01-04..2026-08-07; 14,505 rows | ARTIFACT_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Bounded refreshes; preserve source nulls and observed source endpoint |
| BOK ECOS Korean Treasury yields | Normalized | Official available history-present | 1998-11-13..2026-08-13; 29,674 rows, six tenors | ARTIFACT_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Evidence publication/revision timing before predictive use |
| Toss Korean Treasury yields | Normalized | Available source history-present | 2019-01-02..2026-08-10; 11,162 rows | ARTIFACT_COMPLETE | SOURCE_MAX_COMPLETE | PREDICTIVE_USE_BLOCKED | Resolve source volume unit and observation availability |
| KB Securities realtime snapshots | Landing / Normalized | Realtime onward | No accepted market snapshot; token pilot stopped at HTTP 500 / `E021` | BLOCKED | UNKNOWN | NOT_READY | External provider/app-key authorization |

## 4. Historical Coverage Gaps

| Priority | Dataset | Target | Actual | Missing | Source/Next Step |
|---:|---|---|---|---|---|
| 1 | KOSPI200 futures | 1996-present | 2010-01-04..2026-08-07 | 1996-2009 | KRX paid daily product is preferred; obtain exact coverage/sample/license before purchase |
| 2 | KOSPI200 options | 1997-present | 2010-01-04..2026-08-07 | 1997-2009 | KRX paid daily product is preferred; obtain exact coverage/sample/license before purchase |
| 3 | KOSPI200 PCR | 1997-present | 2010-01-04..2026-08-07 | 1997-2009 | Depends on historical options; deterministic rebuild afterward |
| 4 | Corporate actions | Broad history with canonical events | Dividend snapshot + 13 Rights observations + 152,676 issuance observations + one OpenDART combined paid/free success row | Canonical revision/event identity, broader action families, and PIT timing | Preserve source terms and receipts; do not equate filing versions with canonical events |
| 5 | Adjusted price / total return | 1995-present | Not implemented | All periods | Define accounting policy only after corporate-action evidence |
| 6 | Short-selling Investor | 2008-present | No accepted artifact | Full historical coverage/semantics | Access recovered; require official source evidence or a new semantic design before collection |
| 7 | Valuation | Historical PIT series | Bounded source probes only | Production history | Review a bounded authenticated KRX plan after access safety clears |
| 8 | Foreign ownership | Historical PIT series | Bounded source probes only | Production history | Deferred authenticated KRX pilot |
| 9 | ETF | Historical PIT series | Bounded source probes only | Production history | Deferred authenticated KRX pilot |
| 10 | VKOSPI | 2003-present where source supports | Source audit only | Production history | Resolve official/reproducible source and contract |
| 11 | Program trading | Historical market series | No accepted artifact | Production history | Resolve endpoint/request contract; do not use survivorship-unsafe per-symbol data |
| 12 | Yahoo/FRED provenance | Existing artifact coverage | Current artifacts complete; old responses absent | Lossless provenance for historical rows | Irrecoverable retrospectively; require Landing/ledger for future refreshes |

## 5. Bridge / Derived / Published Layers

| Dataset | Inputs | Purpose | Coverage | Status | Limitations |
|---|---|---|---|---|---|
| Futures provider bridge | Legacy KRX futures + official FSC futures | One provider-boundary-preserving history | 2010-01-04..2026-08-07 | DATA_COMPLETE | Excludes legacy spreads; provider/session boundary retained |
| Options provider bridge | Legacy KRX options + official FSC options | One provider-boundary-preserving history | 2010-01-04..2026-08-07 | DATA_COMPLETE | Official session is unspecified; no continuous-contract claim |
| Nearest-listed futures | Futures bridge + retained contract rows | Deterministic minimum listed maturity | 2010-01-04..2026-08-07; 6,538 rows | DATA_COMPLETE | Nearest source-listed maturity only; expiry and roll policy are not inferred |
| KOSPI200 PCR | Legacy + official options | Daily volume/open-interest put-call ratios | 2010-01-04..2026-08-07 | DATA_COMPLETE | 141 legacy valid-empty weekdays retained; no price-based PCR |
| Investor-flow bridge | Legacy KRX investor + Toss market investor | Preserve both provider segments in one Published interface | 1999-01-04..2026-08-11 | DATA_COMPLETE | Legacy units/availability unknown; cross-segment numeric comparison prohibited |

**Provider Bridge != Continuous Futures.** The bridge preserves provider and
session boundaries. It does not define a front-month roll, expiry switch, calendar
roll, back adjustment, or continuous-contract accounting.

## 6. Active / Paused Operations

| Task | Dataset | Status | Progress | Network | Next Gate |
|---|---|---|---|---|---|
| KRX Investor range semantics | Short-selling Investor | STOPPED_SOURCE_SEMANTICS | Post-cooldown one-call sentinel returned HTTP 200 but only the positive range-end row | No active stream | New official source evidence or a separately reviewed semantic design; no further probe |
| Pre-2010 derivatives source | Futures / options / PCR | SOURCE_FOUND_WITH_LIMITS / LICENSE_GATE | Free KRX Open API explicitly begins in 2010; paid KRX products identified | No active stream | User-approved vendor coverage, sample, price, and license confirmation |
| KB token/access | KB realtime snapshots | BLOCKED_EXTERNAL | Token pilot stopped before market/account/order calls | No active stream | Provider/app-key authorization |
| Dividend snapshot append | Dividend observations | WAITING_FOR_SOURCE_CHANGE | Latest bounded request was valid-empty | No active stream | New non-empty source snapshot |

## 7. Blockers and Next Actions

| Dataset | Blocker | Required Resolution | Current Evidence |
|---|---|---|---|
| Futures/options/PCR target history | Free KRX Open API begins in 2010 | Confirm and license the paid KRX daily products; FnGuide only as a written-coverage fallback | [Source decision](../providers/KOSPI200_PRE2010_DERIVATIVES_SOURCE_DECISION.md) |
| Corporate actions / adjusted returns | Canonical revision/event identity and PIT timing remain incomplete | Reconcile filing versions and cross-source events before defining adjustment factors | [OpenDART known-positive audit](../data/audits/OPENDART_FREE_ISSUE_KNOWN_POSITIVE_AUDIT.md), dividend snapshot, partial Rights, and issuance observations |
| Short-selling Investor | Access is restored for one scope, but historical range behavior remains unresolved | Do not backfill or synthesize missing dates; require official source evidence or a new reviewed semantic design | H1-H3 collapse, H4 boundary shape, parity HTTP 403, then post-cooldown HTTP 200 boundary shape |
| BOK/FRED/Toss rates | Historical knowledge/revision availability is incomplete | Establish availability/vintage policy before predictive use | Valid artifacts and bounded retained source observations |
| Yahoo/FRED legacy provenance | Historical raw responses were not retained | Accept permanent limit; enforce capture on all future refreshes | Immutable local-artifact audits plus new refresh ledgers |
| OpenDART | One combined paid/free success row confirms that operation's schema and economic terms, but original/corrected receipts differ | Define revision lineage and date-filter behavior before any canonical event or broad backfill | [Known-positive audit](../data/audits/OPENDART_FREE_ISSUE_KNOWN_POSITIVE_AUDIT.md) |
| KB realtime | Provider returned token process `E021` | External authorization resolution | One bounded retry-free token pilot; no downstream call |
| Stock-lending execution accounting | Exact task-level calls cannot be reconstructed | Governance acceptance of permanent execution-accounting limitation | 333 unique retained responses and exact Landing-to-Normalized reconciliation |

## 8. References

- Dataset contracts: [`src/stock_data/contracts/`](../../src/stock_data/contracts/)
- Inventory contract and latest point-in-time snapshot link:
  [D001 Dataset Inventory](../data/inventory/D001_DATASET_INVENTORY.md)
- Data audits: [`docs/data/audits/`](../data/audits/)
- Provider source decisions: [`docs/providers/`](../providers/)
- Current Data runbooks: [`docs/runbooks/active/`](../runbooks/active/)
- Deferred Data runbooks: [`docs/runbooks/deferred/`](../runbooks/deferred/)
- Archived handoffs and decisions: [`docs/archive/2026-08-data-phase/`](../archive/2026-08-data-phase/)
- Archived one-off runbooks: [`docs/runbooks/archive/2026-08-data-phase/`](../runbooks/archive/2026-08-data-phase/)
- Preserved pre-dashboard status narrative:
  [Data status before the dashboard refactor](../archive/2026-08-data-phase/DATA_STATUS_PRE_DASHBOARD_20260814.md)
- Project routing: [Project status](PROJECT_STATUS.md)

Document roles are strict: this file is the current dashboard; contracts define
meaning/schema/key; audits establish trust; runbooks define collection/recovery;
handoffs record task execution; decisions record source or policy choices. Update this
matrix in the same integration that materially changes verified coverage. Do not turn
it into an execution log.
