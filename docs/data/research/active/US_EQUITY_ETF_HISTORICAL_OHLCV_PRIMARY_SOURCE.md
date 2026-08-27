# US Equity/ETF Historical Daily OHLCV Primary Source Audit

> Audit date: 2026-08-16  
> Scope: source selection and bounded-pilot design only. No U.S. OHLCV backfill, Landing/Normalized/Canonical artifact, dataset contract, or KRX/pykrx/CFTC path was created or changed.

## Decision

For an **internal, personal-research, survivorship-aware U.S. equity/ETF daily database with the longest practical history**, the leading candidate is **Norgate Data US Stocks Diamond**. It explicitly supplies daily history back to 1950, current and delisted securities, historical index constituents, EOD updates, and documents both adjustment controls and an original unadjusted close/volume interface. It is not a commercial or redistribution solution: its EULA forbids redistribution and commercial use. Its own documentation also warns that the 1950s/early-1960s delisted collection is not claimed complete.

For a **programmatic, bulk-file secondary/cross-check for 2003 onward**, the leading candidate is **Polygon Stocks Advanced**. Its U.S. SIP daily aggregate flat files have OHLCV, all-history access starts 2003-09-10, reference data exposes active/delisted status, date-qualified universe queries and FIGI identifiers, and its APIs expose split events plus split-adjusted or unadjusted aggregates. Corporate-action dividend semantics and ticker-change continuity must be accepted in writing before it can become a primary source.

Sharadar through Nasdaq Data Link remains the leading alternative for a 1997/1998-onward research stack, particularly when its stock and separate fund products are both licensed. It is not selected as the long-history primary because its documented price history begins in 1998 for stocks and 1997 for ETFs/CEFs/ETNs. Tiingo is a useful API cross-check but does not promise a complete historical delisted universe; Stooq and Yahoo are not eligible primary sources.

This is a source-selection result, not authorization to buy a subscription or collect data. Before any data acquisition, obtain the selected provider's then-current written license and preserve it with the source contract.

## Required properties and evidence model

The production source must separately preserve:

- `instrument_id` (vendor-stable identifier where available), vendor ticker, source security type, exchange/MIC, effective listing/delist dates, and ticker-change relationships;
- unadjusted OHLCV as delivered, adjusted OHLCV as a separate source representation, and dividend/split events without deriving either from the other;
- source URL/request, retrieval timestamp, content hash, provider data/as-of timestamp, and product/version/entitlement;
- a dated universe snapshot or reconstructible listing interval. A current security master cannot be used to form a historical universe.

Daily bars alone are insufficient for point-in-time (PIT) backtests. A bar dated `D` can be used only after the selected provider's documented EOD publication/correction window for `D`; securities must be selected using their status/listing interval as known on the strategy date. Vendor corrections and corporate-action revisions require `retrieved_at` versioning. No candidate below offers a free, immutable historical-vintage service by documentation alone.

## Comparison

