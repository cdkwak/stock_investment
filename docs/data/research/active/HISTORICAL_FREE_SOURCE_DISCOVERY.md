# Historical Free-Source Discovery

Status: `DISCOVERY_COMPLETE_WITH_NO_PRODUCTION_BACKFILL / KR_FORWARD_FIELDS_UNSUPPORTED`

This inventory is research routing, not collection authorization. No source was
called by repository code and no production or research dataset was created.

| Variable | Official/public candidate | Coverage/evidence | Classification | PIT / remaining gate |
|---|---|---|---|---|
| SOX price index | Nasdaq SOX historical download and official methodology | Index began 1993-12-01; official page exposes MAX/download controls | `VALIDATION_REQUIRED` | Download terms, automated-access permission, stable schema, finality, actual-vs-backtested flags |
| SOX identity/methodology | Nasdaq SOX methodology/factsheet | 30 large US-listed semiconductor companies; modified market-cap weighting | `DISCOVERY_ONLY` | Methodology is identity evidence, not a historical redistribution license |
| US high-yield credit spread | FRED `BAMLH0A0HYM2` | Daily percent, observed from 1996-12-31 | `VALIDATION_REQUIRED` | FRED operation approval, vintage/realtime availability, and predictive PIT resolver remain mandatory |
| US long-run valuation context | Robert Shiller/Yale monthly U.S. market data | Public monthly price, dividend, earnings, CPI, and CAPE history begins in 1871 | `RESEARCH_BACKFILL_APPROVAL_REQUIRED` | This is broad U.S. market research data, not S&P 500 daily forward valuation; freeze source version, publication/vintage rule, and license before any bounded research copy |
| Cross-sectional trailing valuation | Aswath Damodaran/NYU current and archived market-multiple workbooks | Public country/sector PE, PBV, sales and EV multiple snapshots; archive cadence varies by workbook | `VALIDATION_REQUIRED` | Cross-sectional snapshot methodology and workbook vintages do not establish a daily index series or point-in-time forward-consensus history |
| S&P 500 trailing P/E/P/B | S&P DJI Market Attributes/dashboard material | Current/report snapshots define trailing calculations | `VALIDATION_REQUIRED` | No free stable vintage-complete daily history established |
| S&P 500 forward P/E/P/B | No official free vintage series established | Search completed against S&P official material | `DISCOVERY_ONLY` | Forecast fiscal year, consensus period, publication date, vendor entitlement |
| U.S. EPS consensus | Business Quant Analyst Estimates API | Free-key endpoint documents current annual/quarterly consensus, high, low and reported actuals for US-listed securities | `VALIDATION_REQUIRED_US_ONLY` | It is not a Korean route, a 12M-forward composite, a revision field, or a historical estimate-vintage service |
| Korean Forward EPS / 1W and 1M revisions / Forward ROE | FnGuide/FnSpace paid API is a semantic candidate; Business Quant and OpenDART fail Korean forward-field identity | Company Guide publicly renders the FY1/FY2 Forward EPS terms with a literal `*` between them; a `+` weighted sum is only a contextual inference, so the roll formula remains unresolved. A 2019 FnSpace recipe gives `E312060`, `EPS(Fwd.12M, 지배)`, `원/주`, date-range request parameters and a sample result shape. Complete current API-product Korean coverage, current-schema stability, revision/ROE formulas and replayable PIT vintages remain unproved; the license forbids subscriber-database construction and application/third-party exposure | `UNSUPPORTED_FREE_OBSERVATION` for every field | No free route closes identity, Korean coverage, publication time, immutable PIT vintages, local retention, Dashboard display, redistribution, limits and reproducibility together; keep all four fields numeric-free |
| Filing actuals / trailing fundamentals | SEC EDGAR submissions and XBRL Company Facts APIs; OpenDART for Korean filings | Public filer history and extracted reported facts; filings update throughout the day | `DISCOVERY_ONLY` for valuation support | Official filing facts are not analyst estimates or consensus revisions. A future PIT fundamental contract must bind filing acceptance time, amendments, units, taxonomy and security identity |
| KOSPI/KOSPI200 trailing valuation | KRX official portal | Separate descriptive observation route exists | `OUT_OF_SCOPE_HERE` | Forward PER/PBR identity, licensing and aggregation belong only to `RQ-20260826T012440-3679`; this discovery makes no PER/PBR decision |
| Nasdaq/NDX/S&P index levels | Existing accepted global-price routes | Already represented by retained market-price infrastructure | `REJECTED` as duplicate acquisition in this task | Do not add a second source without a resilience/equivalence contract |

## Korean forward-field free-source decision

