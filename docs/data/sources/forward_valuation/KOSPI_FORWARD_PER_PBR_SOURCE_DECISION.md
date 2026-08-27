# KOSPI Forward PER / PBR Source Decision

Status: `FORWARD_PER_UNSUPPORTED / FORWARD_PBR_UNSUPPORTED / NUMERIC_USE_FORBIDDEN`

Decision date: `2026-08-26 KST`

Evidence check date: `2026-08-26 KST`

This decision is documentation-only. It selects no vendor, creates no
subscription, calls no API, and grants no collection, retention, display,
derived-display, redistribution, or predictive permission.

## Exact requested identity

The requested numerator/universe is the KRX **KOSPI** broad index, not an MSCI
Korea index, a generic vendor `Korea` country aggregate, KOSPI 200, or a current
list of Korean equities. A usable row would have to identify the exact KRX index
and version at its as-of time and preserve historical constituents and weights.

The existing `kr_index_fundamental_daily` contract remains separate. It stores
official KRX KOSPI ticker `1001` weighted PER/PBR and dividend yield as
descriptive, non-predictive source values. The KRX Data Marketplace labels the
official screen only `PER/PBR/배당수익률`; neither that page nor the accepted local
contract identifies the values as analyst forecasts. They must never be
relabeled as forward values.

Primary evidence:

- [KRX Data Marketplace, index PER/PBR/dividend-yield screen](https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd?vsView=Y), accessed 2026-08-26; the catalog names the screen but publishes no forward horizon there.
- [Active KRX index-fundamental runbook](../../operations/KRX_INDEX_FUNDAMENTAL_DAILY.md) and `src/stock_data/contracts/kr_index_fundamental_daily.py`, current repository authority for the retained trailing/descriptive series.

## Candidate-source comparison

`Not evidenced` means the reviewed primary public material does not establish
the field. It is not an assertion that a separately negotiated product cannot
provide it.

| Source class | Exact KOSPI identity | Forecast horizon and denominator | Universe, loss treatment, aggregation | Time/revision/PIT evidence | Schema/access | Rights/cost evidence | Decision |
|---|---|---|---|---|---|---|---|
| KRX official index fundamentals | Exact KOSPI `1001` is accepted locally | No forecast horizon; retained fields are descriptive weighted PER/PBR | Source values are retained without reconstructing a forward denominator | Publication/revision finality remains unresolved | Existing contracted daily source route | Existing project use does not grant a forward-estimate right | Not a forward source; trailing values remain separate |
| LSEG I/B/E/S Global Aggregates | Public catalog says 16,000+ indices but names third-party constituent/weight sources such as MSCI, FTSE and S&P; KOSPI/KRX is not positively identified | Official developer material exposes fiscal-year ratios (`FY0..FY3`); separate custom-ratio guidance uses calendarized `CY1..CY3`. No reviewed document selects NTM versus FY1 for exact KOSPI | Global Aggregates describes bottom-up forecasts and third-party share weights. Custom construction guidance demonstrates that historical constituents, missing values, negative estimates and aggregation choices are material, but no reviewed KOSPI-specific rule/data dictionary closes them | Monthly history from 1985 and weekly history from 2006 are advertised; this does not prove exact release timestamps or immutable vintages for a KOSPI field | API/Web Service/FTP/bulk and other delivery classes are advertised; exact entitled KOSPI field IDs and response schema are not public evidence | Product access says to request details. LSEG states granular entitlement applies and even derived/redistributed dashboard output remains licensing-controlled; direct index-provider licensing may also be required | Forward PER: `UNSUPPORTED`; Forward PBR: `UNSUPPORTED` |
| FactSet Estimates + Market Aggregates | Official material says Market Aggregates cover 3,500+ commercial/exchange indices, but the reviewed material does not positively identify exact KOSPI membership/weights | Point-in-time estimates support annual, quarterly, NTM/LTM and calendarized periods; no reviewed source defines a KOSPI forward-PER field or forecast-book-value PBR | Market Aggregates combine Fundamentals, Estimates and Prices, but no reviewed KOSPI-specific inclusion, losses, negative denominator, share-class, or weighting rule is stated | PIT consensus snapshots are documented from December 2009, with 100-day and 45-day windows and methodology changes; that does not establish an index aggregate's exact vintage | Daily estimates history and OnDemand access are described; no exact KOSPI field/schema sample is evidenced | Public material does not grant this project internal retention, Dashboard/derived display, or redistribution rights and gives no applicable price/limit | Forward PER: `UNSUPPORTED`; Forward PBR: `UNSUPPORTED` |
| MSCI index fundamentals | Transparent methodology applies to MSCI indexes, not the KRX KOSPI identity | MSCI defines `P/E Fwd`; its `P/BV` example uses book value, not a forecast-book-value horizon | MSCI documents market-cap/fundamental aggregation, inclusion factors, FX and missing-value handling for its own index universe | Corporate-event adjustments/restatements are possible; KOSPI vintage semantics are not supplied | Methodology and selected current index metrics are public; this is not a KOSPI API schema | MSCI prohibits reproduction, redistribution, derived works and service use without written permission/license | Methodology reference only; wrong index identity and no forward PBR |