| source | active | delisted / historical universe | history | ETF / REIT | OHLCV | raw / adjusted | dividend / split | identifier | bulk | incremental | PIT suitability | license | public price | blocker |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Norgate US Stocks Diamond** | All currently listed securities | Diamond includes delisted securities and historical index constituents; provider says delisted coverage is extensive but not complete in the 1950s/early 1960s | Daily to 1950 | Listed ETP/ETF/ETN types are included; REIT must be classification-verified in pilot | Daily price/volume | Configurable price/volume adjustment; documented original unadjusted close and volume | Dividend indicators; capital events include splits, reverse splits, stock dividends and reorganizations | Delisted ticker has `-YYYYMM` suffix; not a documented immutable cross-life-cycle ID | Local provider database; subscription supports CSV/ASCII/Python integrations, but no cloud bulk-archive API documented | EOD updater | **Best retail/backtest candidate**: historical constituents and delists; still version source corrections and preserve effective dates | Internal personal use; EULA prohibits redistribution and commercial use | Diamond: USD 787.50 / 12 months (USD 433.13 / 6 months) | Non-commercial/non-redistributable; historical delisted completeness caveat; require provider confirmation that the licensed extraction route may populate this project's local Landing store |
| **Polygon Stocks Advanced** | All U.S. stock tickers/reference data | `active=false`, `delisted_utc`, and a `date` parameter support date-qualified security lookup; complete historical-universe and ticker-change policy needs pilot | Daily aggregate flat files from 2003-09-10 | U.S. equities universe; ETF/REIT type coverage must be verified through reference data | Daily O/H/L/C/V flat files and REST aggregates | Aggregate endpoint supports split-adjusted and `adjusted=false` views | Split reference endpoint documented; dividend event coverage advertised but dividend-field/PIT semantics require acceptance test | Composite FIGI and share-class FIGI where present; ticker and MIC also provided | Daily S3 flat files; plans expose all-history files | Prior-day files updated 11:00 ET; APIs available | **Good for 2003+**, subject to dated universe snapshot and retrieval-version discipline | Individual plans are not redistribution rights; redistribution requires business product/approval | Advanced USD 199/month, 20+ years / all-history file entitlement; business pricing quote | 2003 cutoff; split adjustment is explicit but total-return/dividend adjustment semantics must be verified; commercial/re-distribution needs separate terms |
| **Sharadar / Nasdaq Data Link** | Active tickers included | Active and delisted tickers; `permaticker`, first/last price date and related tickers are documented by the integration guide | Stocks 1998-present; funds (ETF/CEF/ETN) 1997-present | Separate EOD US Fund Prices product; REIT classification needs pilot | Daily O/H/L/C/V | Adjusted and unadjusted price fields are available in the product integration; exact raw O/H/L and adjustment policy must be validated against licensed schema | Corporate-action/adjustment behavior needs direct product-schema test; do not infer from adjusted columns | Sharadar `permaticker`; related tickers; FIGI may be present in security master | Premium Tables API / downloadable exports | Usually nightly (third-party guide); Nasdaq Data Link documents premium API/download access | **Usable 1997/98+**, but no public commitment of immutable historical master vintages | Premium, entitlement-specific; redistribution requires an appropriate institutional/distribution license | Quote / subscription required; no current public list price verified | Two products needed for stocks and ETFs; 1997/98 start; confirm exact corporate-action and raw-field license/schema before selection |
| **Tiingo EOD** | Supported-ticker file and metadata endpoint | Supports delisted data only for tickers not yet recycled; search has `isActive`, but is beta and documentation says permaTicker query support is entitlement-dependent | Advertises 60+ years, from 1962 | Explicitly covers U.S. equities, ETFs and mutual funds; REIT needs type verification | Daily O/H/L/C/volume | Raw and adjusted O/H/L/C/volume all documented | `divCash` (ex-date) and `splitFactor`; detailed corporate-action APIs are beta / enterprise enabled | `permaTicker`, but availability/query support is not universal | Per-ticker REST; no whole-history archive advertised | Most EOD 17:30 ET; corrections may continue to 20:00 ET | **Cross-check only**: price PIT is controllable with retrieval time, but complete delisted universe and stable-ID availability are not guaranteed | Free/Power/Commercial baseline licenses are internal-use-only; separate redistribution offering | Free USD 0; Power USD 30/month; Commercial USD 50/month; 50/10,000/20,000 hourly request limits respectively | Delisted coverage limitation, beta search/security-master behavior, no bulk archive, and mutable evening corrections |
| **Stooq** | Public quote pages / files; no complete official U.S. universe guarantee found | No official delisted/security-master or historical-universe commitment found | Varies by instrument; no authoritative all-U.S. start found | Individual ETFs appear, but no universe/type guarantee | Public daily O/H/L/C/V CSV/download pages | Adjustment policy not adequately documented in official material | No documented complete U.S. dividend/split event feed | Ticker only | Public bulk snapshots/manual downloads exist, but no contractual bulk API identified | No documented SLA/rate limit | **Not suitable**: no survivorship/PIT or provider-vintage guarantees | Terms/redistribution rights and automated-use allowance not sufficiently verified | Publicly accessible / price not stated | License, universe, delisting, corporate actions, identifiers and service guarantees are unverified; fail closed |
| **Yahoo Finance** | Quote pages support stocks and ETFs | No complete delisted universe, dated security master, or stable instrument-ID commitment | Help says historical prices usually do not predate 1970 | Quote pages cover ETFs; no full-universe/type promise | Historical price display/download | Adjusted historical display plus price/dividend/split tables | Historical price, dividend and split views; field/version contract not an official data API | Ticker only for this purpose | Gold CSV download is per quote; no sanctioned U.S.-universe bulk facility | No sanctioned market-data incremental API | **Not suitable**: survivorship unsafe and current website data is mutable | Terms prohibit automated collection without prior permission and reuse/competing databases; commercial use requires explicit rights | Yahoo Finance Gold for CSV download; price varies by plan/region | No authorized bulk/API, no stable historical universe, and terms preclude automated database construction |

### Source-specific notes

1. Norgate's delisted convention prevents ticker recycling ambiguity at the displayed symbol level, but is not by itself a durable entity/instrument relationship graph. The pilot must prove how a current symbol, prior symbol, and delisted suffix map across a corporate event. Its FAQ specifically warns that a delisted company appears under the *last* name/ticker, not necessarily a familiar former ticker.
2. Polygon's all-ticker reference API documents `date`, `active`, `delisted_utc`, composite FIGI, share-class FIGI and primary exchange. That makes it the best programmatic universe-secondary in this review, but only a dated exported master retained in Landing can establish what this project used at a past time.
3. Tiingo's price fields are unusually transparent: raw and adjusted O/H/L/C/volume, `divCash`, and `splitFactor` are explicitly described. The provider states that delisted support excludes recycled symbols and that the search endpoint is beta, so those fields do not solve survivorship by themselves.
4. Sharadar evidence is from Nasdaq Data Link product availability plus the provider's integration documentation; verify the actually licensed schema before committing. Do not assume a blank dividend column in a third-party integration means the original product has no corporate-action information.