Evidence was rechecked on `2026-08-30 KST`; no API call, sample-value capture or
subscription was performed.

| Field | Closest documented Korean field | Free-candidate check | Independent decision |
|---|---|---|---|
| Forward EPS | Company Guide publishes FY1/FY2 controlling-net-income terms joined by literal `*` over a reference-month-end-issued-shares denominator; the 2019 recipe maps `E312060` to `EPS(Fwd.12M, 지배)`, `원/주`, and a date-range sample | The inferred `+` weighted sum is not provider-confirmed; `a`, denominator composition, current schema, complete Korean coverage, publication/PIT vintages and rights remain open. Business Quant is US-only and OpenDART exposes filed actuals | `UNSUPPORTED_FREE_OBSERVATION` |
| EPS revision, one week | FnSpace lists both an `EPS1` one-week revision ratio and a 12M-forward one-week adjusted change; the public page does not prove they are equivalent | No reviewed free source documents the requested Korean field, formula, comparable vintage, revision cause and immutable one-week lookback | `UNSUPPORTED_FREE_OBSERVATION` |
| EPS revision, one month | FnSpace lists an `EPS1` one-month revision ratio; the accessible public list does not establish a matching 12M-forward one-month field | Exact horizon/formula and a replayable one-month PIT vintage remain undocumented | `UNSUPPORTED_FREE_OBSERVATION` |
| Forward ROE | FnSpace lists `ROE(Fwd.12M, 지배)` and an unqualified 12M-forward ROE on the paid daily product | No reviewed free source closes numerator, denominator, accounting scope, Korean coverage, historical vintages and use rights | `UNSUPPORTED_FREE_OBSERVATION` |

The detailed identity, access, rights, cost, limits and reproducibility matrix is
in [Korean Forward Earnings and Revision PIT Contract](KR_FORWARD_EARNINGS_PIT_CONTRACT.md).
Public page visibility is evidence of neither automation permission nor
retention/display rights.

The Help's exact operator is source fact: `*` appears between the FY1 and FY2
weighted terms. Reading those terms as a `+` weighted sum is a contextual
inference only and is not promoted to a provider formula. Likewise, the Forward
EPS line names `기준월말 발행주식수` but does not itself attach the
common/preferred/treasury composition wording found in adjacent trailing
EPS/BPS explanations. Formula and denominator semantics therefore stay open.

For reproducibility, the 2019 recipe documents `ItemListApi` with
`apigb=A000006`, the `Consensus4Api` parameters `key`, `format`, `consolgb`,
`code`, `frdate`, `todate`, and `item`, a `20180621` through `20190620` sample,
ten-field batching, a merge on `DT`, and a printed result head of
`[5 rows x 34 columns]`. This is historical sample documentation, not evidence
that the current entitled schema or its historical values are unchanged.

## Official evidence inspected

Unless a source page states an earlier effective/update date, `2026-08-30 KST`
is the evidence retrieval date.

- `https://indexes.nasdaq.com/docs/methodology_SOX.pdf`
- `https://www.nasdaq.com/market-activity/index/sox/historical`
- `https://indexes.nasdaq.com/Index/Overview/SOX`
- `https://fred.stlouisfed.org/series/BAMLH0A0HYM2`
- `https://fred.stlouisfed.org/docs/api/fred/series_observations.html`
- `https://fred.stlouisfed.org/docs/api/fred/realtime_period.html`
- `https://www.econ.yale.edu/~shiller/data.htm`
- `https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/data.html`
- `https://businessquant.com/docs/api/estimates`
- `https://businessquant.com/docs/api/`
- `https://businessquant.com/pricing`
- `https://businessquant.com/data-sources`
- `https://businessquant.com/commercial-licensing`
- `https://businessquant.com/terms-of-use`
- `https://www.fnspace.com/DataMart/RequestInfo?aid=A000006&cid_p=C001&pid=P0003`
- `https://policy.fnguide.com/FnSpace/Terms`
- `https://wcomp.fnguide.com/CompanyInfo/Consensus`
- `https://wcomp.fnguide.com/Help/Guide?cmp_cd=466690`
- `https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001`
- `https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003`
- `https://www.sec.gov/search-filings/edgar-application-programming-interfaces`
- `https://www.spglobal.com/spdji/en/indices/equity/sp-500/`

The Business Quant result is an explicitly US-only candidate and the OpenDART
and SEC results are filed-actual evidence only. None relaxes the Korean
consensus/revision blocker. FnSpace's field labels do not overcome its paid
access, restrictive license or missing historical-vintage evidence.

No row is `PRODUCTION_BACKFILL_APPROVED` or `RESEARCH_BACKFILL_APPROVED` yet.