Primary candidate evidence, all checked 2026-08-26:

- [LSEG I/B/E/S Global Aggregates catalog](https://www.lseg.com/en/data-catalogue/company-data/ibes-estimates/global-aggregates): coverage, frequency, delivery classes, history and third-party index weighting.
- [LSEG Global Aggregates developer guide](https://developers.lseg.com/en/article-catalog/article/how-to-collect-datastream-ibes-global-aggregate-earnings-data-with-python-and-codebook): fiscal-year ratio periods and credentialed DSWS access.
- [LSEG forward-looking index-ratio guidance](https://developers.lseg.com/en/article-catalog/article/forward-looking-index-ratio-analysis): historical-constituent/calendarization problem and direct index-license warning.
- [LSEG data-redistribution policy](https://www.lseg.com/en/data-analytics/market-data/data-redistribution): entitlement and raw/derived dashboard redistribution boundary.
- [FactSet Point-in-Time Database Methodology](https://insight.factset.com/hubfs/Resources%20Section/White%20Papers/ID11996_point_in_time.pdf): consensus windows, local-market snapshot timing, history boundary, methodology changes and PIT field families.
- [FactSet statistical-package guide](https://insight.factset.com/hubfs/Website_Downloads/Statistical%20Package%20Integration/MATLABRUserGuide.pdf): Estimates daily history and Market Aggregates product scope.
- [MSCI Fundamental Data Methodology](https://www.msci.com/indexes/documents/methodology/0_MSCI_Fundamental_Data_Methodology_20240625.pdf): MSCI P/E-forward and P/BV aggregation definitions.
- [MSCI Index Terms](https://www.msci.com/legal/index-terms): license and redistribution restrictions.

## Independent decisions

### KOSPI forward PER — `UNSUPPORTED`

LSEG I/B/E/S and FactSet are credible licensed **source classes** for a future
evaluation, but neither is currently a selectable dataset for this project.
The reviewed primary material does not bind an entitled field to exact KOSPI,
choose NTM/FY1/CY1, define numerator and diluted/adjusted earnings denominator,
state coverage and negative-earnings treatment, give a reproducible KOSPI
aggregation, expose exact vintage/revision timestamps, provide an accepted
schema sample, or grant the required local retention and Dashboard/derived
display rights. No numeric value may be collected or shown.

### KOSPI forward PBR — `UNSUPPORTED`

No reviewed source defines an exact KOSPI ratio using forecast book value with
a named horizon and consensus basis. KRX weighted PBR and MSCI `P/BV` use a
book-value measure without proving a forward-book denominator. A vendor's label
`P/B`, `P/BV`, or `forward valuation` is insufficient. This field remains
numeric-free independently of the forward-PER decision.

## Reopen gate

Reclassify either field to `SELECTABLE_CANDIDATE` only after written primary or
contractual evidence closes every item below for that field independently:

1. exact KRX KOSPI identifier, index variant/version, constituent effective
   dates, weights, share classes and corporate-action handling;
2. estimate horizon (`NTM`, `FY1`, calendarized period, or another exact basis),
   consensus statistic/window, currency, accounting basis, dilution and
   numerator/denominator units;
3. missing coverage, stale estimates, loss/negative/zero denominator and
   multi-share-class treatment, plus the exact aggregate formula;
4. observation, provider-publication, activation and revision timestamps,
   historical membership and immutable or replayable estimate vintages;
5. exact API/file identifiers, schema, sample response, frequency, history,
   rate/row limits and reproducible extraction procedure;
6. entitlement and written rights for project-local access, raw retention,
   normalized retention, Dashboard numeric display, derived display and any
   remote/redistributed view, with price and limits;
7. a contract and offline tests that keep the accepted KRX trailing series
   distinct and fail closed on absent rights, schema, PIT or semantic evidence.

Until then the Dashboard may explain `선행 PER/PBR: 지원 근거 없음` and continue
to display the separately labelled accepted KRX descriptive PER/PBR. It must not
estimate, backsolve, average vendors, or substitute MSCI Korea/country values.