## Recommendation by use case

| use case | recommendation | reason | condition before execution |
|---|---|---|---|
| Internal personal research, 1950-present, survivorship-aware stocks + ETFs | **Norgate Diamond primary** | Longest stated history, current/delisted universe, historical constituents, daily updates and adjustment controls | Written confirmation of local Landing/database rights and a pilot that validates ETF/REIT classification, raw OHLCV, and ticker continuity |
| Internal research, programmatic raw flat files, 2003-present | **Polygon Advanced primary or secondary** | Daily S3 OHLCV, FIGIs, date-aware active/delisted reference data, corporate-action endpoints | Confirm dividend/adjustment policy, full type coverage, and business/re-distribution terms if applicable |
| 1997/98-present research with licensed fundamentals/prices | **Sharadar via Nasdaq Data Link alternative** | Delisted stocks, permanent ticker and separately licensable funds dataset | License both equity and fund products; schema/pilot acceptance |
| Low-cost API comparison / small validation only | **Tiingo secondary** | Clear raw/adjusted fields and corporate-event fields | Do not use it as historical universe authority |
| Free-only exploration | **No full-universe primary exists in this audit** | Stooq/Yahoo do not provide verified rights plus delisted/PIT identity/universe requirements | Limit any future testing to a few symbols only after terms review; no bulk backfill |

## Bounded pilot design (not executed)

No provider data request was made: the free-source terms and a complete retained-data permission have not been verified sufficiently for the intended local database. The following pilot is the maximum next step after the user selects a licensed candidate.

### Symbols

`SPY`, `QQQ`, `TQQQ`, `JEPI`, `TLT`, `VNQ`, `AAPL`, `NVDA` plus these delisted cases:

- `LEH` / Lehman Brothers (bankruptcy/delisting); and
- `AOL-201506` (Norgate's documented delisted naming example; use the provider's resolved ID, not a guessed ticker, in other vendors).

The exact delisted identifier must be resolved through each vendor's security master first. A missing/ambiguous lookup is a failed test, not a reason to substitute a different security.

### Requests and retained artifacts

1. Capture one dated security-master/universe response for the pilot set and, where supported, an as-of-date query before and after each delisting/change. Preserve raw response bytes, source URL/request parameters, retrieval time, product entitlement, provider `last_updated`/as-of fields and SHA-256.
2. For each symbol, request no more than three short windows: recent 10 trading days, a known dividend window (`SPY`/`JEPI`), and a known split window (`AAPL`/`NVDA`/`TQQQ`). Request unadjusted and adjusted representations separately where the vendor offers both.
3. Retrieve dividend and split events separately. Reconcile only by reporting differences: never overwrite, infer, or synthesize an event or price.
4. For each delisted case, retrieve its last ten trading days and status/listing metadata. For the ticker-change case, prove the vendor's relation using vendor ID/relationship metadata, never same-ticker matching alone.
5. Repeat one EOD bar after the provider's stated correction cutoff. If it differs, retain both payloads and mark the provider as revision-capable; do not silently replace the first capture.

### Acceptance criteria

| test | pass condition | fail-closed condition |
|---|---|---|
| Coverage and type | All eight live symbols return daily bars and correct stock/ETF type; `VNQ` is identifiable as REIT ETF | Any missing symbol, ambiguous type, or an undocumented remapping |
| OHLCV | Numeric daily O/H/L/C/V; date ordering and exchange-calendar gaps are source-consistent | Schema/field-unit change, fabricated zero fills, or unexplained duplicated date |
| Raw vs adjusted | Provider labels both representations; selected split window shows the documented distinction without transformation by this project | Only a single unlabeled price series or ambiguous adjustment basis |
| Dividend / split | Separate event source is present and dates/value/factor are explicit | Event must be inferred from price movement or a vendor offers no documented event data |
| Delisted retrieval | Resolved delisted ID returns history, final trade/delist information and does not collide with a recycled live ticker | Missing history, ticker collision, or status unable to be dated |
| Identifier continuity | A stable vendor ID/relationship bridges the selected change case, with source evidence | Ticker/name-only matching required |
| PIT and revisions | EOD availability cutoff is recorded; universe query/effective dates are retained; any correction is versioned | Current active universe is the only available universe or corrected bytes overwrite old capture |
| License | License explicitly permits the proposed internal Landing retention and extraction method | Rights are ambiguous or prohibit retained database use |

