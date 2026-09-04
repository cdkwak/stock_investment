# U.S. Option Put/Call Source Decision

Status: `CBOE_PERSONAL_DISPLAY_SUPPORTED / REDISTRIBUTION_AND_PREDICTIVE_USE_FORBIDDEN`

Decision date: `2026-08-26 KST`

Evidence check date: `2026-08-26 KST`

Personal-use supersession check: `2026-09-05 KST`

## 2026-09-05 personal-display supersession

The current user instruction supersedes this decision's 2026-08-26
`UNSUPPORTED` result only for a once-daily, personal, non-commercial local
display of Cboe's venue-scoped daily statistics. The Cboe website terms checked
2026-09-05 allow viewing/downloading one copy of website materials for personal
non-commercial use and contain no explicit automated-access clause. This route
therefore permits at most one fetch per observation date, lossless sha256-bound
Landing retention, contract-valid local Normalized promotion, and display in
private mode. Guest/public display, redistribution, remote publication,
Backtest/ML input, and every predictive/PIT claim remain forbidden.

The display identity is exactly `Cboe 거래소 합계 · 지수 · ETP · 개별주 · VIX`;
it is never called the whole U.S. market. Every ratio is computed as put divided
by call and is null when the call count is zero. Volume and OI ratios remain
separate. No retained archive evidence identified a stable machine endpoint.
The provider consequently defaults to the following Historical Options Data
CSV placeholder, which the coordinator must verify with one curl before passing
the live lane's explicit `--endpoint-verified` gate:

`https://www.cboe.com/us/options/market_statistics/historical_data/?download=csv&date={date}`

An identified page XHR or stable `cdn.cboe.com/data/us/options/market_statistics/daily/`
CSV/JSON route should replace that placeholder without changing the contract.
The rest of this document remains the 2026-08-26 historical decision and does
not override this narrow personal-display exception.

The following is the superseded 2026-08-26 documentation-only decision. It did not accept website visibility as
an API or data licence, buy or activate a product, call a provider, retain a
sample value, or grant collection, display, derived-display, redistribution, or
predictive permission.

## Requested identity and non-substitution rule

The requested signal is U.S. listed-option sentiment. There is no single
unqualified `US option P/C` identity. A future Dashboard route must show one or
more separately named scopes and must not merge them:

- an OCC-cleared industry scope, if its exact included product kinds and rights
  are contracted;
- a named venue aggregate, if the exact venue boundary behind a page label such
  as Cboe `SUM OF ALL PRODUCTS` is independently evidenced;
- a named product group such as Cboe index, ETP, or equity options; or
- exact roots such as the Cboe combined `SPX + SPXW` family or one
  provider-defined underlying.

An unresolved `Total` page label is not the entire U.S. market. `SPX + SPXW` is not SPY,
QQQ, NDX, all index options, or all S&P-related options. An underlying aggregate
is not an OCC industry aggregate. Korean KOSPI200 P/C and Yahoo's tested
unauthenticated option route are different datasets and may never substitute.

Every accepted ratio must be **put divided by call**. Volume P/C and
open-interest P/C are independent metrics. The unit of both source counts is
option contracts and the ratio is unitless. When the call denominator is zero,
the ratio is null; a published source zero remains a valid observed zero and is
never converted to missing.

## Primary-source comparison

`Not evidenced` below means that the reviewed public primary material does not
establish the property for this project. It does not claim that a separately
negotiated entitlement cannot establish it.

