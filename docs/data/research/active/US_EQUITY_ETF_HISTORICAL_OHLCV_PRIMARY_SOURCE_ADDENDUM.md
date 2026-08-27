# Addendum — US Equity/ETF OHLCV Primary Source Audit

> Date: 2026-08-16. This supplements, and does not replace, `US_EQUITY_ETF_HISTORICAL_OHLCV_PRIMARY_SOURCE_AUDIT.md`. No provider account, paid trial, data download, or production code was used.

## Evidence-backed refinements

| question | Norgate Diamond | Polygon Stocks Advanced | decision impact |
|---|---|---|---|
| Historical delisted coverage | Diamond states daily history to 1950 and includes delisted securities; Norgate explicitly does **not** claim completeness in the 1950s/early 1960s. Delisted symbols have a last-traded `-YYYYMM` suffix. | Reference records expose `active`, `delisted_utc`, and a date parameter. Documentation does not independently promise a complete historical delisting population or a ticker-change graph. | Norgate is the leading personal-research history candidate; neither source is an automatic historical-universe authority without retained source snapshots. |
| Historical constituents / universe | Platinum/Diamond include historical index constituents, but this is index-universe evidence, not a daily all-listed-security master. | Date-qualified reference lookup can construct a vendor-date universe; retain every source snapshot. | A `us_security_universe_daily` table may only be populated from a dated source result with effective/availability evidence. |
| ETF / REIT | Norgate lists Exchange Traded Products, ETFs and ETNs among U.S. security types. It does not make a separate REIT completeness claim. | Stock reference has type/MIC data but no audited ETF/REIT population guarantee. | Pilot must prove `is_etf` and `is_reit` classification; do not infer REIT from name. |
| Raw versus adjusted | Adjustment settings cover price/volume and capital events; original unadjusted close and volume are documented. Exact raw O/H/L export fields require a licensed-schema pilot. | Aggregates document `adjusted=false` for split-unadjusted data. This is not evidence of dividend-total-return adjustment. | Store source-labelled raw and adjusted views separately; no project-side adjustment derivation. |
| Distributions / splits | Dividend indicators and capital-event adjustment controls are documented; separate event-export schema is unverified. | Split endpoint is documented. Dividend endpoint field and adjustment semantics must be validated before use. | Both candidates remain `PREDICTIVE_USE_BLOCKED` for derived adjusted/total-return price until event and availability evidence is accepted. |
| Identity / ticker continuity | Norgate warns that a delisted security is stored under its final name/ticker and that ticker/name reuse occurs. The suffix disambiguates display symbols but is not a documented immutable cross-lifecycle identifier. | FIGI fields are present where supplied. A historical ticker-change relation endpoint/retained event history was not verified. | Do not bridge records by ticker or name. Require vendor relationship evidence, FIGI/CIK/provider ID, or mark continuity unresolved. |
| Bulk, API, Windows and repository storage | Norgate subscription UI advertises ASCII/CSV and Python (Windows) integrations. Its product is a local Windows updater/database; no supported cloud bulk API or Parquet export contract was found. EULA prohibits redistribution and commercial use. | Daily aggregate S3 flat files are documented. Advanced gives all-history access; files start 2003-09-10 and prior-day files are published around 11:00 ET. Individual-plan terms prohibit redistribution/business use without separate agreement. | Norgate needs written approval for the proposed local Landing/Parquet retention workflow. Polygon needs entitlement and commercial status confirmed before a flat-file backfill. |
| Public price | Diamond is USD 433.13 / 6 months or USD 787.50 / 12 months. | Advanced is USD 199/month; business pricing is separate. | Pricing is an observed public price, not a purchase authorization. |

## Sharadar and Tiingo: bounded roles

- **Sharadar / Nasdaq Data Link** is not a long-history primary: documented stock history begins 1998 and fund history 1997. Stocks and fund prices are separate licensed products. Its `permaticker`/related-ticker model is useful evidence, but exact downloadable raw/corporate-action schema and product license must be inspected under entitlement.
- **Tiingo** is not a survivorship-safe primary: it advertises 1962+ prices and explicit raw/adjusted O/H/L/C/V, `divCash`, and `splitFactor`, but states delisted support is for symbols not yet recycled. Its search endpoint is beta and permaTicker querying is entitlement-dependent. It is therefore an API-level price/event cross-check only, never the historical universe authority.

## Primary selection gate

No provider is selected for execution until all of the following are answered in writing by the provider or demonstrated by a licensed bounded pilot:

1. May the licensed product be retained in the project's isolated Landing archive and converted to a local Parquet representation for internal research?
2. Which vendor identifier bridges ticker changes, mergers, and delisting/relisting; is its historical relation retained?
3. Which O/H/L/C/V fields are raw, split-adjusted, and dividend-adjusted, and which corporate-action event table and availability time supports each?
4. Does the intended ETF/REIT universe include active and delisted instruments, and how are types effective-dated?
5. What version/revision history exists for price, security master, and corporate actions?

## Sources

- Norgate packages/prices: <https://norgatedata.com/stockmarketpackages.php>
- Norgate content and delisted limitation: <https://norgatedata.com/data-content-tables.php>
- Norgate adjustment interfaces: <https://norgatedata.com/amibroker-usage.php>
- Norgate symbol lifecycle: <https://norgatedata.com/data-package-faq.php>
- Norgate EULA: <https://norgatedata.com/subscribe/eula.php>
- Polygon pricing: <https://polygon.io/stocks>
- Polygon daily flat files: <https://polygon.io/docs/flat-files/stocks/day-aggregates/2023/08>
- Polygon ticker reference: <https://polygon.io/docs/rest/crypto/tickers/all-tickers>
- Polygon splits: <https://polygon.io/docs/rest/stocks/corporate-actions/splits>
- Polygon market-data terms: <https://polygon.io/terms/market_data_terms.pdf>
- Nasdaq Data Link product organization: <https://docs.data.nasdaq.com/docs/data-organization>
- Tiingo EOD: <https://www.tiingo.com/documentation/end-of-day>
- Tiingo symbology: <https://www.tiingo.com/documentation/appendix/symbology>