The pilot must remain Landing-only. It creates no Normalized, Canonical, derived adjusted price, universe table, or production scheduler.

## Estimated scale (planning only)

Provider row counts were not downloaded or inferred from an API. A conservative storage-planning range for a 1950-present, all-listed and delisted U.S. daily dataset is **roughly 150–300 million daily rows**, based on an assumed long-run average of 8,000–15,000 eligible instruments across about 76 years and 252 trading days. This is an order-of-magnitude estimate, not a provider guarantee; count the licensed security master and actual archive files before capacity approval. ETF/ETN/CEF, REIT and former OTC records materially affect the result. Retain compressed source archives separately from any future table representation.

## Cboe Put/Call + VIX source audit (research only)

| item | official availability | historical / bulk finding | license and PIT note | disposition |
|---|---|---|---|---|
| Total, Equity, Index put/call | Cboe Daily Market Statistics page exposes ratios plus call/put volume and OI | Date-qualified page is publicly viewable; no documented free range/bulk archive was found | Page says data is for visitor convenience, no accuracy warranty, and use is subject to Cboe website terms; capture after EOD and preserve date/retrieval time | Do not backfill pending rights and archive-coverage confirmation |
| SPX + SPXW put/call | Same daily page | Present by date; no verified free bulk endpoint | Same restriction | Eligible only for a very small manual/approved source test |
| VIX put/call | Same daily page | Present by date; no verified free bulk endpoint | Same restriction | Eligible only for a very small manual/approved source test |
| VIX index daily close | Cboe publishes downloadable VIX daily values | 1990-present, updated daily; a separate 1990-2003 file is linked | Cboe says the series is for convenience and disclaims accuracy; preserve source file/version and do not treat retrieval date as publication time | Potential Landing-only source after a separate contract/license decision |
| Options chain / options history | Cboe directs users to DataShop for custom historical options/futures data | Paid/on-demand route | Out of scope; no options-chain request made | Blocked by current scope |

The daily Cboe page currently shows all requested put/call categories: Total, Index, Equity, VIX, and SPX+SPXW. Its public date parameter proves individual-date availability, not permission or a reliable bulk-history interface. No Cboe collection was started.

## Next action

1. Choose one license path: **Norgate Diamond (personal-only, 1950+)** or **Polygon Advanced/Business (programmatic, 2003+)**.
2. Obtain written confirmation covering local Landing retention, permitted extraction format, research/commercial status, and redistribution restrictions.
3. Run the bounded Landing-only pilot above using a new, isolated U.S. namespace; fail closed on any schema, identity, corporate-action or license anomaly.
4. Review a pilot evidence report and source contract before authorizing any historical backfill or Normalized/Canonical design.

## Official source register

- Norgate packages and current pricing: <https://norgatedata.com/stockmarketpackages.php>
- Norgate data content/delisted coverage: <https://norgatedata.com/data-content-tables.php>
- Norgate adjustment, original-price, and dividend interfaces: <https://norgatedata.com/amibroker-usage.php>
- Norgate ticker lifecycle limitation: <https://norgatedata.com/data-package-faq.php>
- Norgate EULA: <https://norgatedata.com/subscribe/eula.php>
- Polygon U.S. stocks overview and sources: <https://polygon.io/docs/rest/stocks/overview>
- Polygon pricing: <https://polygon.io/stocks>
- Polygon daily aggregate flat files/history: <https://polygon.io/docs/flat-files/stocks/day-aggregates/2023/08>
- Polygon all-tickers reference fields: <https://polygon.io/docs/rest/crypto/tickers/all-tickers>
- Polygon split events: <https://polygon.io/docs/rest/stocks/corporate-actions/splits>
- Polygon market-data terms: <https://polygon.io/terms/market_data_terms.pdf>
- Nasdaq Data Link data organization: <https://docs.data.nasdaq.com/docs/data-organization>
- Nasdaq Data Link Sharadar SEP documentation page: <https://data.nasdaq.com/databases/SEP/documentation>
- Tiingo EOD documentation: <https://www.tiingo.com/documentation/end-of-day>
- Tiingo EOD product/pricing: <https://www.tiingo.com/products/end-of-day-stock-price-data>
- Tiingo symbol/delisted coverage note: <https://www.tiingo.com/documentation/appendix/symbology>
- Tiingo ticker search: <https://www.tiingo.com/documentation/utilities/search>
- Yahoo historical-data help: <https://help.yahoo.com/kb/download-historical-data-yahoo-finance-sln2311.html>
- Yahoo Terms: <https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html>
- Cboe Daily Market Statistics: <https://www.cboe.com/markets/us/options/market-statistics/daily>
- Cboe VIX historical data: <https://www.cboe.com/tradable_products/vix/vix_historical_data>