| Candidate | Product/root universe | Direction, basis, unit | Session, trade date, finality, revisions | API/schema and call budget | Retention/display rights and cost | Decision |
|---|---|---|---|---|---|---|
| Cboe Daily Market Statistics website | Separately labelled page scopes include `SUM OF ALL PRODUCTS`, index, ETP, equity, VIX, and combined `SPX + SPXW`. The linked page does not state whether every scope is C1-only, all four Cboe options exchanges, or another boundary. Therefore even its `TOTAL` identity is unresolved and must not be labelled OCC/all-U.S.-venues total. | The page publishes call, put and total contract counts plus volume P/C. Put-divided-by-call is evidenced by the page's own count/ratio relationship, without retaining a value. It also shows OI counts, but the headline ratios are not documented as OI ratios. | The page is selected by date and identifies a daily statistic. Reviewed public material does not state the timezone, exact close cutoff, publication timestamp, correction window, immutable-final point, holiday/empty response, or revision protocol. | A human website/date control exists. No reviewed public contract grants a stable automated endpoint, schema, rate limit, or call budget. | The website terms allow one personal non-commercial copy but otherwise prohibit electronic storage, display, derivative works and other use without prior written consent. No project consent or licence exists. | `UNSUPPORTED`; semantic labels only, with venue boundary, collection and display all unresolved. |
| OCC Daily Volume / put-call report | Closest public candidate for OCC-cleared listed options. Exact product-kind inclusion/exclusion, futures-option treatment, adjusted classes and per-root grouping must still be bound to a contracted report/schema before it can be labelled industry-wide. | OCC routes expose volume reporting by call/put and product/account dimensions; an exact retained put/call field layout and denominator policy have not been accepted. | The public UI is trade-date driven and states recent-history availability. Exact nightly completion time, late corrections, OI effective date, publication timestamp, empty/error semantics and revision finality are not evidenced. | Public pages and batch-processing guidance exist, but no reviewed material grants this project a stable automated schema, rate/call budget, or unattended retrieval contract for the desired aggregate. | OCC website terms cover the data and prohibit commercial exploitation. Subscription terms restrict subscribed data to expressly authorized uses and treat it as confidential. No applicable project entitlement, retention, Dashboard/derived-display, remote-display, or redistribution grant is evidenced. | `UNSUPPORTED`; preferred identity class to reopen for true industry scope. |
| ORATS Delayed Data API `cores` | Provider-defined aggregates by requested ticker. The evaluated draft allowlist is `SPX`, `QQQ`, and `NDX`; the public API example does not prove whether `SPX` includes `SPXW`, adjusted/weekly roots, or every listed venue. These three rows must remain separate. | `cVolu`, `pVolu`, `cOi`, and `pOi` support provider-scoped volume and OI P/C in contracts. | The `cores` example exposes `tradeDate` and `updatedAt`; unlike other ORATS endpoint examples, it does not expose snapshot fields, and its example `updatedAt` has no UTC offset. Exact timezone, regular/global-session coverage, daily final cutoff, OI effective day, late corrections, revision history and immutable-final timestamp are all unresolved. | Paid token API; `cores` accepts up to ten comma-delimited tickers and field selection. At the evidence date the individual delayed plan lists 20,000 requests/month for USD 99/month. No project entitlement exists and no response/schema pilot for the three roots was run. | The paid subscription auto-renews. Terms restrict the service/materials to one designated user and prohibit distribution or redistribution. Project-local raw/normalized retention and Dashboard/derived/remote display rights are not explicitly granted by the reviewed terms. | `UNSUPPORTED`; best implementation-ready provider draft, but no entitlement or root/finality closure. |
| Cboe DataShop Open-Close Volume Summary | All option series with volume on selected Cboe exchanges, delivered separately for C1, C2, BZX and EDGX; C1 distinguishes regular and global sessions. This is not the full U.S. industry. | Contract volume by participant, action and open/close; EOD files also include total volume and OI. A simple put/call aggregate would have to be derived across an exact selected scope. | EOD is delivered overnight after midnight U.S. Eastern; intraday snapshots follow interval close. Product documentation records historical OI/volume convention changes. Its announced busted/cancelled-trade removal is a future 1-minute enhancement effective 2026-11-08, not current finality evidence on the 2026-08-26 decision date. | Paid SFTP/Snowflake product with published file specifications; exact purchased exchanges, delivery, schema version, row/rate limits and aggregate query are absent because nothing was purchased. | Raw data is licensed for internal use only. External derived display requires additional licensing fees and approval; raw redistribution is prohibited. | `UNSUPPORTED`; wrong default scope and no subscription. |
| Cboe One Options Feed | Consolidated quotes/trades from Cboe's four U.S. options exchanges only. It is neither OCC-wide nor an EOD P/C product. | The feed specification literally labels per-symbol cumulative executed volume in `shares`, not option contracts. Without a separate authoritative unit clarification it cannot satisfy this decision's contract-unit invariant; it also lacks the required daily OI aggregate. | RTH coverage is documented; event corrections and a separate EOD finality/revision contract would still be required. | Licensed high-bandwidth multicast feed with its own protocol and connectivity. It is not a bounded daily HTTP source. | Licensed internal/external distribution product; no project entitlement exists. | Rejected for this use, independent of entitlement. |

Primary evidence, all checked 2026-08-26:

- [Cboe Daily Market Statistics](https://www.cboe.com/markets/us/options/market-statistics/daily/): official scope labels and separate volume/OI count tables.
- [Cboe website terms](https://www.cboe.com/terms): website-copy, storage, display, derivative-use and consent boundary.
- [OCC Daily Volume](https://www.theocc.com/market-data/market-data-reports/volume-and-open-interest/daily-volume): official trade-date report route and recent-history window.
- [OCC batch-processing example](https://www.theocc.com/market-data/market-data-reports/other-market-data-info/batch-processing/volume-query-batch-processing): official call/put and product-query dimensions.
- [OCC site migration note](https://www.theocc.com/specialpages/whats-changed): confirms that Put/Call Ratio data moved into Daily Volume.
- [OCC website terms](https://www.theocc.com/specialpages/legal/terms-and-conditions) and [Data Subscriber Terms](https://www.theocc.com/data-subscriber-terms): public-site and subscribed-data use boundaries.
- [ORATS plans and coverage](https://orats.com/data-api), [Delayed API `cores`](https://orats.com/docs/delayed-data-api), [field definitions](https://orats.com/docs/definitions), and [terms](https://orats.com/terms-conditions).
- [Cboe Open-Close Volume Summary](https://datashop.cboe.com/cboe-options-open-close-volume-summary): exchange/session coverage, delivery timing, historical convention changes and redistribution boundary.
- [Cboe One Options product](https://www.cboe.com/market_data_services/us/options/cboe_one/) and [feed specification](https://www.cboe.com/document/tech-spec/document/technical-specifications/cboe-titanium-cboe-one-options-feed-specification): four-exchange identity, RTH coverage and per-symbol cumulative-volume semantics.

## Independent eligibility decisions

### Volume put/call — `UNSUPPORTED`

No reviewed route simultaneously provides an entitled exact scope, a stable
machine-readable schema, publication/revision finality, and permission to retain
and display the value. Cboe's public daily statistic is the clearest bounded
semantic candidate, while OCC is the preferred identity class if an actual
industry-wide ratio is required. Neither public page authorizes unattended
Landing capture or Dashboard reuse. No numeric value may be collected, retained,
calculated, or displayed under this decision.

### Open-interest put/call — `UNSUPPORTED`

The same rights blocker applies independently. In addition, OI's effective-day
meaning and post-trade-date publication/finality must be established separately
from volume. A volume-final route does not make OI final. Cboe's headline ratio
must not be relabelled as OI P/C, and ORATS `cOi`/`pOi` must not be used until its
root aggregation and timing are evidenced.

## Required zero/missing/error semantics

A future contract must preserve these distinct states:

- source count `0`: valid non-negative observation;
- call count `0`: ratio `null` with `ZERO_DENOMINATOR`, not numeric zero;
- both counts `0`: valid empty activity plus null ratio, not a failed response;
- absent field/root/date: `MISSING`, not zero;
- an explicitly successful no-row response for an eligible closed session:
  `VALID_EMPTY` only if the provider contract defines it;
- HTTP, authentication, entitlement, parse/schema, partial-scope or timeout
  failure: `ERROR`, preserving prior valid data byte-for-byte;
- stale or not-yet-final observation: retained in Landing when authorized but
  ineligible for display and canonical promotion.

## Reopen and bounded Landing-first pilot gate

Reclassify one exact route to `SELECTABLE_CANDIDATE` only after primary written
or contractual evidence closes every item below. A provider trial or sample is
not itself a production entitlement.

1. Name the exact product and every included/excluded venue, product kind,
   underlying/root, weekly/adjusted root, FLEX, futures option, and session.
2. Bind put/call direction, volume versus OI basis, contracts unit, denominator,
   aggregate formula, and zero/missing/valid-empty/error policy.
3. Bind exchange trade date, timezone, regular/global session cutoff, provider
   publication timestamp, volume finality, OI effective date, correction and
   revision window, and immutable or reproducibly versioned history.
4. Obtain the exact API/file identifiers, schema version and sample response;
   document authentication, pagination, rate/row limits, retry/backoff and a
   daily call budget.
5. Obtain written rights for project-local Landing, Normalized and Derived
   retention, historical retention, local Dashboard numeric/derived display,
   future remote display and any redistribution, plus exact price, renewal and
   termination obligations.
6. Define one date and one explicitly allowlisted scope for the first pilot.
   Preflight entitlement before the call; write lossless Landing atomically
   before parsing; store capture/publication timestamps and a content hash; make
   no normalized or display write in the pilot transaction.
7. Offline validation must reject extra/missing scopes, negative/fractional
   counts, date/time mismatches, denominator substitution and partial payloads.
   It must prove valid zero differs from missing/error and preserve prior valid
   data after every failure.
8. Reconcile at least five completed sessions against the provider's own exact
   published scope, including one zero/empty edge case if naturally observed.
   Do not average or compare unlike Cboe, OCC, ORATS, Korean or Yahoo scopes.
9. Register and display only after independent review accepts entitlement,
   semantics, finality and reconciliation. Predictive/PIT use remains separately
   blocked until release vintages and historical reproducibility are proven.

Until then `US_OPTION_PCR` stays hidden or explicitly numeric-free. The
contract-only parser and schemas grant no runtime registration or collection
authority.
